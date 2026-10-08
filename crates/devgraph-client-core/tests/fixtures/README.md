# HTTP response corpus

These raw UTF-8 responses came from the repository's real FastAPI application,
`SecSWorkAdapter`, named mutation implementation and memory-backed graph/outbox.
Requests ran through `fastapi.testclient.TestClient`, with the synthetic Ed25519
authority helpers in `tests/auth/test_secs_work.py`; no live service, native
identity, production credential or external network was used.

`http-responses.json` captures create, duplicate (explicit null Work), protected
get/list and relationships. `http-operation-responses.json` captures all eleven
operations and create for all five public Work kinds. Receipt IDs were supplied
by a deterministic fixture factory. They remain unsigned mutation records.

The operation sequence starts with Proposal, Initiative, Project, Issue and two
Tasks. It patches and transitions the Issue, accepts and converts the Proposal,
parents the first Task to the Issue, adds/removes a dependency on the Project,
adds/removes a Task blocker, then archives the Initiative. Versions, operation
names, subject identity and output envelopes are recorded exactly as the Python
receiver returned them. The request/key accompany each response so Rust checks
consistency against an independent producer rather than its own encoder.

Response integer extrema, invalid framing/JSON, resource limits and adversarial
substitutions are separate Rust tests. The common profile hash corpus lives at
`tests/fixtures/sdk-receiver-profile-v1.json` in the repository root and is
checked by both Python and Rust.
