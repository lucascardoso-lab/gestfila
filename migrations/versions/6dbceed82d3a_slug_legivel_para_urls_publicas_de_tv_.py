"""Slug legivel para URLs publicas de TV e Totem (troca do ID numerico)

Adiciona `slug` (unico por tabela) em `tvs` e `totens`, usado nas novas rotas
`/painel/tv/<slug>` e `/totem/<slug>` (as rotas antigas por ID viram redirect - ver
app/painel/routes.py e app/totem/routes.py). Duplica a lógica de geração de slug de
app/utils/slug.py aqui em vez de importar do pacote da app - mesma convenção já usada
em outras migrations deste projeto (ex.: 2f153fe29e46 duplica HORARIO_PADRAO em vez
de importar do model), para a migration nunca quebrar se o código da app mudar depois.

Coluna adicionada NULLABLE primeiro, preenchida a partir de `nome` (com o mesmo
tratamento de colisão - sufixo numérico só quando necessário) e só então travada como
NOT NULL + UNIQUE - necessário porque a tabela pode já ter linhas reais em produção.

Revision ID: 6dbceed82d3a
Revises: 52df87dc999b
Create Date: 2026-07-02 00:00:00.000000

"""
import re
import unicodedata

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '6dbceed82d3a'
down_revision = '52df87dc999b'
branch_labels = None
depends_on = None


def _slug_base(nome):
    # remove indicadores ordinais (º/ª) antes de normalizar - ver app/utils/slug.py
    # para a justificativa (NFKD os decompõe numa letra 'o'/'a' literal, senão).
    texto = nome.replace("º", "").replace("ª", "")
    texto = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode("ascii")
    texto = re.sub(r"[^a-zA-Z0-9]+", "-", texto).strip("-").lower()
    return texto or "item"


def _slug_unico(nome, ja_usados):
    base = _slug_base(nome)
    if base not in ja_usados:
        return base
    n = 2
    while f"{base}-{n}" in ja_usados:
        n += 1
    return f"{base}-{n}"


def _backfill_slugs(bind, tabela):
    linhas = bind.execute(sa.select(tabela.c.id, tabela.c.nome)).fetchall()
    ja_usados = set()
    for linha in linhas:
        slug = _slug_unico(linha.nome, ja_usados)
        ja_usados.add(slug)
        bind.execute(tabela.update().where(tabela.c.id == linha.id).values(slug=slug))


def upgrade():
    bind = op.get_bind()

    with op.batch_alter_table('tvs', schema=None) as batch_op:
        batch_op.add_column(sa.Column('slug', sa.String(length=160), nullable=True))
    with op.batch_alter_table('totens', schema=None) as batch_op:
        batch_op.add_column(sa.Column('slug', sa.String(length=160), nullable=True))

    tvs_t = sa.table('tvs', sa.column('id', sa.Integer), sa.column('nome', sa.String), sa.column('slug', sa.String))
    totens_t = sa.table(
        'totens', sa.column('id', sa.Integer), sa.column('nome', sa.String), sa.column('slug', sa.String)
    )

    _backfill_slugs(bind, tvs_t)
    _backfill_slugs(bind, totens_t)

    with op.batch_alter_table('tvs', schema=None) as batch_op:
        batch_op.alter_column('slug', existing_type=sa.String(length=160), nullable=False)
        batch_op.create_unique_constraint('uq_tv_slug', ['slug'])
    with op.batch_alter_table('totens', schema=None) as batch_op:
        batch_op.alter_column('slug', existing_type=sa.String(length=160), nullable=False)
        batch_op.create_unique_constraint('uq_totem_slug', ['slug'])


def downgrade():
    with op.batch_alter_table('totens', schema=None) as batch_op:
        batch_op.drop_constraint('uq_totem_slug', type_='unique')
        batch_op.drop_column('slug')
    with op.batch_alter_table('tvs', schema=None) as batch_op:
        batch_op.drop_constraint('uq_tv_slug', type_='unique')
        batch_op.drop_column('slug')
