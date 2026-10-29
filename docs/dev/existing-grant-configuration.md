# Configure an existing grant

Authenticated operator inboxes advertise
`activation.configure_existing_grants = "devgraph.grant-configuration.v1"`.
An eligible active-grant row includes an optional `configuration` object:

```json
{
  "schema": "devgraph.grant-configuration.v1",
  "selection": { "read": { "schema": "devgraph.access-draft.v5" } }
}
```

The abbreviated draft above stands for the complete persisted assignment, with
all original issuer, receiver, holder, installation, validity, credential lifetime,
action, resource, path and filter fields. It is not a template to submit. Consumers
must not infer permission rows from the summary's `access_mode`. Missing snapshots
mean configuration is unavailable, not that the grant has no permissions. A snapshot
may be omitted to keep the authenticated inbox within its existing response bound,
or when a component has been superseded by another request. Existing clients ignore
these additive fields; no stored assignment or native policy is migrated.

The existing signed operator action contracts support the two decision states:

* `expected_version: 1`: review/approve a pending request, unchanged.
* `expected_version: 2`: review/approve edits to an active grant. This is the decided
  lifecycle state, not an edit counter; exact assignment digests are the concurrency
  preconditions. Read/write mixed edits bind both existing digests.

Configuration uses the original request ID and request digest. It does not create a
new access request, require its former submission timeout to remain valid, or change
the recipient. Each existing component must remain present and nonempty. Adding or
removing a whole read/write component is intentionally unsupported by v1; omission
keeps its original meaning in pending-request provisioning. Explicit revocation
remains a separate signed action.

The server reconstructs the baseline from current active assignments, checks they
still belong to that request, validates the selection against the current operator
ceiling, and checks exact previous assignment digests. Review returns
`current_selection` alongside the proposed `selection` and activation plan. Review
only records review evidence: it does not activate, revoke or alter a grant.

Approval still requires the same operator's exact previous review, current policy
revision/digest and exact unchanged activation plan. In one transaction it replaces
the assignment(s) and decided selection, preserves the original request, and returns
`configured_existing_grant: true`. A stale review or assignment fails closed. Failure
rolls the entire transaction back. The existing original-action journal/status path
reconciles unknown results without repeating the mutation. The grant remains at
active lifecycle version 2 and can still be revoked normally.

Editing and cancelling are entirely local. A client should show current permissions
separately from proposed changes, label final approval as an update, and never send
an action merely to open the editor. If its shared editor cannot round-trip all
stored permission rows exactly, it must preserve the grant and refuse to prepare a
replacement rather than normalize away restrictions.

Verification uses synthetic signed identities and isolated databases only:

```sh
uv run pytest -q tests/native_read/test_existing_grant_configuration.py
```

These tests do not install authority, operate a production graph, or grant access
to any real Wallet. Runtime rollout is coordinated separately with Transfer.
