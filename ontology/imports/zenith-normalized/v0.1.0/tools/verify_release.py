"""Build and verify an offline ontology release and its exact dependency closure."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from jsonschema import Draft202012Validator
from rdflib import Graph, URIRef
from rdflib.namespace import OWL, RDF, RDFS
from referencing import Registry, Resource

ROOT = Path(__file__).resolve().parents[1]
IGNORED = {".git", ".venv", "dist", "__pycache__", ".pytest_cache", ".DS_Store"}
NAMESPACES = {
    "zn": "https://zenith-research.ca/ontology/terms/",
    "sai": "https://saiengineering.ca/ontology/terms/",
}
TERM_TYPES = {OWL.Class, OWL.ObjectProperty, OWL.DatatypeProperty, OWL.AnnotationProperty, OWL.NamedIndividual}


def read(path):
    return json.loads(path.read_text())


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def ontology_paths(root):
    return [root / "ontology.jsonld", *sorted((root / "modules").glob("*.jsonld"))]


def ontology_header(path):
    data = read(path)
    if not isinstance(data["@context"], dict):
        raise ValueError(f"Expected inline JSON-LD context in {path}")
    header, = (node for node in data["@graph"] if node["@type"] == "owl:Ontology")
    return header


def manifest_for(root):
    metadata = read(root / "release.json")
    files = []
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if path.is_file() and not set(relative.parts) & IGNORED and relative.as_posix() != "manifest.json":
            files.append({"path": relative.as_posix(), "bytes": path.stat().st_size, "sha256": sha256(path)})
    records = "".join(f"{entry['sha256']}  {entry['path']}\n" for entry in files)
    documents = [
        {"path": path.relative_to(root).as_posix(), "ontologyId": header["@id"], "versionIri": header["versionIRI"]}
        for path in ontology_paths(root)
        for header in [ontology_header(path)]
    ]
    return {**metadata, "bundleDigest": hashlib.sha256(records.encode()).hexdigest(),
            "bundleDigestAlgorithm": "sha256 of path-sorted '<sha256>  <path>\\n' entries",
            "ontologyDocuments": documents, "files": files}


def verify_manifest(root):
    manifest = read(root / "manifest.json")
    if manifest != manifest_for(root):
        raise ValueError(f"Manifest or file integrity mismatch: {root}")
    return manifest


def ontologies(roots):
    documents = {}
    for root in roots:
        for path in ontology_paths(root):
            header = ontology_header(path)
            version = URIRef(header["versionIRI"])
            if version in documents:
                raise ValueError(f"Duplicate ontology version IRI: {version}")
            graph = Graph().parse(data=path.read_text(), format="json-ld")
            documents[version] = (URIRef(header["@id"]), graph, path)
    return documents


def import_closure(documents, entry):
    visiting, closure = set(), set()

    def visit(version):
        if version in visiting:
            raise ValueError(f"Cyclic ontology import: {version}")
        if version in closure:
            return
        if version not in documents:
            raise ValueError(f"Unresolved imported version: {version}")
        visiting.add(version)
        ontology, graph, _ = documents[version]
        if (ontology, RDF.type, OWL.Ontology) not in graph or (ontology, OWL.versionIRI, version) not in graph:
            raise ValueError(f"Missing ontology identity or version axiom: {version}")
        for dependency in graph.objects(ontology, OWL.imports):
            visit(dependency)
        visiting.remove(version)
        closure.add(version)

    visit(entry)
    return closure


def declared_terms(graph):
    return {term for term, kind in graph.subject_objects(RDF.type) if kind in TERM_TYPES}


def verify_ontologies(root, manifest, roots):
    documents = ontologies(roots)
    entry = URIRef(manifest["versionIri"])
    complete = import_closure(documents, entry)
    if complete != set(documents):
        raise ValueError(f"Unreachable ontology documents: {set(documents) - complete}")
    owner_namespace = NAMESPACES[manifest["publisher"]]
    owned, all_terms = set(), set()
    for version in complete:
        _, graph, path = documents[version]
        defined = declared_terms(graph)
        if path.is_relative_to(root):
            if any(not str(term).startswith(owner_namespace) and not str(term).startswith(f"urn:{manifest['publisher']}:domain:") for term in defined):
                raise ValueError(f"Foreign term declared in {path}")
            if owned & defined:
                raise ValueError(f"Duplicate module declarations in {path}: {owned & defined}")
            owned.update(defined)
        all_terms.update(defined)
    for version in complete:
        _, graph, path = documents[version]
        available = set().union(*(declared_terms(documents[v][1]) for v in import_closure(documents, version)))
        for triple in graph:
            for term in triple:
                if isinstance(term, URIRef) and str(term).startswith(tuple(NAMESPACES.values())) and term not in available:
                    raise ValueError(f"Term outside the direct or transitive imports of {path}: {term}")
    own_graph = Graph()
    for version in complete:
        _, graph, path = documents[version]
        if path.is_relative_to(root):
            own_graph += graph
    classes = set(own_graph.subjects(RDF.type, OWL.Class))
    expected = {URIRef(owner_namespace + term.split(":", 1)[1]) for term in manifest["classes"]}
    if classes != expected:
        raise ValueError(f"Unexpected class inventory: {classes ^ expected}")
    for name, parent in manifest["parents"].items():
        child = URIRef(owner_namespace + name.split(":", 1)[1])
        prefix, local = parent.split(":", 1)
        iri = URIRef(str(OWL) + local if prefix == "owl" else NAMESPACES[prefix] + local)
        if set(own_graph.objects(child, RDFS.subClassOf)) != {iri}:
            raise ValueError(f"Unexpected declared parent for {name}")
    return len(classes), all_terms, len(complete)


def values(item, key):
    if isinstance(item, dict):
        if key in item:
            yield item[key]
        for value in item.values():
            yield from values(value, key)
    elif isinstance(item, list):
        for value in item:
            yield from values(value, key)


def verify_schemas(root, roots, terms, publisher):
    docs = {}
    for location in roots:
        for path in sorted((location / "schemas").glob("*.json")):
            document = read(path)
            if document["$id"] in docs:
                raise ValueError(f"Duplicate schema ID in {path}")
            Draft202012Validator.check_schema(document)
            docs[document["$id"]] = path, document
    registry = Registry().with_resources((key, Resource.from_contents(document)) for key, (_, document) in docs.items())
    for key, (path, document) in docs.items():
        for ref in values(document, "$ref"):
            registry.resolver(key).lookup(ref)
        for scoped in values(document, "x-targetClass"):
            prefix, term = scoped.split(":", 1)
            if URIRef(NAMESPACES[prefix] + term) not in terms:
                raise ValueError(f"Unknown target class {scoped} in {path}")
    cases = read(root / "examples" / "schema-cases.json")
    for case in cases:
        schema, _, fragment = case["schema"].partition("#")
        if schema not in docs:
            raise ValueError(f"Unknown schema for fixture {case['name']}")
        validator = Draft202012Validator({"$ref": schema + ("#" + fragment if fragment else "")}, registry=registry,
                                       format_checker=Draft202012Validator.FORMAT_CHECKER)
        errors = list(validator.iter_errors(case["data"]))
        if bool(errors) == case.get("valid", True):
            raise ValueError(f"Unexpected fixture result {case['name']}: {[e.message for e in errors]}")
    domain_schema = next(key for key in docs if key.endswith("/domain.schema.json"))
    validator = Draft202012Validator({"$ref": domain_schema}, registry=registry,
                                   format_checker=Draft202012Validator.FORMAT_CHECKER)
    domains = read(root / "domains.json")
    for domain in domains:
        validator.validate(domain)
        if domain["publisher"] != publisher:
            raise ValueError(f"Unexpected domain publisher: {domain['publisher']}")
    for field in ("id", "key", "vocabularyRef"):
        assigned = [domain[field] for domain in domains if field in domain]
        if len(assigned) != len(set(assigned)):
            raise ValueError(f"Duplicate domain field: {field}")
    return len(docs), len(cases)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--upstream", type=Path, help="Unpacked Zenith release or exact Zenith checkout")
    parser.add_argument("--write-manifest", action="store_true")
    args = parser.parse_args()
    if args.write_manifest:
        (ROOT / "manifest.json").write_text(json.dumps(manifest_for(ROOT), indent=2, ensure_ascii=False) + "\n")
    manifest = verify_manifest(ROOT)
    roots = [ROOT]
    if "extends" in manifest:
        if args.upstream is None:
            parser.error("--upstream is required for this extension")
        upstream = args.upstream.resolve()
        dependency = verify_manifest(upstream)
        for key in ("ontologyId", "version", "versionIri", "bundleDigest"):
            if dependency[key] != manifest["extends"][key]:
                raise ValueError(f"Pinned Zenith dependency differs in {key}")
        if URIRef(dependency["versionIri"]) not in ontologies([upstream]):
            raise ValueError("Missing pinned Zenith entrypoint")
        roots.append(upstream)
    classes, terms, ontologies_count = verify_ontologies(ROOT, manifest, roots)
    schemas, cases = verify_schemas(ROOT, roots, terms, manifest["publisher"])
    print(json.dumps({"version": manifest["version"], "versionIri": manifest["versionIri"],
                      "bundleDigest": manifest["bundleDigest"], "ownedClasses": classes,
                      "ontologyDocumentsInClosure": ontologies_count, "schemasInClosure": schemas,
                      "schemaFixtures": cases}, indent=2))


if __name__ == "__main__":
    main()
