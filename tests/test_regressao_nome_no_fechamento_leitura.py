"""
Teste de regressão (adendo 10.82, proteção da LEITURA): o 'chamada_nome' já
gravado colado a um fechamento (Nome digitado no Finalizar/Aguardar até o
10.82, no Realocar até o 10.62) é descartado por
`_descartar_nome_no_fechamento` logo depois de carregar os logs - sem isso o
segmento saía ~0 s e a visita contava 2 chamadas.

Regra: ignora o 'chamada_nome' quando o evento SEGUINTE da mesma senha é
'fim_atendimento', 'voltou_aguardar' ou 'realocado', do MESMO operador, até
2 s depois. Logs montados direto no ORM (dado antigo - a gravação não cria
mais 'chamada_nome' desde a 1ª parte do 10.82), com o relógio simulado em
08/10 12:00 de Brasília (patch de app.utils.tempo._instante_utc, padrão do
10.74) para o "hoje" do dashboard.

Senhas (segundos a partir de t0):
- A: chamada 60, nome 360 + fim 360 (0 s)       -> descartado; atendimento 300 s
- B: chamada 60, nome 120 + aguardar 121 (1 s)  -> descartado
- C: chamada 60, nome 180 + realocado 182 (2 s) -> descartado; repasse 122 s
- D: histórico (sem snapshot), nome 150 + realocado 150 -> descartado;
     1º segmento do fallback 90 s (não 0 s)
- E: nome 297 + fim 300 (3 s)                   -> MANTIDO
- F: nome de OUTRO operador 1 s antes do fim    -> MANTIDO
- G: nome como último evento da senha           -> MANTIDO, sem erro

Também: Ranking (volume e tempo), feed "últimas chamadas" (inclusive o corte
em `limite`), KPI "chamadas hoje" e gráfico por hora; e a prova de que
`_segmentos_espera_por_senha`/`tempos_por_senha` não precisam da função (o
nome colado vem sempre depois de uma chamada, sem espera aberta).

Execução: python -m tests.test_regressao_nome_no_fechamento_leitura (a partir
da raiz do projeto, com o virtualenv ativado).
"""
import os
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

os.environ.setdefault("SECRET_KEY", "test")
os.environ.setdefault("FLASK_ENV", "testing")

from app import create_app
from app.core.dashboard_queries import calcular_kpis, montar_periodo
from app.extensions import db
from app.models import Empresa, Fila, Local, LogAtendimento, Senha, Unidade, Usuario
from app.relatorios.helpers import Filtros
from app.relatorios.queries import (
    _descartar_nome_no_fechamento, _pares_inicio_fim, _segmentos_espera_por_senha,
    _segmentos_intermediarios_realocacao, ranking_operadores, tempo_atendimento, tempos_por_senha,
    ultimas_chamadas,
)

app = create_app("testing")

passos = []


def ok(msg):
    passos.append(msg)
    print("OK:", msg)


# 08/10 15:00 UTC = 12:00 de Brasília; logs a partir de 13:00 UTC do mesmo dia.
RELOGIO = datetime(2026, 10, 8, 15, 0, tzinfo=UTC)
T0 = datetime(2026, 10, 8, 13, 0)


def relogio():
    return patch("app.utils.tempo._instante_utc", return_value=RELOGIO)


