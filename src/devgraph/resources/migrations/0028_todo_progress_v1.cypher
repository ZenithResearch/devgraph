// Forward-only audited mapping implemented transactionally by the migration adapter.
// Backlog -> not_started, validated active stages -> in_progress, guarded Done -> done.
// Legacy Draft/Accepted alone remain absent. Archive flag, stage, evidence and history survive.
RETURN 'todo_progress_v1' AS migration;
