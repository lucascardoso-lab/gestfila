"""Tempo de espera no rodape do painel de TV (adendo 10.88): tvs.mostra_tempo_espera

Revision ID: f6c1d8e3a2b9
Revises: e5b7c2d9a4f8
Create Date: 2026-11-24 00:00:00.000000

Opcao por TV, desligada por padrao. server_default '0' para as TVs ja
existentes nao violarem o NOT NULL na criacao da coluna (mesmo padrao de
usa_audio_pregravado, f01b63e1bf65); o default logico (False) fica no model.
Downgrade so remove a coluna (configuracao, nao historico).
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'f6c1d8e3a2b9'
down_revision = 'e5b7c2d9a4f8'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('tvs', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column('mostra_tempo_espera', sa.Boolean(), nullable=False, server_default='0')
        )


def downgrade():
    with op.batch_alter_table('tvs', schema=None) as batch_op:
        batch_op.drop_column('mostra_tempo_espera')
