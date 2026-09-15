# Supporting material and linked documents

The monitor can resolve material attached to a public Work object through an additive, read-only API. Public Work kinds remain **Proposal, Initiative, Project, Issue, and Task**. This does not add generic Artifact, ExternalLink, Requirement, AcceptanceCriterion, or Plan CRUD routes, and does not change stored records or require a migration.

## Read attached metadata

`GET /work/{kind}/{work_id}/supporting-material?limit=50&after=…`

Each call verifies the bearer credential and requires `devgraph.read` before accessing storage. This expands that scope's resource coverage to allowlisted attached metadata and the separately bounded document preview described below. It grants no writes, event delivery, export authority, or access to arbitrary filesystem paths. Exact-path monitor proof-of-possession routes are unchanged. Internal/redacted/public export scopes and the private-by-default export classification of Artifact and ExternalLink remain unchanged.

```json
{
  "schema": "devgraph.work-supporting-material.v1",
  "work": {"kind": "Task", "id": "example", "version": 4},
  "items": [
    {
      "kind": "Artifact",
      "id": "plan-document",
      "via": [
        {"field": "artifact_ids", "relationships": [], "requirement_id": null},
        {"field": null, "relationships": ["HAS_ARTIFACT"], "requirement_id": null}
      ],
      "via_truncated": false,
      "resolution": "available",
      "metadata": {
        "title": "Implementation plan",
        "role": "proposal_document",
        "summary": "Authored implementation steps",
        "uri": "https://example.test/plan",
        "media_type": "text/markdown",
        "archived": false
      },
      "issues": []
    }
  ],
  "next_cursor": null,
  "content_access": "metadata_only"
}
```

The union includes `artifact_ids`, `external_link_ids`, and outgoing `HAS_ARTIFACT`, `HAS_EXTERNAL_LINK`, `HAS_REQUIREMENT`, and `HAS_ACCEPTANCE_CRITERION` relationships with matching target kinds. Reference fields and graph edges are independent. Neither source substitutes for the other. It also includes acceptance criteria reached through exactly one directly attached Requirement. That path is recorded as `relationships: ["HAS_REQUIREMENT", "HAS_ACCEPTANCE_CRITERION"]` with `requirement_id`. It does not recursively traverse supporting work or infer acceptance evidence.

Typed identities are deduplicated while preserving distinct attachment paths. Each item returns at most 100 sorted paths; `via_truncated` reports additional paths. Archived targets remain readable and marked as archived. A dangling field reference remains an item with `resolution: missing`; an unlinked object is not returned.

| Kind | Allowlisted metadata |
|---|---|
| Artifact | title, description, role, summary, uri, media_type, archived |
| ExternalLink | title, description, role, summary, url, external_id, archived |
| Requirement, AcceptanceCriterion | title, description, status, priority, version, archived |

Fields that were never stored remain absent; empty stored strings remain empty. String roles are preserved, including old values outside current enums. No timestamps, credentials, raw payloads, arbitrary properties, or document bytes are included. Credential-shaped text is scrubbed. Description/summary fields are capped at 65,536 characters; other strings are capped at 4,096. `issues` reports fixed field codes such as `description_truncated`, `summary_redacted`, `title_missing`, `priority_invalid`, and `uri_unsafe`. Unsafe locations are omitted; only validated HTTP(S), local file URIs, or absolute local paths are eligible location metadata. A location's presence does not verify remote availability or authorize a local preview.

Resolution states:

- `available`: allowlisted metadata is readable. This does not mean document bytes were fetched.
- `missing`: an attachment reference exists, but its target record does not; `metadata` is null.
- `malformed`: metadata has missing required fields or invalid values. Safe valid fields may still be returned, with `issues`; no repair is attempted.
- `unsupported_profile`: an Artifact uses the distinct InitiativeObservation profile. `metadata` is null and `issues` directs the caller to the existing typed observation reader. It must not be presented as an empty document.

An empty `items` list after a successful first-page read means no supporting references were found. A 404, authorization failure, or backend failure must not appear as that empty state. Requirements and criteria require stored title, description, status, priority, and version to be considered available; older partial records remain visible as malformed. An Artifact or ExternalLink requires a readable title; other unstored fields are optional.

