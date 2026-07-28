"""Cadastro de Locais (secao 10.3)

Revision ID: 74691016b6bf
Revises: 235bf852e44c
Create Date: 2026-06-24 17:12:20.021379

Não dropa nem deixa de popular `senhas.guiche_local` (continua morta/legada, mesmo
tratamento do adendo 10.1 para `servicos`). Migra os dados existentes: cada valor
distinto de `guiche_local` (por empresa/unidade) vira um `Local` próprio - sem
tentar adivinhar se "rec-01"/"REC1-01" são o mesmo posto físico (decisão de
consolidação manual, não automática, para não perder histórico por engano).
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '74691016b6bf'
down_revision = '235bf852e44c'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()

    op.create_table('locais',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('empresa_id', sa.Integer(), nullable=False),
    sa.Column('unidade_id', sa.Integer(), nullable=False),
    sa.Column('nome', sa.String(length=150), nullable=False),
    sa.Column('tipo', sa.Enum('recepcao', 'coleta', 'outro', name='tipo_local'), nullable=True),
    sa.Column('ativo', sa.Boolean(), nullable=False),
    sa.ForeignKeyConstraint(['empresa_id'], ['empresas.id'], ),
    sa.ForeignKeyConstraint(['unidade_id'], ['unidades.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('senhas', schema=None) as batch_op:
        batch_op.add_column(sa.Column('local_id', sa.Integer(), nullable=True))
        batch_op.create_foreign_key(None, 'locais', ['local_id'], ['id'])

    # --- migração de dados: cada guiche_local distinto -> 1 Local -------------
    locais_t = sa.table(
        'locais',
        sa.column('id', sa.Integer), sa.column('empresa_id', sa.Integer),
        sa.column('unidade_id', sa.Integer), sa.column('nome', sa.String),
        sa.column('tipo', sa.String), sa.column('ativo', sa.Boolean),
    )
    senhas_t = sa.table(
        'senhas',
        sa.column('id', sa.Integer), sa.column('empresa_id', sa.Integer),
        sa.column('unidade_id', sa.Integer), sa.column('guiche_local', sa.String),
        sa.column('local_id', sa.Integer),
    )

    combinacoes = bind.execute(
        sa.select(senhas_t.c.empresa_id, senhas_t.c.unidade_id, senhas_t.c.guiche_local)
        .where(senhas_t.c.guiche_local.isnot(None))
        .distinct()
    ).fetchall()

    for combinacao in combinacoes:
        resultado = bind.execute(
            locais_t.insert().values(
                empresa_id=combinacao.empresa_id, unidade_id=combinacao.unidade_id,
                nome=combinacao.guiche_local, tipo=None, ativo=True,
            )
        )
        local_id = resultado.lastrowid
        bind.execute(
            senhas_t.update()
            .where(senhas_t.c.empresa_id == combinacao.empresa_id)
            .where(senhas_t.c.unidade_id == combinacao.unidade_id)
            .where(senhas_t.c.guiche_local == combinacao.guiche_local)
            .values(local_id=local_id)
        )


def downgrade():
    with op.batch_alter_table('senhas', schema=None) as batch_op:
        batch_op.drop_constraint(None, type_='foreignkey')
        batch_op.drop_column('local_id')

    op.drop_table('locais')
