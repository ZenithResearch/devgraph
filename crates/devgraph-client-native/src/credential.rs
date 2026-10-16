//! Generic terminal Wallet ceremony. Only the fixed Wallet process opens the seed.
use super::*;
use base64::{engine::general_purpose::STANDARD, Engine};
use castalia_wallet_presentation as wallet;
use devgraph_work_protocol::{
    canonical_json, digest, idempotency_key_digest, strict_json, WorkRequest,
};
use secs_native_private_files as private;
use serde_json::{json, Value};
use std::{
    os::unix::fs::{MetadataExt, PermissionsExt},
    path::{Path, PathBuf},
    process::Stdio,
    sync::Arc,
    time::Duration,
};

/// Explicit existing custody references; never seed bytes or an implicit key generator.
pub struct WalletSignerReference {
    pub key_file: PathBuf,
    pub public_key: String,
}
fn failure() -> NativeError {
    NativeError::new("invalid_credential_authority", false)
}
fn encode(value: &impl serde::Serialize) -> Result<Vec<u8>> {
    canonical_json(value).map_err(|_| failure())
}
fn decode(raw: &[u8]) -> Result<Value> {
    strict_json(raw, 262_144).map_err(|_| failure())
}
fn bindings(request: &WorkRequest, key: &str) -> Result<(Vec<u8>, Value)> {
    let key_digest = idempotency_key_digest(key).map_err(|_| failure())?;
    let mut bytes = b"devgraph.credential-request.v2\0".to_vec();
    bytes.extend(encode(&json!({"schema":"devgraph.credential-request.v2","request":request.value(),"idempotency_key_digest_sha256":key_digest}))?);
    let mut statements = vec![
        format!("Operation: {}", request.operation()),
        format!("Resources: {}", request.resources().join(", ")),
        format!(
            "Expected version: {}",
            request.value()["expected_version"]
                .as_u64()
                .map(|v| v.to_string())
                .unwrap_or("new".into())
        ),
        format!("Idempotency digest: {key_digest}"),
    ];
    let mut text = std::str::from_utf8(request.canonical()).map_err(|_| failure())?;
    let mut i = 1;
    while !text.is_empty() {
        let mut end = text.len().min(480);
        while !text.is_char_boundary(end) {
            end -= 1;
        }
        statements.push(format!("Request part {i}: {}", &text[..end]));
        text = &text[end..];
        i += 1;
    }
    if bytes.len() > 131_072 || statements.len() > 144 {
        return Err(failure());
    }
    Ok((
        bytes,
        json!({"title":"Devgraph request","statements":statements}),
    ))
}
fn private_json(path: &Path, value: &impl serde::Serialize) -> Result<()> {
    let bytes = encode(value)?;
    private::Output::prepare(path)
        .and_then(|out| out.write(&bytes))
        .map_err(|_| failure())
}
/// Execute only owner-private fixed binaries; argv never contains a secret value.
async fn run(
    binary: &Path,
    args: &[&std::ffi::OsStr],
    signer: Option<&WalletSignerReference>,
    cancel: &CancellationToken,
) -> Result<()> {
    let held = private::open(binary, 128 * 1024 * 1024)
        .map_err(|_| NativeError::new("credential_installation_unavailable", false))?;
    let metadata = held.metadata().map_err(|_| failure())?;
    if metadata.mode() & 0o100 == 0 {
        return Err(failure());
    }
    let current = private::open(binary, 128 * 1024 * 1024)
        .map_err(|_| failure())?
        .metadata()
        .map_err(|_| failure())?;
    if (metadata.dev(), metadata.ino()) != (current.dev(), current.ino()) {
        return Err(failure());
    }
    if cancel.is_cancelled() {
        return Err(NativeError::new("cancelled", false));
    }
    let mut command = tokio::process::Command::new(binary);
    command
        .env_clear()
        .current_dir("/")
        .args(args)
        .kill_on_drop(true);
    if let Some(signer) = signer {
        command
            .env("CASTALIA_SIGNING_KEY_FILE", &signer.key_file)
            .env("CASTALIA_SIGNING_PUBLIC_KEY", &signer.public_key)
            .stdin(Stdio::inherit())
            .stdout(Stdio::inherit())
            .stderr(Stdio::inherit());
    } else {
        command
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null());
    }
    let mut child = command
        .spawn()
        .map_err(|_| NativeError::new("credential_installation_unavailable", false))?;
    let timeout = Duration::from_secs(if signer.is_some() { 130 } else { 10 });
    let result = tokio::select! {
        _=cancel.cancelled()=>{let _=child.kill().await;let _=child.wait().await;return Err(NativeError::new("cancelled",false));},
        result=tokio::time::timeout(timeout,child.wait())=>result,
    };
    match result {
        Ok(Ok(status)) if status.success() => Ok(()),
        _ => {
            let _ = child.kill().await;
            let _ = child.wait().await;
            Err(NativeError::new("credential_approval_denied", false))
        }
    }
}

