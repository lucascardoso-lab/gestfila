"""Rodizio de chamadas por Local (adendo 10.8, Fase 10.8-A - so o schema)

Nova tabela local_rodizio_filas: association object (classe mapeada de verdade,
LocalRodizioFila), nao tabela associativa simples - carrega `ordem` e
`chamadas_seguidas` por par Local x Fila, alem das duas FKs. Mesma unidade
obrigatoria entre local_id e fila_id - validado na camada de aplicacao (Fase
10.8-B), nunca no schema.

Tabela nasce vazia: zero impacto em qualquer Local/fila ja cadastrado. Um Local
sem nenhuma linha aqui continua no fluxo por fila_id existente, sem nenhuma
mudanca de comportamento (ver BLUEPRINT.md, adendo 10.8, secao 5).

Revision ID: b267038dee93
Revises: 2f78bf016c0a
Create Date: 2026-07-05 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'b267038dee93'
down_revision = '2f78bf016c0a'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'local_rodizio_filas',
        sa.Column('local_id', sa.Integer(), nullable=False),
        sa.Column('fila_id', sa.Integer(), nullable=False),
        sa.Column('ordem', sa.SmallInteger(), nullable=False),
        sa.Column('chamadas_seguidas', sa.SmallInteger(), nullable=False),
        sa.ForeignKeyConstraint(['local_id'], ['locais.id']),
        sa.ForeignKeyConstraint(['fila_id'], ['filas.id']),
        sa.PrimaryKeyConstraint('local_id', 'fila_id'),
    )


def downgrade():
    op.drop_table('local_rodizio_filas')
