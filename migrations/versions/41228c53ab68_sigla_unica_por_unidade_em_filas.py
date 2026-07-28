"""Sigla unica por unidade em filas (secao 3/8 do BLUEPRINT - constraint que so
existia na tabela legada servicos ficou faltando apos a fusao da secao 10.1)

Revision ID: 41228c53ab68
Revises: f2b93036a584
Create Date: 2026-07-01 00:00:00.000000

"""
from alembic import op


# revision identifiers, used by Alembic.
revision = '41228c53ab68'
down_revision = 'f2b93036a584'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('filas', schema=None) as batch_op:
        batch_op.create_unique_constraint('uq_fila_unidade_sigla', ['unidade_id', 'sigla'])


def downgrade():
    with op.batch_alter_table('filas', schema=None) as batch_op:
        batch_op.drop_constraint('uq_fila_unidade_sigla', type_='unique')
