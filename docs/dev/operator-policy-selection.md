# Operator policy and scope selection

The access inbox at `/monitor/access/` displays the authenticated server policy
before the operator selects permissions. Its revision and digest identify the
current ceiling; separate read and write rules preserve the relationship between
operations, kinds, explicit resources and Arena restrictions. The ceiling is
returned only after independent operator Wallet authentication. The static page
contains no policy data or credentials.

Read-operation and object-kind checkboxes only narrow that ceiling. Clearing all
choices does not mean all permissions. The client preserves canonical server
ordering so a legitimate selection survives the exact request comparison before
Wallet consent. Write choices still require the server's supported activation
mode and native admission. Workflow, evidence and completion requirements remain
authoritative on the server.

The primary direct-access product flow is to request a server-advertised preset
or make a request without one; then narrow the proposal by Arena, Initiative or
Project, with finer objects under Advanced. Contributor and Project Manager are
permission templates, not implicit group membership or signing authority.
Reviewer/approver and credential administration remain explicit capabilities.

Those preset/catalog and browsable hierarchy surfaces are **not implemented by
this policy-display change**. The existing request contract has typed intent but
no preset/version reference. Current scopes include Arena filters and explicit
object IDs. Selecting a Project ID alone does not grant descendants. A future
hierarchy picker must either use a versioned, receiver-enforced subtree predicate
or explicitly disclose an exact current-object snapshot; it cannot claim that a
UI checkbox changes the credential's scope semantics.

Reuse the same direct request/decision store for native clients, the monitor and
future harness integrations. Notifications through Matrix or Nostr are deferred;
transport messages do not approve grants or mint credentials. A desktop container
may reuse the operator UI, but neither the recovery-file viewer nor the existing
browser-only secS policy editor is currently a credential management application.

Verification includes operation/kind attenuation, empty-choice denial, correlated
resource rules, actual JS-to-Python canonical selection parity, served policy
assets, and synthetic read/write review, lost-result recovery and revocation in
the existing browser walkthrough. Synthetic checks are not installed Wallet,
physical-device or live grant qualification.
