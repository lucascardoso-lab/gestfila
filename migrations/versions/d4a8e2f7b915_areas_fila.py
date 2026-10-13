"""Áreas: agrupamento cadastrável de filas para os relatórios (adendo 10.76)

Revision ID: d4a8e2f7b915
Revises: c7e2a9d4f1b3
Create Date: 2026-10-13 00:00:00.000000

Só ADITIVA - nada em `senhas` nem em `logs_atendimento`:
- tabela nova `areas_fila` (empresa, unidade, nome, ativo; nome único por
  unidade);
- `filas.area_fila_id` (nullable - NULL = "Sem área");
- `logs_exportacao_relatorio.area_fila_id` (nullable - filtro de área na
  auditoria de exportação do 10.67).

FKs e índices com NOME explícito, e o índice criado ANTES da FK: o MySQL
apoia a FK nesse índice nomeado em vez de criar um implícito - o downgrade
derruba exatamente o que o upgrade criou, na ordem inversa (FK -> índice ->
coluna), sem o erro 1553 visto no 10.71.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'd4a8e2f7b915'
down_revision = 'c7e2a9d4f1b3'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'areas_fila',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('empresa_id', sa.Integer(), nullable=False),
        sa.Column('unidade_id', sa.Integer(), nullable=False),
        sa.Column('nome', sa.String(length=100), nullable=False),
        sa.Column('ativo', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.ForeignKeyConstraint(['empresa_id'], ['empresas.id'], name='fk_areas_fila_empresa'),
        sa.ForeignKeyConstraint(['unidade_id'], ['unidades.id'], name='fk_areas_fila_unidade'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('unidade_id', 'nome', name='uq_areas_fila_unidade_nome'),
    )

    with op.batch_alter_table('filas', schema=None) as batch_op:
        batch_op.add_column(sa.Column('area_fila_id', sa.Integer(), nullable=True))
        batch_op.create_index('ix_filas_area_fila_id', ['area_fila_id'])
        batch_op.create_foreign_key('fk_filas_area_fila', 'areas_fila', ['area_fila_id'], ['id'])

    with op.batch_alter_table('logs_exportacao_relatorio', schema=None) as batch_op:
        batch_op.add_column(sa.Column('area_fila_id', sa.Integer(), nullable=True))
        batch_op.create_index('ix_logs_exportacao_relatorio_area_fila_id', ['area_fila_id'])
        batch_op.create_foreign_key('fk_logs_exportacao_area_fila', 'areas_fila', ['area_fila_id'], ['id'])


def downgrade():
    with op.batch_alter_table('logs_exportacao_relatorio', schema=None) as batch_op:
        batch_op.drop_constraint('fk_logs_exportacao_area_fila', type_='foreignkey')
        batch_op.drop_index('ix_logs_exportacao_relatorio_area_fila_id')
        batch_op.drop_column('area_fila_id')

    with op.batch_alter_table('filas', schema=None) as batch_op:
        batch_op.drop_constraint('fk_filas_area_fila', type_='foreignkey')
        batch_op.drop_index('ix_filas_area_fila_id')
        batch_op.drop_column('area_fila_id')

    op.drop_table('areas_fila')
