# Zenith Rolodex ontology

The Rolodex ontology names the people, agents, and organizations that
participate in Zenith work. It is a directory and provenance layer. It does
not replace Devgraph work objects, Castalia identity custody, or secS Magik
authority verification.

## Class hierarchy

```text
Actor
├── Person
├── Agent
└── Organization
```

| Class | Meaning |
|---|---|
| `Actor` | Common directory identity for an entity that can participate in work. |
| `Person` | Human Actor. |
| `Agent` | Software, AI, service, or other automated Actor. |
| `Organization` | Collective Actor such as a company, institution, project, or working group. |

Organizations are Actors because a collective entity can own, receive an
assignment, and receive attribution for work. The three concrete subclasses
are intended to be distinct categories. v0.4.0 does not publish a disjointness
axiom or infer a category from names, keys, or external metadata.

## Relationships

| Predicate | Shape | Meaning |
|---|---|---|
| `MEMBER_OF` | Actor → Organization | Simple current directory membership, including nested organizations. |
| `OPERATED_BY` | Agent → Actor | Operational responsibility or an Agent delegation chain. |
| `ASSIGNED_TO` | Todo-derived work → Actor | Expected executor of work. |
| `OWNED_BY` | Todo-derived work → Actor | Accountable steward for work. |
| `ATTRIBUTED_TO` | Work/evidence/provenance → Actor | Directory-level authorship or contribution attribution. |
| `HAS_EXTERNAL_LINK` | Actor → ExternalLink | Reviewed public or non-secret external reference. |

These edges are descriptive. `MEMBER_OF` does not grant organizational
authority; `OPERATED_BY` does not delegate a key; `ASSIGNED_TO` and `OWNED_BY`
do not authorize mutations; `ATTRIBUTED_TO` is not cryptographic proof.

## Identity and authority separation

An Actor id is an opaque directory reference. It is not a public key, wallet
member key, authentication Subject, credential, capability, session, or vault.
A verified transport context may later resolve its `actor_id` to an Actor, but
unverified payload data and stored directory edges cannot establish or expand
authority.

Cryptographic identity remains under its owning authority system. Castalia
wallets retain key custody, `.castaway` remains a protected vault, and secS
Magik verifies and routes capabilities. Devgraph owns the Actor vocabulary and
any future directory projection without becoming a key custodian.

## Privacy and export boundary

Generally readable Rolodex data must exclude private keys, passphrases,
recovery material, credentials, capability bodies, private contact details,
and unreviewed personal data. `HAS_EXTERNAL_LINK` may point to a reviewed
public profile or non-secret reference; it is not a substitute for a private
contact-point model.

Reified `Membership`, `Role`, validity intervals, appointment evidence,
private contact points, visibility policy, consent, and field-level export
rules are intentionally deferred.

## Runtime status

All four Rolodex classes have `runtimeLabel: false` in v0.4.0. This release
adds no Neo4j label or constraint, migration, Work kind, HTTP route, lifecycle,
or mutation operation. Runtime admission requires a separate accepted contract
covering:

- stable id and profile schemas;
- lifecycle and non-destructive archival;
- membership and role history;
- privacy, consent, visibility, and export/redaction policy;
- read and mutation operations with explicit scopes;
- actor resolution from verified authority context;
- storage constraints, migration, and backward-compatible decoders.

## Verification

```bash
rg "Actor|Person|Agent|Organization|MEMBER_OF|OPERATED_BY|ASSIGNED_TO|OWNED_BY|ATTRIBUTED_TO" ontology/
python3 -m pytest tests/docs/test_ontology_boundaries.py tests/docs/test_ontology_publication.py -q
```
