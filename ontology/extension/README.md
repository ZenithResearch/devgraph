# Devgraph ontology extension v0.2.0

Imports [Zenith normalized v0.1.0][core] as an exact offline bundle. The
ontology uses version IRIs from the Zenith manifest. The source index records
the GitHub archive used to distribute those bytes.

The extension defines five repository-owned classes.

| Devgraph class | Parent |
| --- | --- |
| SessionRef | zn:ExternalLink |
| SessionLink | zn:Artifact |
| LocalResourceRegistry | zn:Collection |
| ServiceInstance | owl:Thing |
| ZenithRepository | owl:Thing |

The four provider session reference names remain profiles of the qualified
SessionRef schema. Work evidence records can use the shared Artifact and Receipt
types when projected. See [the legacy term mapping](legacy-term-mappings.json)
for all 22 dispositions and [ontology ownership](../../docs/ontology-ownership.md)
for migration and packaging details.

The retained operation profiles implement the v0.12 runtime contract. Existing
storage labels, record IDs, receipt retry state, and approved preset snapshots
continue to use that contract. The extension is an ontology source for
inspection and projection design.

[core]: https://github.com/ZenithResearch/zenith-ontology-normalized/releases/tag/v0.1.0
