# Zenith normalized ontology · 0.1.0

This greenfield release packages the accepted Zenith portion of working proposal 35. It starts a new release line under the normalized ontology IRI and preserves the published zn: term namespace. The root ontology imports the versioned Core, Work, Directory, Request Management, Vocabulary, and Messaging modules. Evidence is a schema package.

The release contains ontology definitions and review-aligned schemas. Devgraph admission, provider deployment and historical data migration are separate deliverables. CasePlan content adequacy and application delivery state remain application concerns. The previously released monolithic vocabulary is separate provenance. The compatibility alias zn:Actor remains equivalent to zn:Entity.

Use the local archive or the Git tag v0.1.0. The HTTPS IRIs identify the bundle documents. The release verifier resolves every import offline.

    python3 -m venv .venv
    .venv/bin/pip install -r requirements.txt
    .venv/bin/python tools/verify_release.py

release.json records the version and reviewed class paths. manifest.json records artifact hashes and the bundle digest. domains.json records subject domains. profiles.json records schema conformance and contextual membership. The examples are synthetic.

Upstream comparison source: Zenith Core 0.13.0, canonical source commit 142f515675d4127736676c9e7a8b1bb158621eae. This release is a separate repository and release line.
