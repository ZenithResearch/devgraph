# Security

Devgraph's beta is a local, loopback service. Keep the API and database bound to
loopback. A read capability permits reads only; signed writes require the
operator's native Wallet identity and current secS grants. Do not place a
credential, private signing key, generated trust bundle, database, or backup in
the source repository or an integration package.

Report a suspected vulnerability through GitHub's
[private vulnerability reporting form](https://github.com/ZenithResearch/devgraph/security/advisories/new).
Sign in to GitHub to submit the report. Reports are shared privately with the
repository's security maintainers; do not put exploit details, credentials or
private work data in a public issue.

If GitHub prevents submission, open a public issue titled **Private security
reporting unavailable** with no vulnerability details or sensitive attachments.
A maintainer can restore the private reporting channel before you submit the
report. This is a channel-availability fallback, not a request to disclose the
vulnerability publicly.

Include the affected release, platform, prerequisites, impact, and a minimal
reproduction using synthetic data. Strip credentials, private keys, real work
content, and operator paths from logs and screenshots. Security reports should
not require access to a reporter's live graph.

Beta security fixes target the latest beta release. Historical ontology bundles
are immutable contracts; a contract correction uses a new version.

Installing an agent skill or plugin does not create a Devgraph identity, widen
grants, or authorize a graph mutation. Each user configures their own local
service. The included demo uses disposable memory and an explicitly synthetic
read credential; it must not be exposed on a public interface.