fn verify_projection(
    request: &WorkRequest,
    key: &str,
    attached: &wallet::PresentationRequest,
    presentation: &wallet::Presentation,
    raw: &[u8],
    trust: &authority::Trust,
    clock: u64,
) -> Result<()> {
    let mut value = decode(raw)?;
    let signature = value
        .as_object_mut()
        .ok_or_else(failure)?
        .remove("secs_verifier_signature")
        .ok_or_else(failure)?;
    let signature = castalia_wallet_devgraph_presentation::decode64::<64>(
        signature.as_str().ok_or_else(failure)?,
    )
    .map_err(|_| failure())?;
    let p = presentation.unsigned();
    let claims = &attached.credential.claims;
    let holder = wallet::decode_hex::<32>(&p.holder_public_key).map_err(|_| failure())?;
    let actor = format!("pubkey:sha256:{}", digest(b"", &holder));
    let policy_expiry = trust
        .policy
        .authorize_until(&actor, request, clock)
        .map_err(|_| NativeError::new("authority_denied", false))?;
    let issued = value["issued_at"].as_u64().ok_or_else(failure)?;
    let expires = value["expires_at"].as_u64().ok_or_else(failure)?;
    if issued > clock
        || expires <= clock
        || expires <= issued
        || expires - issued > 60
        || expires > policy_expiry
        || claims.policy_digest_sha256 != trust.policy.digest().map_err(|_| failure())?
        || expires > p.expires_at
        || issued < p.issued_at
    {
        return Err(failure());
    }
    let p_digest = digest(b"", &presentation.canonical().map_err(|_| failure())?);
    let expected = json!({
        "schema":"secs-devgraph-work-authority.v2","schema_version":2,"actor_id":actor,"actor_signature_suite":"Ed25519","audience":AUDIENCE,
        "operation":request.operation(),"resources":request.resources(),"request_digest_sha256":request.request_digest(),
        "idempotency_key_digest_sha256":idempotency_key_digest(key).map_err(|_|failure())?,
        "receiver_policy_id":trust.policy.policy_id,"receiver_policy_version":trust.policy.policy_version,"receiver_policy_digest_sha256":trust.policy.digest().map_err(|_|failure())?,
        "replay_scope":"credential:operation:nonce","nonce":p.nonce,"session_id":claims.nonce,
        "secs_verifier_key_id":trust.verifier_id,"secs_verifier_signature_suite":"Ed25519",
        "credential_presentation_digest_sha256":p_digest,"credential_request_digest_sha256":claims.request_digest_sha256,
        "credential_digest_sha256":p.credential_digest_sha256,"disclosure_digest_sha256":claims.disclosure_digest_sha256,
        "issued_at":issued,"expires_at":expires,"secs_context_id":format!("ctx:sha256:{}",digest(b"secs-devgraph-work-context.v2\0", &encode(&json!({"policy":trust.policy.digest().map_err(|_|failure())?,"presentation":p_digest,"signer":trust.verifier_id}))?)),
    });
    if value != expected {
        return Err(failure());
    }
    let mut transcript = b"secs-devgraph-work-authority.v2/signature\0".to_vec();
    transcript.extend(encode(&value)?);
    ed25519_dalek::VerifyingKey::from_bytes(&trust.public)
        .map_err(|_| failure())?
        .verify_strict(
            &transcript,
            &ed25519_dalek::Signature::from_bytes(&signature),
        )
        .map_err(|_| failure())
}

