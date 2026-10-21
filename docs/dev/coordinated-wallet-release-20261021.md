# Coordinated Wallet access release — 2026-10-21

Status: source integration candidate. No deployment, trust or grant is activated
by this document or by the source pins. Wallet keys remain operator identities;
portable admin credentials and generic resource adapters are follow-on work.

## Dependency and review order

1. Publish/review Wallet graph access and native operator consent, then secS
   standing validity and the byte-matched read-contract snapshot.
2. Pin exact available commits in the source-set manifest. Compare all seven
   retained source files, including the dated-validity helper. Frozen legacy
   decoder dependencies remain separate and must not be mechanically repinned.
3. Run DevGraph full repository/frontend/receiver checks and matched companion
   checks with the exact producer commits. Root secS tests do not include its
   standalone read-authority workspace. Consumer builds require clean sources.
4. Merge passing reviewed sources and build immutable artifacts. New source
   commits need their own CI; historical installed passes are not a substitute.

## Runtime cutover

The configured private production host and opt-in signed-read receiver are
separate compositions. Preserve the protected private host, existing signer,
issuer and receiver pins, retained assignments, graph and replay/audit history.
An explicit HTTPS configuration is required; a git pull alone does not publish
Wallet settings or install certificates. The existing guarded LAN factory admits
only its closed route set and requires current scoped authorization on every use.

Publish the actual public HTTPS profile through the registered stable service
UUID, review changed TLS/issuer settings, and verify both Transfer's native
operator actions and iPad access on the exact installed artifact. Local discovery
or unlock does not grant operator admission. Keep until-revoked member rules
separate from challenge, read-credential, signing-key and certificate deadlines.
No implicit new identity, new ceiling, test timer renewal or legacy signer fallback.

## Acceptance and rollback

Preserve canonical request/idempotency bytes after uncertain dispatch; recover
only the original result before preparing another action. Required checks cover
no-grant/anonymous denial, scope isolation before paging/commit, dynamic descendants
and moves, retained credential revocation, version/policy races, atomic mixed
read/write grants and unchanged legacy records. Never reset replay state to make
a test pass. An installed pending EventReceipt is local commit evidence only.

Keep rollback artifacts and exact public process/configuration observations.
New certificate trust, broader grants or private custody changes require explicit
operator review. The source release does not authorize direct database access,
a writable bearer or unverified proxy identity. Physical-device and human unlock
checks remain open when the operator is unavailable; report the precise remaining
gate instead of marking the MVP complete.
