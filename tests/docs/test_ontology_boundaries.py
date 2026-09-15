from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ONTOLOGY = ROOT / "ontology"


def read(name: str) -> str:
    return (ONTOLOGY / name).read_text()


def test_v0_classes_document_keep_defer_and_excluded_sections():
    text = read("classes.md")
    assert "## Keep: v0 first-class labels" in text
    assert "## Watch/defer: not v0 first-class labels" in text
    assert "## Excluded from v0" in text
    for label in [
        "Todo",
        "Proposal",
        "Initiative",
        "Project",
        "Issue",
        "Task",
        "Requirement",
        "AcceptanceCriterion",
        "Blocker",
        "Decision",
        "Handoff",
        "ReviewPacket",
        "Milestone",
        "Artifact",
        "ExternalLink",
        "SyncShadow",
        "StatusSet",
        "StatusValue",
        "StatusTransition",
        "EventReceipt",
        "HermesSessionRef",
        "ReadinessAssessment",
    ]:
        assert f"`{label}`" in text


def test_deferred_and_excluded_terms_are_explicitly_classified():
    text = read("classes.md")
    for label in [
        "Subject",
        "Membership",
        "VisibilityPolicy",
        "Phase",
        "Board",
        "BoardLane",
        "SavedQuery",
        "WorkRequestRef",
        "CaseRef",
    ]:
        assert f"`{label}`" in text
    for label in [
        "Case",
        "Subcase",
        "WorkRequest",
        "WorkOrder",
        "Repository",
        "Release",
        "Deployment",
        "Commit",
        "Deliverable",
        "LinearSync",
        "PublicPortal",
    ]:
        assert f"`{label}`" in text


def test_required_predicates_are_documented():
    text = read("predicates.md")
    for rel in [
        "BLOCKS",
        "DEPENDS_ON",
        "HAS_REQUIREMENT",
        "HAS_ACCEPTANCE_CRITERION",
        "ACCEPTED_BY_DECISION",
        "HAS_ARTIFACT",
        "HAS_EXTERNAL_LINK",
        "EMITTED_EVENT",
        "ATTRIBUTED_TO_HERMES_SESSION",
        "HAS_READINESS_ASSESSMENT",
    ]:
        assert f"`{rel}`" in text


def test_taxonomy_documents_required_categories():
    text = read("taxonomy.md")
    for term in [
        "draft",
        "review",
        "accepted",
        "archived",
        "proposal_document",
        "github_issue_url",
        "work_created",
        "objective_clarity",
        "token_input_count",
    ]:
        assert f"`{term}`" in text


def test_invariants_capture_proposal_archive_and_forbidden_runtime_classes():
    text = read("invariants.md")
    assert "accepted Proposal requires Decision provenance" in text
    assert "non-destructively archived" in text
    assert "Direct delete is not part of the v0 service contract" in text
    for forbidden in ["WorkRequest", "Case", "Subcase"]:
        assert f"`{forbidden}`" in text


def test_constraints_are_idempotent_for_keep_labels_only():
    text = (ONTOLOGY / "neo4j" / "constraints.cypher").read_text()
    assert "IF NOT EXISTS" in text
    for label in ["Todo", "Proposal", "Issue", "HermesSessionRef", "ReadinessAssessment"]:
        assert f":{label})" in text
    for forbidden in [
        "WorkRequest",
        "Case",
        "Subcase",
        "Repository",
        "Release",
        "Commit",
        "Deliverable",
    ]:
        assert f":{forbidden})" not in text


def test_canonical_zenith_repository_is_not_a_runtime_label():
    classes = read("classes.md")
    invariants = read("invariants.md")
    constraints = (ONTOLOGY / "neo4j" / "constraints.cypher").read_text()

    assert "`ZenithRepository`" in classes
    assert "`runtimeLabel: false`" in classes
    assert "Devgraph is the canonical ontology authority" in invariants
    assert ":ZenithRepository)" not in constraints


def test_rolodex_vocabulary_is_canonical_but_not_runtime_bound():
    classes = read("classes.md")
    predicates = read("predicates.md")
    rolodex = read("rolodex.md")
    invariants = read("invariants.md")
    constraints = (ONTOLOGY / "neo4j" / "constraints.cypher").read_text()

    for label in ["Actor", "Entity", "Person", "Agent", "Organization"]:
        assert f"`{label}`" in classes
        assert f":{label})" not in constraints
    for relationship in [
        "MEMBER_OF",
        "OPERATED_BY",
        "ASSIGNED_TO",
        "OWNED_BY",
        "ATTRIBUTED_TO",
    ]:
        assert f"`{relationship}`" in predicates
    assert "not an authentication `Subject`" in classes
    assert "cannot mint authority" in invariants
    assert "All four Rolodex classes have `runtimeLabel: false`" in rolodex
    assert "private contact points" in rolodex
