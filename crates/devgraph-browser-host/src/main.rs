use devgraph_browser_host::Host;
use devgraph_client_native::{InstallationProfile, NativeClient};

#[tokio::main]
async fn main() {
    if run().await.is_err() {
        eprintln!("{{\"error\":\"native_host_unavailable\"}}");
        std::process::exit(2);
    }
}
async fn run() -> Result<(), Box<dyn std::error::Error>> {
    let mut args = std::env::args_os().skip(1);
    let first = args.next().ok_or("missing_origin")?;
    let (profile_path, origin) = if first == "--profile" {
        let path = args.next().ok_or("missing_profile")?;
        let origin = args.next().ok_or("missing_origin")?;
        (std::path::PathBuf::from(path), origin)
    } else {
        let binary = std::env::current_exe()?;
        (
            binary
                .parent()
                .ok_or("missing_profile")?
                .join("profile.json"),
            first,
        )
    };
    if args.next().is_some() {
        return Err("invalid_arguments".into());
    }
    let origin = origin.to_str().ok_or("invalid_origin")?;
    let client = NativeClient::new(InstallationProfile::load(&profile_path)?)?;
    client.validate_caller(origin)?;
    let host = Host::new(client);
    devgraph_browser_host::serve(host, tokio::io::stdin(), tokio::io::stdout()).await?;
    Ok(())
}
