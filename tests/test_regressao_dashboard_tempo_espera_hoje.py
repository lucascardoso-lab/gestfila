"""
Teste de regressão: `_tempo_medio_espera_hoje` (app/core/dashboard_queries.py,
tile "Tempo médio de espera" do dashboard operacional) tinha uma implementação
PRÓPRIA, independente do relatório completo, com o MESMO padrão de bug
corrigido pelo adendo 10.66 em `tempo_medio_espera`/`_segmentos_espera_por_
senha` (`app/relatorios/queries.py`): dict que sobrescrevia em caso de
múltiplos 'chamada_senha', e só considerava ('gerada', 'chamada_senha') -
nunca 'chamada_nome'/'realocado'/'voltou_aguardar'.

Correção (mesmo lote do 10.66): `_tempo_medio_espera_hoje` passou a
reaproveitar `_segmentos_espera_por_senha` (não duplica mais a lógica de
pareamento), achatando TODOS os intervalos de espera do dia numa única
média - formato de saída não mudou (um único `int` de segundos, ou `None`).

Cobre:
1. Senha realocada HOJE (2 estágios de espera) - a média agora inclui os
   DOIS intervalos, não só o primeiro.
2. Nenhuma senha com intervalo de espera fechado hoje -> None (não 0).
3. Dado histórico simulado (sem snapshot) -> cai no fallback, sem erro.

Chama `_tempo_medio_espera_hoje` diretamente (mesmo estilo de
test_regressao_segmentos_espera.py) - o contrato HTTP (`/api/dashboard/kpis`)
já é coberto por test_regressao_dashboard_operacional.py, sem necessidade de
duplicar aqui.

Execução: python -m tests.test_regressao_dashboard_tempo_espera_hoje (a
partir da raiz do projeto, com o virtualenv ativado).
"""
import os
from datetime import datetime, timedelta

os.environ.setdefault("SECRET_KEY", "test")
os.environ.setdefault("FLASK_ENV", "testing")

from app import create_app
from app.core.dashboard_queries import _tempo_medio_espera_hoje
from app.extensions import db
from app.models import Empresa, Fila, LogAtendimento, Senha, Unidade

app = create_app("testing")

passos = []


def ok(msg):
    passos.append(msg)
    print("OK:", msg)


with app.app_context():
    db.create_all()

    empresa = Empresa(nome="Clinica Dashboard Espera")
    db.session.add(empresa)
    db.session.commit()

    unidade = Unidade(empresa_id=empresa.id, nome="Unidade D1")
    db.session.add(unidade)
    db.session.commit()

    fila_recepcao = Fila(empresa_id=empresa.id, unidade_id=unidade.id, nome="Recepção", sigla="RC")
    fila_coleta = Fila(empresa_id=empresa.id, unidade_id=unidade.id, nome="Coleta", sigla="CL")
    db.session.add_all([fila_recepcao, fila_coleta])
    db.session.commit()

    def nova_senha(fila_id, codigo, criada_em):
        s = Senha(
            empresa_id=empresa.id, unidade_id=unidade.id, fila_id=fila_id,
            codigo=codigo, sequencial=1, prioridade=False, status="finalizada", criada_em=criada_em,
        )
        db.session.add(s)
        db.session.commit()
        return s.id

    def log(senha_id, evento, timestamp, fila_id):
        db.session.add(
            LogAtendimento(
                senha_id=senha_id, evento=evento, unidade_id=unidade.id,
                timestamp=timestamp, fila_id=fila_id,
            )
        )

    hoje = datetime.utcnow().replace(hour=10, minute=0, second=0, microsecond=0)
    inicio_hoje = hoje.replace(hour=0, minute=0, second=0, microsecond=0)
    fim_hoje = inicio_hoje + timedelta(days=1)

    # -------------------------------------------------------------------
    # 1) Senha realocada HOJE: 2 intervalos de espera (Recepção 60s, Coleta
    #    40s) - a média (50s) precisa incluir os DOIS, não só o primeiro.
    # -------------------------------------------------------------------
    id_realocada = nova_senha(fila_recepcao.id, "D0001", criada_em=hoje)
    log(id_realocada, "gerada", hoje, fila_recepcao.id)
    log(id_realocada, "chamada_senha", hoje + timedelta(seconds=60), fila_recepcao.id)  # espera 1: 60s
    log(id_realocada, "realocado", hoje + timedelta(seconds=90), fila_recepcao.id)
    log(id_realocada, "chamada_nome", hoje + timedelta(seconds=130), fila_coleta.id)  # espera 2: 40s
    log(id_realocada, "fim_atendimento", hoje + timedelta(seconds=200), fila_coleta.id)

    db.session.commit()

    ids = dict(empresa=empresa.id, unidade=unidade.id, fila_recepcao=fila_recepcao.id, fila_coleta=fila_coleta.id)


