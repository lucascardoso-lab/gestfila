"""
Teste de regressão: cartões de tempo do Dashboard (adendo 10.86), que
substituem o cartão "Tempo médio de espera hoje".

Histórico: o cartão antigo (`_tempo_medio_espera_hoje`) fazia a média por
SEGMENTO de espera (adendo 10.66) - numa senha realocada, as duas esperas
(Recepção e Coleta) entravam como dois números. O Tempos por senha (10.73/
10.83) mede POR SENHA, e os dois números de espera diferentes na mesma tela
confundiam. O 10.86 tirou o cartão antigo; os cartões novos ("Espera
inicial", "Atendimento executor", "Permanência total") são `tempos_por_senha`
+ `_resumo_tempos`, sem cálculo próprio.

Cobre (mesmos cenários do teste antigo, agora nos cartões novos):
1. Senha realocada hoje (2 esperas): "Espera inicial" é a 1ª espera da senha
   (60 s), não a média dos dois segmentos (50 s do cartão antigo); executor e
   permanência total da mesma senha; tudo igual ao resumo do Tempos por senha.
2. Nenhuma senha -> None (texto None, "—" na tela), nunca 0.
3. Dado histórico (sem snapshot de fila) -> fallback, sem erro (80 s).

"Hoje" é o dia da CLÍNICA (`janela_dia_clinica_em_utc`) - o teste antigo
usava `datetime.utcnow()` às 10h UTC, que depois das 21h de Brasília já é o
dia seguinte da clínica.

Execução: python -m tests.test_regressao_dashboard_tempo_espera_hoje (a
partir da raiz do projeto, com o virtualenv ativado).
"""
import os
from datetime import timedelta

os.environ.setdefault("SECRET_KEY", "test")
os.environ.setdefault("FLASK_ENV", "testing")

from app import create_app
from app.core.dashboard_queries import calcular_kpis, montar_periodo
from app.extensions import db
from app.models import Empresa, Fila, Local, LogAtendimento, Senha, Unidade
from app.relatorios.queries import _resumo_tempos, tempos_por_senha
from app.utils.tempo import janela_dia_clinica_em_utc

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
    guiche = Local(empresa_id=empresa.id, unidade_id=unidade.id, nome="Guichê", nome_exibicao="Guichê",
                   identificador_admin="Guichê D1", tipo="recepcao")
    sala = Local(empresa_id=empresa.id, unidade_id=unidade.id, nome="Sala", nome_exibicao="Sala",
                 identificador_admin="Sala D1", tipo="coleta")
    db.session.add_all([guiche, sala])
    db.session.commit()

    def nova_senha(fila_id, codigo, criada_em):
        s = Senha(
            empresa_id=empresa.id, unidade_id=unidade.id, fila_id=fila_id,
            codigo=codigo, sequencial=1, prioridade=False, status="finalizada", criada_em=criada_em,
        )
        db.session.add(s)
        db.session.commit()
        return s.id

    def log(senha_id, evento, timestamp, fila_id, local_id=None):
        db.session.add(
            LogAtendimento(
                senha_id=senha_id, evento=evento, unidade_id=unidade.id,
                timestamp=timestamp, fila_id=fila_id, local_id=local_id,
            )
        )

    inicio_hoje, _fim = janela_dia_clinica_em_utc()
    hoje = inicio_hoje + timedelta(hours=10)  # 10h do dia da clínica, em UTC naive

    # -------------------------------------------------------------------
    # 1) Senha realocada HOJE: espera 1 (Recepção) 60 s, atendente 30 s,
    #    espera 2 (Coleta) 40 s, executor 70 s; permanência 200 s.
    # -------------------------------------------------------------------
    id_realocada = nova_senha(fila_coleta.id, "D0001", criada_em=hoje)
    db.session.get(Senha, id_realocada).local_id = sala.id  # finalizada mantém o Local (o atendimento final usa ele)
    log(id_realocada, "gerada", hoje, fila_recepcao.id)
    log(id_realocada, "chamada_senha", hoje + timedelta(seconds=60), fila_recepcao.id, guiche.id)
    log(id_realocada, "realocado", hoje + timedelta(seconds=90), fila_recepcao.id, guiche.id)
    log(id_realocada, "chamada_nome", hoje + timedelta(seconds=130), fila_coleta.id, sala.id)
    log(id_realocada, "fim_atendimento", hoje + timedelta(seconds=200), fila_coleta.id, sala.id)
    db.session.commit()

    ids = dict(empresa=empresa.id, unidade=unidade.id)


def kpis_de(empresa_id, unidade_id):
    with app.app_context():
        periodo = montar_periodo(empresa_id, [unidade_id])
        kpis = calcular_kpis(db.session.get(Empresa, empresa_id), [unidade_id], periodo)
        resumo = _resumo_tempos(tempos_por_senha(periodo.filtros)[0])
        return kpis, resumo


# ===========================================================================
# 1) Senha realocada: espera inicial = 1ª espera da senha (60 s), igual ao
#    Tempos por senha; executor 70 s; permanência 200 s.
# ===========================================================================
kpis, resumo = kpis_de(ids["empresa"], ids["unidade"])
tempos = kpis["tempos"]
assert tempos["espera_inicial"]["media_segundos"] == 60, tempos  # cartão antigo daria 50 (média dos 2 segmentos)
assert tempos["espera_inicial"]["mediana_segundos"] == 60 and tempos["espera_inicial"]["media_texto"] == "01:00"
assert tempos["atendimento_executor"]["media_segundos"] == 70, tempos
assert tempos["permanencia_total"]["media_segundos"] == 200 and tempos["permanencia_total"]["media_texto"] == "03:20"
for chave in ("espera_inicial", "atendimento_executor", "permanencia_total"):
    assert tempos[chave]["media_segundos"] == resumo[chave]["media"], chave
    assert tempos[chave]["mediana_segundos"] == resumo[chave]["mediana"], chave
assert kpis["geradas"] == 1 and kpis["atendidas"] == 1, kpis
ok("Senha realocada hoje: Espera inicial 60 s (1ª espera, não a média de 50 s dos 2 segmentos do cartão antigo), "
   "executor 70 s, permanência 200 s - iguais ao resumo do Tempos por senha")

# ===========================================================================
# 2) Sem nenhuma senha -> None (o template mostra "—"), nunca 0.
# ===========================================================================
with app.app_context():
    empresa_vazia = Empresa(nome="Clinica Sem Senhas Hoje")
    db.session.add(empresa_vazia)
    db.session.commit()
    unidade_vazia = Unidade(empresa_id=empresa_vazia.id, nome="U Vazia")
    db.session.add(unidade_vazia)
    db.session.commit()
    ids_vazia = (empresa_vazia.id, unidade_vazia.id)
kpis_vazio, _ = kpis_de(*ids_vazia)
for chave, t in kpis_vazio["tempos"].items():
    assert t["media_segundos"] is None and t["media_texto"] is None and t["quantidade"] == 0, (chave, t)
assert kpis_vazio["geradas"] == 0 and kpis_vazio["atendidas"] == 0
ok("Nenhuma senha: cartões de tempo None (\"—\" na tela), não 0")

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
    ids_hist = (empresa_hist.id, unidade_hist.id)

kpis_hist, _ = kpis_de(*ids_hist)
assert kpis_hist["tempos"]["espera_inicial"]["media_segundos"] == 80, kpis_hist
ok("Dado histórico (sem snapshot): Espera inicial cai no fallback (80 s), sem erro")

print()
print(f"TODOS OS {len(passos)} TESTES DOS CARTÕES DE TEMPO DO DASHBOARD PASSARAM")
