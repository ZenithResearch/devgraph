use super::*;
use devgraph_work_protocol::{canonical_json, digest, strict_json};
use secs_native_private_files as private;
use serde::Deserialize;
use std::path::{Path, PathBuf};

#[derive(Clone, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub(crate) struct Configuration {
    pub(crate) schema: String,
    origin: String,
    audience: String,
    stable_issuer: String,
    #[serde(default)]
    extension_ids: Vec<String>,
    pub(crate) read_credential_file: PathBuf,
    pub(crate) secs_executable: PathBuf,
    pub(crate) secs_data_root: PathBuf,
}
#[derive(Clone)]
pub struct InstallationProfile {
    pub(crate) config: Configuration,
    path: PathBuf,
    source_digest: String,
    receiver_profile: String,
    bound_data_root: PathBuf,
}
#[derive(Clone, Serialize)]
pub struct PublicProfile {
    pub receiver_profile: String,
    pub stable_issuer: String,
    pub audience: String,
    pub origin: String,
}
impl InstallationProfile {
    pub fn load(path: &Path) -> Result<Self> {
        Self::load_bound(path, &canonical_data_root()?)
    }
    fn load_bound(path: &Path, bound_data_root: &Path) -> Result<Self> {
        let raw = private::read(path, 16_384).map_err(|_| denied())?;
        let value = strict_json(&raw, 16_384).map_err(|_| denied())?;
        let config: Configuration = serde_json::from_value(value.clone()).map_err(|_| denied())?;
        if !matches!(
            config.schema.as_str(),
            "devgraph.browser-host-profile.v1" | "devgraph.native-profile.v2"
        ) || config.origin != ORIGIN
            || config.audience != AUDIENCE
            || (config.schema == "devgraph.browser-host-profile.v1"
                && config.extension_ids.is_empty())
            || (config.schema == "devgraph.native-profile.v2" && !config.extension_ids.is_empty())
            || config.extension_ids.len() > 16
            || config
                .extension_ids
                .iter()
                .any(|id| id.len() != 32 || !id.bytes().all(|b| (b'a'..=b'p').contains(&b)))
            || config
                .extension_ids
                .iter()
                .collect::<std::collections::HashSet<_>>()
                .len()
                != config.extension_ids.len()
            || !config.read_credential_file.is_absolute()
            || !config.secs_executable.is_absolute()
            || !config.secs_data_root.is_absolute()
        {
            return Err(denied());
        }
        // The production secS CLI uses passwd, not HOME or an argument, to select
        // its root. Reject a different configured root before opening any state.
        if config.secs_data_root != bound_data_root {
            return Err(NativeError::new("unsupported_profile", false));
        }
        private::Directory::open(path.parent().ok_or_else(denied)?).map_err(|_| denied())?;
        private::Directory::open(&config.secs_data_root).map_err(|_| denied())?;
        let receiver_profile = devgraph_client_core::receiver_profile_digest(&config.stable_issuer)
            .map_err(|_| denied())?;
        let source_digest = digest(b"", &canonical_json(&value).map_err(|_| denied())?);
        Ok(Self {
            config,
            path: path.to_owned(),
            source_digest,
            receiver_profile,
            bound_data_root: bound_data_root.to_owned(),
        })
    }
    pub fn public(&self) -> PublicProfile {
        PublicProfile {
            receiver_profile: self.receiver_profile.clone(),
            stable_issuer: self.config.stable_issuer.clone(),
            audience: self.config.audience.clone(),
            origin: self.config.origin.clone(),
        }
    }
    pub fn validate_caller(&self, caller: &str) -> Result<()> {
        if self
            .config
            .extension_ids
            .iter()
            .any(|id| caller == format!("chrome-extension://{id}/"))
        {
            Ok(())
        } else {
            Err(NativeError::new("unapproved_extension", false))
        }
    }
    pub fn check_current(&self) -> Result<()> {
        let current = Self::load_bound(&self.path, &self.bound_data_root)?;
        if current.source_digest != self.source_digest {
            return Err(NativeError::new("receiver_profile_changed", false));
        }
        Ok(())
    }
    pub(crate) fn read_credential(&self) -> Result<zeroize::Zeroizing<String>> {
        self.check_current()?;
        let raw = zeroize::Zeroizing::new(
            private::read(&self.config.read_credential_file, 64)
                .map_err(|_| NativeError::new("read_authority_unavailable", false))?,
        );
        let token = std::str::from_utf8(&raw)
            .map_err(|_| NativeError::new("read_authority_unavailable", false))?
            .trim_end_matches('\n');
        if token.len() != 51
            || !token.starts_with("dgread1_")
            || !token[8..]
                .bytes()
                .all(|b| b.is_ascii_alphanumeric() || b == b'_' || b == b'-')
        {
            return Err(NativeError::new("read_authority_unavailable", false));
        }
        Ok(zeroize::Zeroizing::new(token.to_owned()))
    }
}

