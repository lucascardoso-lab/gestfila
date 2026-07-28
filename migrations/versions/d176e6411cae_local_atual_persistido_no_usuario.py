"""Local atual persistido no usuario (diagnostico item 1 - visibilidade de quem
esta em cada local agora)

Antes, o Local escolhido pelo operador vivia so na sessao Flask (cookie
client-side, sem backend de sessao server-side) - nenhuma query no servidor
conseguia enxergar quem estava em qual local. Adiciona local_atual_id (FK nullable
para locais) e local_desde (DATETIME nullable) em usuarios, gravados em
escolher_local() e limpos no logout.

Ambas as colunas sao nullable - nenhum backfill necessario (usuario existente
simplesmente comeca com local_atual_id/local_desde NULL, equivalente a "nunca
escolheu local ainda", que ja era o estado implicito antes desta mudanca).

Revision ID: d176e6411cae
Revises: 6dbceed82d3a
Create Date: 2026-07-03 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'd176e6411cae'
down_revision = '6dbceed82d3a'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('usuarios', schema=None) as batch_op:
        batch_op.add_column(sa.Column('local_atual_id', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('local_desde', sa.DateTime(), nullable=True))
        batch_op.create_foreign_key('fk_usuario_local_atual', 'locais', ['local_atual_id'], ['id'])


def downgrade():
    with op.batch_alter_table('usuarios', schema=None) as batch_op:
        batch_op.drop_constraint('fk_usuario_local_atual', type_='foreignkey')
        batch_op.drop_column('local_desde')
        batch_op.drop_column('local_atual_id')
