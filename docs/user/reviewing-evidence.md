# Reviewing evidence

Treat evidence in descending order of authority:

1. source and tests at the named commit;
2. deterministic generated-artifact checks;
3. repository verification and CI at the same commit;
4. live probes of the exact runtime being discussed;
5. issue and planning history.

Keep these claims separate: a class exists; a test proves it in memory; an
adapter supports Neo4j; a specific Neo4j instance passed a live probe; a
service is deployed. None implies the next.

For this documentation baseline, the private local host was not treated as
repository evidence. The branch's memory-backed monitor is explicitly a
development fixture.
