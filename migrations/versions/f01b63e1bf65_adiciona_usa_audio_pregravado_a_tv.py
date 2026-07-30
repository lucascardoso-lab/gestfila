"""adiciona usa_audio_pregravado a tv

Revision ID: f01b63e1bf65
Revises: 5ba0cdce86cb
Create Date: 2026-07-30 18:01:30.805253

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'f01b63e1bf65'
down_revision = '5ba0cdce86cb'
branch_labels = None
depends_on = None


def upgrade():
    # server_default='0' para as linhas já existentes não violarem NOT NULL na
    # criação da coluna; o default lógico da aplicação (False) fica no model.
    with op.batch_alter_table('tvs', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column('usa_audio_pregravado', sa.Boolean(), nullable=False, server_default='0')
        )


def downgrade():
    with op.batch_alter_table('tvs', schema=None) as batch_op:
        batch_op.drop_column('usa_audio_pregravado')
