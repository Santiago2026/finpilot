"""Enforce append-only audit_events at the database boundary."""
from alembic import op

revision = "0002_audit_triggers"
down_revision = "0001_initial"
branch_labels = None
depends_on = None

SQLITE_TRIGGERS = (
    """CREATE TRIGGER audit_events_no_update BEFORE UPDATE ON audit_events
       BEGIN SELECT RAISE(ABORT, 'audit_events es append-only'); END""",
    """CREATE TRIGGER audit_events_no_delete BEFORE DELETE ON audit_events
       BEGIN SELECT RAISE(ABORT, 'audit_events es append-only'); END""",
)

POSTGRES_FUNCTION = """CREATE OR REPLACE FUNCTION prevent_audit_modification()
RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'audit_events es append-only';
END;
$$ LANGUAGE plpgsql"""

def upgrade():
    dialect = op.get_bind().dialect.name
    if dialect == "sqlite":
        for statement in SQLITE_TRIGGERS:
            op.execute(statement)
    elif dialect == "postgresql":
        op.execute(POSTGRES_FUNCTION)
        op.execute("""CREATE TRIGGER audit_events_no_update BEFORE UPDATE ON audit_events
                     FOR EACH ROW EXECUTE FUNCTION prevent_audit_modification()""")
        op.execute("""CREATE TRIGGER audit_events_no_delete BEFORE DELETE ON audit_events
                     FOR EACH ROW EXECUTE FUNCTION prevent_audit_modification()""")
    else:
        raise RuntimeError(f"Dialect no soportado para audit append-only: {dialect}")

def downgrade():
    dialect = op.get_bind().dialect.name
    op.execute("DROP TRIGGER IF EXISTS audit_events_no_update")
    op.execute("DROP TRIGGER IF EXISTS audit_events_no_delete")
    if dialect == "postgresql":
        op.execute("DROP FUNCTION IF EXISTS prevent_audit_modification()")
