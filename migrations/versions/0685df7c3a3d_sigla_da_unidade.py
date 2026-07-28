"""Sigla da unidade (UX de contexto multi-unidade)

Revision ID: 0685df7c3a3d
Revises: f4900d785aee
Create Date: 2026-07-29 00:00:00.000000

Adiciona `unidades.sigla` (VARCHAR(4), opcional) - usada como prefixo
"[SIGLA] Nome do item" nos selects/listas que misturam itens de unidades
diferentes (locais, filas, botões do totem etc.), pra reduzir a confusão
visual do super admin. Não obrigatório: sem sigla cadastrada, o sistema usa
os 3 primeiros caracteres do nome da unidade como fallback (ver
`Unidade.sigla_exibicao`, só em Python - nenhuma lógica de fallback no banco).
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '0685df7c3a3d'
down_revision = 'f4900d785aee'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('unidades', schema=None) as batch_op:
        batch_op.add_column(sa.Column('sigla', sa.String(length=4), nullable=True))


def downgrade():
    with op.batch_alter_table('unidades', schema=None) as batch_op:
        batch_op.drop_column('sigla')
