"""Fusao de Servico em Fila (secao 10.1)

Revision ID: 2f153fe29e46
Revises: 9472b5a2e965
Create Date: 2026-06-24 15:10:45.079112

Não dropa a tabela `servicos` (fica "morta" propositalmente - seção 10.1 do
BLUEPRINT) nem os dados de `totem_botoes.servico_id`/`senhas.servico_id`. Os
novos campos de `filas` são adicionados como NULLABLE primeiro, preenchidos a
partir de `servicos` e só então travados como NOT NULL - necessário porque a
tabela já tem linhas reais em produção (não dá pra adicionar coluna NOT NULL
sem default em tabela não-vazia).
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

# revision identifiers, used by Alembic.
revision = '2f153fe29e46'
down_revision = '9472b5a2e965'
branch_labels = None
depends_on = None

DIAS_SEMANA = ("domingo", "segunda", "terca", "quarta", "quinta", "sexta", "sabado")
# dict puro (não json.dumps) - a coluna é sa.JSON, que já serializa o valor no
# bind; dar um dict pré-serializado aqui resultaria em JSON duplamente
# escapado (uma string contendo JSON, em vez de um objeto JSON).
HORARIO_PADRAO = {dia: {"manha": True, "tarde": True} for dia in DIAS_SEMANA}


def upgrade():
    bind = op.get_bind()

    op.create_table('totens',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('empresa_id', sa.Integer(), nullable=False),
    sa.Column('unidade_id', sa.Integer(), nullable=False),
    sa.Column('nome', sa.String(length=150), nullable=False),
    sa.Column('ativo', sa.Boolean(), nullable=False),
    sa.ForeignKeyConstraint(['empresa_id'], ['empresas.id'], ),
    sa.ForeignKeyConstraint(['unidade_id'], ['unidades.id'], ),
    sa.PrimaryKeyConstraint('id')
    )

    with op.batch_alter_table('filas', schema=None) as batch_op:
        batch_op.add_column(sa.Column('sigla', sa.String(length=4), nullable=True))
        batch_op.add_column(sa.Column('digitos_sequencial', sa.SmallInteger(), nullable=True))
        batch_op.add_column(sa.Column('zera_diariamente', sa.Boolean(), nullable=True))
        batch_op.add_column(sa.Column('prioridade_padrao', sa.SmallInteger(), nullable=True))
        batch_op.add_column(sa.Column('totem_id', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('tv_id', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('horario_funcionamento', sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column('tempo_espera_maximo_min', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('tempo_atendimento_maximo_min', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('alerta_email', sa.Boolean(), nullable=True))
        batch_op.alter_column('servico_id', existing_type=mysql.INTEGER(), nullable=True)
        batch_op.create_foreign_key(None, 'tvs', ['tv_id'], ['id'])
        batch_op.create_foreign_key(None, 'totens', ['totem_id'], ['id'])

    with op.batch_alter_table('senhas', schema=None) as batch_op:
        batch_op.alter_column('servico_id', existing_type=mysql.INTEGER(), nullable=True)

    with op.batch_alter_table('totem_botoes', schema=None) as batch_op:
        batch_op.add_column(sa.Column('fila_id', sa.Integer(), nullable=True))
        batch_op.create_foreign_key(None, 'filas', ['fila_id'], ['id'])

    # --- migração de dados: copia servicos -> filas, sem descartar nada -------
    filas_t = sa.table(
        'filas',
        sa.column('id', sa.Integer), sa.column('nome', sa.String), sa.column('servico_id', sa.Integer),
        sa.column('sigla', sa.String), sa.column('digitos_sequencial', sa.SmallInteger),
        sa.column('zera_diariamente', sa.Boolean), sa.column('prioridade_padrao', sa.SmallInteger),
        sa.column('horario_funcionamento', sa.JSON), sa.column('alerta_email', sa.Boolean),
    )
    servicos_t = sa.table(
        'servicos',
        sa.column('id', sa.Integer), sa.column('sigla', sa.String),
        sa.column('digitos_sequencial', sa.SmallInteger), sa.column('zera_diariamente', sa.Boolean),
        sa.column('prioridade_padrao', sa.SmallInteger),
    )
    totem_botoes_t = sa.table(
        'totem_botoes',
        sa.column('id', sa.Integer), sa.column('servico_id', sa.Integer), sa.column('fila_id', sa.Integer),
    )

    servico_por_id = {row.id: row for row in bind.execute(sa.select(servicos_t)).fetchall()}

    fila_id_por_servico_id = {}
    for fila in bind.execute(sa.select(filas_t)).fetchall():
        servico = servico_por_id.get(fila.servico_id)
        if servico is not None:
            fila_id_por_servico_id[servico.id] = fila.id
            valores = dict(
                sigla=servico.sigla,
                digitos_sequencial=servico.digitos_sequencial,
                zera_diariamente=servico.zera_diariamente,
                prioridade_padrao=servico.prioridade_padrao,
            )
        else:
            # fila sem servico_id correspondente (não deveria ocorrer nos dados
            # conhecidos, mas a migration não pode falhar por isso) - usa as
            # mesmas convenções padrão do model Fila.
            valores = dict(
                sigla=(fila.nome or "FL")[:4].upper(),
                digitos_sequencial=3,
                zera_diariamente=True,
                prioridade_padrao=0,
            )
        bind.execute(
            filas_t.update().where(filas_t.c.id == fila.id).values(
                horario_funcionamento=HORARIO_PADRAO, alerta_email=False, **valores
            )
        )

    for botao in bind.execute(sa.select(totem_botoes_t)).fetchall():
        if botao.servico_id is None:
            continue
        fila_id = fila_id_por_servico_id.get(botao.servico_id)
        if fila_id is not None:
            bind.execute(totem_botoes_t.update().where(totem_botoes_t.c.id == botao.id).values(fila_id=fila_id))

    # agora que toda fila existente tem os campos preenchidos, trava como NOT NULL
    with op.batch_alter_table('filas', schema=None) as batch_op:
        batch_op.alter_column('sigla', existing_type=sa.String(length=4), nullable=False)
        batch_op.alter_column('digitos_sequencial', existing_type=sa.SmallInteger(), nullable=False)
        batch_op.alter_column('zera_diariamente', existing_type=sa.Boolean(), nullable=False)
        batch_op.alter_column('prioridade_padrao', existing_type=sa.SmallInteger(), nullable=False)
        batch_op.alter_column('horario_funcionamento', existing_type=sa.JSON(), nullable=False)
        batch_op.alter_column('alerta_email', existing_type=sa.Boolean(), nullable=False)


def downgrade():
    with op.batch_alter_table('totem_botoes', schema=None) as batch_op:
        batch_op.drop_constraint(None, type_='foreignkey')
        batch_op.drop_column('fila_id')

    with op.batch_alter_table('senhas', schema=None) as batch_op:
        batch_op.alter_column('servico_id', existing_type=mysql.INTEGER(), nullable=False)

    with op.batch_alter_table('filas', schema=None) as batch_op:
        batch_op.drop_constraint(None, type_='foreignkey')
        batch_op.drop_constraint(None, type_='foreignkey')
        batch_op.alter_column('servico_id', existing_type=mysql.INTEGER(), nullable=False)
        batch_op.drop_column('alerta_email')
        batch_op.drop_column('tempo_atendimento_maximo_min')
        batch_op.drop_column('tempo_espera_maximo_min')
        batch_op.drop_column('horario_funcionamento')
        batch_op.drop_column('tv_id')
        batch_op.drop_column('totem_id')
        batch_op.drop_column('prioridade_padrao')
        batch_op.drop_column('zera_diariamente')
        batch_op.drop_column('digitos_sequencial')
        batch_op.drop_column('sigla')

    op.drop_table('totens')
