"""Renomeia perfis recepcao/coleta/recepcao_coleta para atendente/executor/atendente_executor

Revision ID: f4900d785aee
Revises: d53cf46b3068
Create Date: 2026-07-28 00:00:00.000000

Renomeia a NOMENCLATURA do perfil (RBAC/papel do usuario, tabela `perfis`) a
pedido do cliente - `recepcao` -> `atendente`, `coleta` -> `executor`,
`recepcao_coleta` -> `atendente_executor`.

Escopo deliberadamente restrito ao Perfil: o "setor" (area de trabalho usada nas
URLs /atendimento/recepcao|coleta/..., em `Local.tipo` e `Senha.setor`) NAO muda -
sao conceitos distintos que so coincidiam de nome por convencao. `perfis.nome` e
um VARCHAR simples (nao ENUM), entao isto e um UPDATE de dados, nao uma alteracao
de schema - as associacoes usuario_perfil (por ID) permanecem intactas.
"""
from alembic import op


# revision identifiers, used by Alembic.
revision = 'f4900d785aee'
down_revision = 'd53cf46b3068'
branch_labels = None
depends_on = None


def upgrade():
    op.execute("UPDATE perfis SET nome = 'atendente' WHERE nome = 'recepcao'")
    op.execute("UPDATE perfis SET nome = 'executor' WHERE nome = 'coleta'")
    op.execute("UPDATE perfis SET nome = 'atendente_executor' WHERE nome = 'recepcao_coleta'")

    op.execute(
        "UPDATE perfis SET descricao = 'Operação do totem e chamada de senha (setor recepção)' "
        "WHERE nome = 'atendente'"
    )
    op.execute(
        "UPDATE perfis SET descricao = 'Chamada de senha e chamada por nome (setor coleta)' "
        "WHERE nome = 'executor'"
    )
    op.execute(
        "UPDATE perfis SET descricao = 'União dos perfis atendente e executor' "
        "WHERE nome = 'atendente_executor'"
    )


def downgrade():
    op.execute("UPDATE perfis SET nome = 'recepcao' WHERE nome = 'atendente'")
    op.execute("UPDATE perfis SET nome = 'coleta' WHERE nome = 'executor'")
    op.execute("UPDATE perfis SET nome = 'recepcao_coleta' WHERE nome = 'atendente_executor'")

    op.execute(
        "UPDATE perfis SET descricao = 'Operação do totem e chamada de senha' WHERE nome = 'recepcao'"
    )
    op.execute(
        "UPDATE perfis SET descricao = 'Chamada de senha e chamada por nome' WHERE nome = 'coleta'"
    )
    op.execute(
        "UPDATE perfis SET descricao = 'União dos perfis recepção e coleta' WHERE nome = 'recepcao_coleta'"
    )
