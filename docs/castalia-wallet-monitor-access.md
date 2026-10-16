# Historical Wallet monitor proposal

The old Wallet read-connection proposal is superseded. Browser SDK reads now go
directly to Devgraph using an explicitly supplied in-memory read credential;
native SDK reads retain owner-private access. Reads do not require Wallet.

The existing exact monitor proof receiver and historical fixtures remain for
compatibility. They do not establish a deployed remote service or a supported
Wallet read bridge. New integrations follow
[application-owned transport](dev/kanban-generic-wallet-migration.md).
