CREATE CONSTRAINT acceptance_criterion_id IF NOT EXISTS FOR (n:AcceptanceCriterion) REQUIRE n.id IS UNIQUE;
