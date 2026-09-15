from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ONTOLOGY = ROOT / "ontology"


def test_readiness_assessment_shape_documents_required_metrics():
    text = (ONTOLOGY / "readiness.md").read_text()
    for term in [
        "ReadinessAssessment",
        "objective_clarity_score",
        "isolation_boundary_score",
        "ease_to_understand_score",
        "rigour_score",
        "resource_adequacy_score",
        "dependency_certainty_score",
        "verification_executability_score",
        "pr_size_safety_score",
        "security_control_score",
        "overall_readiness_score",
    ]:
        assert f"`{term}`" in text


def test_readiness_assessment_documents_score_range_and_recommendations():
    text = (ONTOLOGY / "readiness.md").read_text()
    assert "1..10" in text
    for rec in ["ready", "harden", "split", "block"]:
        assert f"`{rec}`" in text


def test_readiness_edges_are_documented_in_readiness_and_predicates_docs():
    readiness = (ONTOLOGY / "readiness.md").read_text()
    predicates = (ONTOLOGY / "predicates.md").read_text()
    for rel in ["HAS_READINESS_ASSESSMENT", "IDENTIFIED_GAP", "USES_RUBRIC", "SUPPORTED_BY"]:
        assert f"`{rel}`" in readiness
        assert f"`{rel}`" in predicates


def test_readiness_invariant_keeps_assessment_as_evidence_not_replacement():
    text = (ONTOLOGY / "readiness.md").read_text()
    assert (
        "does not replace Issue, Task, Requirement, AcceptanceCriterion, Blocker, or Decision"
        in text
    )
    assert "docs/schema-only" in text
