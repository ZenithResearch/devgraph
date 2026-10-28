CREATE CONSTRAINT session_work_state_id IF NOT EXISTS FOR (n:SessionWorkState) REQUIRE n.id IS UNIQUE;
