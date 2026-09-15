CREATE CONSTRAINT event_receipt_id IF NOT EXISTS FOR (n:EventReceipt) REQUIRE n.id IS UNIQUE;
