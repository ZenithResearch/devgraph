CREATE CONSTRAINT work_mutation_guard_id IF NOT EXISTS FOR (n:WorkMutationGuard) REQUIRE n.id IS UNIQUE;