with app.app_context():
    db.create_all()
    empresa = Empresa(nome="Clinica A")
    db.session.add(empresa)
    db.session.commit()
    unidade = Unidade(empresa_id=empresa.id, nome="Unidade A1")
    db.session.add(unidade)
    db.session.commit()
    f_rec = Fila(empresa_id=empresa.id, unidade_id=unidade.id, nome="Recepcao", sigla="RC")
    f_col = Fila(empresa_id=empresa.id, unidade_id=unidade.id, nome="Coleta", sigla="CL")
    db.session.add_all([f_rec, f_col])
    db.session.commit()
    local = Local(empresa_id=empresa.id, unidade_id=unidade.id, nome="G1", nome_exibicao="G1",
                  identificador_admin="[UNI] G1", tipo="recepcao")
    db.session.add(local)
    op1 = Usuario(empresa_id=empresa.id, nome="op1", login="op1")
    op2 = Usuario(empresa_id=empresa.id, nome="op2", login="op2")
    for u in (op1, op2):
        u.set_senha("senha123")
    db.session.add_all([op1, op2])
    db.session.commit()

    nomes_log = {}  # codigo -> id do log 'chamada_nome'

    def senha(codigo, seq, fila, status, eventos, snapshot=True):
        s = Senha(empresa_id=empresa.id, unidade_id=unidade.id, fila_id=fila.id, codigo=codigo, sequencial=seq,
                  prioridade=False, status=status, criada_em=T0, local_id=local.id)
        db.session.add(s)
        db.session.commit()
        for evento, segundos, operador, fila_evt in eventos:
            log = LogAtendimento(
                senha_id=s.id, evento=evento, timestamp=T0 + timedelta(seconds=segundos),
                usuario_id=operador.id if operador else None, unidade_id=unidade.id,
                fila_id=fila_evt.id if snapshot else None, local_id=local.id if snapshot and operador else None,
            )
            db.session.add(log)
            db.session.commit()
            if evento == "chamada_nome":
                nomes_log[codigo] = log.id
        return s.id

    senha("A01", 1, f_rec, "finalizada", [("gerada", 0, None, f_rec), ("chamada_senha", 60, op1, f_rec),
                                          ("chamada_nome", 360, op1, f_rec), ("fim_atendimento", 360, op1, f_rec)])
    senha("B01", 2, f_rec, "finalizada", [("gerada", 0, None, f_rec), ("chamada_senha", 60, op1, f_rec),
                                          ("chamada_nome", 120, op1, f_rec), ("voltou_aguardar", 121, op1, f_rec),
                                          ("chamada_senha", 200, op1, f_rec), ("fim_atendimento", 500, op1, f_rec)])
    senha("C01", 3, f_col, "finalizada", [("gerada", 0, None, f_rec), ("chamada_senha", 60, op1, f_rec),
                                          ("chamada_nome", 180, op1, f_rec), ("realocado", 182, op1, f_rec),
                                          ("chamada_senha", 240, op2, f_col), ("fim_atendimento", 300, op2, f_col)])
    senha("D01", 4, f_col, "finalizada", [("gerada", 0, None, f_rec), ("chamada_senha", 60, op1, f_rec),
                                          ("chamada_nome", 150, op1, f_rec), ("realocado", 150, op1, f_rec),
                                          ("chamada_senha", 200, op2, f_col), ("fim_atendimento", 400, op2, f_col)],
          snapshot=False)
    senha("E01", 5, f_rec, "finalizada", [("gerada", 0, None, f_rec), ("chamada_senha", 60, op1, f_rec),
                                          ("chamada_nome", 297, op1, f_rec), ("fim_atendimento", 300, op1, f_rec)])
    senha("F01", 6, f_rec, "finalizada", [("gerada", 0, None, f_rec), ("chamada_senha", 60, op1, f_rec),
                                          ("chamada_nome", 299, op2, f_rec), ("fim_atendimento", 300, op1, f_rec)])
    senha("G01", 7, f_rec, "chamada", [("gerada", 0, None, f_rec), ("chamada_senha", 60, op1, f_rec),
                                       ("chamada_nome", 100, op1, f_rec)])

    ids = dict(empresa=empresa.id, unidade=unidade.id, op1=op1.id, op2=op2.id)


def filtros():
    return Filtros(
        empresa_id=ids["empresa"], unidades_permitidas=None, unidade_id=None, fila_id=None, operador_id=None,
        data_inicio=datetime(2026, 10, 8), data_fim_exclusivo=datetime(2026, 10, 9),
        data_inicio_str="", data_fim_str="",
    )


def codigo_de(log):
    return db.session.get(Senha, log.senha_id).codigo


