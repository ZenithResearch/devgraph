# Third-party dependencies

The Python wheel and agent bundles contain Devgraph source; they do not vendor
the dependencies below. `uv sync --locked` installs the versions in `uv.lock`; ordinary wheel installation
resolves versions within the package dependency constraints.
Each dependency is distributed under its own license, whose authoritative text
ships with that distribution. This inventory was read from the locked Python
environment metadata; it includes development/test dependencies.

| Distribution | Locked version | Declared license |
| --- | --- | --- |
| annotated-doc | 0.0.4 | MIT |
| annotated-types | 0.7.0 | MIT License |
| anyio | 4.14.1 | MIT |
| certifi | 2026.6.17 | Mozilla Public License 2.0 (MPL 2.0) |
| cffi | 2.1.1 | MIT-0 |
| click | 8.4.2 | BSD-3-Clause |
| cryptography | 50.0.1 | Apache-2.0 OR BSD-3-Clause |
| exceptiongroup | 1.3.1 | MIT License |
| fastapi | 0.136.3 | MIT |
| h11 | 0.16.0 | MIT License |
| httpcore | 1.0.9 | BSD-3-Clause |
| httpx | 0.28.1 | BSD License |
| idna | 3.18 | BSD-3-Clause |
| iniconfig | 2.3.0 | MIT |
| neo4j | 6.2.0 | Apache-2.0 AND Python-2.0 |
| packaging | 26.2 | Apache-2.0 OR BSD-2-Clause |
| pluggy | 1.6.0 | MIT License |
| pycparser | 3.0 | BSD-3-Clause |
| pydantic | 2.13.4 | MIT |
| pydantic_core | 2.46.4 | MIT |
| Pygments | 2.20.0 | BSD-2-Clause |
| pytest | 9.1.1 | MIT |
| pytz | 2026.2 | MIT License |
| ruff | 0.15.21 | MIT |
| starlette | 1.3.1 | BSD-3-Clause |
| tomli | 2.4.1 | MIT |
| typing-inspection | 0.4.2 | MIT |
| typing_extensions | 4.16.0 | PSF-2.0 |
| uvicorn | 0.39.0 | BSD-3-Clause |

Neo4j Community and Java are separately installed runtimes and are not bundled
with the Devgraph release. Native Wallet/secS prerequisites have independent
source/license manifests in the native bundle; their terms do not follow from
Devgraph’s license. Neither agent package includes Hermes or Codex itself.

The project-selection page uses the repository’s dependency-free JavaScript
modules. It does not require or redistribute the separate preview browser SDK.

## Rust request protocol

The source-only `devgraph-work-protocol` crate depends on `serde` (MIT OR Apache-2.0),
`serde_json` (MIT OR Apache-2.0), and `sha2` (MIT OR Apache-2.0). Cargo resolves their
transitive dependencies from the registry under the versions/checksums in
`Cargo.lock`; their respective notices remain authoritative. This repository does
not vendor those dependencies. The Devgraph crate itself follows the repository's
AGPL-3.0-only license and does not change the licenses of separate native companions.

## Zenith UI tokens

The frontend packages Zenith UI's `public/tokens.css` as `zenith-tokens.css`.
Its exact source revision and SHA-256 are recorded in `docs/dev/zenith-tokens.json`;
`scripts/sync_zenith_tokens.py` verifies the imported bytes. The import includes
token definitions only, with no Zenith React components or font files.
