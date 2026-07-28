"""Turno ativo - configuracao de turno do operador (secao 10.19)

Revision ID: d53cf46b3068
Revises: c3d9f4a1e6b2
Create Date: 2026-07-24 00:00:00.000000

Nova tabela `turnos_ativos`: filas (com peso de rodizio) e Local escolhidos pelo
proprio operador na tela "Iniciar Turno", antes de acessar a tela de atendimento.
Uma linha por usuario (unique em usuario_id) - reiniciar o turno atualiza a mesma
linha, sem acumular historico. `filas_config` guarda a lista de filas escolhidas
como JSON (mesmo tipo de coluna ja usado em `filas.horario_funcionamento`).
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'd53cf46b3068'
down_revision = 'c3d9f4a1e6b2'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'turnos_ativos',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('usuario_id', sa.Integer(), nullable=False),
        sa.Column('local_id', sa.Integer(), nullable=False),
        sa.Column('filas_config', sa.JSON(), nullable=False),
        sa.Column('iniciado_em', sa.DateTime(), nullable=False),
        sa.Column('ativo', sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(['usuario_id'], ['usuarios.id']),
        sa.ForeignKeyConstraint(['local_id'], ['locais.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('usuario_id', name='uq_turnos_ativos_usuario_id'),
    )


def downgrade():
    op.drop_table('turnos_ativos')
