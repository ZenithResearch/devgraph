# Scoped holder integration candidate

Status: candidate, not a production migration. Existing runtime configurations
default to `holder_grant_schema: secs.holder-grant.v1`. An explicitly reviewed
configuration may choose `secs.holder-grant.v2` with the matched helper binary
and its exact digest. Do not change only the label or substitute an old binary.

Both versions verify against the canonical provider registry. V1 signed bytes,
fixed-ID meaning, revocation and replay records are retained. V2 uses the shared
secS role-selector contract and Wallet Core dual readers. Original application
conditions remain signed and are intersected with the selector. All means live
type-bounded coverage; selected IDs do not expand to descendants. Hierarchy
checks resolve current placement and preserve inherited constraints on narrowing.

The existing monitor/native read routes use the same native cryptographic
verification, registry status and replay checks. Results are captured while graph
state and provider policy/revocation are fenced. Bounded materialization of exact
or hierarchy selectors filters before pagination/disclosure. All selectors avoid
an issuance-time object inventory. Capacity overflow, ambiguous containment,
unsupported roles/relations or malformed helper responses fail closed.

V2 configuration also enables paired checks on the existing native signed
Work/Arena mutation path. The provider resolves subject, related records, actual
source, requested destination and affected descendants. One complete installed
receiver rule must cover all effects. It retains the same rule and original
source during the postcheck, under the existing graph transaction and policy
lock. Failed checks roll back mutation, receipt and audit. Existing native
verification, versions and idempotency remain mandatory; helper output alone is
not a write credential. Creation uses provider-only prospective witnesses for
request-owned new IDs and verifies their actual existence before commit.

This changes no public ontology definition. `devgraph.containment.v1` is the
installed adapter mapping over validated runtime containment, not a claim that
namespace ScopeRef or ResourceInstance capacity conveys authorization.

Current limits: reusable holder issuance is read-only. Writes still use the
existing signed presentation/projection flow with the new paired guard. Generic
per-role administration and holder-derived write issuance are not complete.
Changing a code/configuration pin does not grant a member new access.

Qualify secS, Core/WASM, Chrome, Transfer and iOS together before rollout. A
production review must identify exact code/helper hashes and canonical and routed
configurations. App replacement and live policy/grants remain separate actions.
