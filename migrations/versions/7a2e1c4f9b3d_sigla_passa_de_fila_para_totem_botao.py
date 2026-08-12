"""Sigla passa de Fila para TotemBotao (adendo 10.47 - realinha com a secao 2
original do BLUEPRINT: "sigla definida pelo admin ao criar o botao do totem")

Revision ID: 7a2e1c4f9b3d
Revises: 6f6d09c50b0d
Create Date: 2026-08-12 00:00:00.000000

`totem_botoes.sigla` nasce NULLABLE (só é obrigatória em botões-FOLHA, ou seja,
com `fila_id` preenchido - botões-menu como "MEDICOS" nunca têm sigla, mesma
convenção já usada para `fila_id` desde a 10.1). Migração de dados copia
`fila.sigla` -> `totem_botao.sigla` para cada botão-folha existente, ponto de
partida sem perder nenhuma sigla já cadastrada (mesmo padrão da 2f153fe29e46).

`filas.sigla` fica DEPRECIADA (não lida/escrita por código novo, ver comentário
no model `Fila`) mas NÃO é dropada ainda - mantida "morta" por 1 deploy antes de
remover, mesmo padrão da tabela `servicos` na 10.1. Por isso vira NULLABLE aqui
(a tela de filas deixa de exigir sigla para criar/salvar - fila pode ficar
órfã de sigla até ganhar um botão).
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '7a2e1c4f9b3d'
down_revision = '6f6d09c50b0d'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()

    with op.batch_alter_table('totem_botoes', schema=None) as batch_op:
        batch_op.add_column(sa.Column('sigla', sa.String(length=4), nullable=True))

    # --- migração de dados: copia filas.sigla -> totem_botoes.sigla para cada
    # botão-folha (fila_id IS NOT NULL) já existente ------------------------
    totem_botoes_t = sa.table(
        'totem_botoes',
        sa.column('id', sa.Integer), sa.column('fila_id', sa.Integer), sa.column('sigla', sa.String),
    )
    filas_t = sa.table('filas', sa.column('id', sa.Integer), sa.column('sigla', sa.String))

    sigla_por_fila_id = {row.id: row.sigla for row in bind.execute(sa.select(filas_t)).fetchall()}

    for botao in bind.execute(sa.select(totem_botoes_t).where(totem_botoes_t.c.fila_id.isnot(None))).fetchall():
        sigla = sigla_por_fila_id.get(botao.fila_id)
        if sigla is not None:
            bind.execute(totem_botoes_t.update().where(totem_botoes_t.c.id == botao.id).values(sigla=sigla))

    with op.batch_alter_table('totem_botoes', schema=None) as batch_op:
        batch_op.create_unique_constraint('uq_totem_botao_unidade_sigla', ['unidade_id', 'sigla'])

    # filas.sigla: depreciada, mantida como coluna "morta" - deixa de ser exigida
    # na criação/edição de fila (tela de filas não pede mais este campo).
    with op.batch_alter_table('filas', schema=None) as batch_op:
        batch_op.alter_column('sigla', existing_type=sa.String(length=4), nullable=True)


def downgrade():
    with op.batch_alter_table('filas', schema=None) as batch_op:
        batch_op.alter_column('sigla', existing_type=sa.String(length=4), nullable=False)

    with op.batch_alter_table('totem_botoes', schema=None) as batch_op:
        batch_op.drop_constraint('uq_totem_botao_unidade_sigla', type_='unique')
        batch_op.drop_column('sigla')
