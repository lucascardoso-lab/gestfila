"""Unicidade de unidades.sigla por empresa (adendo 10.71)

Revision ID: c7e2a9d4f1b3
Revises: b3d6f8a1c4e7
Create Date: 2026-10-08 00:00:00.000000

UniqueConstraint (empresa_id, sigla) - rede de segurança do banco para a
validação da tela de Unidades (app/admin/unidades.py), que já rejeita siglas
repetidas com mensagem clara. `sigla` continua opcional: o MySQL permite
vários NULL num índice único, então unidades sem sigla (fallback de 3 letras,
Unidade.sigla_exibicao) não conflitam entre si. A collation da coluna
(utf8mb4_0900_ai_ci) já torna o índice case/accent-insensitive.

ANTES de aplicar em produção, conferir que não há duplicatas (a migration
falharia):
    SELECT empresa_id, sigla, COUNT(*) FROM unidades
    WHERE sigla IS NOT NULL GROUP BY empresa_id, sigla HAVING COUNT(*) > 1;
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'c7e2a9d4f1b3'
down_revision = 'b3d6f8a1c4e7'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('unidades', schema=None) as batch_op:
        batch_op.create_unique_constraint('uq_unidades_empresa_sigla', ['empresa_id', 'sigla'])


def downgrade():
    # No MySQL, a FK unidades.empresa_id -> empresas.id usava um índice
    # implícito ("empresa_id"); ao criar o índice único (empresa_id, sigla),
    # o MySQL descarta esse índice implícito e passa a apoiar a FK no novo -
    # que então não pode ser removido sozinho (erro 1553, "needed in a foreign
    # key constraint" - encontrado testando o downgrade no MySQL real).
    # Recria o índice simples antes, devolvendo o schema ao estado anterior.
    indices = sa.inspect(op.get_bind()).get_indexes('unidades')
    tem_indice_empresa = any(
        ix['column_names'] == ['empresa_id'] for ix in indices
    )
    if op.get_bind().dialect.name == 'mysql' and not tem_indice_empresa:
        op.create_index('empresa_id', 'unidades', ['empresa_id'])
    with op.batch_alter_table('unidades', schema=None) as batch_op:
        batch_op.drop_constraint('uq_unidades_empresa_sigla', type_='unique')
