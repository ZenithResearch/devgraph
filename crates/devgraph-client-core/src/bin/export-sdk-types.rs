//! Explicit native generation; not a library/consumer install side effect.
use devgraph_client_core::*;
use ts_rs::{Config, TS};

fn main() -> std::result::Result<(), Box<dyn std::error::Error>> {
    let config = Config::default();
    let mut output = String::from(
        "// Generated from devgraph-client-core Rust models by ts-rs 12.0.1. Do not edit.\n",
    );
    macro_rules! emit { ($($ty:ty),+ $(,)?) => { $(output.push_str("export ");
        output.push_str(&<$ty>::decl(&config));output.push('\n');)+ } }
    emit!(
        TodoProgress,
        ProgressPayload,
        RejectPayload,
        ProgressWork,
        ProgressMutationResult,
        WorkKind,
        ProposalKind,
        TaskKind,
        ChildKind,
        WorkStatus,
        RequestSchema,
        RequestReference,
        CreatePayload,
        PatchPayload,
        StatusPayload,
        EmptyPayload,
        AcceptPayload,
        ConvertPayload,
        ParentPayload,
        TargetPayload,
        TaskReference,
        TaskTargetPayload,
        WorkflowAssignPayload,
        WorkflowTransitionPayload,
        WorkflowReviewPayload,
        WorkflowEvidence,
        WorkflowRequirement,
        WorkflowLayer,
        WorkRequestInput,
        Work,
        MutationRecord,
        MutationResult,
        WorkFilters,
        Subject,
        ExpectedResult,
        CypherScalar,
        CypherResult,
        ReadInput,
        ErrorKind,
        Error
    );
    output.push_str(
        "export type NamedWorkOperation = `devgraph.work.${WorkRequestInput['operation']}.${'v1' | 'v2'}`;\n",
    );
    if output.contains("any") {
        return Err("unsupported declaration mapping".into());
    }
    if let Some(path) = std::env::args().nth(1) {
        std::fs::write(path, output)?;
    } else {
        print!("{output}");
    }
    Ok(())
}
