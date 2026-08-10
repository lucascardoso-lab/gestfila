"""adiciona logo_filename a empresa

Revision ID: 6f6d09c50b0d
Revises: f01b63e1bf65
Create Date: 2026-08-10 20:30:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '6f6d09c50b0d'
down_revision = 'f01b63e1bf65'
branch_labels = None
depends_on = None


def upgrade():
    # nullable, sem server_default - empresas existentes ficam sem logo
    # (fallback pra nome em texto, sem quebrar nada). Guarda só o nome do
    # arquivo (ex.: 'empresa_7.png'), não o caminho completo nem o blob -
    # o arquivo físico fica em app/static/uploads/logos/.
    with op.batch_alter_table('empresas', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column('logo_filename', sa.String(length=60), nullable=True)
        )


def downgrade():
    with op.batch_alter_table('empresas', schema=None) as batch_op:
        batch_op.drop_column('logo_filename')
