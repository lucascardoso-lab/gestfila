"""Senhas de dias anteriores (adendo 10.77): novo evento de log 'expirada_aguardando'

Revision ID: e5b7c2d9a4f8
Revises: d4a8e2f7b915
Create Date: 2026-10-14 00:00:00.000000

Amplia o ENUM `logs_atendimento.evento` com 'expirada_aguardando' - usado quando
o sistema cancela uma senha que ficou 'aguardando' de um dia anterior da clinica
(varredura por dia do middleware de encerramento e comando `flask
encerrar-pendentes`). Mesmo padrao da migration c3d9f4a1e6b2 - o autogenerate do
Alembic nao detecta mudancas de ENUM em MySQL, por isso o ALTER e escrito a mao.
Um ALTER de ENUM no MySQL substitui a lista inteira: EVENTO_LOG_ANTIGO tem TODOS
os valores existentes (os 8 de app/models/log_atendimento.py antes do 10.77), na
mesma ordem, e o novo vai no fim (so acrescentar no fim nao reescreve as linhas).

Nao muda `senhas.status` (a senha expirada vira 'cancelada', valor ja existente).

Downgrade: RECUSA se houver linhas com 'expirada_aguardando' - o MySQL (fora do
modo estrito) gravaria '' no lugar do valor removido, apagando em silencio a
trilha de auditoria. Para voltar, decidir antes o destino dessas linhas.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql


# revision identifiers, used by Alembic.
revision = 'e5b7c2d9a4f8'
down_revision = 'd4a8e2f7b915'
branch_labels = None
depends_on = None

EVENTO_LOG_ANTIGO = (
    "gerada", "chamada_senha", "chamada_nome", "inicio_atendimento", "fim_atendimento",
    "realocado", "voltou_aguardar", "encerramento_automatico_sistema",
)
EVENTO_LOG_NOVO = EVENTO_LOG_ANTIGO + ("expirada_aguardando",)


def upgrade():
    with op.batch_alter_table('logs_atendimento', schema=None) as batch_op:
        batch_op.alter_column(
            'evento',
            existing_type=mysql.ENUM(*EVENTO_LOG_ANTIGO, name="evento_log"),
            type_=mysql.ENUM(*EVENTO_LOG_NOVO, name="evento_log"),
            existing_nullable=False,
        )


def downgrade():
    em_uso = op.get_bind().execute(
        sa.text("SELECT COUNT(*) FROM logs_atendimento WHERE evento = 'expirada_aguardando'")
    ).scalar()
    if em_uso:
        raise RuntimeError(
            f"Downgrade de e5b7c2d9a4f8 recusado: {em_uso} linha(s) de logs_atendimento com "
            "evento 'expirada_aguardando'. Remover o valor do ENUM apagaria esse evento da "
            "trilha de auditoria (adendo 10.77). Decida antes o destino dessas linhas (ex.: "
            "manter o ENUM e reverter so o codigo)."
        )
    with op.batch_alter_table('logs_atendimento', schema=None) as batch_op:
        batch_op.alter_column(
            'evento',
            existing_type=mysql.ENUM(*EVENTO_LOG_NOVO, name="evento_log"),
            type_=mysql.ENUM(*EVENTO_LOG_ANTIGO, name="evento_log"),
            existing_nullable=False,
        )
