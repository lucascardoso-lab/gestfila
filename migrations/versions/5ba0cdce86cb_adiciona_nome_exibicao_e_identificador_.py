"""adiciona nome_exibicao e identificador_admin a local

Revision ID: 5ba0cdce86cb
Revises: c53c321f3868
Create Date: 2026-07-29 12:37:17.261561

Separa o campo unico `Local.nome` em dois (BLUEPRINT.md - adendo "Separacao
nome de exibicao / identificador administrativo em Local"), mesmo espirito de
um cadastro de usuario (nome de exibicao vs. login):

- nome_exibicao: mostrado ao paciente/atendente (painel de TV, tela de
  Atendimento) - pode se repetir entre unidades diferentes sem problema.
- identificador_admin: usado so nas telas administrativas - unico por
  empresa (nao so por unidade). Valor inicial = "[SIGLA da Unidade] nome"
  (mesma convencao de Unidade.sigla_exibicao, ja usada em outros pontos do
  admin - adendo 10.22), aplicado a TODOS os Locais existentes de forma
  uniforme (nao so aos que colidem sem o prefixo) - decisao explicita do
  cliente, pra nunca ter uma mistura de Locais com/sem prefixo no mesmo
  sistema.

Desempate residual (defensivo, nao necessario nos dados reais na data desta
migration): se, mesmo depois do prefixo "[SIGLA] nome", ainda sobrar colisao
case-insensitive dentro da mesma empresa (ex.: duas unidades cujos 3
primeiros caracteres do nome coincidem, quando nenhuma delas tem
Unidade.sigla cadastrada), o id do Local e anexado entre parenteses ao final
de TODOS os membros do grupo colidente (ex.: "[MAT] Guiche 01 (7)") - nunca
so nos "duplicados", pra o resultado ser simetrico e previsivel. Se um dia
esse sufixo aparecer num identificador_admin real, e esperado, nao bug.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import text


# revision identifiers, used by Alembic.
revision = '5ba0cdce86cb'
down_revision = 'c53c321f3868'
branch_labels = None
depends_on = None


def _sigla_exibicao(sigla, unidade_nome):
    return (sigla or unidade_nome[:3]).upper()


def upgrade():
    with op.batch_alter_table('locais', schema=None) as batch_op:
        batch_op.add_column(sa.Column('nome_exibicao', sa.String(length=150), nullable=True))
        batch_op.add_column(sa.Column('identificador_admin', sa.String(length=200), nullable=True))

    bind = op.get_bind()

    unidades = {
        row.id: (row.sigla, row.nome)
        for row in bind.execute(text("SELECT id, sigla, nome FROM unidades"))
    }
    locais = list(bind.execute(text("SELECT id, empresa_id, unidade_id, nome FROM locais")))

    # 1a passada: nome_exibicao = nome atual (inalterado), identificador_admin
    # = "[SIGLA] nome" pra todo mundo, uniformemente.
    valores = {}
    for local in locais:
        sigla, unidade_nome = unidades[local.unidade_id]
        prefixo = _sigla_exibicao(sigla, unidade_nome)
        valores[local.id] = {
            "empresa_id": local.empresa_id,
            "nome_exibicao": local.nome,
            "identificador_admin": f"[{prefixo}] {local.nome}",
        }

    # 2a passada: detecta colisao case-insensitive residual dentro da mesma
    # empresa (mesmo espirito da collation utf8mb4_*_ci real do banco) e
    # aplica o desempate por id a TODOS os membros do grupo colidente.
    grupos = {}
    for local_id, dados in valores.items():
        chave = (dados["empresa_id"], dados["identificador_admin"].strip().lower())
        grupos.setdefault(chave, []).append(local_id)

    for (_empresa_id, _chave), ids_do_grupo in grupos.items():
        if len(ids_do_grupo) > 1:
            for local_id in ids_do_grupo:
                valores[local_id]["identificador_admin"] += f" ({local_id})"

    for local_id, dados in valores.items():
        bind.execute(
            text("UPDATE locais SET nome_exibicao = :nome_exibicao, identificador_admin = :identificador_admin WHERE id = :id"),
            {"nome_exibicao": dados["nome_exibicao"], "identificador_admin": dados["identificador_admin"], "id": local_id},
        )

    with op.batch_alter_table('locais', schema=None) as batch_op:
        batch_op.alter_column('nome_exibicao', existing_type=sa.String(length=150), nullable=False)
        batch_op.alter_column('identificador_admin', existing_type=sa.String(length=200), nullable=False)
        batch_op.create_unique_constraint('uq_locais_empresa_identificador_admin', ['empresa_id', 'identificador_admin'])


def downgrade():
    with op.batch_alter_table('locais', schema=None) as batch_op:
        batch_op.drop_constraint('uq_locais_empresa_identificador_admin', type_='unique')
        batch_op.drop_column('identificador_admin')
        batch_op.drop_column('nome_exibicao')