def calcular():
    with app.app_context():
        empresa_obj = db.session.get(Empresa, ids["empresa"])
        return _tempo_medio_espera_hoje(empresa_obj, {ids["unidade"]}, inicio_hoje, fim_hoje)


# ===========================================================================
# 1) Média inclui os DOIS intervalos de espera da senha realocada hoje
#    (60s + 40s = média 50s), não só o primeiro.
# ===========================================================================
media = calcular()
assert media == 50, media
ok("Tile do dashboard: média inclui os 2 intervalos de espera da senha realocada hoje (60s+40s=50s), não só o 1º")

# ===========================================================================
# 2) Sem nenhuma senha com intervalo de espera fechado hoje -> None.
# ===========================================================================
with app.app_context():
    empresa_vazia = Empresa(nome="Clinica Sem Senhas Hoje")
    db.session.add(empresa_vazia)
    db.session.commit()
    unidade_vazia = Unidade(empresa_id=empresa_vazia.id, nome="U Vazia")
    db.session.add(unidade_vazia)
    db.session.commit()
    resultado_vazio = _tempo_medio_espera_hoje(empresa_vazia, {unidade_vazia.id}, inicio_hoje, fim_hoje)
    assert resultado_vazio is None
ok("Nenhuma senha com intervalo de espera fechado hoje: retorna None, não 0")

# ===========================================================================
# 3) Dado histórico (sem snapshot em nenhum evento) cai no fallback, sem erro.
# ===========================================================================
with app.app_context():
    empresa_hist = Empresa(nome="Clinica Historica Dashboard")
    db.session.add(empresa_hist)
    db.session.commit()
    unidade_hist = Unidade(empresa_id=empresa_hist.id, nome="U Historica")
    db.session.add(unidade_hist)
    db.session.commit()
    fila_hist = Fila(empresa_id=empresa_hist.id, unidade_id=unidade_hist.id, nome="Fila Hist", sigla="FH")
    db.session.add(fila_hist)
    db.session.commit()

    senha_hist = Senha(
        empresa_id=empresa_hist.id, unidade_id=unidade_hist.id, fila_id=fila_hist.id,
        codigo="H0001", sequencial=1, prioridade=False, status="finalizada", criada_em=hoje,
    )
    db.session.add(senha_hist)
    db.session.commit()
    db.session.add(LogAtendimento(
        senha_id=senha_hist.id, evento="gerada", unidade_id=unidade_hist.id, timestamp=hoje, fila_id=None,
    ))
    db.session.add(LogAtendimento(
        senha_id=senha_hist.id, evento="chamada_senha", unidade_id=unidade_hist.id,
        timestamp=hoje + timedelta(seconds=80), fila_id=None,
    ))
    db.session.commit()

    resultado_hist = _tempo_medio_espera_hoje(empresa_hist, {unidade_hist.id}, inicio_hoje, fim_hoje)
    assert resultado_hist == 80, resultado_hist
ok("Dado histórico (sem snapshot): cai no fallback de 1 intervalo (80s), sem erro")

print()
print(f"TODOS OS {len(passos)} TESTES DE TEMPO MEDIO DE ESPERA (DASHBOARD, HOJE) PASSARAM")
