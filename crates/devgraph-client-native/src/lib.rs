//! Native-only fixed receiver, private read capability, and trusted secS process.
//! Generic signing requires an explicit v2 profile and interactive Wallet approval.
//! No key generation, custody export or grant administration is exposed.
mod authority;
mod credential;
pub use credential::WalletSignerReference;
mod http;
mod profile;

pub use authority::AuthorizedWork;
pub use http::{DispatchState, NativeResponse, ResponseHead};
pub use profile::{InstallationProfile, PublicProfile};
use serde::Serialize;
use std::{
    fmt,
    time::{SystemTime, UNIX_EPOCH},
};
pub use tokio_util::sync::CancellationToken;

pub const ORIGIN: &str = "http://127.0.0.1:8080";
pub const AUDIENCE: &str = "devgraph://receiver-local";
pub const PREVIEW_URL: &str = "http://127.0.0.1:8080/sdk-preview/";
pub const CHUNK_BYTES: usize = 49_152;
pub const FRAME_BYTES: usize = 131_072;
pub type Result<T> = std::result::Result<T, NativeError>;

#[derive(Clone, Copy, Debug, Serialize, PartialEq, Eq)]
pub struct NativeError {
    pub code: &'static str,
    pub dispatched: bool,
}
impl NativeError {
    pub const fn new(code: &'static str, dispatched: bool) -> Self {
        Self { code, dispatched }
    }
}
impl fmt::Display for NativeError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(self.code)
    }
}
impl std::error::Error for NativeError {}
pub(crate) fn denied() -> NativeError {
    NativeError::new("unsafe_profile", false)
}
pub fn now() -> Result<u64> {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs())
        .ok()
        .filter(|t| *t <= devgraph_work_protocol::MAX_SAFE - 900)
        .ok_or(NativeError::new("invalid_clock", false))
}
pub fn opaque_id() -> Result<String> {
    use rand::RngCore;
    let mut bytes = [0; 32];
    rand::rngs::OsRng
        .try_fill_bytes(&mut bytes)
        .map_err(|_| NativeError::new("randomness_unavailable", false))?;
    Ok(castalia_wallet_devgraph_presentation::base64url(&bytes))
}
pub fn decode_bytes(text: &str, maximum: usize) -> Result<Vec<u8>> {
    use base64::engine::general_purpose::URL_SAFE_NO_PAD;
    use base64::Engine;
    if text.len() > maximum.saturating_mul(4).div_ceil(3) {
        return Err(NativeError::new("invalid_message", false));
    }
    let bytes = URL_SAFE_NO_PAD
        .decode(text)
        .map_err(|_| NativeError::new("invalid_message", false))?;
    if bytes.len() > maximum || URL_SAFE_NO_PAD.encode(&bytes) != text {
        return Err(NativeError::new("invalid_message", false));
    }
    Ok(bytes)
}

#[derive(Clone)]
pub struct NativeClient {
    profile: InstallationProfile,
    http: reqwest::Client,
}
impl NativeClient {
    pub fn new(profile: InstallationProfile) -> Result<Self> {
        let http = reqwest::Client::builder()
            .no_proxy()
            .redirect(reqwest::redirect::Policy::none())
            .no_gzip()
            .no_brotli()
            .no_deflate()
            .no_zstd()
            .pool_max_idle_per_host(0)
            .timeout(std::time::Duration::from_secs(30))
            .build()
            .map_err(|_| NativeError::new("transport_unavailable", false))?;
        Ok(Self { profile, http })
    }
    pub fn public_profile(&self) -> PublicProfile {
        self.profile.public()
    }
    pub fn validate_caller(&self, caller: &str) -> Result<()> {
        self.profile.validate_caller(caller)
    }
    pub async fn check_current(&self) -> Result<()> {
        let profile = self.profile.clone();
        tokio::task::spawn_blocking(move || profile.check_current())
            .await
            .map_err(|_| denied())?
    }
}
