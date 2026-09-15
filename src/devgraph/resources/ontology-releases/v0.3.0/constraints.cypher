// GENERATED; NOT EXECUTED. Byte mirror of migrations/manifest.json payloads.
// Hand editing is forbidden; regenerate from ordered migration payloads.

CREATE CONSTRAINT todo_id IF NOT EXISTS FOR (n:Todo) REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT proposal_id IF NOT EXISTS FOR (n:Proposal) REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT initiative_id IF NOT EXISTS FOR (n:Initiative) REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT project_id IF NOT EXISTS FOR (n:Project) REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT issue_id IF NOT EXISTS FOR (n:Issue) REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT task_id IF NOT EXISTS FOR (n:Task) REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT requirement_id IF NOT EXISTS FOR (n:Requirement) REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT acceptance_criterion_id IF NOT EXISTS FOR (n:AcceptanceCriterion) REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT blocker_id IF NOT EXISTS FOR (n:Blocker) REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT decision_id IF NOT EXISTS FOR (n:Decision) REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT handoff_id IF NOT EXISTS FOR (n:Handoff) REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT review_packet_id IF NOT EXISTS FOR (n:ReviewPacket) REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT milestone_id IF NOT EXISTS FOR (n:Milestone) REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT artifact_id IF NOT EXISTS FOR (n:Artifact) REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT external_link_id IF NOT EXISTS FOR (n:ExternalLink) REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT sync_shadow_id IF NOT EXISTS FOR (n:SyncShadow) REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT status_set_id IF NOT EXISTS FOR (n:StatusSet) REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT status_value_id IF NOT EXISTS FOR (n:StatusValue) REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT status_transition_id IF NOT EXISTS FOR (n:StatusTransition) REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT event_receipt_id IF NOT EXISTS FOR (n:EventReceipt) REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT hermes_session_ref_id IF NOT EXISTS FOR (n:HermesSessionRef) REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT readiness_assessment_id IF NOT EXISTS FOR (n:ReadinessAssessment) REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT event_receipt_idempotency_claim_digest IF NOT EXISTS FOR (n:EventReceipt) REQUIRE n.idempotency_claim_digest IS UNIQUE;
