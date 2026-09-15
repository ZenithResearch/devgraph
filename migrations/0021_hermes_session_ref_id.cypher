CREATE CONSTRAINT hermes_session_ref_id IF NOT EXISTS FOR (n:HermesSessionRef) REQUIRE n.id IS UNIQUE;