## Pagination and consistency

`limit` is 1–100. Pages sort by kind and ID and include a bounded lookahead to determine `next_cursor`. Follow the opaque cursor with the **same parent and limit**. Do not construct cursors or treat them as capabilities. Every page independently rechecks read authorization and attachment membership. A cursor cannot select an unrelated target.

Malformed or differently bound cursors return 400. A changed parent version or changed parent reference fields return **409** with detail `supporting_material_changed_restart_pagination`; discard the accumulated page set and restart. Cursors are keyset positions, not frozen graph snapshots. Edge-only changes do not currently have a persisted graph revision: newly attached targets before the cursor require a fresh first-page read, and detached targets disappear. Refresh should restart pagination when the user needs the current complete attachment set. This limitation is explicit and requires no stored-data migration.

Both adapters use bounded parent-specific reference reads and bounded target reads. Neo4j queries anchor attachment traversal to the requested parent; they do not collect all graph edges. Reads make no Work mutations and create no EventReceipts; successful reads use the existing safe audit path.

## Preview a linked document

`GET /work/{kind}/{work_id}/supporting-material/Artifact/{artifact_id}/document`

The server reauthorizes the parent and resolves this exact attached Artifact before reading any file. Callers cannot submit a URI, replacement location, or arbitrary path. An unrelated Artifact returns 404 even if it exists elsewhere. Missing and malformed metadata remain explicit response states.

```json
{
  "schema": "devgraph.work-document.v1",
  "artifact_id": "plan-document",
  "state": "readable",
  "message": "Document loaded.",
  "media_type": "text/markdown",
  "size_bytes": 17,
  "content": "# Plan\n\nDo work.\n"
}
```

The `message` is user-facing explanation, not a stable machine discriminator; branch on `state`. All envelope keys are present, with null `content`, `media_type`, or `size_bytes` where unavailable. States are `readable`, `metadata_only` (remote source or no local URI), `unconfigured`, `missing`, `unsupported`, and `unavailable`. The API never fetches arbitrary remote URLs. A browser may offer an explicit HTTP(S) “Open source” action; it must not forward the local bearer credential or embed active third-party HTML.

Local previews require an explicit `DEVGRAPH_DOCUMENT_ROOTS` environment value: a JSON array of 1–16 absolute, narrow document directories. The default is empty, so the reader returns `unconfigured`. Invalid configuration fails closed. Never configure `/`, a home directory, or a directory containing application credentials as a document root. On macOS, use canonical physical paths rather than symlink aliases. Configuration alone does not create directories or import files into Devgraph.

The reader supports UTF-8 plain text and Markdown, bounded to 256 KiB of file bytes. An optional leading UTF-8 byte-order mark is removed from the returned text; `size_bytes` is the UTF-8 byte length of that returned `content`. The file limit still includes the byte-order mark. It uses descriptor-relative traversal with no-follow flags for root ancestors and document descendants, accepts only regular files, checks the same descriptor while reading, and rejects escaping paths, symlinks, nonregular files, and unsupported formats. Render returned content as text; never execute embedded HTML. Files not linked to Work are outside this API's scope. A description or an attached document can contain an authored plan; the API never invents planning content.

## Python client

`DevgraphHttpClient.get_supporting_material(context, kind="Task", work_id="example", limit=50, after=None)` returns `SupportingMaterialEnvelope`. `get_work_document(context, kind="Task", work_id="example", artifact_id="plan-document")` returns `WorkDocumentEnvelope`. The existing strict Work DTO and its five-kind contract are unchanged. Both new methods issue one bounded read request and validate the response identity; neither automatically follows cursors or opens links.

The shared wire models live in `devgraph.supporting_material_contract`. Isolated tests cover reader authorization, all four kinds, field/edge union and one-hop traversal, missing/legacy/malformed records, safe metadata, paging, adapter query contracts, unchanged export denials, client/API round trips, and linked-document containment. Live linked-document availability must be established separately; fixture success is not evidence that a real stored attachment exists.