with app.app_context():
    # 1) A regra em si
    todos = LogAtendimento.query.filter_by(evento="chamada_nome").all()
    mantidos = {codigo_de(l) for l in _descartar_nome_no_fechamento(todos)}
    assert mantidos == {"E01", "F01", "G01"}, mantidos
    ok("Regra: colado a fim_atendimento (A), voltou_aguardar (B) e realocado (C, D) descartado; "
       "3 s (E), outro operador (F) e último evento (G) mantidos")

    # 2) Atendimento final e repasses
    pares = {codigo_de(i): (f.timestamp - i.timestamp).total_seconds() for i, f, *_ in _pares_inicio_fim(filtros())}
    assert pares["A01"] == 300.0, pares
    assert pares["E01"] == 3.0 and pares["F01"] == 1.0, pares  # nome mantido continua sendo o início
    ok("_pares_inicio_fim: A = 300 s (não 0 s); E e F, com o nome mantido, medem a partir dele")

    repasses = {codigo_de(i): (r.timestamp - i.timestamp).total_seconds()
                for i, r, *_ in _segmentos_intermediarios_realocacao(filtros())}
    assert repasses["C01"] == 122.0, repasses
    assert repasses["D01"] == 90.0, repasses
    ok("_segmentos_intermediarios_realocacao: C (snapshot) = 122 s e D (fallback histórico, pré-10.62) = 90 s - não 0 s")

    linha = next(l for l in tempo_atendimento(filtros())
                 if l["operador_id"] == ids["op1"] and l["tipo_fechamento"] == "repasse" and l["fila_id"] is None)
    assert linha["tempo_medio_segundos"] == 90.0, linha
    ok("tempo_atendimento: linha histórica de Repasse de op1 = 90 s")

    # 3) Ranking: volume e tempo
    ranking = {r["operador_id"]: r for r in ranking_operadores(filtros())}
    # op1: A1 B2 C1 D1 E2 F1 G2 = 10 chamadas; op2: C1 D1 F1 = 3
    assert ranking[ids["op1"]]["volume"] == 10, ranking
    assert ranking[ids["op2"]]["volume"] == 3, ranking
    # tempo (início por quem chamou): op1 = A 300, B 300, E 3; op2 = C 60, D 200, F 1
    assert ranking[ids["op1"]]["tempo_medio_segundos"] == 201.0, ranking
    assert ranking[ids["op2"]]["tempo_medio_segundos"] == 87.0, ranking
    ok("Ranking: volume 10/3 (1 chamada por visita no dado antigo) e tempo médio 201 s/87 s")

    # 4) Esperas e Tempos por senha não precisam da função
    esperas = {}
    for abertura, fechamento, *_ in _segmentos_espera_por_senha(filtros()):
        esperas.setdefault(codigo_de(abertura), []).append((fechamento.timestamp - abertura.timestamp).total_seconds())
    assert esperas["A01"] == [60.0] and esperas["B01"] == [60.0, 79.0] and esperas["C01"] == [60.0, 58.0], esperas
    assert all(f.evento == "chamada_senha" for _a, f, *_ in _segmentos_espera_por_senha(filtros())
               if codigo_de(f) in ("A01", "B01", "C01", "D01"))
    ok("_segmentos_espera_por_senha: nenhum nome colado fecha espera (não há espera aberta antes dele) - fica de fora")

    por_senha = {l["codigo"]: l for l in tempos_por_senha(filtros())[0]}
    assert por_senha["A01"]["atendimento_total_segundos"] == 300.0
    assert por_senha["C01"]["atendimento_total_segundos"] == 182.0 and por_senha["C01"]["etapas"] == 2
    assert por_senha["B01"]["atendimento_total_segundos"] == 300.0
    assert por_senha["D01"]["situacao"] == "Histórico parcial"
    assert por_senha["G01"]["situacao"] == "Chamada, não finalizada"
    ok("tempos_por_senha: corrigido pelas funções de segmento (A 300 s, C 122+60 s); situações sem mudança - fica de fora")

    # 5) Feed, KPI e gráfico do dashboard
    with relogio():
        feed = ultimas_chamadas(db.session.get(Empresa, ids["empresa"]), [ids["unidade"]])
        assert len(feed) == 13, len(feed)
        assert len(ultimas_chamadas(db.session.get(Empresa, ids["empresa"]), [ids["unidade"]], limite=3)) == 3
        # adendo 10.86: o cartão "Chamadas hoje" e o gráfico de chamadas saíram -
        # o Dashboard conta por SENHA ("Senhas geradas"), então a mesma visita
        # nunca conta duas vezes, com ou sem 'chamada_nome' colado.
        kpis = calcular_kpis(db.session.get(Empresa, ids["empresa"]), [ids["unidade"]],
                             montar_periodo(ids["empresa"], [ids["unidade"]]), com_tempos=False)
        assert kpis["geradas"] == Senha.query.filter_by(unidade_id=ids["unidade"]).count(), kpis
    ok("Feed (13, e o corte em limite=3): 1 chamada por visita; Dashboard conta por senha (10.86)")

print()
print(f"TODOS OS {len(passos)} TESTES DE 'NOME NO FECHAMENTO (LEITURA)' PASSARAM")
