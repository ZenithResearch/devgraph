from __future__ import annotations

from pathlib import Path
from threading import Barrier, Lock, Thread

from test_migrations import MemoryMigrationStore

from devgraph.ops.migrate import apply_migrations, load_manifest

ROOT = Path(__file__).parents[2]


class ConcurrentStore(MemoryMigrationStore):
    def __init__(self) -> None:
        super().__init__()
        self.guard = Lock()
        self.barrier = Barrier(2)
        self.acquired_barrier = Barrier(2)

    def acquire_owner(self, attempt_id: str) -> bool:
        self.barrier.wait()
        with self.guard:
            acquired = super().acquire_owner(attempt_id)
        self.acquired_barrier.wait()
        return acquired


def test_two_contenders_have_one_winner_and_loser_has_zero_mutation() -> None:
    store = ConcurrentStore()
    manifest = load_manifest(ROOT / "migrations/manifest.json")
    results = []

    def run(attempt: str) -> None:
        results.append(apply_migrations(manifest, store, attempt_id=attempt))

    threads = [Thread(target=run, args=(name,)) for name in ("attempt-a", "attempt-b")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(result.reason for result in results) == ["clean", "migration_lock_busy"]
    assert len(store.ddl_calls) == 25


def test_state_is_reread_after_acquisition_before_any_new_mutation() -> None:
    manifest = load_manifest(ROOT / "migrations/manifest.json")

    class CompletedBetweenInspectionAndAcquire(MemoryMigrationStore):
        injected = False

        def acquire_owner(self, attempt_id: str) -> bool:
            if not self.injected:
                self.injected = True
                other = MemoryMigrationStore()
                assert apply_migrations(manifest, other, attempt_id="other").ready is True
                self.bootstrap = other.bootstrap
                self.journal = dict(other.journal)
                self.objects = dict(other.objects)
            return super().acquire_owner(attempt_id)

    store = CompletedBetweenInspectionAndAcquire()
    result = apply_migrations(manifest, store, attempt_id="late-contender")
    assert result.ready is True
    assert store.ddl_calls == []
    assert store.owner is None
