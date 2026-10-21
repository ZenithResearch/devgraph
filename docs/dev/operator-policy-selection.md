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

The current candidate implements the versioned preset catalog and hierarchical
multi-area picker described in [the catalog contract](../contracts/access-catalog/v1/README.md).
Requester preferences do not grant access. An operator can select globally within
the ceiling, or select multiple Arena/Initiative/Project paths. Each selected
container includes resources beneath it at each access, including future
resources; moved-out work loses access through that path. The separate Advanced
explicit-ID filter selects only those objects, not their descendants. Path
creation/evidence and native admission still have independent limits.

Current active grants can be revoked. Editing a grant without another member
request needs the [standing-grant revision change](standing-grant-revision.md);
it is not implemented by the picker or by this status-display fix.

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


### Connection state and request history

The Wallet connection controls report the current read-connection stage separately
from access-request progress. A completed request result is labeled “Last access
request update”; it is a receipt of that earlier response, not a live statement
that the grant remains pending or active. Connecting reads must show the accepted
connection even when the last request response was pending. Request history is
hidden when the corresponding Wallet identity/installation is disconnected.
Original request bytes and status recovery remain unchanged.
