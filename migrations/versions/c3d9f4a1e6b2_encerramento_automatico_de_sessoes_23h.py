"""Encerramento automatico de sessoes (secao 10.18): novo evento de log

Revision ID: c3d9f4a1e6b2
Revises: a1c4e8f2b7d3
Create Date: 2026-07-26 00:00:00.000000

Amplia o ENUM `logs_atendimento.evento` com 'encerramento_automatico_sistema' -
usado quando o sistema cancela automaticamente uma senha 'chamada'/'em_atendimento'
que ainda estava aberta no horario de encerramento configurado (HORARIO_ENCERRAMENTO_
SESSOES, default 23:00) - a clinica fecha antes desse horario, entao nunca ha
atendimento genuino em curso nesse ponto. Mesmo padrao ja usado na migration
235bf852e44c para 'realocado'/'voltou_aguardar' - o autogenerate do Alembic nao
detecta mudancas de ENUM em MySQL, por isso esse ALTER foi escrito a mao.

Nao muda `senhas.status` (schema ja previa 'cancelada' desde a secao 4 original,
so nunca tinha sido usado ate agora).
"""
from alembic import op
from sqlalchemy.dialects import mysql


# revision identifiers, used by Alembic.
revision = 'c3d9f4a1e6b2'
down_revision = 'a1c4e8f2b7d3'
branch_labels = None
depends_on = None

EVENTO_LOG_ANTIGO = (
    "gerada", "chamada_senha", "chamada_nome", "inicio_atendimento", "fim_atendimento",
    "realocado", "voltou_aguardar",
)
EVENTO_LOG_NOVO = EVENTO_LOG_ANTIGO + ("encerramento_automatico_sistema",)


def upgrade():
    with op.batch_alter_table('logs_atendimento', schema=None) as batch_op:
        batch_op.alter_column(
            'evento',
            existing_type=mysql.ENUM(*EVENTO_LOG_ANTIGO, name="evento_log"),
            type_=mysql.ENUM(*EVENTO_LOG_NOVO, name="evento_log"),
            existing_nullable=False,
        )


def downgrade():
    with op.batch_alter_table('logs_atendimento', schema=None) as batch_op:
        batch_op.alter_column(
            'evento',
            existing_type=mysql.ENUM(*EVENTO_LOG_NOVO, name="evento_log"),
            type_=mysql.ENUM(*EVENTO_LOG_ANTIGO, name="evento_log"),
            existing_nullable=False,
        )
