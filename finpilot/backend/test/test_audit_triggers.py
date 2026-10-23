import sqlite3
from pathlib import Path
import importlib.util
import pytest

MIGRATION_PATH = Path(__file__).parents[1] / "migrations/versions/0002_audit_triggers.py"
spec = importlib.util.spec_from_file_location("audit_triggers_migration", MIGRATION_PATH)
migration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(migration)

@pytest.mark.parametrize("statement", [
    "UPDATE audit_events SET event_type='changed' WHERE id=1",
    "DELETE FROM audit_events WHERE id=1",
])
def test_sqlite_audit_triggers_reject_direct_mutations(statement):
    connection = sqlite3.connect(":memory:")
    connection.execute("CREATE TABLE audit_events (id INTEGER PRIMARY KEY, event_type TEXT NOT NULL)")
    connection.execute("INSERT INTO audit_events (event_type) VALUES ('CREATED')")
    for trigger in migration.SQLITE_TRIGGERS:
        connection.execute(trigger)
    with pytest.raises(sqlite3.IntegrityError, match="audit_events es append-only"):
        connection.execute(statement)
    assert connection.execute("SELECT event_type FROM audit_events WHERE id=1").fetchone() == ("CREATED",)
