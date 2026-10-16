//! Qualification tooling only. No OS-home override and no physical-host execution.
use serde_json::Value;
use std::{path::PathBuf, process::Command};

pub fn guest() -> Result<PathBuf, &'static str> {
    if !cfg!(target_os = "macos")
        || std::env::var("BUILDKITE").as_deref() != Ok("true")
        || std::env::var("BUILDKITE_SOURCE").as_deref() != Ok("ui")
        || unsafe { libc::geteuid() } == 0
    {
        return Err("manual_disposable_macos_guest_required");
    }
    let model = Command::new("/usr/sbin/sysctl")
        .args(["-n", "hw.model"])
        .env_clear()
        .output()
        .map_err(|_| "guest_environment_unavailable")?;
    if !model.status.success()
        || !std::str::from_utf8(&model.stdout)
            .map_err(|_| "invalid_hardware_model")?
            .trim()
            .starts_with("VirtualMac")
    {
        return Err("physical_host_denied");
    }
    // secS independently derives this directory from the OS account database.
    let root = server::devgraph_issue_create_cli::canonical_data_root()
        .map_err(|_| "unsafe_os_account_home")?;
    let home = root.ancestors().nth(4).ok_or("unsafe_os_account_home")?;
    if home.join("Library/Application Support/Zenith/secS") != root {
        return Err("unsafe_os_account_home");
    }
    let raw =
        secs_native_private_files::read(&home.join(".devgraph-qualification-guest.json"), 4096)
            .map_err(|_| "qualification_guest_marker_required")?;
    let marker = devgraph_client_core::protocol::strict_json(&raw, 4096)
        .map_err(|_| "invalid_guest_marker")?;
    if marker.get("uid").and_then(Value::as_u64) != Some(unsafe { libc::geteuid() }.into())
        || marker
            .get("id")
            .and_then(Value::as_str)
            .is_none_or(str::is_empty)
    {
        return Err("invalid_guest_marker");
    }
    Ok(home.to_owned())
}

#[cfg(test)]
mod tests {
    #[test]
    fn physical_or_noninteractive_host_is_refused_without_reading_authority() {
        // This test is deliberately not an overrideable "test guest" path.
        assert!(super::guest().is_err());
    }
}
