//! Calls the pinned shared admin module, with no v1 signing entrypoint or fallback.
use clap::Parser;

#[tokio::main]
async fn main() {
    let cli = match server::devgraph_work_admin::AdminCli::try_parse() {
        Ok(cli) => cli,
        Err(error) if matches!(error.kind(), clap::error::ErrorKind::DisplayHelp) => {
            print!("{error}");
            return;
        }
        Err(_) => {
            eprintln!("{{\"error\":\"invalid_arguments\"}}");
            std::process::exit(2);
        }
    };
    if let Err(code) = devgraph_qualification_tools::guest() {
        eprintln!("{{\"error\":\"{code}\"}}");
        std::process::exit(2);
    }
    match server::devgraph_work_admin::run(cli).await {
        Ok(value) => println!("{value}"),
        Err(reason) => {
            eprintln!("{{\"error\":\"{reason}\"}}");
            std::process::exit(2);
        }
    }
}
