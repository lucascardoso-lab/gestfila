"""Sessao_versao em usuarios - permite forcar logout remoto

Ate aqui, "desconectar" um usuario da tela de ocupacao de locais so liberava o
Local (local_atual_id/local_desde), sem derrubar a sessao de login dele - nao
havia como, ja que a autenticacao e por cookie assinado client-side (Flask-Login
padrao), sem lista de revogacao server-side.

Adiciona usuarios.sessao_versao (INTEGER NOT NULL DEFAULT 0). O numero atual e
gravado no cookie de sessao no momento do login (auth/routes.py::login). A cada
request, load_user() (app/__init__.py) compara o numero do cookie com o numero
gravado no banco - se nao bater, trata como nao autenticado. "Desconectar"
(admin/locais.py::desconectar_usuario_local) incrementa sessao_versao no banco -
o cookie antigo do usuario passa a ter um numero desatualizado, e a proxima
requisicao dele e tratada como deslogada (sem precisar de nenhuma lista de
sessoes ativas em memoria/banco).

Coluna NOT NULL com default 0 - nenhum backfill manual necessario, usuarios
existentes comecam com sessao_versao=0 (equivalente a "nunca foi forcado a
deslogar"), sem quebrar nenhuma sessao ja aberta (cookies antigos nao tem essa
chave ainda - tratado como versao 0 tambem no lado da aplicacao).

Revision ID: a1c4e8f2b7d3
Revises: b267038dee93
Create Date: 2026-07-25 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a1c4e8f2b7d3'
down_revision = 'b267038dee93'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('usuarios', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column('sessao_versao', sa.Integer(), nullable=False, server_default='0')
        )


def downgrade():
    with op.batch_alter_table('usuarios', schema=None) as batch_op:
        batch_op.drop_column('sessao_versao')