impl NativeClient {
    /// Each call requires fresh interactive Wallet approval. `reconcile` only reads
    /// the matching receipt and cannot resubmit a mutation whose outcome is unknown.
    pub async fn execute_with_wallet(
        &self,
        request_raw: &[u8],
        key: &str,
        signer: &WalletSignerReference,
        reconcile: bool,
        cancel: &CancellationToken,
        dispatch: Arc<DispatchState>,
    ) -> Result<NativeResponse> {
        self.profile.check_current()?;
        if self.profile.config.schema != "devgraph.native-profile.v2"
            || !signer.key_file.is_absolute()
        {
            return Err(NativeError::new("generic_profile_required", false));
        }
        wallet::decode_hex::<32>(&signer.public_key).map_err(|_| failure())?;
        let request = WorkRequest::parse(request_raw).map_err(|_| failure())?;
        if request.canonical() != request_raw {
            return Err(failure());
        }
        let (wrapper, disclosure) = bindings(&request, key)?;
        let root = self
            .profile
            .config
            .secs_data_root
            .parent()
            .ok_or_else(failure)?;
        let wallet_binary = root.join("CastaliaWallet/bin/castalia-wallet-present-credential-v2");
        let secs_binary = root.join("secS/bin/secs-devgraph-work-v2");
        if self.profile.config.secs_executable != secs_binary {
            return Err(failure());
        }
        let trust_file = root.join("CastaliaWallet/trust/credential-presentation-v2.json");
        let trust_bytes = private::read(&trust_file, 65_536).map_err(|_| failure())?;
        let wallet_trust = wallet::TrustConfig::parse(&trust_bytes).map_err(|_| failure())?;
        if wallet_trust.caller.kind != "terminal" {
            return Err(failure());
        }
        let directory = tempfile::Builder::new()
            .prefix("devgraph-credential-")
            .permissions(std::fs::Permissions::from_mode(0o700))
            .tempdir()
            .map_err(|_| failure())?;
        let directory_path = directory.path().canonicalize().map_err(|_| failure())?;
        let preflight = directory_path.join("preflight.json");
        let keyfile = directory_path.join("key.txt");
        let attached_file = directory_path.join("credential.json");
        let presentation_file = directory_path.join("presentation.json");
        let authorization_file = directory_path.join("authorization.json");
        let projection_file = directory_path.join("projection.json");
        private_json(
            &preflight,
            &json!({"schema":"secs-devgraph-credential-input.v2","schema_version":2,"request":request.value(),"holder_public_key":signer.public_key,"caller":wallet_trust.caller}),
        )?;
        private::Output::prepare(&keyfile)
            .and_then(|out| out.write(format!("{key}\n").as_bytes()))
            .map_err(|_| failure())?;
        run(
            &secs_binary,
            &[
                "issue-credential".as_ref(),
                "--request-file".as_ref(),
                preflight.as_os_str(),
                "--idempotency-key-file".as_ref(),
                keyfile.as_os_str(),
                "--presentation-request-output".as_ref(),
                attached_file.as_os_str(),
            ],
            None,
            cancel,
        )
        .await?;
        let attached_raw = private::read(&attached_file, 262_144).map_err(|_| failure())?;
        wallet::prepare_request(
            &attached_raw,
            &wallet_trust.pins,
            &wallet_trust.caller,
            &signer.public_key,
            now()?,
        )
        .map_err(|_| failure())?;
        let attached: wallet::PresentationRequest =
            serde_json::from_value(decode(&attached_raw)?).map_err(|_| failure())?;
        if attached.request_bytes_base64 != STANDARD.encode(&wrapper)
            || serde_json::to_value(&attached.disclosure).map_err(|_| failure())? != disclosure
        {
            return Err(failure());
        }
        run(
            &wallet_binary,
            &[
                "--request-file".as_ref(),
                attached_file.as_os_str(),
                "--trust-config-file".as_ref(),
                trust_file.as_os_str(),
                "--presentation-output".as_ref(),
                presentation_file.as_os_str(),
            ],
            Some(signer),
            cancel,
        )
        .await?;
        let presentation_raw = private::read(&presentation_file, 16_384).map_err(|_| failure())?;
        let presentation = wallet::verify_presentation(
            &attached_raw,
            &presentation_raw,
            &wallet_trust.pins,
            &wallet_trust.caller,
            now()?,
        )
        .map_err(|_| failure())?;
        private_json(
            &authorization_file,
            &json!({"schema":"secs-devgraph-work-producer-input.v2","schema_version":2,"request":request.value(),"credential":attached.credential,"disclosure":attached.disclosure,"presentation":presentation}),
        )?;
        run(
            &secs_binary,
            &[
                "authorize".as_ref(),
                "--request-file".as_ref(),
                authorization_file.as_os_str(),
                "--idempotency-key-file".as_ref(),
                keyfile.as_os_str(),
                "--signed-projection-output".as_ref(),
                projection_file.as_os_str(),
            ],
            None,
            cancel,
        )
        .await?;
        let projection = private::read(&projection_file, 16_384).map_err(|_| failure())?;
        self.profile.check_current()?;
        if private::read(&trust_file, 65_536).map_err(|_| failure())? != trust_bytes {
            return Err(NativeError::new("authority_changed", false));
        }
        let trust = authority::current_trust(&self.profile, now()?)?;
        verify_projection(
            &request,
            key,
            &attached,
            &presentation,
            &projection,
            &trust,
            now()?,
        )?;
        if cancel.is_cancelled() {
            return Err(NativeError::new("cancelled", false));
        }
        let route = match request.value()["schema"].as_str() {
            Some("devgraph.arena-request.v1") => "arena-operations/v2",
            Some("devgraph.work-request.v2") => "todo-operations/v2",
            _ => "work-operations/v2",
        };
        let route = if reconcile {
            format!("{route}/status")
        } else {
            route.into()
        };
        let builder = self
            .http
            .post(format!("{ORIGIN}/{route}"))
            .header("Accept-Encoding", "identity")
            .header("Cache-Control", "no-store")
            .header("Accept", "application/json")
            .header("Content-Type", "application/json")
            .header("Idempotency-Key", key)
            .header(
                "X-Devgraph-Work-Authority",
                castalia_wallet_devgraph_presentation::base64url(&projection),
            )
            .body(request_raw.to_vec());
        if !dispatch.begin() {
            return Err(NativeError::new("cancelled", false));
        }
        self.open_response(
            builder,
            8 * 1024 * 1024,
            tokio::time::Instant::now() + Duration::from_secs(30),
            cancel,
            true,
        )
        .await
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn independently_produced_secs_projections_match_native_bindings() {
        for bytes in [
            include_bytes!("../../../tests/fixtures/credential-v2/authority-vectors.json")
                .as_slice(),
            include_bytes!("../../../tests/fixtures/credential-v2/progress-authority-vectors.json")
                .as_slice(),
        ] {
            let fixture: Value = serde_json::from_slice(bytes).unwrap();
            for vector in fixture["vectors"].as_array().unwrap() {
                let request = WorkRequest::parse(&encode(&vector["request"]).unwrap()).unwrap();
                let key = vector["idempotency_key"].as_str().unwrap();
                let attached: wallet::PresentationRequest =
                    serde_json::from_value(vector["presentation_request"].clone()).unwrap();
                let presentation =
                    wallet::Presentation::parse(&encode(&vector["presentation"]).unwrap()).unwrap();
                let trust = authority::Trust {
                    policy: secs_devgraph_work_contract::WorkPolicy::parse(
                        &encode(&vector["policy"]).unwrap(),
                    )
                    .unwrap(),
                    verifier_id: vector["projection"]["secs_verifier_key_id"]
                        .as_str()
                        .unwrap()
                        .into(),
                    public: wallet::decode_hex::<32>(
                        fixture["issuer_public_key"].as_str().unwrap(),
                    )
                    .unwrap(),
                };
                let (wrapper, disclosure) = bindings(&request, key).unwrap();
                assert_eq!(STANDARD.encode(wrapper), attached.request_bytes_base64);
                assert_eq!(disclosure, vector["presentation_request"]["disclosure"]);
                verify_projection(
                    &request,
                    key,
                    &attached,
                    &presentation,
                    &encode(&vector["projection"]).unwrap(),
                    &trust,
                    vector["now"].as_u64().unwrap(),
                )
                .unwrap();
                for field in [
                    "operation",
                    "request_digest_sha256",
                    "credential_digest_sha256",
                    "session_id",
                ] {
                    let mut wrong = vector["projection"].clone();
                    wrong[field] = json!("changed");
                    assert!(verify_projection(
                        &request,
                        key,
                        &attached,
                        &presentation,
                        &encode(&wrong).unwrap(),
                        &trust,
                        vector["now"].as_u64().unwrap()
                    )
                    .is_err());
                }
            }
        }
    }
    #[tokio::test]
    async fn legacy_profiles_cannot_select_generic_signing_implicitly() {
        let fixture = profile::tests::fixture();
        let client = NativeClient::new(fixture.profile).unwrap();
        let error = client
            .execute_with_wallet(
                b"{}",
                "generic-test-idempotency",
                &WalletSignerReference {
                    key_file: PathBuf::from("/no-key"),
                    public_key: "00".repeat(32),
                },
                false,
                &CancellationToken::new(),
                Arc::new(DispatchState::default()),
            )
            .await
            .err()
            .unwrap();
        assert_eq!(error.code, "generic_profile_required");
        assert!(!error.dispatched);
    }
}
