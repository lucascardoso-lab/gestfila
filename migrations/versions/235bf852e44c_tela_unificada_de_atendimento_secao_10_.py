"""Tela unificada de atendimento (secao 10.2): observacoes em senhas e novos eventos de log

Revision ID: 235bf852e44c
Revises: 2f153fe29e46
Create Date: 2026-06-24 16:27:57.661951

Adiciona `senhas.observacoes` (pedido explicitamente pela seção 10.2) e também
amplia o ENUM `logs_atendimento.evento` com 'realocado'/'voltou_aguardar' - os novos
botões "Realocar"/"Aguardar" da tela unificada precisam de um evento de auditoria
próprio (mesmo padrão já usado para 'chamada_senha'/'fim_atendimento'); o autogenerate
do Alembic não detecta mudanças de ENUM em MySQL, por isso esse ALTER foi escrito à mão.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


# revision identifiers, used by Alembic.
revision = '235bf852e44c'
down_revision = '2f153fe29e46'
branch_labels = None
depends_on = None

EVENTO_LOG_ANTIGO = ("gerada", "chamada_senha", "chamada_nome", "inicio_atendimento", "fim_atendimento")
EVENTO_LOG_NOVO = EVENTO_LOG_ANTIGO + ("realocado", "voltou_aguardar")


def upgrade():
    with op.batch_alter_table('senhas', schema=None) as batch_op:
        batch_op.add_column(sa.Column('observacoes', sa.Text(), nullable=True))

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

    with op.batch_alter_table('senhas', schema=None) as batch_op:
        batch_op.drop_column('observacoes')
