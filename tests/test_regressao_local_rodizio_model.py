"""
Teste de regressão: model LocalRodizioFila (adendo 10.8, Fase 10.8-A) - só o
schema/model nesta fase, sem nenhuma lógica de seleção ainda (isso é a Fase
10.8-C). Cobre: relacionamento ordenado por `ordem`, defaults de `ordem`/
`chamadas_seguidas`, e a chave composta (local_id, fila_id) rejeitando duplicata.

Execução: python -m tests.test_regressao_local_rodizio_model (a partir da raiz
do projeto, com o virtualenv ativado).
"""
import os

os.environ.setdefault("SECRET_KEY", "test")
os.environ.setdefault("FLASK_ENV", "testing")

from sqlalchemy.exc import IntegrityError

from app import create_app
from app.extensions import db
from app.models import Empresa, Fila, Local, LocalRodizioFila, Unidade

app = create_app("testing")

passos = []


def ok(msg):
    passos.append(msg)
    print("OK:", msg)


with app.app_context():
    db.create_all()

    empresa = Empresa(nome="Clinica A")
    db.session.add(empresa)
    db.session.commit()

    unidade = Unidade(empresa_id=empresa.id, nome="Unidade A1")
    db.session.add(unidade)
    db.session.commit()

    fila_coleta = Fila(empresa_id=empresa.id, unidade_id=unidade.id, nome="Coleta", sigla="CL")
    fila_pcd = Fila(empresa_id=empresa.id, unidade_id=unidade.id, nome="PCD", sigla="PC")
    db.session.add_all([fila_coleta, fila_pcd])
    db.session.commit()

    local = Local(empresa_id=empresa.id, unidade_id=unidade.id, nome="Guiche 01")
    db.session.add(local)
    db.session.commit()

    ids = dict(
        empresa=empresa.id, unidade=unidade.id, fila_coleta=fila_coleta.id, fila_pcd=fila_pcd.id, local=local.id,
    )

print("IDS", ids)

# ---------------------------------------------------------------------------
# 1) Local sem nenhuma configuração: rodizio_ordenado vazio (Fase A não muda
#    nada pra quem já existe - continua no fluxo por fila_id, ver BLUEPRINT.md).
# ---------------------------------------------------------------------------
with app.app_context():
    local_db = db.session.get(Local, ids["local"])
    assert local_db.rodizio_ordenado == []
ok("Local sem configuração: rodizio_ordenado é uma lista vazia (comportamento atual preservado)")

# ---------------------------------------------------------------------------
# 2) Defaults: ordem=0, chamadas_seguidas=1 quando não informados.
# ---------------------------------------------------------------------------
with app.app_context():
    item = LocalRodizioFila(local_id=ids["local"], fila_id=ids["fila_coleta"])
    db.session.add(item)
    db.session.commit()
    assert item.ordem == 0
    assert item.chamadas_seguidas == 1
ok("LocalRodizioFila: defaults ordem=0, chamadas_seguidas=1 quando não informados")

# ---------------------------------------------------------------------------
# 3) rodizio_ordenado reflete a ordem configurada (não a ordem de inserção).
# ---------------------------------------------------------------------------
with app.app_context():
    item_pcd = LocalRodizioFila(local_id=ids["local"], fila_id=ids["fila_pcd"], ordem=0, chamadas_seguidas=3)
    db.session.add(item_pcd)
    db.session.commit()

    # reordena: Coleta passa a vir depois de PCD
    coleta_item = LocalRodizioFila.query.filter_by(local_id=ids["local"], fila_id=ids["fila_coleta"]).first()
    coleta_item.ordem = 1
    db.session.commit()

    local_db = db.session.get(Local, ids["local"])
    nomes_em_ordem = [item.fila.nome for item in local_db.rodizio_ordenado]
    assert nomes_em_ordem == ["PCD", "Coleta"]
ok("rodizio_ordenado retorna as filas ordenadas por 'ordem' (PCD antes de Coleta, após reordenar)")

# ---------------------------------------------------------------------------
# 4) chamadas_seguidas é lido corretamente por item.
# ---------------------------------------------------------------------------
with app.app_context():
    local_db = db.session.get(Local, ids["local"])
    chamadas_por_fila = {item.fila.nome: item.chamadas_seguidas for item in local_db.rodizio_ordenado}
    assert chamadas_por_fila == {"PCD": 3, "Coleta": 1}
ok("chamadas_seguidas configurado por par Local×Fila é lido corretamente (PCD=3, Coleta=1)")

# ---------------------------------------------------------------------------
# 5) Chave composta (local_id, fila_id) rejeita duplicata.
# ---------------------------------------------------------------------------
with app.app_context():
    duplicata = LocalRodizioFila(local_id=ids["local"], fila_id=ids["fila_coleta"], ordem=5)
    db.session.add(duplicata)
    try:
        db.session.commit()
        assert False, "deveria ter rejeitado a chave duplicada (local_id, fila_id)"
    except IntegrityError:
        db.session.rollback()
ok("Chave composta (local_id, fila_id) rejeita uma segunda linha para o mesmo par")

print()
print(f"TODOS OS {len(passos)} TESTES DO MODEL LocalRodizioFila (FASE 10.8-A) PASSARAM")
