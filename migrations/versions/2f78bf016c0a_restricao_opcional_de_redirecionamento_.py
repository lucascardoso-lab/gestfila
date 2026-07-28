"""Restricao opcional de redirecionamento por Local (diagnostico item 2)

Nova tabela associativa local_filas_destino (N:N entre locais e filas), sem
unidade_id propria - Local e Fila ja pertencem a uma unidade cada, a validacao de
que ambas batem fica na camada de aplicacao (admin/locais.py), nunca no schema.

Lista vazia (nenhuma linha para aquele local_id) continua significando "sem
restricao" - mantem o comportamento historico (todas as filas ativas da unidade)
em atendimento/helpers.py::filas_destino(). Nao ha dado a migrar: tabela nova, sem
linhas por padrao, comportamento de quem ja usa o sistema hoje nao muda em nada.

Revision ID: 2f78bf016c0a
Revises: d176e6411cae
Create Date: 2026-07-04 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '2f78bf016c0a'
down_revision = 'd176e6411cae'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'local_filas_destino',
        sa.Column('local_id', sa.Integer(), nullable=False),
        sa.Column('fila_id', sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(['local_id'], ['locais.id']),
        sa.ForeignKeyConstraint(['fila_id'], ['filas.id']),
        sa.PrimaryKeyConstraint('local_id', 'fila_id'),
    )


def downgrade():
    op.drop_table('local_filas_destino')
