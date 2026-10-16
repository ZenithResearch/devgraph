use super::*;
use bytes::Bytes;
use devgraph_client_core::prepare_read;
use serde::Serialize;
use std::{
    sync::{
        atomic::{AtomicU8, Ordering},
        Arc,
    },
    time::Duration,
};
use tokio::time::Instant;

/// One irreversible dispatch boundary shared by execution and cancellation.
#[derive(Default)]
pub struct DispatchState(AtomicU8);
impl DispatchState {
    pub fn begin(&self) -> bool {
        self.0
            .compare_exchange(0, 2, Ordering::AcqRel, Ordering::Acquire)
            .is_ok()
    }
    /// False proves that this operation cannot dispatch after cancellation.
    pub fn cancel(&self) -> bool {
        match self
            .0
            .compare_exchange(0, 1, Ordering::AcqRel, Ordering::Acquire)
        {
            Ok(_) => false,
            Err(previous) => previous == 2,
        }
    }
    pub fn dispatched(&self) -> bool {
        self.0.load(Ordering::Acquire) == 2
    }
}

#[derive(Clone, Serialize)]
pub struct ResponseHead {
    pub status: u16,
    pub content_type: String,
    pub content_encoding: String,
    pub content_length: Option<String>,
    pub limit: usize,
    pub dispatched: bool,
}
pub struct NativeResponse {
    head: ResponseHead,
    response: reqwest::Response,
    pending: Bytes,
    received: usize,
    emitted: usize,
    deadline: Instant,
    finished: bool,
}
fn transport_error(dispatched: bool) -> NativeError {
    NativeError::new(
        if dispatched {
            "outcome_unknown"
        } else {
            "transport_error"
        },
        dispatched,
    )
}
impl NativeResponse {
    pub fn head(&self) -> &ResponseHead {
        &self.head
    }
    pub fn deadline(&self) -> Instant {
        self.deadline
    }
    pub async fn next_chunk(
        &mut self,
        cancel: &CancellationToken,
    ) -> Result<(Vec<u8>, usize, bool)> {
        if self.finished {
            return Err(NativeError::new("stream_closed", self.head.dispatched));
        }
        let mut output = Vec::with_capacity(CHUNK_BYTES);
        loop {
            if Instant::now() >= self.deadline {
                return Err(transport_error(self.head.dispatched));
            }
            if cancel.is_cancelled() {
                return Err(NativeError::new(
                    if self.head.dispatched {
                        "outcome_unknown"
                    } else {
                        "cancelled"
                    },
                    self.head.dispatched,
                ));
            }
            if !self.pending.is_empty() {
                let take = (CHUNK_BYTES - output.len()).min(self.pending.len());
                output.extend_from_slice(&self.pending.split_to(take));
                self.emitted += take;
                if output.len() == CHUNK_BYTES {
                    return Ok((output, self.emitted, false));
                }
            }
            let chunk = tokio::select! {
                _=cancel.cancelled()=>return Err(NativeError::new(if self.head.dispatched {"outcome_unknown"} else {"cancelled"},self.head.dispatched)),
                result=tokio::time::timeout_at(self.deadline,self.response.chunk())=>result.map_err(|_|transport_error(self.head.dispatched))?.map_err(|_|transport_error(self.head.dispatched))?,
            };
            match chunk {
                None => {
                    self.finished = true;
                    return Ok((output, self.emitted, true));
                }
                Some(chunk) => {
                    self.received = self
                        .received
                        .checked_add(chunk.len())
                        .ok_or_else(|| transport_error(self.head.dispatched))?;
                    if self.received > self.head.limit {
                        return Err(NativeError::new(
                            if self.head.dispatched {
                                "outcome_unknown"
                            } else {
                                "response_too_large"
                            },
                            self.head.dispatched,
                        ));
                    }
                    self.pending = chunk;
                }
            }
        }
    }
}
fn one_header(response: &reqwest::Response, name: &str) -> Result<Option<String>> {
    let values = response.headers().get_all(name).iter().collect::<Vec<_>>();
    if values.len() > 1 {
        return Err(NativeError::new("invalid_response", false));
    }
    values
        .first()
        .map(|v| {
            v.to_str()
                .map(str::to_owned)
                .map_err(|_| NativeError::new("invalid_response", false))
        })
        .transpose()
}
fn checked_response(
    response: reqwest::Response,
    limit: usize,
    deadline: Instant,
    dispatched: bool,
) -> Result<NativeResponse> {
    let status = response.status().as_u16();
    let limit = if status >= 400 {
        65_536.min(limit)
    } else {
        limit
    };
    let invalid = || {
        NativeError::new(
            if dispatched {
                "outcome_unknown"
            } else {
                "invalid_response"
            },
            dispatched,
        )
    };
    let encoding = one_header(&response, "content-encoding")
        .map_err(|_| invalid())?
        .unwrap_or_else(|| "identity".into());
    if !encoding.eq_ignore_ascii_case("identity") {
        return Err(invalid());
    }
    let content_type = one_header(&response, "content-type")
        .map_err(|_| invalid())?
        .ok_or_else(invalid)?;
    let expected_type = if (200..300).contains(&status) {
        "application/json"
    } else if status >= 400 {
        "application/problem+json"
    } else {
        return Err(invalid());
    };
    if content_type.len() > 128
        || !content_type
            .split(';')
            .next()
            .is_some_and(|s| s.trim().eq_ignore_ascii_case(expected_type))
    {
        return Err(invalid());
    }
    let content_length = one_header(&response, "content-length").map_err(|_| invalid())?;
    if content_length.as_ref().is_some_and(|s| {
        s.is_empty()
            || !s.bytes().all(|b| b.is_ascii_digit())
            || s.parse::<usize>().map_or(true, |n| n > limit)
    }) {
        return Err(invalid());
    }
    Ok(NativeResponse {
        head: ResponseHead {
            status,
            content_type,
            content_encoding: "identity".into(),
            content_length,
            limit,
            dispatched,
        },
        response,
        pending: Bytes::new(),
        received: 0,
        emitted: 0,
        deadline,
        finished: false,
    })
}
impl NativeClient {
    pub(crate) async fn open_response(
        &self,
        builder: reqwest::RequestBuilder,
        limit: usize,
        deadline: Instant,
        cancel: &CancellationToken,
        dispatched: bool,
    ) -> Result<NativeResponse> {
        let response = tokio::select! {
            _=cancel.cancelled()=>return Err(NativeError::new(if dispatched {"outcome_unknown"} else {"cancelled"},dispatched)),
            result=tokio::time::timeout_at(deadline,builder.send())=>result.map_err(|_|transport_error(dispatched))?.map_err(|_|transport_error(dispatched))?,
        };
        checked_response(response, limit, deadline, dispatched)
    }
    pub async fn read(
        &self,
        descriptor: &[u8],
        lease_expires_at: u64,
        cancel: &CancellationToken,
    ) -> Result<NativeResponse> {
        let remaining = lease_expires_at
            .checked_sub(now()?)
            .filter(|n| *n > 0)
            .ok_or(NativeError::new("read_context_expired", false))?;
        let deadline = Instant::now() + Duration::from_secs(30.min(remaining));
        let request =
            prepare_read(descriptor).map_err(|_| NativeError::new("invalid_request", false))?;
        if !request.path().starts_with('/') || request.path().starts_with("//") {
            return Err(NativeError::new("invalid_request", false));
        }
        let profile = self.profile.clone();
        let token = tokio::select! {
            _=cancel.cancelled()=>return Err(NativeError::new("cancelled",false)),
            result=tokio::time::timeout_at(deadline,tokio::task::spawn_blocking(move||profile.read_credential()))=>result.map_err(|_|transport_error(false))?.map_err(|_|denied())??,
        };
        let method = match request.method() {
            "GET" => reqwest::Method::GET,
            "POST" => reqwest::Method::POST,
            _ => return Err(NativeError::new("invalid_request", false)),
        };
        let mut builder = self
            .http
            .request(method, format!("{ORIGIN}{}", request.path()))
            .header("Accept-Encoding", "identity")
            .header("Cache-Control", "no-store")
            .header("Accept", "application/json")
            .bearer_auth(token.as_str());
        if let Some(body) = request.body() {
            builder = builder
                .header("Content-Type", "application/json")
                .body(body.to_vec());
        }
        self.open_response(
            builder,
            request.max_response_bytes(),
            deadline,
            cancel,
            false,
        )
        .await
    }
    pub async fn execute(
        &self,
        authorized: AuthorizedWork,
        actor: &str,
        cancel: &CancellationToken,
        dispatch: Arc<DispatchState>,
    ) -> Result<NativeResponse> {
        if authorized.actor_id() != actor {
            return Err(NativeError::new("actor_changed", false));
        }
        let deadline = Instant::now() + Duration::from_secs(30);
        let profile = self.profile.clone();
        let checked = tokio::task::spawn_blocking(move || {
            authority::verify_authorized(&profile, &authorized, now()?)?;
            Ok::<_, NativeError>(authorized)
        });
        let authorized = tokio::select! {
            _=cancel.cancelled()=>return Err(NativeError::new("cancelled",false)),
            result=tokio::time::timeout_at(deadline,checked)=>result.map_err(|_|transport_error(false))?.map_err(|_|denied())??,
        };
        if cancel.is_cancelled() {
            return Err(NativeError::new("cancelled", false));
        }
        if authorized.expires_at() <= now()? {
            return Err(NativeError::new("authorization_expired", false));
        }
        let builder = self
            .http
            .post(format!(
                "{ORIGIN}/sdk/{}",
                if authorized.request.value()["schema"] == "devgraph.work-request.v2" {
                    "todo-operations/v2"
                } else {
                    "work-operations/v1"
                }
            ))
            .header("Accept-Encoding", "identity")
            .header("Cache-Control", "no-store")
            .header("Accept", "application/json")
            .header("Content-Type", "application/json")
            .header("Idempotency-Key", authorized.key.as_str())
            .header(
                "X-Devgraph-Work-Authority",
                castalia_wallet_devgraph_presentation::base64url(&authorized.projection),
            )
            .header("X-Devgraph-Receiver-Profile", authorized.receiver_profile)
            .body(authorized.request.canonical().to_vec());
        // Conservative: cancellation/loss after this point may follow HTTP dispatch.
        if !dispatch.begin() {
            return Err(NativeError::new("cancelled", false));
        }
        self.open_response(builder, 8 * 1024 * 1024, deadline, cancel, true)
            .await
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    fn response(bytes: impl Into<reqwest::Body>, dispatched: bool, cap: usize) -> NativeResponse {
        let response = ::http::Response::builder()
            .header("content-type", "application/json")
            .body(bytes.into())
            .unwrap();
        checked_response(
            response.into(),
            cap,
            Instant::now() + Duration::from_secs(30),
            dispatched,
        )
        .unwrap()
    }
    #[test]
    fn cancellation_and_dispatch_share_one_atomic_boundary() {
        let state = Arc::new(DispatchState::default());
        let barrier = Arc::new(std::sync::Barrier::new(2));
        let worker_state = state.clone();
        let worker_barrier = barrier.clone();
        let worker = std::thread::spawn(move || {
            worker_barrier.wait();
            worker_state.begin()
        });
        assert!(!state.cancel());
        barrier.wait();
        assert!(!worker.join().unwrap());
        assert!(!state.dispatched());
        let state = DispatchState::default();
        assert!(state.begin());
        assert!(state.cancel());
        assert!(state.dispatched());
        assert!(!state.begin());
    }
    #[tokio::test]
    async fn more_than_one_megabyte_is_pulled_in_bounded_chunks() {
        let expected = vec![b'x'; 1_048_577];
        let mut reader = response(expected.clone(), false, 8 * 1024 * 1024);
        let mut actual = Vec::new();
        loop {
            let (chunk, total, done) = reader.next_chunk(&CancellationToken::new()).await.unwrap();
            assert!(chunk.len() <= CHUNK_BYTES);
            actual.extend_from_slice(&chunk);
            assert_eq!(total, actual.len());
            if done {
                break;
            }
        }
        assert_eq!(actual, expected);
        assert_eq!(
            reader
                .next_chunk(&CancellationToken::new())
                .await
                .unwrap_err()
                .code,
            "stream_closed"
        );
    }
    #[test]
    fn rejects_encoding_duplicate_headers_and_oversized_length_before_body() {
        for (name, value) in [
            ("content-encoding", "gzip"),
            ("content-length", "999999999999999999999"),
            ("content-length", "65537"),
        ] {
            let raw = ::http::Response::builder()
                .header("content-type", "application/json")
                .header(name, value)
                .body(reqwest::Body::from("{}"))
                .unwrap();
            let error = checked_response(
                raw.into(),
                65536,
                Instant::now() + Duration::from_secs(30),
                true,
            )
            .err()
            .unwrap();
            assert_eq!(error, NativeError::new("outcome_unknown", true));
        }
        let raw = ::http::Response::builder()
            .header("content-type", "application/json")
            .header("content-type", "application/json")
            .body(reqwest::Body::from("{}"))
            .unwrap();
        assert!(checked_response(raw.into(), 65536, Instant::now(), false).is_err());
    }
    #[test]
    fn normalizes_valid_identity_encoding_for_bridge_consumers() {
        let raw = ::http::Response::builder()
            .header("content-type", "application/json")
            .header("content-encoding", "Identity")
            .body(reqwest::Body::from("{}"))
            .unwrap();
        let response = checked_response(
            raw.into(),
            65536,
            Instant::now() + Duration::from_secs(30),
            false,
        )
        .unwrap();
        assert_eq!(response.head().content_encoding, "identity");
    }
    #[tokio::test]
    async fn streaming_overflow_partial_failure_cancellation_and_deadline_are_terminal() {
        let mut read = response(vec![0; 100], false, 99);
        assert_eq!(
            read.next_chunk(&CancellationToken::new())
                .await
                .unwrap_err()
                .code,
            "response_too_large"
        );
        let chunks: Vec<std::io::Result<Vec<u8>>> = vec![
            Ok(vec![0; CHUNK_BYTES]),
            Err(std::io::Error::other("fixture private detail")),
        ];
        let mut read = response(
            reqwest::Body::wrap_stream(futures_util::stream::iter(chunks)),
            true,
            8 * 1024 * 1024,
        );
        assert_eq!(
            read.next_chunk(&CancellationToken::new())
                .await
                .unwrap()
                .0
                .len(),
            CHUNK_BYTES
        );
        assert_eq!(
            read.next_chunk(&CancellationToken::new())
                .await
                .unwrap_err(),
            NativeError::new("outcome_unknown", true)
        );
        let cancel = CancellationToken::new();
        cancel.cancel();
        let mut read = response("{}", true, 100);
        assert_eq!(
            read.next_chunk(&cancel).await.unwrap_err(),
            NativeError::new("outcome_unknown", true)
        );
        let forever = futures_util::stream::pending::<std::io::Result<Vec<u8>>>();
        let mut read = response(reqwest::Body::wrap_stream(forever), false, 100);
        read.deadline = Instant::now() + Duration::from_millis(5);
        assert_eq!(
            read.next_chunk(&CancellationToken::new())
                .await
                .unwrap_err()
                .code,
            "transport_error"
        );
    }
    #[tokio::test]
    async fn actual_http_problem_body_reaches_definitive_rejection_decoder() {
        use devgraph_client_core::{ErrorKind, PreparedMutation, ResponseMetadata};
        use std::io::{Read, Write};
        let mutation = PreparedMutation::parse(br#"{"schema":"devgraph.work-request.v1","operation":"create","kind":"Task","id":"http-fixture","expected_version":null,"payload":{"id":"http-fixture","title":"Fixture"}}"#, "fixture-http-idempotency-key").unwrap();
        for (status, title, expected_kind) in [
            (403, "Named Work authority denied", ErrorKind::Rejected),
            (503, "Service unavailable", ErrorKind::Transport),
        ] {
            let body = serde_json::to_vec(&serde_json::json!({"type":"about:blank","title":title,"status":status,"detail":"Synthetic fixture"})).unwrap();
            let expected_body = body.clone();
            let listener = std::net::TcpListener::bind(("127.0.0.1", 0)).unwrap();
            listener.set_nonblocking(true).unwrap();
            let address = listener.local_addr().unwrap();
            let server = std::thread::spawn(move || {
                let deadline = std::time::Instant::now() + Duration::from_secs(3);
                let mut stream = loop {
                    match listener.accept() {
                        Ok((stream, _)) => break stream,
                        Err(error) if error.kind() == std::io::ErrorKind::WouldBlock => {
                            assert!(std::time::Instant::now() < deadline);
                            std::thread::sleep(Duration::from_millis(5));
                        }
                        Err(error) => panic!("fixture listener failed: {error}"),
                    }
                };
                stream.set_nonblocking(false).unwrap();
                stream
                    .set_read_timeout(Some(Duration::from_secs(2)))
                    .unwrap();
                let mut request = Vec::new();
                let mut buf = [0; 1024];
                while !request.windows(4).any(|b| b == b"\r\n\r\n") {
                    let read = stream.read(&mut buf).unwrap();
                    assert!(read > 0);
                    request.extend_from_slice(&buf[..read]);
                    assert!(request.len() < 8192);
                }
                let header_end = request.windows(4).position(|b| b == b"\r\n\r\n").unwrap() + 4;
                while request.len() < header_end + 2 {
                    let read = stream.read(&mut buf).unwrap();
                    assert!(read > 0);
                    request.extend_from_slice(&buf[..read]);
                }
                write!(stream,"HTTP/1.1 {status} Fixture\r\nContent-Type: application/problem+json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n",body.len()).unwrap();
                stream.write_all(&body).unwrap();
            });
            let fixture = profile::tests::fixture();
            let client = NativeClient::new(fixture.profile).unwrap();
            // Only this private unit fixture supplies an ephemeral server URL;
            // public read/execute methods always use fixed production ORIGIN.
            let builder = client
                .http
                .post(format!("http://{address}/fixture"))
                .body("{}");
            let mut response = client
                .open_response(
                    builder,
                    8 * 1024 * 1024,
                    Instant::now() + Duration::from_secs(2),
                    &CancellationToken::new(),
                    true,
                )
                .await
                .unwrap();
            assert_eq!(response.head().limit, 65536);
            assert!(response.head().dispatched);
            let metadata = ResponseMetadata {
                status,
                content_type: response.head().content_type.clone(),
                content_encoding: Some(response.head().content_encoding.clone()),
                max_bytes: Some(response.head().limit),
            };
            let mut received = Vec::new();
            loop {
                let (chunk, _, done) = response
                    .next_chunk(&CancellationToken::new())
                    .await
                    .unwrap();
                received.extend_from_slice(&chunk);
                if done {
                    break;
                }
            }
            assert_eq!(received, expected_body);
            assert_eq!(
                mutation
                    .decode_response(&received, &metadata)
                    .unwrap_err()
                    .kind,
                expected_kind
            );
            server.join().unwrap();
        }
    }
}
