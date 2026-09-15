# Devgraph documentation

These documents describe the public Devgraph beta. They separate implemented
package behavior from the memory-backed demo and machine-specific deployment state.

## Use Devgraph

- [Generalist guide](user/generalists.md) — explore Work, Arenas, descriptions,
  plans and observations in the monitor, with an optional agent workflow.
- [Programmer guide](user/programmers.md) — install, develop, read through the API,
  integrate agents and understand signed operations.
- [Beta setup](user/beta.md) — persistent local hosting and credentials.
- [Usage guide](usage.md) — install, verify, run the local monitor, and use the
  Python and HTTP surfaces.
- [HTTP API reference](api.md) — routes, scopes, payloads, idempotency,
  concurrency, and errors.
- [Operator frontend](user/operator-frontend.md) — current UI behavior and its
  local-fixture boundary.
- [Initiative observations](initiative-observations.md) — the implemented
  additive `Artifact` profile.
- [Operations runbooks](runbooks/) — migration, backup/restore, and readiness
  procedures supported by repository scripts.

## Understand the implementation

- [Technical reference](technical-reference.md)
- [Current-state evidence and limits](current-state.md)
- [Service boundaries](boundaries.md)
- [Authorization](auth.md)
- [Exact monitor proof of possession](monitor-proof-of-possession.md)
- [Exports and redaction](export-redaction.md)
- [Events and outbox](events.md)
- [Observability](observability.md)
- [Canonical ontology classes](../ontology/classes.md)
- [Rolodex ontology](../ontology/rolodex.md)
- [Arena ontology](../ontology/arenas.md)

The public beta begins with a clean source snapshot. Earlier internal issue,
review and commit history is retained privately rather than shipped here.
