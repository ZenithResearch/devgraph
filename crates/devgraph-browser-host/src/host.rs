use castalia_wallet_devgraph_presentation::{actor_id, base64url};
use devgraph_client_native::{
    decode_bytes, now, opaque_id, AuthorizedWork, CancellationToken, DispatchState, NativeClient,
    NativeError, NativeResponse, Result, FRAME_BYTES, ORIGIN,
};
use devgraph_work_protocol::{canonical_json, strict_json};
use serde::Deserialize;
use serde_json::{json, Value};
use std::{
    collections::{HashMap, HashSet},
    sync::{
        atomic::{AtomicBool, Ordering},
        Arc,
    },
};
use tokio::sync::Mutex;

#[derive(Clone)]
pub struct Host {
    client: Backend,
    state: Arc<Mutex<State>>,
    shutdown: CancellationToken,
}
#[derive(Clone)]
enum Backend {
    Native(Arc<NativeClient>),
    #[cfg(test)]
    Unavailable,
    #[cfg(test)]
    Fixture(Arc<FixtureBackend>),
}
#[derive(Default)]
struct State {
    connection: Option<Connection>,
    seen: HashSet<String>,
    // Every generated handle stays reserved until disconnect, including expired
    // read leases/streams: late cleanup must never resolve to a newer control.
    generated_handles: HashSet<String>,
    cancelled: HashSet<String>,
    terminal_history: HashMap<String, bool>,
    aliases: HashMap<String, Arc<Mutation>>,
    reserved_aliases: HashMap<u64, usize>,
    next_generation: u64,
    shutdown: CancellationToken,
    disposed: bool,
    pending: HashMap<String, Pending>,
    authorizations: HashMap<String, (Authority, Arc<Mutation>)>,
    streams: HashMap<String, Arc<Stream>>,
    reads: usize,
    work: Option<Arc<Mutation>>,
}
struct Connection {
    id: String,
    actor: String,
    read: Option<ReadLease>,
}
struct ReadLease {
    id: String,
    expires: u64,
}
enum Authority {
    Native(Box<AuthorizedWork>),
    #[cfg(test)]
    Fixture {
        expires: u64,
    },
}
impl Authority {
    fn expires_at(&self) -> u64 {
        match self {
            Self::Native(a) => a.expires_at(),
            #[cfg(test)]
            Self::Fixture { expires } => *expires,
        }
    }
}
#[cfg(test)]
struct FixtureBackend {
    authorize_started: tokio::sync::Notify,
    authorize_ready: tokio::sync::Semaphore,
    execute_started: tokio::sync::Notify,
    execute_ready: tokio::sync::Semaphore,
    dispatched: tokio::sync::Notify,
    response_ready: tokio::sync::Semaphore,
    dispatches: std::sync::atomic::AtomicUsize,
    expires: std::sync::atomic::AtomicU64,
}
#[cfg(test)]
impl FixtureBackend {
    fn new() -> Self {
        Self {
            authorize_started: tokio::sync::Notify::new(),
            authorize_ready: tokio::sync::Semaphore::new(0),
            execute_started: tokio::sync::Notify::new(),
            execute_ready: tokio::sync::Semaphore::new(0),
            dispatched: tokio::sync::Notify::new(),
            response_ready: tokio::sync::Semaphore::new(0),
            dispatches: std::sync::atomic::AtomicUsize::new(0),
            expires: std::sync::atomic::AtomicU64::new(now().unwrap() + 60),
        }
    }
}
struct Mutation {
    generation: u64,
    cancel: CancellationToken,
    dispatched: Arc<DispatchState>,
}
struct Pending {
    mutation: Option<Arc<Mutation>>,
    cancel: CancellationToken,
    dispatched: Arc<DispatchState>,
}
struct Stream {
    mutation: Option<Arc<Mutation>>,
    reader: Mutex<StreamReader>,
    cancel: CancellationToken,
    dispatched: bool,
    lease_expires: Option<u64>,
    pulling: AtomicBool,
}
struct StreamReader {
    response: ResponseReader,
    next_seq: i64,
    previous_ack: i64,
    done: bool,
}
enum ResponseReader {
    Native(Box<NativeResponse>),
    #[cfg(test)]
    Fixture(FixtureResponse),
}
impl ResponseReader {
    async fn next_chunk(&mut self, cancel: &CancellationToken) -> Result<(Vec<u8>, usize, bool)> {
        match self {
            Self::Native(response) => response.next_chunk(cancel).await,
            #[cfg(test)]
            Self::Fixture(fixture) => {
                fixture.started.notify_one();
                if let Some(ready) = fixture.ready.take() {
                    ready.await.unwrap();
                    if let Some(completed) = &fixture.completed {
                        completed.notify_one();
                    }
                    Ok((b"{}".to_vec(), 2, fixture.done))
                } else {
                    std::future::pending().await
                }
            }
        }
    }
}
#[cfg(test)]
struct FixtureResponse {
    done: bool,
    ready: Option<tokio::sync::oneshot::Receiver<()>>,
    completed: Option<Arc<tokio::sync::Notify>>,
    started: Arc<tokio::sync::Notify>,
    dropped: Arc<AtomicBool>,
}
#[cfg(test)]
impl Drop for FixtureResponse {
    fn drop(&mut self) {
        self.dropped.store(true, Ordering::Release);
    }
}
#[derive(Deserialize)]
#[serde(tag = "action", deny_unknown_fields)]
enum Command {
    #[serde(rename = "connect")]
    Connect {
        origin: String,
        document_id: String,
        wallet_public_key: String,
    },
    #[serde(rename = "request_read_access")]
    ReadAccess { connection_id: String },
    #[serde(rename = "read")]
    Read {
        connection_id: String,
        read_context: String,
        request: Value,
    },
    #[serde(rename = "authorize")]
    Authorize {
        connection_id: String,
        request_b64: String,
        idempotency_key: String,
        presentation_b64: String,
    },
    #[serde(rename = "execute")]
    Execute {
        connection_id: String,
        authorization_id: String,
    },
    #[serde(rename = "pull")]
    Pull {
        connection_id: String,
        stream_id: String,
        ack_seq: i64,
    },
    #[serde(rename = "cancel")]
    Cancel {
        connection_id: String,
        target_id: String,
    },
    #[serde(rename = "dispose")]
    Dispose { connection_id: String },
}
fn failure(code: &'static str) -> NativeError {
    NativeError::new(code, false)
}
fn valid_id(id: &str) -> bool {
    !id.is_empty() && id.len() <= 128 && id.bytes().all(|b| b.is_ascii_graphic())
}
fn bound(state: &State, id: &str) -> Result<()> {
    if state.connection.as_ref().is_some_and(|c| c.id == id) {
        Ok(())
    } else {
        Err(failure("invalid_connection"))
    }
}
const MAX_CONTROLS: usize = 65_536;
const MAX_ALIASES: usize = 131_072;
impl State {
    fn clear(&mut self) {
        if let Some(work) = &self.work {
            work.dispatched.cancel();
            work.cancel.cancel();
        }
        for pending in self.pending.values() {
            pending.dispatched.cancel();
            pending.cancel.cancel();
        }
        for stream in self.streams.values() {
            stream.cancel.cancel();
        }
        self.connection = None;
        self.disposed = true;
        self.authorizations.clear();
        self.streams.clear();
        self.pending.clear();
        self.aliases.clear();
        self.terminal_history.clear();
        self.reserved_aliases.clear();
        self.seen.clear();
        self.generated_handles.clear();
        self.cancelled.clear();
        self.reads = 0;
        self.work = None;
        self.shutdown.cancel();
    }
    fn owns(&self, mutation: &Mutation) -> bool {
        self.work
            .as_ref()
            .is_some_and(|work| work.generation == mutation.generation)
    }
    fn reserve(&mut self, mutation: &Mutation, count: usize) -> Result<()> {
        let used = self.aliases.len()
            + self.terminal_history.len()
            + self.reserved_aliases.values().sum::<usize>();
        if count > MAX_ALIASES.saturating_sub(used) {
            self.clear();
            return Err(failure("control_limit"));
        }
        *self
            .reserved_aliases
            .entry(mutation.generation)
            .or_default() += count;
        Ok(())
    }
    fn alias(&mut self, id: &str, mutation: &Arc<Mutation>) {
        // Callers reserve capacity and prove uniqueness under the same state lock.
        let reserved = self.reserved_aliases.get_mut(&mutation.generation).unwrap();
        assert!(*reserved > 0);
        *reserved -= 1;
        assert!(self.aliases.insert(id.into(), mutation.clone()).is_none());
        if self.cancelled.contains(id) {
            mutation.dispatched.cancel();
            mutation.cancel.cancel();
        }
    }
    fn unique_handle(&mut self) -> Result<String> {
        // Each accepted control can generate at most one handle. Retention is
        // bounded independently of the mutation alias/history budget.
        if self.generated_handles.len() >= MAX_CONTROLS {
            self.clear();
            return Err(failure("control_limit"));
        }
        for _ in 0..16 {
            let id = opaque_id()?;
            if self.handle_available(&id) {
                self.generated_handles.insert(id.clone());
                return Ok(id);
            }
        }
        self.clear();
        Err(failure("randomness_unavailable"))
    }
    fn handle_available(&self, id: &str) -> bool {
        !self.seen.contains(id)
            && !self.generated_handles.contains(id)
            && !self.cancelled.contains(id)
            && !self.aliases.contains_key(id)
            && !self.terminal_history.contains_key(id)
            && !self.streams.contains_key(id)
            && !self.authorizations.contains_key(id)
            && !self
                .connection
                .as_ref()
                .is_some_and(|c| c.id == id || c.read.as_ref().is_some_and(|r| r.id == id))
    }
    fn pending(&mut self, id: &str) -> Pending {
        self.insert_pending(id, None)
    }
    fn mutation_pending(&mut self, id: &str, mutation: &Arc<Mutation>) -> Pending {
        self.alias(id, mutation);
        self.insert_pending(id, Some(mutation.clone()))
    }
    fn insert_pending(&mut self, id: &str, mutation: Option<Arc<Mutation>>) -> Pending {
        let pending = Pending {
            cancel: mutation
                .as_ref()
                .map_or_else(CancellationToken::new, |m| m.cancel.clone()),
            dispatched: mutation.as_ref().map_or_else(
                || Arc::new(DispatchState::default()),
                |m| m.dispatched.clone(),
            ),
            mutation,
        };
        if self.cancelled.contains(id) {
            pending.dispatched.cancel();
            pending.cancel.cancel();
        }
        self.pending.insert(
            id.into(),
            Pending {
                mutation: pending.mutation.clone(),
                cancel: pending.cancel.clone(),
                dispatched: pending.dispatched.clone(),
            },
        );
        pending
    }
    fn start_mutation(&mut self, id: &str) -> Result<Pending> {
        if self.work.is_some() {
            return Err(failure("mutation_limit"));
        }
        self.next_generation += 1;
        let mutation = Arc::new(Mutation {
            generation: self.next_generation,
            cancel: CancellationToken::new(),
            dispatched: Arc::new(DispatchState::default()),
        });
        self.reserve(&mutation, 2)?; // authorize control plus its eventual handle
        self.work = Some(mutation.clone());
        Ok(self.mutation_pending(id, &mutation))
    }
    fn finish_mutation(&mut self, mutation: &Mutation) -> bool {
        // This CAS must precede the cancellation response and all resource cleanup.
        let dispatched = mutation.dispatched.cancel();
        mutation.cancel.cancel();
        if self.owns(mutation) {
            self.work = None;
        }
        self.pending.retain(|_, p| {
            p.mutation
                .as_ref()
                .is_none_or(|m| m.generation != mutation.generation)
        });
        self.authorizations
            .retain(|_, (_, m)| m.generation != mutation.generation);
        self.streams.retain(|_, s| {
            s.mutation
                .as_ref()
                .is_none_or(|m| m.generation != mutation.generation)
        });
        let history = &mut self.terminal_history;
        self.aliases.retain(|id, m| {
            if m.generation == mutation.generation {
                history.insert(id.clone(), dispatched);
                false
            } else {
                true
            }
        });
        self.reserved_aliases.remove(&mutation.generation);
        dispatched
    }
}
impl Host {
    pub fn new(client: NativeClient) -> Self {
        let state = State::default();
        Self {
            client: Backend::Native(Arc::new(client)),
            shutdown: state.shutdown.clone(),
            state: Arc::new(Mutex::new(state)),
        }
    }
    pub(crate) fn shutdown(&self) -> CancellationToken {
        self.shutdown.clone()
    }
    fn client(&self) -> &NativeClient {
        match &self.client {
            Backend::Native(client) => client,
            #[cfg(test)]
            Backend::Unavailable | Backend::Fixture(_) => {
                panic!("test must not call the native backend")
            }
        }
    }
    async fn authorize_work(
        &self,
        request: &[u8],
        key: &str,
        presentation: &[u8],
        actor: &str,
        cancel: &CancellationToken,
    ) -> Result<Authority> {
        match &self.client {
            Backend::Native(client) => client
                .authorize(request, key, presentation, actor, cancel)
                .await
                .map(|authority| Authority::Native(Box::new(authority))),
            #[cfg(test)]
            Backend::Fixture(backend) => {
                backend.authorize_started.notify_one();
                backend.authorize_ready.acquire().await.unwrap().forget();
                // Deliberately permits a late completion: the host must discard it.
                Ok(Authority::Fixture {
                    expires: backend.expires.load(Ordering::Acquire),
                })
            }
            #[cfg(test)]
            Backend::Unavailable => panic!("no backend in this unit fixture"),
        }
    }
    async fn execute_work(
        &self,
        authority: Authority,
        actor: &str,
        pending: &Pending,
    ) -> Result<NativeResponse> {
        match (&self.client, authority) {
            (Backend::Native(client), Authority::Native(authority)) => {
                client
                    .execute(
                        *authority,
                        actor,
                        &pending.cancel,
                        pending.dispatched.clone(),
                    )
                    .await
            }
            #[cfg(test)]
            (Backend::Fixture(backend), Authority::Fixture { .. }) => {
                backend.execute_started.notify_one();
                backend.execute_ready.acquire().await.unwrap().forget();
                if !pending.dispatched.begin() {
                    return Err(failure("cancelled"));
                }
                backend.dispatches.fetch_add(1, Ordering::AcqRel);
                backend.dispatched.notify_one();
                backend.response_ready.acquire().await.unwrap().forget();
                Err(NativeError::new("outcome_unknown", true))
            }
            #[cfg(test)]
            _ => panic!("mismatched unit fixture authority"),
        }
    }
    pub async fn disconnect(&self) {
        self.state.lock().await.clear();
    }
    pub async fn handle(&self, raw: &[u8]) -> Value {
        let decoded = (|| -> Result<(String, Command)> {
            let mut value =
                strict_json(raw, FRAME_BYTES).map_err(|_| failure("invalid_message"))?;
            let object = value
                .as_object_mut()
                .ok_or_else(|| failure("invalid_message"))?;
            if object.remove("v") != Some(json!(1)) {
                return Err(failure("unsupported_version"));
            }
            let id = object
                .remove("id")
                .and_then(|v| v.as_str().map(str::to_owned))
                .filter(|s| valid_id(s))
                .ok_or_else(|| failure("invalid_message"))?;
            let command = serde_json::from_value(value).map_err(|_| failure("invalid_message"))?;
            Ok((id, command))
        })();
        let (id, command) = match decoded {
            Ok(value) => value,
            Err(error) => return json!({"v":1,"id":"invalid","ok":false,"error":error}),
        };
        {
            let mut state = self.state.lock().await;
            if state.disposed {
                return json!({"v":1,"id":id,"ok":false,"error":failure("connection_disposed")});
            }
            if state.seen.len() >= MAX_CONTROLS {
                state.clear();
                return json!({"v":1,"id":id,"ok":false,"error":failure("duplicate_or_exhausted_id")});
            }
            if state.generated_handles.contains(&id)
                || state.aliases.contains_key(&id)
                || state.terminal_history.contains_key(&id)
                || !state.seen.insert(id.clone())
            {
                return json!({"v":1,"id":id,"ok":false,"error":failure("duplicate_or_exhausted_id")});
            }
        }
        match self.dispatch(&id, command).await {
            Ok(result) => json!({"v":1,"id":id,"ok":true,"result":result}),
            Err(error) => json!({"v":1,"id":id,"ok":false,"error":error}),
        }
    }
    async fn dispatch(&self, id: &str, command: Command) -> Result<Value> {
        match command {
            Command::Connect {
                origin,
                document_id,
                wallet_public_key,
            } => {
                if origin != ORIGIN
                    || !valid_id(&document_id)
                    || wallet_public_key.len() != 64
                    || !wallet_public_key
                        .bytes()
                        .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
                {
                    return Err(failure("unapproved_origin"));
                }
                self.client().check_current().await?;
                let mut public = [0; 32];
                for (index, byte) in public.iter_mut().enumerate() {
                    *byte = u8::from_str_radix(&wallet_public_key[index * 2..index * 2 + 2], 16)
                        .map_err(|_| failure("invalid_identity"))?;
                }
                let actor = actor_id(&public);

                let profile = self.client().public_profile();
                let mut state = self.state.lock().await;
                if state.disposed {
                    return Err(failure("connection_disposed"));
                }
                if state.connection.is_some() {
                    return Err(failure("already_connected"));
                }
                let connection_id = state.unique_handle()?;
                state.connection = Some(Connection {
                    id: connection_id.clone(),
                    actor: actor.clone(),
                    read: None,
                });
                Ok(
                    json!({"connection_id":connection_id,"actor_id":actor,"receiver_profile":profile.receiver_profile,"stable_issuer":profile.stable_issuer,"audience":profile.audience,"origin":profile.origin,"capabilities":["read","work.v1","workflow.v1","progress.v1"]}),
                )
            }
            Command::ReadAccess { connection_id } => {
                self.client().check_current().await?;
                let mut state = self.state.lock().await;
                bound(&state, &connection_id)?;
                let read_context = state.unique_handle()?;
                let expires_at = now()? + 900;
                // Read consent is checked by the extension. The host does not mint a credential.
                state.connection.as_mut().unwrap().read = Some(ReadLease {
                    id: read_context.clone(),
                    expires: expires_at,
                });
                Ok(json!({"read_context":read_context,"expires_at":expires_at}))
            }
            Command::Read {
                connection_id,
                read_context,
                request,
            } => {
                let (pending, expires) = {
                    let mut state = self.state.lock().await;
                    bound(&state, &connection_id)?;
                    let lease = state
                        .connection
                        .as_ref()
                        .unwrap()
                        .read
                        .as_ref()
                        .filter(|r| r.id == read_context && r.expires > now().unwrap_or(u64::MAX))
                        .ok_or_else(|| failure("invalid_read_context"))?;
                    let expires = lease.expires;
                    if state.reads >= 4 {
                        return Err(failure("read_limit"));
                    }
                    state.reads += 1;
                    (state.pending(id), expires)
                };
                let raw = canonical_json(&request).map_err(|_| failure("invalid_request"))?;
                let result = self.client().read(&raw, expires, &pending.cancel).await;
                self.finish_stream(id, &connection_id, pending, result, Some(expires))
                    .await
            }
            Command::Authorize {
                connection_id,
                request_b64,
                idempotency_key,
                presentation_b64,
            } => {
                let request = decode_bytes(&request_b64, 65_536)?;
                let presentation = decode_bytes(&presentation_b64, 16_384)?;
                let (pending, actor) = {
                    let mut state = self.state.lock().await;
                    bound(&state, &connection_id)?;
                    self.expire_authorizations(&mut state)?;
                    let actor = state.connection.as_ref().unwrap().actor.clone();
                    (state.start_mutation(id)?, actor)
                };
                let result = self
                    .authorize_work(
                        &request,
                        &idempotency_key,
                        &presentation,
                        &actor,
                        &pending.cancel,
                    )
                    .await;
                let mut state = self.state.lock().await;
                let mutation = pending.mutation.as_ref().unwrap();
                let still_pending = state.pending.remove(id).is_some();
                if !still_pending
                    || !state.owns(mutation)
                    || pending.cancel.is_cancelled()
                    || bound(&state, &connection_id).is_err()
                {
                    state.finish_mutation(mutation);
                    return Err(failure("cancelled"));
                }
                match result {
                    Ok(authorization) => {
                        let authorization_id = match state.unique_handle() {
                            Ok(id) => id,
                            Err(error) => {
                                state.finish_mutation(mutation);
                                return Err(error);
                            }
                        };
                        let expires_at = authorization.expires_at();
                        state.alias(&authorization_id, mutation);
                        let remaining = match now() {
                            Ok(clock) => expires_at.saturating_sub(clock),
                            Err(error) => {
                                state.finish_mutation(mutation);
                                return Err(error);
                            }
                        };
                        state
                            .authorizations
                            .insert(authorization_id.clone(), (authorization, mutation.clone()));
                        self.arm_authorization_expiry(
                            authorization_id.clone(),
                            mutation.generation,
                            tokio::time::Instant::now() + std::time::Duration::from_secs(remaining),
                            mutation.cancel.clone(),
                        );
                        Ok(json!({"authorization_id":authorization_id,"expires_at":expires_at}))
                    }
                    Err(error) => {
                        state.finish_mutation(mutation);
                        Err(error)
                    }
                }
            }
            Command::Execute {
                connection_id,
                authorization_id,
            } => {
                let (pending, actor, authorized) = {
                    let mut state = self.state.lock().await;
                    bound(&state, &connection_id)?;
                    self.expire_authorizations(&mut state)?;
                    let mutation = state
                        .authorizations
                        .get(&authorization_id)
                        .map(|(_, mutation)| mutation.clone())
                        .ok_or_else(|| failure("invalid_authorization"))?;
                    state.reserve(&mutation, 2)?; // execute control plus eventual stream
                    let (authorized, _) = state.authorizations.remove(&authorization_id).unwrap();
                    let actor = state.connection.as_ref().unwrap().actor.clone();
                    (state.mutation_pending(id, &mutation), actor, authorized)
                };
                let result = self.execute_work(authorized, &actor, &pending).await;
                self.finish_stream(id, &connection_id, pending, result, None)
                    .await
            }
            Command::Pull {
                connection_id,
                stream_id,
                ack_seq,
            } => self.pull(id, &connection_id, &stream_id, ack_seq).await,
            Command::Cancel {
                connection_id,
                target_id,
            } => {
                let mut state = self.state.lock().await;
                bound(&state, &connection_id)?;
                if !valid_id(&target_id) {
                    return Err(failure("invalid_message"));
                }
                if state.cancelled.len() >= MAX_CONTROLS && !state.cancelled.contains(&target_id) {
                    state.clear();
                    return Err(failure("control_limit"));
                }
                state.cancelled.insert(target_id.clone());
                if let Some(mutation) = state.aliases.get(&target_id).cloned() {
                    let dispatched = state.finish_mutation(&mutation);
                    return Ok(json!({"cancelled":true,"dispatched":dispatched}));
                }
                let mut dispatched = state
                    .terminal_history
                    .get(&target_id)
                    .copied()
                    .unwrap_or(false);
                if let Some(pending) = state.pending.get(&target_id) {
                    dispatched |= pending.dispatched.cancel();
                    pending.cancel.cancel();
                }
                if let Some(stream) = state.streams.remove(&target_id) {
                    stream.cancel.cancel();
                    dispatched |= stream.dispatched;
                    state.reads = state.reads.saturating_sub(1);
                }
                Ok(json!({"cancelled":true,"dispatched":dispatched}))
            }
            Command::Dispose { connection_id } => {
                let mut state = self.state.lock().await;
                bound(&state, &connection_id)?;
                state.clear();
                Ok(json!({"disposed":true}))
            }
        }
    }
    fn expire_authorizations(&self, state: &mut State) -> Result<()> {
        let clock = now()?;
        let expired = state
            .authorizations
            .values()
            .filter(|(a, _)| a.expires_at() <= clock)
            .map(|(_, m)| m.clone())
            .collect::<Vec<_>>();
        for mutation in expired {
            state.finish_mutation(&mutation);
        }
        Ok(())
    }
    async fn finish_stream(
        &self,
        id: &str,
        connection: &str,
        pending: Pending,
        result: Result<NativeResponse>,
        lease: Option<u64>,
    ) -> Result<Value> {
        let mut state = self.state.lock().await;
        let still_pending = state.pending.remove(id).is_some();
        let dispatched = pending.dispatched.dispatched();
        if !still_pending
            || pending.cancel.is_cancelled()
            || bound(&state, connection).is_err()
            || pending.mutation.as_ref().is_some_and(|m| !state.owns(m))
        {
            if let Some(mutation) = &pending.mutation {
                state.finish_mutation(mutation);
            } else if still_pending {
                state.reads = state.reads.saturating_sub(1);
            }
            return Err(NativeError::new(
                if dispatched {
                    "outcome_unknown"
                } else {
                    "cancelled"
                },
                dispatched,
            ));
        }
        match result {
            Ok(response) => {
                let stream_id = match state.unique_handle() {
                    Ok(id) => id,
                    Err(error) => {
                        if let Some(mutation) = &pending.mutation {
                            state.finish_mutation(mutation);
                        } else {
                            state.reads = state.reads.saturating_sub(1);
                        }
                        return Err(NativeError::new(
                            if dispatched {
                                "outcome_unknown"
                            } else {
                                error.code
                            },
                            dispatched,
                        ));
                    }
                };
                if let Some(mutation) = &pending.mutation {
                    state.alias(&stream_id, mutation);
                }
                let mut head =
                    serde_json::to_value(response.head()).expect("response head is serializable");
                head["stream_id"] = json!(stream_id);
                let deadline = response.deadline();
                let expiry_id = stream_id.clone();
                state.streams.insert(
                    stream_id,
                    Arc::new(Stream {
                        mutation: pending.mutation,
                        dispatched: response.head().dispatched,
                        lease_expires: lease,
                        cancel: pending.cancel,
                        pulling: AtomicBool::new(false),
                        reader: Mutex::new(StreamReader {
                            response: ResponseReader::Native(Box::new(response)),
                            next_seq: 0,
                            previous_ack: -1,
                            done: false,
                        }),
                    }),
                );
                self.arm_expiry(
                    expiry_id.clone(),
                    deadline,
                    state.streams[&expiry_id].cancel.clone(),
                );
                Ok(head)
            }
            Err(error) => {
                if let Some(mutation) = &pending.mutation {
                    state.finish_mutation(mutation);
                } else {
                    state.reads = state.reads.saturating_sub(1);
                }
                Err(error)
            }
        }
    }
    fn arm_authorization_expiry(
        &self,
        id: String,
        generation: u64,
        deadline: tokio::time::Instant,
        cancel: CancellationToken,
    ) {
        let state_weak = Arc::downgrade(&self.state);
        tokio::spawn(async move {
            tokio::select! { _=cancel.cancelled()=>return, _=tokio::time::sleep_until(deadline)=>{} }
            if let Some(state) = state_weak.upgrade() {
                let mut state = state.lock().await;
                let expired = state
                    .authorizations
                    .get(&id)
                    .and_then(|(_, m)| (m.generation == generation).then(|| m.clone()));
                if let Some(mutation) = expired {
                    state.finish_mutation(&mutation);
                }
            }
        });
    }
    fn arm_expiry(&self, id: String, deadline: tokio::time::Instant, cancel: CancellationToken) {
        let state_weak = Arc::downgrade(&self.state);
        tokio::spawn(async move {
            tokio::select! { _=cancel.cancelled()=>return, _=tokio::time::sleep_until(deadline)=>{} }
            if let Some(state) = state_weak.upgrade() {
                let mut state = state.lock().await;
                if let Some(stream) = state.streams.remove(&id) {
                    stream.cancel.cancel();
                    if let Some(mutation) = &stream.mutation {
                        state.finish_mutation(mutation);
                    } else {
                        state.reads = state.reads.saturating_sub(1);
                    }
                }
            }
        });
    }
    async fn pull(&self, control_id: &str, connection: &str, id: &str, ack: i64) -> Result<Value> {
        let (stream, pending) = {
            let mut state = self.state.lock().await;
            bound(&state, connection)?;
            let stream = state
                .streams
                .get(id)
                .cloned()
                .ok_or_else(|| failure("invalid_stream"))?;
            if stream.pulling.swap(true, Ordering::AcqRel) {
                return Err(NativeError::new("concurrent_pull", stream.dispatched));
            }
            let pending = if let Some(mutation) = &stream.mutation {
                state.reserve(mutation, 1)?;
                state.mutation_pending(control_id, mutation)
            } else {
                state.pending(control_id)
            };
            (stream, pending)
        };
        let result = async {
            if stream.lease_expires.is_some_and(|expires| now().map_or(true, |n| n >= expires)) {
                return Err(failure("read_context_expired"));
            }
            let mut reader = stream.reader.lock().await;
            if reader.done || ack != reader.previous_ack {
                return Err(NativeError::new("invalid_sequence", stream.dispatched));
            }
            let (bytes, total, done) = tokio::select! {
                _ = pending.cancel.cancelled() => return Err(NativeError::new(if stream.dispatched { "outcome_unknown" } else { "cancelled" }, stream.dispatched)),
                result = reader.response.next_chunk(&stream.cancel) => result?,
            };
            if stream.cancel.is_cancelled() || pending.cancel.is_cancelled() {
                return Err(NativeError::new(if stream.dispatched { "outcome_unknown" } else { "cancelled" }, stream.dispatched));
            }
            let seq = reader.next_seq;
            reader.next_seq += 1;
            reader.previous_ack = seq;
            reader.done = done;
            Ok(json!({"stream_id":id,"seq":seq,"total":total,"chunk_b64":base64url(&bytes),"done":done}))
        }.await;
        let mut state = self.state.lock().await;
        let result = if pending.cancel.is_cancelled()
            || stream.cancel.is_cancelled()
            || bound(&state, connection).is_err()
        {
            Err(NativeError::new(
                if stream.dispatched {
                    "outcome_unknown"
                } else {
                    "cancelled"
                },
                stream.dispatched,
            ))
        } else {
            result
        };
        state.pending.remove(control_id);
        stream.pulling.store(false, Ordering::Release);
        let terminal = result.as_ref().map_or(true, |value| value["done"] == true);
        if terminal && state.streams.remove(id).is_some() {
            stream.cancel.cancel();
            if let Some(mutation) = &stream.mutation {
                state.finish_mutation(mutation);
            } else {
                state.reads = state.reads.saturating_sub(1);
            }
        }
        result
    }
}

