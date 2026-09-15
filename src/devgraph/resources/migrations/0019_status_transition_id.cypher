CREATE CONSTRAINT status_transition_id IF NOT EXISTS FOR (n:StatusTransition) REQUIRE n.id IS UNIQUE;
