"""borrado de cuenta: autores opcionales en pacientes y consentimientos

Al borrar una cuenta (`DELETE /users/me`) los pacientes gestionados que siguen teniendo otros
cuidadores se conservan, y el historial de consentimientos se conserva como prueba. Por eso
`patients.created_by_user_id` y `consent_events.actor_user_id` pasan a ser opcionales y sus
claves foráneas a `ON DELETE SET NULL` (antes `CASCADE` arrastraba pacientes y consentimientos).

Downgrade: para volver a `NOT NULL` hay que eliminar las filas sin autor. En `patients` se intenta
primero reasignar el autor al cuidador vinculado más antiguo (y solo se borran, con todo su
contenido, los pacientes que no tienen ningún vínculo); en `consent_events` se borran las filas
sin actor, es decir, se pierde esa parte del historial.

Revision ID: 0013
Revises: 0012
Create Date: 2026-11-03 09:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013"
down_revision: str | Sequence[str] | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_FKS = (
    ("patients", "created_by_user_id", "fk_patients_created_by_user_id_users"),
    ("consent_events", "actor_user_id", "fk_consent_events_actor_user_id_users"),
)


def _recreate_fk(table: str, column: str, name: str, ondelete: str) -> None:
    op.drop_constraint(op.f(name), table, type_="foreignkey")
    op.create_foreign_key(op.f(name), table, "users", [column], ["id"], ondelete=ondelete)


def upgrade() -> None:
    for table, column, name in _FKS:
        _recreate_fk(table, column, name, "SET NULL")
        op.alter_column(table, column, existing_type=sa.Uuid(), nullable=True)


def downgrade() -> None:
    op.execute(
        """
        UPDATE patients p
        SET created_by_user_id = (
            SELECT c.caregiver_user_id FROM care_links c
            WHERE c.patient_id = p.id
            ORDER BY c.created_at, c.id
            LIMIT 1
        )
        WHERE p.created_by_user_id IS NULL
        """
    )
    op.execute("DELETE FROM patients WHERE created_by_user_id IS NULL")
    op.execute("DELETE FROM consent_events WHERE actor_user_id IS NULL")
    for table, column, name in _FKS:
        op.alter_column(table, column, existing_type=sa.Uuid(), nullable=False)
        _recreate_fk(table, column, name, "CASCADE")