#[cfg(test)]
pub(crate) fn fixture_host() -> Host {
    let state = State::default();
    Host {
        client: Backend::Unavailable,
        shutdown: state.shutdown.clone(),
        state: Arc::new(Mutex::new(state)),
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    pub(super) async fn connected() -> Host {
        let host = fixture_host();
        host.state.lock().await.connection = Some(Connection {
            id: "connection".into(),
            actor: "fixture-actor".into(),
            read: None,
        });
        host
    }
    pub(super) async fn stream(
        host: &Host,
        dispatched: bool,
    ) -> (Arc<tokio::sync::Notify>, Arc<AtomicBool>) {
        let started = Arc::new(tokio::sync::Notify::new());
        let dropped = Arc::new(AtomicBool::new(false));
        let mut state = host.state.lock().await;
        let mutation = if dispatched {
            let pending = state.start_mutation("fixture-authorize").unwrap();
            let mutation = pending.mutation.unwrap();
            state.alias("authorization", &mutation);
            state.pending.remove("fixture-authorize");
            state.reserve(&mutation, 2).unwrap();
            state.mutation_pending("execute-control", &mutation);
            state.pending.remove("execute-control");
            state.alias("stream", &mutation);
            assert!(mutation.dispatched.begin());
            Some(mutation)
        } else {
            None
        };
        let cancel = mutation
            .as_ref()
            .map_or_else(CancellationToken::new, |m| m.cancel.clone());
        let response = Stream {
            mutation,
            reader: Mutex::new(StreamReader {
                response: ResponseReader::Fixture(FixtureResponse {
                    done: false,
                    ready: None,
                    completed: None,
                    started: started.clone(),
                    dropped: dropped.clone(),
                }),
                next_seq: 0,
                previous_ack: -1,
                done: false,
            }),
            cancel: cancel.clone(),
            dispatched,
            lease_expires: None,
            pulling: AtomicBool::new(false),
        };
        state.streams.insert("stream".into(), Arc::new(response));
        if !dispatched {
            state.reads += 1;
        }
        host.arm_expiry(
            "stream".into(),
            tokio::time::Instant::now() + std::time::Duration::from_secs(30),
            cancel,
        );
        (started, dropped)
    }
    async fn command(host: &Host, value: Value) -> Value {
        host.handle(&canonical_json(&value).unwrap()).await
    }
    #[tokio::test]
    async fn closed_protocol_and_id_budget_reject_replay() {
        let host = connected().await;
        let value = json!({"v":1,"id":"first","action":"cancel","connection_id":"connection","target_id":"later"});
        assert_eq!(command(&host, value.clone()).await["ok"], true);
        assert_eq!(
            command(&host, value).await["error"]["code"],
            "duplicate_or_exhausted_id"
        );
        assert_eq!(
            host.handle(
                br#"{"v":1,"v":1,"id":"dup","action":"dispose","connection_id":"connection"}"#
            )
            .await["error"]["code"],
            "invalid_message"
        );
        assert_eq!(command(&host,json!({"v":1,"id":"unknown","action":"dispose","connection_id":"connection","url":"http://attacker"})).await["error"]["code"], "invalid_message");
        let mut state = host.state.lock().await;
        let pending = state.pending("later");
        assert!(pending.cancel.is_cancelled());
        assert!(!pending.dispatched.begin());
        state.seen = (0..65536).map(|n| format!("used-{n}")).collect();
        drop(state);
        assert_eq!(
            command(
                &host,
                json!({"v":1,"id":"one-more","action":"dispose","connection_id":"connection"})
            )
            .await["error"]["code"],
            "duplicate_or_exhausted_id"
        );
    }
    #[tokio::test(start_paused = true)]
    async fn idle_response_expiry_drops_reader_and_releases_both_slot_types() {
        for dispatched in [false, true] {
            let host = connected().await;
            let (_, dropped) = stream(&host, dispatched).await;
            tokio::task::yield_now().await;
            tokio::time::advance(std::time::Duration::from_secs(30)).await;
            tokio::task::yield_now().await;
            let state = host.state.lock().await;
            assert!(state.streams.is_empty());
            assert_eq!(state.reads, 0);
            assert!(state.work.is_none());
            assert!(dropped.load(Ordering::Acquire));
        }
    }
    #[tokio::test]
    async fn cancellation_by_pending_pull_id_drops_reader_and_prevents_late_chunk() {
        let host = connected().await;
        let (started, dropped) = stream(&host, true).await;
        let pulling_host = host.clone();
        let pull = tokio::spawn(async move {
            command(&pulling_host,json!({"v":1,"id":"pending-pull","action":"pull","connection_id":"connection","stream_id":"stream","ack_seq":-1})).await
        });
        started.notified().await;
        let cancelled = command(&host,json!({"v":1,"id":"cancel-pull","action":"cancel","connection_id":"connection","target_id":"pending-pull"})).await;
        assert_eq!(cancelled["result"]["dispatched"], true);
        let answer = tokio::time::timeout(std::time::Duration::from_secs(1), pull)
            .await
            .unwrap()
            .unwrap();
        assert_eq!(answer["ok"], false);
        assert_eq!(answer["error"]["code"], "outcome_unknown");
        assert!(answer.get("result").is_none());
        assert!(dropped.load(Ordering::Acquire));
        assert!(host.state.lock().await.streams.is_empty());
    }
    #[tokio::test]
    async fn disposal_cancels_pending_issuance_and_prevents_revival() {
        let host = connected().await;
        let pending = host.state.lock().await.pending("issuance");
        assert_eq!(
            command(
                &host,
                json!({"v":1,"id":"dispose","action":"dispose","connection_id":"connection"})
            )
            .await["ok"],
            true
        );
        assert!(pending.cancel.is_cancelled());
        assert!(!pending.dispatched.begin());
        let state = host.state.lock().await;
        assert!(state.disposed);
        assert!(state.pending.is_empty());
        assert!(state.connection.is_none());
    }
    #[tokio::test]
    async fn cancellation_winning_final_state_lock_suppresses_already_ready_chunk() {
        let host = connected().await;
        let (started, dropped) = stream(&host, true).await;
        let response = host.state.lock().await.streams["stream"].clone();
        let (release, ready) = tokio::sync::oneshot::channel();
        let completed = Arc::new(tokio::sync::Notify::new());
        if let ResponseReader::Fixture(fixture) = &mut response.reader.lock().await.response {
            fixture.ready = Some(ready);
            fixture.completed = Some(completed.clone());
        }
        drop(response);
        let pulling = host.clone();
        let pull = tokio::spawn(async move {
            command(&pulling,json!({"v":1,"id":"pull-ready","action":"pull","connection_id":"connection","stream_id":"stream","ack_seq":-1})).await
        });
        started.notified().await;
        let held = host.state.lock().await;
        let cancelling = host.clone();
        let cancel = tokio::spawn(async move {
            cancelling
                .dispatch(
                    "cancel-ready",
                    Command::Cancel {
                        connection_id: "connection".into(),
                        target_id: "pull-ready".into(),
                    },
                )
                .await
        });
        tokio::task::yield_now().await;
        release.send(()).unwrap();
        completed.notified().await;
        tokio::task::yield_now().await;
        drop(held);
        assert_eq!(cancel.await.unwrap().unwrap()["dispatched"], true);
        assert_eq!(pull.await.unwrap()["error"]["code"], "outcome_unknown");
        assert!(dropped.load(Ordering::Acquire));
    }
    #[tokio::test]
    async fn finished_execute_control_retains_dispatched_evidence_until_disconnect() {
        let host = connected().await;
        let pending = host.state.lock().await.start_mutation("executed").unwrap();
        assert!(pending.dispatched.begin());
        let result = host
            .finish_stream(
                "executed",
                "connection",
                pending,
                Err(NativeError::new("outcome_unknown", true)),
                None,
            )
            .await;
        assert_eq!(result.unwrap_err().code, "outcome_unknown");
        assert!(host.state.lock().await.pending.is_empty());
        let answer = command(&host,json!({"v":1,"id":"cancel-finished","action":"cancel","connection_id":"connection","target_id":"executed"})).await;
        assert_eq!(answer["result"]["dispatched"], true);
    }
}

#[cfg(test)]
mod lifecycle_regressions {
    use super::*;
    #[tokio::test]
    async fn cancelled_consumed_authorization_excludes_later_dispatch() {
        let host = fixture_host();
        let pending = {
            let mut state = host.state.lock().await;
            state.connection = Some(Connection {
                id: "connection".into(),
                actor: "fixture-actor".into(),
                read: None,
            });
            let authorization = state.start_mutation("authorize-control").unwrap();
            let mutation = authorization.mutation.unwrap();
            state.alias("consumed-authorization", &mutation);
            state.pending.remove("authorize-control");
            state.reserve(&mutation, 2).unwrap();
            // Exact registration performed when Execute consumes its authority.
            state.mutation_pending("execute-control", &mutation)
        };
        let result = host.handle(br#"{"v":1,"id":"cancel-auth","action":"cancel","connection_id":"connection","target_id":"consumed-authorization"}"#).await;
        assert_eq!(result["result"]["dispatched"], false);
        assert!(
            !pending.dispatched.begin(),
            "false cancellation must exclude later dispatch through the consumed authorization"
        );
    }
}

#[cfg(test)]
mod lifecycle_integration {
    use super::*;
    async fn fixture() -> (Host, Arc<FixtureBackend>) {
        let mut host = super::tests::connected().await;
        let backend = Arc::new(FixtureBackend::new());
        host.client = Backend::Fixture(backend.clone());
        (host, backend)
    }
    async fn call(host: &Host, value: Value) -> Value {
        host.handle(&canonical_json(&value).unwrap()).await
    }
    async fn authorize(host: &Host, control: &str) -> Value {
        call(host, json!({"v":1,"id":control,"action":"authorize","connection_id":"connection","request_b64":"e30","presentation_b64":"e30","idempotency_key":"fixture-idempotency-key"})).await
    }
    async fn prepared(host: &Host, backend: &FixtureBackend, control: &str) -> String {
        backend.authorize_ready.add_permits(1);
        let answer = authorize(host, control).await;
        backend.authorize_started.notified().await;
        assert_eq!(answer["ok"], true, "{answer}");
        answer["result"]["authorization_id"]
            .as_str()
            .unwrap()
            .into()
    }
    async fn cancel(host: &Host, id: &str, target: &str) -> Value {
        call(host, json!({"v":1,"id":id,"action":"cancel","connection_id":"connection","target_id":target})).await
    }
    async fn execute(host: &Host, authorization: &str) -> Value {
        call(host, json!({"v":1,"id":"execute-control","action":"execute","connection_id":"connection","authorization_id":authorization})).await
    }
    #[tokio::test]
    async fn cancellation_through_each_execution_alias_has_one_atomic_truth() {
        for after_dispatch in [false, true] {
            for target_kind in ["authorize-control", "authorization", "execute-control"] {
                let (host, backend) = fixture().await;
                let authorization = prepared(&host, &backend, "authorize-control").await;
                let target = if target_kind == "authorization" {
                    authorization.clone()
                } else {
                    target_kind.into()
                };
                let executing = host.clone();
                let pending =
                    tokio::spawn(async move { execute(&executing, &authorization).await });
                backend.execute_started.notified().await;
                if after_dispatch {
                    backend.execute_ready.add_permits(1);
                    backend.dispatched.notified().await;
                }
                let answer = cancel(&host, "cancel-control", &target).await;
                assert_eq!(answer["result"]["dispatched"], after_dispatch);
                assert_eq!(
                    cancel(&host, "repeat-cancel", &target).await["result"]["dispatched"],
                    after_dispatch
                );
                if !after_dispatch {
                    backend.execute_ready.add_permits(1);
                }
                backend.response_ready.add_permits(1);
                let answer = pending.await.unwrap();
                assert_eq!(answer["error"]["dispatched"], after_dispatch);
                assert_eq!(
                    backend.dispatches.load(Ordering::Acquire),
                    usize::from(after_dispatch)
                );
                assert_eq!(
                    cancel(&host, "terminal-cancel", &target).await["result"]["dispatched"],
                    after_dispatch
                );
                let state = host.state.lock().await;
                assert!(
                    state.work.is_none() && state.aliases.is_empty() && state.pending.is_empty()
                );
                assert!(state.authorizations.is_empty() && state.reserved_aliases.is_empty());
                assert_eq!(state.terminal_history.len(), 3);
            }
        }
    }
    #[tokio::test]
    async fn cancel_before_authorize_and_before_execute_never_dispatches() {
        let (host, backend) = fixture().await;
        assert_eq!(
            cancel(&host, "forward-cancel", "authorize-control").await["result"]["dispatched"],
            false
        );
        backend.authorize_ready.add_permits(1);
        let denied = authorize(&host, "authorize-control").await;
        assert_eq!(denied["error"]["code"], "cancelled");
        backend.authorize_started.notified().await;
        let authorization = prepared(&host, &backend, "second-authorize").await;
        assert_eq!(
            cancel(&host, "cancel-auth", &authorization).await["result"]["dispatched"],
            false
        );
        assert_eq!(
            execute(&host, &authorization).await["error"]["code"],
            "invalid_authorization"
        );
        assert_eq!(backend.dispatches.load(Ordering::Acquire), 0);
    }
    #[tokio::test]
    async fn late_issuer_and_execute_completions_cannot_release_a_new_generation() {
        let (host, backend) = fixture().await;
        let old_host = host.clone();
        let old = tokio::spawn(async move { authorize(&old_host, "old-authorize").await });
        backend.authorize_started.notified().await;
        cancel(&host, "cancel-old", "old-authorize").await;
        let new_host = host.clone();
        let new = tokio::spawn(async move { authorize(&new_host, "new-authorize").await });
        backend.authorize_started.notified().await;
        let generation = host.state.lock().await.work.as_ref().unwrap().generation;
        backend.authorize_ready.add_permits(2);
        assert_eq!(old.await.unwrap()["error"]["code"], "cancelled");
        let authorized = new.await.unwrap();
        assert_eq!(authorized["ok"], true);
        let authorization = authorized["result"]["authorization_id"]
            .as_str()
            .unwrap()
            .to_owned();
        assert_eq!(
            host.state.lock().await.work.as_ref().unwrap().generation,
            generation
        );
        let old_host = host.clone();
        let execution = tokio::spawn(async move { execute(&old_host, &authorization).await });
        backend.execute_started.notified().await;
        cancel(&host, "cancel-executing", "new-authorize").await;
        let latest = prepared(&host, &backend, "latest-authorize").await;
        let generation = host.state.lock().await.work.as_ref().unwrap().generation;
        backend.execute_ready.add_permits(1);
        assert_eq!(execution.await.unwrap()["error"]["dispatched"], false);
        let state = host.state.lock().await;
        assert_eq!(state.work.as_ref().unwrap().generation, generation);
        assert!(state.authorizations.contains_key(&latest));
        assert_eq!(backend.dispatches.load(Ordering::Acquire), 0);
    }
    #[tokio::test]
    async fn stream_alias_cancellation_releases_reader_and_preserves_terminal_truth() {
        for target in [
            "fixture-authorize",
            "authorization",
            "execute-control",
            "stream",
        ] {
            let host = super::tests::connected().await;
            let (_, dropped) = super::tests::stream(&host, true).await;
            assert_eq!(
                cancel(&host, "cancel-stream", target).await["result"]["dispatched"],
                true
            );
            assert!(dropped.load(Ordering::Acquire));
            for (index, alias) in [
                "fixture-authorize",
                "authorization",
                "execute-control",
                "stream",
            ]
            .iter()
            .enumerate()
            {
                assert_eq!(
                    cancel(&host, &format!("terminal-{index}"), alias).await["result"]
                        ["dispatched"],
                    true
                );
            }
            let state = host.state.lock().await;
            assert!(state.streams.is_empty() && state.work.is_none());
            assert_eq!(state.terminal_history.len(), 4);
        }
    }
    #[tokio::test]
    async fn authorization_expiry_releases_only_unused_authority() {
        let (host, backend) = fixture().await;
        let old = prepared(&host, &backend, "old").await;
        {
            let mut state = host.state.lock().await;
            let (Authority::Fixture { expires }, _) = state.authorizations.get_mut(&old).unwrap()
            else {
                panic!()
            };
            *expires = now().unwrap();
        }
        let current = prepared(&host, &backend, "renewed").await;
        assert_eq!(
            cancel(&host, "cancel-expired", &old).await["result"]["dispatched"],
            false
        );
        assert!(host
            .state
            .lock()
            .await
            .authorizations
            .contains_key(&current));
        let running = host.clone();
        let executing = tokio::spawn(async move { execute(&running, &current).await });
        backend.execute_started.notified().await;
        let generation = host.state.lock().await.work.as_ref().unwrap().generation;
        let rejected = authorize(&host, "while-executing").await;
        assert_eq!(rejected["error"]["code"], "mutation_limit");
        assert_eq!(
            host.state.lock().await.work.as_ref().unwrap().generation,
            generation
        );
        cancel(&host, "end", "execute-control").await;
        backend.execute_ready.add_permits(1);
        executing.await.unwrap();
    }
    #[tokio::test]
    async fn tracking_exhaustion_revokes_and_handle_collisions_do_not_rebind() {
        let (host, backend) = fixture().await;
        let authorization = prepared(&host, &backend, "authorize").await;
        let collision = call(&host, json!({"v":1,"id":authorization,"action":"cancel","connection_id":"connection","target_id":"authorize"})).await;
        assert_eq!(collision["error"]["code"], "duplicate_or_exhausted_id");
        let lifecycle = host.state.lock().await.work.as_ref().unwrap().clone();
        {
            let mut state = host.state.lock().await;
            state
                .terminal_history
                .extend((0..MAX_ALIASES - 2).map(|i| (format!("history-{i}"), true)));
            for collision in [
                "authorize",
                authorization.as_str(),
                "history-0",
                "connection",
            ] {
                assert!(!state.handle_available(collision));
            }
            state.cancelled.insert("cancelled-handle".into());
            assert!(!state.handle_available("cancelled-handle"));
        }
        let denied = execute(&host, &authorization).await;
        assert_eq!(denied["error"]["code"], "control_limit");
        assert!(host.shutdown.is_cancelled());
        assert!(!lifecycle.dispatched.begin());
        let state = host.state.lock().await;
        assert!(state.disposed && state.aliases.is_empty() && state.terminal_history.is_empty());
        assert!(
            state.authorizations.is_empty()
                && state.reserved_aliases.is_empty()
                && state.work.is_none()
        );
    }
    #[tokio::test]
    async fn disconnect_clears_tracking_and_prevents_late_authority() {
        let (host, backend) = fixture().await;
        let pending_host = host.clone();
        let pending = tokio::spawn(async move { authorize(&pending_host, "authorizing").await });
        backend.authorize_started.notified().await;
        let lifecycle = host.state.lock().await.work.as_ref().unwrap().clone();
        host.disconnect().await;
        backend.authorize_ready.add_permits(1);
        assert_eq!(pending.await.unwrap()["error"]["code"], "cancelled");
        assert!(!lifecycle.dispatched.begin());
        let state = host.state.lock().await;
        assert!(state.disposed && state.authorizations.is_empty() && state.pending.is_empty());
        assert!(
            state.aliases.is_empty()
                && state.terminal_history.is_empty()
                && state.cancelled.is_empty()
        );
    }

    #[tokio::test(start_paused = true)]
    async fn idle_authorization_expiry_compacts_aliases_without_affecting_execution() {
        let (host, backend) = fixture().await;
        let authorization = prepared(&host, &backend, "idle-authorize").await;
        tokio::task::yield_now().await;
        tokio::time::advance(std::time::Duration::from_secs(60)).await;
        tokio::task::yield_now().await;
        {
            let state = host.state.lock().await;
            assert!(
                state.work.is_none() && state.authorizations.is_empty() && state.aliases.is_empty()
            );
            assert_eq!(state.terminal_history.get(&authorization), Some(&false));
        }
        let authorization = prepared(&host, &backend, "execute-authorize").await;
        let executing = host.clone();
        let execution = tokio::spawn(async move { execute(&executing, &authorization).await });
        backend.execute_started.notified().await;
        let generation = host.state.lock().await.work.as_ref().unwrap().generation;
        tokio::time::advance(std::time::Duration::from_secs(60)).await;
        tokio::task::yield_now().await;
        assert_eq!(
            host.state.lock().await.work.as_ref().unwrap().generation,
            generation
        );
        cancel(&host, "end", "execute-control").await;
        backend.execute_ready.add_permits(1);
        execution.await.unwrap();
    }
    #[tokio::test]
    async fn completed_stream_retains_truth_for_every_alias_including_final_pull() {
        let host = super::tests::connected().await;
        let (_, dropped) = super::tests::stream(&host, true).await;
        let stream = host.state.lock().await.streams["stream"].clone();
        let (send, ready) = tokio::sync::oneshot::channel();
        if let ResponseReader::Fixture(fixture) = &mut stream.reader.lock().await.response {
            fixture.ready = Some(ready);
            fixture.done = true;
        }
        drop(stream);
        send.send(()).unwrap();
        let answer = call(&host, json!({"v":1,"id":"final-pull","action":"pull","connection_id":"connection","stream_id":"stream","ack_seq":-1})).await;
        assert_eq!(answer["result"]["done"], true);
        assert!(dropped.load(Ordering::Acquire));
        for (i, alias) in [
            "fixture-authorize",
            "authorization",
            "execute-control",
            "stream",
            "final-pull",
        ]
        .iter()
        .enumerate()
        {
            assert_eq!(
                cancel(&host, &format!("after-{i}"), alias).await["result"]["dispatched"],
                true
            );
        }
        let state = host.state.lock().await;
        assert!(
            state.work.is_none() && state.pending.is_empty() && state.reserved_aliases.is_empty()
        );
        assert_eq!(state.terminal_history.len(), 5);
    }
    #[tokio::test]
    async fn id_exhaustion_closes_an_idle_open_native_port() {
        let host = super::tests::connected().await;
        host.state.lock().await.seen = (0..MAX_CONTROLS).map(|i| format!("seen-{i}")).collect();
        let (mut input, host_input) = tokio::io::duplex(1024);
        let (host_output, _output) = tokio::io::duplex(1024);
        let running = tokio::spawn(crate::serve(host.clone(), host_input, host_output));
        crate::write_frame(&mut input, &json!({"v":1,"id":"exhausted","action":"cancel","connection_id":"connection","target_id":"unknown"})).await.unwrap();
        assert!(
            tokio::time::timeout(std::time::Duration::from_secs(1), running)
                .await
                .unwrap()
                .unwrap()
                .is_err()
        );
        assert!(host.shutdown.is_cancelled());
    }

    #[tokio::test]
    async fn control_ids_cannot_reuse_generated_connection_lease_or_read_stream_handles() {
        for kind in ["connection", "lease", "stream"] {
            let (host, backend) = fixture().await;
            if kind == "stream" {
                super::tests::stream(&host, false).await;
            }
            let (handle, connection) = {
                let mut state = host.state.lock().await;
                let handle = state.unique_handle().unwrap();
                match kind {
                    "connection" => state.connection.as_mut().unwrap().id = handle.clone(),
                    "lease" => {
                        state.connection.as_mut().unwrap().read = Some(ReadLease {
                            id: handle.clone(),
                            expires: now().unwrap() + 900,
                        })
                    }
                    "stream" => {
                        let reader = state.streams.remove("stream").unwrap();
                        state.streams.insert(handle.clone(), reader);
                    }
                    _ => unreachable!(),
                }
                let connection = state.connection.as_ref().unwrap().id.clone();
                (handle, connection)
            };
            backend.authorize_ready.add_permits(1);
            let answer = call(&host, json!({"v":1,"id":handle,"action":"authorize","connection_id":connection,"request_b64":"e30","presentation_b64":"e30","idempotency_key":"fixture-idempotency-key"})).await;
            assert_eq!(
                answer["error"]["code"], "duplicate_or_exhausted_id",
                "{kind}: {answer}"
            );
            assert!(host.state.lock().await.work.is_none());
            if kind == "stream" {
                let cancelled = call(&host, json!({"v":1,"id":"cancel-read","action":"cancel","connection_id":connection,"target_id":handle})).await;
                assert_eq!(cancelled["result"]["dispatched"], false);
                assert!(host.state.lock().await.streams.is_empty());
            }
        }
    }
    #[tokio::test]
    async fn retired_generated_handles_cannot_become_later_mutation_controls() {
        let (host, backend) = fixture().await;
        let retired = {
            let mut state = host.state.lock().await;
            let handle = state.unique_handle().unwrap();
            state.connection.as_mut().unwrap().read = Some(ReadLease {
                id: handle.clone(),
                expires: now().unwrap(),
            });
            state.connection.as_mut().unwrap().read = None;
            handle
        };
        backend.authorize_ready.add_permits(1);
        let answer = authorize(&host, &retired).await;
        assert_eq!(answer["error"]["code"], "duplicate_or_exhausted_id");
        backend.authorize_ready.forget_permits(1);
        let current = prepared(&host, &backend, "fresh-control").await;
        cancel(&host, "late-old-cancel", &retired).await;
        assert!(host
            .state
            .lock()
            .await
            .authorizations
            .contains_key(&current));
        host.disconnect().await;
        assert!(host.state.lock().await.generated_handles.is_empty());
    }

    #[tokio::test]
    async fn generated_handle_budget_exhaustion_revokes_before_another_handle_is_exposed() {
        let (host, backend) = fixture().await;
        prepared(&host, &backend, "authorized").await;
        let mut state = host.state.lock().await;
        let mutation = state.work.as_ref().unwrap().clone();
        state.generated_handles = (0..MAX_CONTROLS)
            .map(|i| format!("generated-{i}"))
            .collect();
        assert_eq!(state.unique_handle().unwrap_err().code, "control_limit");
        assert!(
            state.disposed && state.generated_handles.is_empty() && state.authorizations.is_empty()
        );
        assert!(!mutation.dispatched.begin());
        assert!(host.shutdown.is_cancelled());
    }
}
