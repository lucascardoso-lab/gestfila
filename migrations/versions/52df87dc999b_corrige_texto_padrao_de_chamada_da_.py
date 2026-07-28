"""Corrige texto padrao de chamada da secao 6 (texto real decidido com o cliente)

A migration 9f1d8e7b5f06 gravou um texto provisorio (o que ja estava hardcoded no
JS antes desta feature existir) como server_default de empresas.texto_chamada_*_
template, por falta do texto real no momento. O texto definitivo foi decidido
depois - esta migration troca o server_default da coluna E atualiza (via UPDATE
direcionado, so nas linhas que ainda tem o valor provisorio) as empresas que nunca
teriam customizado isso manualmente, ja que nao existe tela admin para editar este
campo ainda.

Revision ID: 52df87dc999b
Revises: 9f1d8e7b5f06
Create Date: 2026-07-01 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '52df87dc999b'
down_revision = '9f1d8e7b5f06'
branch_labels = None
depends_on = None

TEXTO_SENHA_PROVISORIO = "Senha {senha}, dirija-se a {local}."
TEXTO_NOME_PROVISORIO = "{nome}, dirija-se a {local}."

TEXTO_SENHA_DEFINITIVO = "Atenção, senha {senha}. Por favor, dirija-se a {local}."
TEXTO_NOME_DEFINITIVO = "{nome}, por favor, dirija-se a {local}."


def upgrade():
    bind = op.get_bind()

    with op.batch_alter_table('empresas', schema=None) as batch_op:
        batch_op.alter_column(
            'texto_chamada_senha_template', existing_type=sa.String(length=255),
            server_default=TEXTO_SENHA_DEFINITIVO,
        )
        batch_op.alter_column(
            'texto_chamada_nome_template', existing_type=sa.String(length=255),
            server_default=TEXTO_NOME_DEFINITIVO,
        )

    empresas_t = sa.table(
        'empresas',
        sa.column('id', sa.Integer),
        sa.column('texto_chamada_senha_template', sa.String),
        sa.column('texto_chamada_nome_template', sa.String),
    )
    bind.execute(
        empresas_t.update()
        .where(empresas_t.c.texto_chamada_senha_template == TEXTO_SENHA_PROVISORIO)
        .values(texto_chamada_senha_template=TEXTO_SENHA_DEFINITIVO)
    )
    bind.execute(
        empresas_t.update()
        .where(empresas_t.c.texto_chamada_nome_template == TEXTO_NOME_PROVISORIO)
        .values(texto_chamada_nome_template=TEXTO_NOME_DEFINITIVO)
    )


def downgrade():
    bind = op.get_bind()

    empresas_t = sa.table(
        'empresas',
        sa.column('id', sa.Integer),
        sa.column('texto_chamada_senha_template', sa.String),
        sa.column('texto_chamada_nome_template', sa.String),
    )
    bind.execute(
        empresas_t.update()
        .where(empresas_t.c.texto_chamada_senha_template == TEXTO_SENHA_DEFINITIVO)
        .values(texto_chamada_senha_template=TEXTO_SENHA_PROVISORIO)
    )
    bind.execute(
        empresas_t.update()
        .where(empresas_t.c.texto_chamada_nome_template == TEXTO_NOME_DEFINITIVO)
        .values(texto_chamada_nome_template=TEXTO_NOME_PROVISORIO)
    )

    with op.batch_alter_table('empresas', schema=None) as batch_op:
        batch_op.alter_column(
            'texto_chamada_senha_template', existing_type=sa.String(length=255),
            server_default=TEXTO_SENHA_PROVISORIO,
        )
        batch_op.alter_column(
            'texto_chamada_nome_template', existing_type=sa.String(length=255),
            server_default=TEXTO_NOME_PROVISORIO,
        )
