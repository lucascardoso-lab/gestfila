"""Log de auditoria de exportação de relatórios (adendo 10.67 - exportação em
PDF, complementar ao CSV/Excel existente)

Revision ID: b3d6f8a1c4e7
Revises: 9c1f6a3e5d7b
Create Date: 2026-08-24 00:00:00.000000

Nova tabela `logs_exportacao_relatorio` - motivada pela mesma lente de LGPD já
usada no restante do projeto (README, seção "Dado sensível"): registrar quem
exportou o quê (usuário, filtros aplicados, formato) é parte de conseguir
responder "quem teve acesso a este dado" se algum dia for preciso. Não é
redundante com `logs_atendimento` (audita o ATENDIMENTO em si, não o
ACESSO/exportação de dados por um usuário do admin). Passa a valer para os 3
formatos (CSV/Excel/PDF) - nenhum dos dois primeiros tinha esse registro antes.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'b3d6f8a1c4e7'
down_revision = '9c1f6a3e5d7b'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'logs_exportacao_relatorio',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('usuario_id', sa.Integer(), nullable=False),
        sa.Column('empresa_id', sa.Integer(), nullable=False),
        sa.Column('tipo_relatorio', sa.String(length=50), nullable=False),
        sa.Column('formato', sa.Enum('csv', 'xlsx', 'pdf', name='formato_exportacao'), nullable=False),
        sa.Column('unidade_id', sa.Integer(), nullable=True),
        sa.Column('fila_id', sa.Integer(), nullable=True),
        sa.Column('operador_id', sa.Integer(), nullable=True),
        sa.Column('data_inicio', sa.String(length=10), nullable=False),
        sa.Column('data_fim', sa.String(length=10), nullable=False),
        sa.Column('timestamp', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['usuario_id'], ['usuarios.id']),
        sa.ForeignKeyConstraint(['empresa_id'], ['empresas.id']),
        sa.ForeignKeyConstraint(['unidade_id'], ['unidades.id']),
        sa.ForeignKeyConstraint(['fila_id'], ['filas.id']),
        sa.ForeignKeyConstraint(['operador_id'], ['usuarios.id']),
        sa.PrimaryKeyConstraint('id'),
    )


def downgrade():
    op.drop_table('logs_exportacao_relatorio')
