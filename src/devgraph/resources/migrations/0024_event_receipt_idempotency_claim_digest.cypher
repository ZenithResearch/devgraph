CREATE CONSTRAINT event_receipt_idempotency_claim_digest IF NOT EXISTS FOR (n:EventReceipt) REQUIRE n.idempotency_claim_digest IS UNIQUE;