fn canonical_data_root() -> Result<PathBuf> {
    use std::{
        ffi::{CStr, OsStr},
        os::unix::ffi::OsStrExt,
    };
    let mut record = std::mem::MaybeUninit::<libc::passwd>::uninit();
    let mut result = std::ptr::null_mut();
    let mut buffer = vec![0u8; 16 * 1024];
    // SAFETY: live writable buffers; pw_dir is copied before buffer is dropped.
    let code = unsafe {
        libc::getpwuid_r(
            libc::geteuid(),
            record.as_mut_ptr(),
            buffer.as_mut_ptr().cast(),
            buffer.len(),
            &mut result,
        )
    };
    if code != 0 || result.is_null() {
        return Err(denied());
    }
    // SAFETY: successful getpwuid_r initialized the record and pw_dir string.
    let record = unsafe { record.assume_init() };
    if record.pw_dir.is_null() {
        return Err(denied());
    }
    let bytes = unsafe { CStr::from_ptr(record.pw_dir) }.to_bytes();
    if bytes.is_empty() {
        return Err(denied());
    }
    let home = PathBuf::from(OsStr::from_bytes(bytes));
    #[cfg(target_os = "macos")]
    let root = home.join("Library/Application Support/Zenith/secS");
    #[cfg(not(target_os = "macos"))]
    let root = home.join(".local/share/Zenith/secS");
    Ok(root)
}

#[cfg(test)]
pub(crate) mod tests {
    use super::*;
    use std::os::unix::fs::PermissionsExt;

