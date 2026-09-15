CREATE CONSTRAINT external_link_id IF NOT EXISTS FOR (n:ExternalLink) REQUIRE n.id IS UNIQUE;
