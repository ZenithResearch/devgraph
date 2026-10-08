use super::*;
use castalia_wallet_devgraph_presentation::{decode64, Presentation};
use devgraph_work_protocol::{canonical_json, digest, strict_json, WorkRequest, MAX_SAFE};
use secs_devgraph_work_contract::{
    verify_presentation, verify_projection, ProjectionExpectation, WorkPolicy,
};
use secs_native_private_files as private;
use serde::Deserialize;
use std::{
    fs::File,
    os::unix::fs::{MetadataExt, PermissionsExt},
    process::Stdio,
    time::Duration,
};

pub struct AuthorizedWork {
    pub(crate) request: WorkRequest,
    pub(crate) key: zeroize::Zeroizing<String>,
    pub(crate) presentation: Presentation,
    pub(crate) projection: Vec<u8>,
    pub(crate) receiver_profile: String,
    pub(crate) actor: String,
    expires_at: u64,
}
impl AuthorizedWork {
    pub fn expires_at(&self) -> u64 {
        self.expires_at
    }
    pub fn actor_id(&self) -> &str {
        &self.actor
    }
    pub fn receiver_profile(&self) -> &str {
        &self.receiver_profile
    }
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Manifest {
    schema: String,
    schema_version: u64,
    audience: String,
    receiver_policy_digest_sha256: String,
    replay_schema: String,
    secs_public_key_registry_sha256: String,
    secs_verifier_key_id: String,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Registry {
    schema: String,
    schema_version: u64,
    keys: Vec<Key>,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Key {
    algorithm: String,
    key_id: String,
    production_authority: bool,
    public_key_base64url: String,
    status: String,
    #[serde(default)]
    not_before: Option<u64>,
    #[serde(default)]
    not_after: Option<u64>,
    #[serde(default)]
    revoked_at: Option<u64>,
    #[serde(default)]
    replaced_by: Option<String>,
}
fn parse<T: serde::de::DeserializeOwned>(raw: &[u8], cap: usize) -> Result<T> {
    serde_json::from_value(strict_json(raw, cap).map_err(|_| denied())?).map_err(|_| denied())
}
fn label(s: &str) -> bool {
    !s.is_empty()
        && s.len() <= 128
        && s.bytes()
            .all(|b| b.is_ascii_alphanumeric() || b"._:-".contains(&b))
}
pub(crate) struct Trust {
    pub(crate) policy: WorkPolicy,
    pub(crate) verifier_id: String,
    pub(crate) public: [u8; 32],
}
pub(crate) fn current_trust(profile: &InstallationProfile, clock: u64) -> Result<Trust> {
    profile.check_current()?;
    let root = profile
        .config
        .secs_data_root
        .join("authority/devgraph.work.v1");
    let _root = private::Directory::open(&root).map_err(|_| denied())?;
    let manifest_raw =
        private::read(&root.join("producer-manifest.json"), 65_536).map_err(|_| denied())?;
    let manifest: Manifest = parse(&manifest_raw, 65_536)?;
    let policy = WorkPolicy::parse(
        &private::read(&root.join("receiver-policy.json"), 262_144).map_err(|_| denied())?,
    )
    .map_err(|_| denied())?;
    let registry_raw = private::read(&root.join("secs-public-key-registry.json"), 262_144)
        .map_err(|_| denied())?;
    if manifest.schema != "secs-devgraph-work-producer-manifest.v1"
        || manifest.schema_version != 1
        || manifest.audience != AUDIENCE
        || manifest.replay_schema != "secs-devgraph-work-replay.v1"
        || !label(&manifest.secs_verifier_key_id)
        || manifest.receiver_policy_digest_sha256 != policy.digest().map_err(|_| denied())?
        || manifest.secs_public_key_registry_sha256 != digest(b"", &registry_raw)
    {
        return Err(denied());
    }
    let registry: Registry = parse(&registry_raw, 262_144)?;
    if registry.schema != "secs-public-verifier-key-registry.v1"
        || registry.schema_version != 1
        || registry.keys.is_empty()
        || registry.keys.len() > 256
    {
        return Err(denied());
    }
    let mut ids = std::collections::HashSet::new();
    let mut selected = None;
    for key in registry.keys {
        if key.algorithm != "ed25519"
            || !label(&key.key_id)
            || !ids.insert(key.key_id.clone())
            || key
                .not_before
                .into_iter()
                .chain(key.not_after)
                .chain(key.revoked_at)
                .any(|n| n > MAX_SAFE)
            || key.replaced_by.as_deref().is_some_and(|s| !label(s))
            || !matches!(
                key.status.as_str(),
                "active" | "revoked" | "expired" | "unknown" | "not_yet_valid"
            )
        {
            return Err(denied());
        }
        let public = decode64::<32>(&key.public_key_base64url).map_err(|_| denied())?;
        ed25519_dalek::VerifyingKey::from_bytes(&public).map_err(|_| denied())?;
        if key.key_id == manifest.secs_verifier_key_id {
            if key.status != "active"
                || !key.production_authority
                || key.not_before.is_some_and(|n| clock < n)
                || key.not_after.is_some_and(|n| clock >= n)
                || key.revoked_at.is_some_and(|n| clock >= n)
            {
                return Err(NativeError::new("authority_denied", false));
            }
            selected = Some(public);
        }
    }
    // An atomic generation switch during the three reads cannot silently mix trust.
    if private::read(&root.join("producer-manifest.json"), 65_536).map_err(|_| denied())?
        != manifest_raw
    {
        return Err(NativeError::new("authority_changed", false));
    }
    Ok(Trust {
        policy,
        verifier_id: manifest.secs_verifier_key_id,
        public: selected.ok_or_else(denied)?,
    })
}
pub(crate) fn verify_authorized(
    profile: &InstallationProfile,
    authorized: &AuthorizedWork,
    clock: u64,
) -> Result<()> {
    if authorized.receiver_profile != profile.public().receiver_profile
        || authorized.expires_at <= clock
    {
        return Err(NativeError::new("authorization_expired", false));
    }
    let trust = current_trust(profile, clock)?;
    trust
        .policy
        .authorize_until(&authorized.actor, &authorized.request, clock)
        .map_err(|_| NativeError::new("authority_denied", false))?;
    verify_projection(
        &authorized.projection,
        ProjectionExpectation {
            request: &authorized.request,
            idempotency_key: &authorized.key,
            presentation: &authorized.presentation,
            audience: AUDIENCE,
            actor_id: &authorized.actor,
            receiver_policy_id: &trust.policy.policy_id,
            receiver_policy_version: trust.policy.policy_version,
            receiver_policy_digest: &trust.policy.digest().map_err(|_| denied())?,
            verifier_key_id: &trust.verifier_id,
            verifier_public_key: &trust.public,
            now: clock,
        },
    )
    .map_err(|_| NativeError::new("invalid_authority", false))?;
    Ok(())
}
struct Invocation {
    _temporary: tempfile::TempDir,
    input: std::path::PathBuf,
    key: std::path::PathBuf,
    output: std::path::PathBuf,
    binary: File,
}
impl NativeClient {
    pub async fn authorize(
        &self,
        request_raw: &[u8],
        key: &str,
        presentation_raw: &[u8],
        actor: &str,
        cancel: &CancellationToken,
    ) -> Result<AuthorizedWork> {
        let deadline = tokio::time::Instant::now() + Duration::from_secs(10);
        let request = WorkRequest::parse(request_raw)
            .map_err(|_| NativeError::new("invalid_request", false))?;
        if request.canonical() != request_raw
            || !matches!(
                request.value()["schema"].as_str(),
                Some("devgraph.work-request.v1" | "devgraph.work-request.v2")
            )
        {
            return Err(NativeError::new("invalid_request", false));
        }
        let presentation = Presentation::parse(presentation_raw)
            .map_err(|_| NativeError::new("invalid_presentation", false))?;
        let verified = verify_presentation(&request, &presentation, key, now()?)
            .map_err(|_| NativeError::new("invalid_presentation", false))?;
        if verified.actor_id() != actor {
            return Err(NativeError::new("actor_changed", false));
        }
        let producer_input = canonical_json(&serde_json::json!({"schema":"secs-devgraph-work-producer-input.v1", "schema_version":1,"request":request.value(),"wallet_presentation":presentation})).map_err(|_| denied())?;
        let key_file = format!("{key}\n");
        let profile = self.profile.clone();
        let prepared = tokio::task::spawn_blocking(move || -> Result<Invocation> {
            profile.check_current()?;
            let binary = private::open(&profile.config.secs_executable, 128 * 1024 * 1024)
                .map_err(|_| NativeError::new("issuer_unavailable", false))?;
            if binary.metadata().map_err(|_| denied())?.mode() & 0o100 == 0 {
                return Err(NativeError::new("issuer_unavailable", false));
            }
            let temporary = tempfile::Builder::new()
                .prefix("devgraph-browser-work-")
                .permissions(std::fs::Permissions::from_mode(0o700))
                .tempdir()
                .map_err(|_| denied())?;
            let root = temporary.path().canonicalize().map_err(|_| denied())?;
            let _directory = private::Directory::open(&root).map_err(|_| denied())?;
            let input = root.join("producer-input.json");
            let key = root.join("idempotency.txt");
            let output = root.join("projection.json");
            private::Output::prepare(&input)
                .and_then(|f| f.write(&producer_input))
                .map_err(|_| denied())?;
            private::Output::prepare(&key)
                .and_then(|f| f.write(key_file.as_bytes()))
                .map_err(|_| denied())?;
            Ok(Invocation {
                _temporary: temporary,
                input,
                key,
                output,
                binary,
            })
        });
        let invocation = tokio::select! {
            _ = cancel.cancelled() => return Err(NativeError::new("cancelled",false)),
            result = tokio::time::timeout_at(deadline, prepared) => result.map_err(|_| NativeError::new("issuer_timeout",false))?.map_err(|_| denied())??,
        };
        if cancel.is_cancelled() || tokio::time::Instant::now() >= deadline {
            return Err(NativeError::new("cancelled", false));
        }
        let expected = invocation.binary.metadata().map_err(|_| denied())?;
        let current = private::open(&self.profile.config.secs_executable, 128 * 1024 * 1024)
            .map_err(|_| denied())?
            .metadata()
            .map_err(|_| denied())?;
        if expected.dev() != current.dev() || expected.ino() != current.ino() {
            return Err(NativeError::new("issuer_changed", false));
        }
        let mut child = tokio::process::Command::new(&self.profile.config.secs_executable)
            .env_clear()
            .current_dir("/")
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .kill_on_drop(true)
            .arg("--request-file")
            .arg(&invocation.input)
            .arg("--idempotency-key-file")
            .arg(&invocation.key)
            .arg("--signed-projection-output")
            .arg(&invocation.output)
            .spawn()
            .map_err(|_| NativeError::new("issuer_unavailable", false))?;
        let status = tokio::select! {
            _ = cancel.cancelled() => { let _ = child.kill().await; let _ = child.wait().await; return Err(NativeError::new("cancelled",false)); },
            result = tokio::time::timeout_at(deadline, child.wait()) => match result {
                Ok(result) => result.map_err(|_| NativeError::new("issuer_unavailable",false))?,
                Err(_) => { let _ = child.kill().await; let _ = child.wait().await; return Err(NativeError::new("issuer_timeout",false)); }
            }
        };
        if !status.success() {
            return Err(NativeError::new("authority_denied", false));
        }
        let profile = self.profile.clone();
        let actor = actor.to_owned();
        let key = zeroize::Zeroizing::new(key.to_owned());
        let loaded = tokio::task::spawn_blocking(move || -> Result<AuthorizedWork> {
            let projection = private::read(&invocation.output, 16_384)
                .map_err(|_| NativeError::new("invalid_authority", false))?;
            let mut authorized = AuthorizedWork {
                request,
                key,
                presentation,
                projection,
                actor,
                receiver_profile: profile.public().receiver_profile,
                expires_at: MAX_SAFE,
            };
            let current = now()?;
            verify_authorized(&profile, &authorized, current)?;
            let projection_value =
                strict_json(&authorized.projection, 16_384).map_err(|_| denied())?;
            authorized.expires_at = projection_value["expires_at"].as_u64().ok_or_else(denied)?;
            Ok(authorized)
        });
        tokio::select! {
            _ = cancel.cancelled() => Err(NativeError::new("cancelled",false)),
            result = tokio::time::timeout_at(deadline,loaded) => result.map_err(|_| NativeError::new("issuer_timeout",false))?.map_err(|_| denied())?,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use ed25519_dalek::{Signer, SigningKey};
    use std::os::unix::fs::PermissionsExt;
    fn signed_request() -> (Vec<u8>, Vec<u8>, String) {
        let request = WorkRequest::parse(br#"{"schema":"devgraph.work-request.v1","operation":"create","kind":"Task","id":"fixture-task","expected_version":null,"payload":{"id":"fixture-task","title":"Fixture"}}"#).unwrap();
        let wallet = SigningKey::from_bytes(&[37; 32]);
        let public = wallet.verifying_key().to_bytes();
        let prepared = castalia_wallet_devgraph_presentation::prepare(
            &request,
            "fixture-idempotency-key-0001",
            &public,
            now().unwrap(),
            &[5; 16],
            &[9; 12],
        )
        .unwrap();
        let signature = wallet.sign(prepared.signature_transcript()).to_bytes();
        let presentation =
            castalia_wallet_devgraph_presentation::complete(prepared, &signature).unwrap();
        (
            request.canonical().to_vec(),
            presentation.canonical().unwrap(),
            castalia_wallet_devgraph_presentation::actor_id(&public),
        )
    }
    fn sleeping_issuer(fixture: &profile::tests::Fixture) -> std::path::PathBuf {
        let pidfile = fixture.root.path().canonicalize().unwrap().join("pid");
        let script = format!(
            "#!/bin/sh\necho $$ > '{}'\nexec /bin/sleep 60\n",
            pidfile.display()
        );
        std::fs::write(&fixture.profile.config.secs_executable, script).unwrap();
        std::fs::set_permissions(
            &fixture.profile.config.secs_executable,
            std::fs::Permissions::from_mode(0o700),
        )
        .unwrap();
        pidfile
    }
    async fn launched(pidfile: &std::path::Path) -> i32 {
        for _ in 0..200 {
            if let Ok(raw) = std::fs::read_to_string(pidfile) {
                return raw.trim().parse().unwrap();
            }
            tokio::time::sleep(Duration::from_millis(10)).await;
        }
        panic!("controlled fixture issuer did not launch");
    }
    #[tokio::test]
    async fn arena_is_rejected_before_the_work_only_issuer_path() {
        let fixture = profile::tests::fixture();
        let pidfile = sleeping_issuer(&fixture);
        let client = NativeClient::new(fixture.profile).unwrap();
        let request = WorkRequest::parse(br#"{"schema":"devgraph.arena-request.v1","operation":"create","kind":"Arena","id":"fixture-arena","expected_version":null,"payload":{"id":"fixture-arena","title":"Fixture"}}"#).unwrap();
        let wallet = SigningKey::from_bytes(&[37; 32]);
        let public = wallet.verifying_key().to_bytes();
        let key = "fixture-idempotency-key-0001";
        let prepared = castalia_wallet_devgraph_presentation::prepare(
            &request,
            key,
            &public,
            now().unwrap(),
            &[5; 16],
            &[9; 12],
        )
        .unwrap();
        let signature = wallet.sign(prepared.signature_transcript()).to_bytes();
        let presentation =
            castalia_wallet_devgraph_presentation::complete(prepared, &signature).unwrap();
        let error = client
            .authorize(
                request.canonical(),
                key,
                &presentation.canonical().unwrap(),
                &castalia_wallet_devgraph_presentation::actor_id(&public),
                &CancellationToken::new(),
            )
            .await
            .err()
            .unwrap();
        assert_eq!(error, NativeError::new("invalid_request", false));
        assert!(!pidfile.exists());
    }

    #[tokio::test]
    async fn cancelled_issuer_is_killed_and_reaped_without_projection() {
        let fixture = profile::tests::fixture();
        let pidfile = sleeping_issuer(&fixture);
        let client = NativeClient::new(fixture.profile).unwrap();
        let (raw, presentation, actor) = signed_request();
        let cancel = CancellationToken::new();
        let child_cancel = cancel.clone();
        let pending = tokio::spawn(async move {
            client
                .authorize(
                    &raw,
                    "fixture-idempotency-key-0001",
                    &presentation,
                    &actor,
                    &child_cancel,
                )
                .await
        });
        let pid = launched(&pidfile).await;
        cancel.cancel();
        let error = pending.await.unwrap().err().unwrap();
        assert_eq!(error, NativeError::new("cancelled", false));
        // SAFETY: signal zero only queries whether the synthetic child still exists.
        assert_eq!(unsafe { libc::kill(pid, 0) }, -1);
    }
    #[tokio::test]
    async fn issuer_has_a_whole_operation_deadline() {
        let fixture = profile::tests::fixture();
        let pidfile = sleeping_issuer(&fixture);
        let client = NativeClient::new(fixture.profile).unwrap();
        let (raw, presentation, actor) = signed_request();
        let error = client
            .authorize(
                &raw,
                "fixture-idempotency-key-0001",
                &presentation,
                &actor,
                &CancellationToken::new(),
            )
            .await
            .err()
            .unwrap();
        assert_eq!(error, NativeError::new("issuer_timeout", false));
        let pid = launched(&pidfile).await;
        // SAFETY: signal zero only queries whether the synthetic child still exists.
        assert_eq!(unsafe { libc::kill(pid, 0) }, -1);
    }
    #[test]
    fn malformed_registry_and_duplicate_contract_fields_are_denied() {
        assert!(parse::<Registry>(br#"{"schema":"secs-public-verifier-key-registry.v1","schema_version":1,"keys":[],"keys":[]}"#, 262144).is_err());
        assert!(parse::<Manifest>(br#"{"schema":"other","unknown":true}"#, 65536).is_err());
    }
}

#[cfg(test)]
mod golden_tests {
    use super::*;
    use serde_json::{json, Value};
    fn write(path: &std::path::Path, value: &Value) {
        let bytes = canonical_json(value).unwrap();
        if path.exists() {
            std::fs::write(path, bytes).unwrap();
        } else {
            private::Output::prepare(path)
                .unwrap()
                .write(&bytes)
                .unwrap();
        }
    }
    fn install_public_trust(
        profile: &InstallationProfile,
        vector: &Value,
        revoked: bool,
        deny_rule: bool,
    ) {
        let root = profile
            .config
            .secs_data_root
            .join("authority/devgraph.work.v1");
        for path in [
            profile.config.secs_data_root.join("authority"),
            root.clone(),
        ] {
            if !path.exists() {
                std::fs::create_dir(&path).unwrap();
                std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o700)).unwrap();
            }
        }
        let registry = json!({"schema":"secs-public-verifier-key-registry.v1","schema_version":1,"keys":[{"algorithm":"ed25519","key_id":vector["projection"]["secs_verifier_key_id"],"production_authority":true,"public_key_base64url":vector["public_key"],"status":if revoked {"revoked"} else {"active"},"not_before":1799999900u64,"not_after":1800001000u64}]});
        let mut policy = vector["policy"].clone();
        if deny_rule {
            policy["rules"][0]["effect"] = json!("deny");
        }
        let policy_digest = WorkPolicy::parse(&canonical_json(&policy).unwrap())
            .unwrap()
            .digest()
            .unwrap();
        let manifest = json!({"schema":"secs-devgraph-work-producer-manifest.v1","schema_version":1,"audience":AUDIENCE,"receiver_policy_digest_sha256":policy_digest,"replay_schema":"secs-devgraph-work-replay.v1","secs_public_key_registry_sha256":digest(b"",&canonical_json(&registry).unwrap()),"secs_verifier_key_id":vector["projection"]["secs_verifier_key_id"]});
        write(&root.join("receiver-policy.json"), &policy);
        write(&root.join("secs-public-key-registry.json"), &registry);
        write(&root.join("producer-manifest.json"), &manifest);
    }
    fn authorized(profile: &InstallationProfile, vector: &Value) -> AuthorizedWork {
        AuthorizedWork {
            request: WorkRequest::parse(&canonical_json(&vector["request"]).unwrap()).unwrap(),
            key: zeroize::Zeroizing::new(vector["key"].as_str().unwrap().into()),
            presentation: Presentation::parse(
                &canonical_json(&vector["wallet_presentation"]).unwrap(),
            )
            .unwrap(),
            projection: canonical_json(&vector["projection"]).unwrap(),
            receiver_profile: profile.public().receiver_profile,
            actor: vector["projection"]["actor_id"].as_str().unwrap().into(),
            expires_at: vector["projection"]["expires_at"].as_u64().unwrap(),
        }
    }
    #[test]
    fn all_frozen_native_issuer_projections_verify_against_current_private_public_trust() {
        let vectors: Vec<Value> = serde_json::from_slice(include_bytes!(
            "../../../tests/fixtures/named-work-v1/signed-vectors.json"
        ))
        .unwrap();
        assert_eq!(vectors.len(), 15);
        for vector in &vectors {
            let fixture = profile::tests::fixture();
            install_public_trust(&fixture.profile, vector, false, false);
            let authorization = authorized(&fixture.profile, vector);
            verify_authorized(&fixture.profile, &authorization, 1800000000).unwrap();
            let mut changed = authorized(&fixture.profile, vector);
            changed.key = zeroize::Zeroizing::new("changed-idempotency-key-0001".into());
            assert!(verify_authorized(&fixture.profile, &changed, 1800000000).is_err());
            let mut changed = authorized(&fixture.profile, vector);
            changed.receiver_profile = "0".repeat(64);
            assert!(verify_authorized(&fixture.profile, &changed, 1800000000).is_err());
            let mut changed_request = vector["request"].clone();
            changed_request["id"] = json!("different-id");
            if changed_request["operation"] == "create" {
                changed_request["payload"]["id"] = json!("different-id");
            }
            let mut changed = authorized(&fixture.profile, vector);
            changed.request =
                WorkRequest::parse(&canonical_json(&changed_request).unwrap()).unwrap();
            assert!(verify_authorized(&fixture.profile, &changed, 1800000000).is_err());

            assert!(verify_authorized(&fixture.profile, &authorization, 1800000060).is_err());
            install_public_trust(&fixture.profile, vector, true, false);
            assert_eq!(
                verify_authorized(&fixture.profile, &authorization, 1800000000)
                    .unwrap_err()
                    .code,
                "authority_denied"
            );
            install_public_trust(&fixture.profile, vector, false, true);
            assert_eq!(
                verify_authorized(&fixture.profile, &authorization, 1800000000)
                    .unwrap_err()
                    .code,
                "authority_denied"
            );
            install_public_trust(&fixture.profile, vector, false, false);
            let mut tampered = vector.clone();
            tampered["projection"]["secs_verifier_signature"] =
                json!(castalia_wallet_devgraph_presentation::base64url(&[0u8; 64]));
            assert_eq!(
                verify_authorized(
                    &fixture.profile,
                    &authorized(&fixture.profile, &tampered),
                    1800000000
                )
                .unwrap_err()
                .code,
                "invalid_authority"
            );
        }
    }
}
