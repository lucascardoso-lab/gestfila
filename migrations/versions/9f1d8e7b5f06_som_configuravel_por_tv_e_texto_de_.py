"""Som configuravel por TV e texto de chamada por empresa (secao 6)

Revision ID: 9f1d8e7b5f06
Revises: 41228c53ab68
Create Date: 2026-07-01 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '9f1d8e7b5f06'
down_revision = '41228c53ab68'
branch_labels = None
depends_on = None

TEXTO_CHAMADA_SENHA_PADRAO = "Senha {senha}, dirija-se a {local}."
TEXTO_CHAMADA_NOME_PADRAO = "{nome}, dirija-se a {local}."


def upgrade():
    with op.batch_alter_table('tvs', schema=None) as batch_op:
        batch_op.add_column(sa.Column('som_ativo', sa.Boolean(), nullable=False, server_default=sa.true()))
        batch_op.add_column(sa.Column('volume', sa.SmallInteger(), nullable=False, server_default='100'))

    with op.batch_alter_table('empresas', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                'texto_chamada_senha_template', sa.String(length=255), nullable=False,
                server_default=TEXTO_CHAMADA_SENHA_PADRAO,
            )
        )
        batch_op.add_column(
            sa.Column(
                'texto_chamada_nome_template', sa.String(length=255), nullable=False,
                server_default=TEXTO_CHAMADA_NOME_PADRAO,
            )
        )


def downgrade():
    with op.batch_alter_table('empresas', schema=None) as batch_op:
        batch_op.drop_column('texto_chamada_nome_template')
        batch_op.drop_column('texto_chamada_senha_template')

    with op.batch_alter_table('tvs', schema=None) as batch_op:
        batch_op.drop_column('volume')
        batch_op.drop_column('som_ativo')