    pub(crate) struct Fixture {
        pub root: tempfile::TempDir,
        pub profile: InstallationProfile,
    }
    pub(crate) fn fixture() -> Fixture {
        let root = tempfile::tempdir().unwrap();
        let path = root.path().canonicalize().unwrap();
        std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o700)).unwrap();
        let data_root = path.join("secs");
        std::fs::create_dir(&data_root).unwrap();
        std::fs::set_permissions(&data_root, std::fs::Permissions::from_mode(0o700)).unwrap();
        let value = serde_json::json!({"schema":"devgraph.browser-host-profile.v1", "origin":ORIGIN,"audience":AUDIENCE,"stable_issuer":"fixture.secs", "extension_ids":["aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"], "read_credential_file":path.join("read-token"),"secs_executable":path.join("issuer"),"secs_data_root":data_root});
        private::Output::prepare(&path.join("profile.json"))
            .unwrap()
            .write(&canonical_json(&value).unwrap())
            .unwrap();
        let profile =
            InstallationProfile::load_bound(&path.join("profile.json"), &data_root).unwrap();
        Fixture { root, profile }
    }
    #[test]
    fn native_profile_accepts_private_reads_without_extension_or_wallet() {
        let fixture = fixture();
        let path = &fixture.profile.path;
        let mut value: serde_json::Value =
            serde_json::from_slice(&std::fs::read(path).unwrap()).unwrap();
        value["schema"] = serde_json::json!("devgraph.native-profile.v2");
        value.as_object_mut().unwrap().remove("extension_ids");
        std::fs::write(path, canonical_json(&value).unwrap()).unwrap();
        let native =
            InstallationProfile::load_bound(path, &fixture.profile.bound_data_root).unwrap();
        assert!(native
            .validate_caller("chrome-extension://aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/")
            .is_err());
        assert!(!native.config.secs_executable.exists());
        let credential = format!("dgread1_{}", "a".repeat(43));
        private::Output::prepare(&native.config.read_credential_file)
            .unwrap()
            .write(credential.as_bytes())
            .unwrap();
        assert_eq!(native.read_credential().unwrap().as_str(), credential);
        value["extension_ids"] = serde_json::json!(["aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"]);
        std::fs::write(path, canonical_json(&value).unwrap()).unwrap();
        assert!(InstallationProfile::load_bound(path, &native.bound_data_root).is_err());
    }
    #[test]
    fn production_rejects_other_root_before_state_or_issuer_access() {
        let fixture = fixture();
        assert!(!fixture.profile.config.secs_executable.exists());
        // Removing the fixture root proves the mismatch guard runs before its
        // directory-open check (and before any producer invocation).
        std::fs::remove_dir(&fixture.profile.config.secs_data_root).unwrap();
        let error = InstallationProfile::load(&fixture.profile.path)
            .err()
            .unwrap();
        assert_eq!(error.code, "unsupported_profile");
        assert!(!error.dispatched);
    }
    #[test]
    fn private_profile_and_credential_require_current_exact_snapshot() {
        let fixture = fixture();
        fixture
            .profile
            .validate_caller("chrome-extension://aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/")
            .unwrap();
        assert!(fixture
            .profile
            .validate_caller("chrome-extension://aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/other")
            .is_err());
        let credential = format!("dgread1_{}", "a".repeat(43));
        private::Output::prepare(&fixture.profile.config.read_credential_file)
            .unwrap()
            .write(credential.as_bytes())
            .unwrap();
        assert_eq!(
            fixture.profile.read_credential().unwrap().as_str(),
            credential
        );
        std::fs::set_permissions(
            &fixture.profile.config.read_credential_file,
            std::fs::Permissions::from_mode(0o644),
        )
        .unwrap();
        assert_eq!(
            fixture.profile.read_credential().unwrap_err().code,
            "read_authority_unavailable"
        );
        let mut value: serde_json::Value =
            serde_json::from_slice(&std::fs::read(&fixture.profile.path).unwrap()).unwrap();
        value["stable_issuer"] = serde_json::json!("rotated.secs");
        std::fs::write(&fixture.profile.path, canonical_json(&value).unwrap()).unwrap();
        assert_eq!(
            fixture.profile.check_current().unwrap_err().code,
            "receiver_profile_changed"
        );
    }
    #[test]
    fn profile_rejects_duplicate_fields_symlinks_and_public_permissions() {
        let fixture = fixture();
        let raw = std::fs::read(&fixture.profile.path).unwrap();
        let mut duplicate = b"{\"origin\":\"http://127.0.0.1:8080\",".to_vec();
        duplicate.extend_from_slice(&raw[1..]);
        std::fs::write(&fixture.profile.path, duplicate).unwrap();
        assert!(InstallationProfile::load_bound(
            &fixture.profile.path,
            &fixture.profile.bound_data_root
        )
        .is_err());
        std::fs::write(&fixture.profile.path, raw).unwrap();
        let alias = fixture.root.path().join("alias.json");
        std::os::unix::fs::symlink(&fixture.profile.path, &alias).unwrap();
        assert!(InstallationProfile::load_bound(&alias, &fixture.profile.bound_data_root).is_err());
        std::fs::set_permissions(
            &fixture.profile.path,
            std::fs::Permissions::from_mode(0o644),
        )
        .unwrap();
        assert!(InstallationProfile::load_bound(
            &fixture.profile.path,
            &fixture.profile.bound_data_root
        )
        .is_err());
    }
}
