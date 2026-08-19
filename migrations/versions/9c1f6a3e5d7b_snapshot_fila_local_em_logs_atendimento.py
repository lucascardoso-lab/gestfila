"""Snapshot de fila/local por evento em logs_atendimento (adendo 10.62 - segmentos
de tempo de atendimento para senhas realocadas mais de uma vez)

Revision ID: 9c1f6a3e5d7b
Revises: 7a2e1c4f9b3d
Create Date: 2026-08-19 00:00:00.000000

`logs_atendimento.fila_id`/`local_id` nascem NULLABLE de propósito e SEM
migração de dados: linhas já existentes ficam NULL pra sempre (decisão de
produto confirmada - dados históricos nunca ganham a granularidade nova),
só linhas gravadas a partir deste deploy passam a preencher os dois campos
(ver app/atendimento/helpers.py, app/totem/routes.py,
app/middleware/encerramento.py). `app/relatorios/queries.py` usa a presença
desses valores para decidir, por senha, se reconstrói todos os segmentos
intermediários de uma realocação em cadeia ou cai no comportamento antigo
(só o primeiro segmento, sem fila/local).
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '9c1f6a3e5d7b'
down_revision = '7a2e1c4f9b3d'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('logs_atendimento', schema=None) as batch_op:
        batch_op.add_column(sa.Column('fila_id', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('local_id', sa.Integer(), nullable=True))
        batch_op.create_foreign_key('fk_logs_atendimento_fila_id', 'filas', ['fila_id'], ['id'])
        batch_op.create_foreign_key('fk_logs_atendimento_local_id', 'locais', ['local_id'], ['id'])


def downgrade():
    with op.batch_alter_table('logs_atendimento', schema=None) as batch_op:
        batch_op.drop_constraint('fk_logs_atendimento_local_id', type_='foreignkey')
        batch_op.drop_constraint('fk_logs_atendimento_fila_id', type_='foreignkey')
        batch_op.drop_column('local_id')
        batch_op.drop_column('fila_id')
