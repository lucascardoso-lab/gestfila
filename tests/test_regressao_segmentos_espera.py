"""
Teste de regressão: `tempo_medio_espera` (app/relatorios/queries.py, relatório
"Tempo médio de espera") media só 1 intervalo por senha ('gerada' até a 1ª
'chamada_senha', com resultado NÃO-DETERMINÍSTICO se houvesse mais de uma) -
mesma classe de bug já corrigida em `tempo_atendimento` pelos adendos 10.62/
10.63, agora neste relatório irmão (nunca tinha sido tocada, não era o escopo
daquela correção).

3 bugs confirmados na versão anterior (ver docstring de
`_segmentos_espera_por_senha` pro detalhe completo):
1. `eventos_por_senha[senha_id][evento] = timestamp` sobrescrevia em caso de
   múltiplos 'chamada_senha' pra mesma senha, sem `.order_by()` garantindo
   determinismo.
2. Buscava só `("gerada", "chamada_senha")` - nunca 'chamada_nome' (2º
   estágio, ex. Coleta), nunca 'realocado', nunca 'voltou_aguardar'.
3. Consequência: só 1 intervalo por senha, ignorando o resto da jornada.

Decisões de produto confirmadas com o cliente (diferente do 10.63):
- Cada intervalo de espera é atribuído à fila de DESTINO (onde o paciente
  passa a esperar) - INVERSO da convenção de `tempo_atendimento` (que usa
  ORIGEM) - ver docstring de `_segmentos_espera_por_senha` pro porquê (não é
  inconsistência, é a mesma regra "usa o snapshot do evento que fecha o
  segmento" aplicada a eventos de fechamento diferentes).
- SEM `tipo_fechamento` - a fila de destino já separa os estágios.
- Dado histórico (sem o snapshot novo) cai no comportamento ANTIGO (1
  intervalo só, 'gerada'->1ª 'chamada_senha'), mesmo fallback "por senha" do
  10.62 - só a não-determinismo do bug 1 é corrigida também para esses casos.

Cobre:
1. Senha com 2 realocações -> 3 intervalos de espera (gerada->1ª chamada,
   realocado->2ª chamada, realocado->3ª chamada), cada um na fila de DESTINO.
2. Senha sem realocação -> continua 1 intervalo só (sem regressão).
3. Senha com MÚLTIPLOS 'chamada_senha' pra mesma fila (ex.: 'voltou_aguardar'
   seguido de re-chamada) - resultado determinístico (bug 1 corrigido), soma
   os DOIS intervalos de espera daquela fila (não descarta nenhum).
4. Segmento fechado por 'chamada_nome' (não só 'chamada_senha' - bug 2
   corrigido).
5. Dado histórico simulado (sem o snapshot novo em NENHUM evento) - cai no
   fallback de 1 intervalo, sem erro, fila/unidade do estado ATUAL da Senha.

Execução: python -m tests.test_regressao_segmentos_espera (a partir da raiz
do projeto, com o virtualenv ativado).
"""
import os
from datetime import datetime, timedelta

os.environ.setdefault("SECRET_KEY", "test")
os.environ.setdefault("FLASK_ENV", "testing")

from app import create_app
from app.extensions import db
from app.models import Empresa, Fila, LogAtendimento, Senha, Unidade
from app.relatorios.helpers import Filtros
from app.relatorios.queries import tempo_medio_espera

app = create_app("testing")

passos = []


def ok(msg):
    passos.append(msg)
    print("OK:", msg)


with app.app_context():
    db.create_all()

    empresa = Empresa(nome="Clinica Espera")
    db.session.add(empresa)
    db.session.commit()

    unidade = Unidade(empresa_id=empresa.id, nome="Unidade E1")
    db.session.add(unidade)
    db.session.commit()

    def nova_fila(nome, sigla):
        f = Fila(empresa_id=empresa.id, unidade_id=unidade.id, nome=nome, sigla=sigla)
        db.session.add(f)
        db.session.commit()
        return f

    fila_recepcao = nova_fila("Recepção", "RC")
    fila_triagem = nova_fila("Triagem", "TR")
    fila_coleta = nova_fila("Coleta", "CL")
    fila_simples = nova_fila("Fila Simples", "FS")
    fila_re_chamada = nova_fila("Fila Re-chamada", "RCH")
    fila_historica = nova_fila("Fila Historica", "HIS")

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

    t0 = datetime.utcnow() - timedelta(hours=3)

    # -------------------------------------------------------------------
    # 1) Senha com 2 realocações: Recepção -> Triagem -> Coleta -> Finalizado.
    #    3 intervalos de espera: gerada->1ª chamada (Recepção, 50s),
    #    realocado->2ª chamada (Triagem, 30s), realocado->3ª chamada
    #    (Coleta, 20s) - cada um na fila de DESTINO (onde a espera terminou).
    # -------------------------------------------------------------------
    id_multi = nova_senha(fila_recepcao.id, "M0001", criada_em=t0)
    log(id_multi, "gerada", t0, fila_recepcao.id)
    log(id_multi, "chamada_senha", t0 + timedelta(seconds=50), fila_recepcao.id)  # espera 1: 50s, Recepção
    log(id_multi, "realocado", t0 + timedelta(seconds=100), fila_recepcao.id)  # fecha atendimento na Recepção (origem)
    log(id_multi, "chamada_senha", t0 + timedelta(seconds=130), fila_triagem.id)  # espera 2: 30s, Triagem
    log(id_multi, "realocado", t0 + timedelta(seconds=160), fila_triagem.id)
    log(id_multi, "chamada_nome", t0 + timedelta(seconds=180), fila_coleta.id)  # espera 3: 20s, Coleta (via chamada_nome)
    log(id_multi, "fim_atendimento", t0 + timedelta(seconds=240), fila_coleta.id)

    # -------------------------------------------------------------------
    # 2) Senha sem realocação: gerada -> 1 chamada -> finalizado. Continua
    #    gerando exatamente 1 intervalo de espera, sem regressão.
    # -------------------------------------------------------------------
    id_simples = nova_senha(fila_simples.id, "S0001", criada_em=t0)
    log(id_simples, "gerada", t0, fila_simples.id)
    log(id_simples, "chamada_senha", t0 + timedelta(seconds=90), fila_simples.id)  # espera: 90s
    log(id_simples, "fim_atendimento", t0 + timedelta(seconds=150), fila_simples.id)

    # -------------------------------------------------------------------
    # 3) Senha com MÚLTIPLOS 'chamada_senha' na MESMA fila (voltou_aguardar
    #    seguido de re-chamada) - o dict-overwrite antigo sobrescrevia um dos
    #    dois; agora os DOIS intervalos de espera daquela fila são somados,
    #    de forma determinística.
    # -------------------------------------------------------------------
    id_rechamada = nova_senha(fila_re_chamada.id, "R0001", criada_em=t0)
    log(id_rechamada, "gerada", t0, fila_re_chamada.id)
    log(id_rechamada, "chamada_senha", t0 + timedelta(seconds=40), fila_re_chamada.id)  # espera A: 40s
    log(id_rechamada, "voltou_aguardar", t0 + timedelta(seconds=45), fila_re_chamada.id)
    log(id_rechamada, "chamada_senha", t0 + timedelta(seconds=105), fila_re_chamada.id)  # espera B: 60s (45->105)
    log(id_rechamada, "fim_atendimento", t0 + timedelta(seconds=200), fila_re_chamada.id)

    # -------------------------------------------------------------------
    # 5) Dado histórico: NENHUM evento tem fila_id (simula logs gravados
    #    antes do snapshot existir) - cai no fallback de 1 intervalo só
    #    (gerada -> 1ª chamada_senha), fila/unidade do estado ATUAL da Senha.
    #    Tem inclusive um 'realocado'/'chamada_nome' no meio (ignorados pelo
    #    fallback, igual ao escopo original da consulta).
    # -------------------------------------------------------------------
    id_historico = nova_senha(fila_historica.id, "H0001", criada_em=t0)
    log(id_historico, "gerada", t0, fila_id=None)
    log(id_historico, "chamada_senha", t0 + timedelta(seconds=70), fila_id=None)  # espera esperada: 70s
    log(id_historico, "realocado", t0 + timedelta(seconds=120), fila_id=None)
    log(id_historico, "chamada_nome", t0 + timedelta(seconds=140), fila_id=None)  # ignorado pelo fallback
    log(id_historico, "fim_atendimento", t0 + timedelta(seconds=200), fila_id=None)

    db.session.commit()

    ids = dict(
        empresa=empresa.id, unidade=unidade.id,
        fila_recepcao=fila_recepcao.id, fila_triagem=fila_triagem.id, fila_coleta=fila_coleta.id,
        fila_simples=fila_simples.id, fila_re_chamada=fila_re_chamada.id, fila_historica=fila_historica.id,
    )


def relatorio():
    with app.app_context():
        filtros = Filtros(
            empresa_id=ids["empresa"], unidades_permitidas=None, unidade_id=None,
            fila_id=None, operador_id=None,
            data_inicio=datetime.utcnow() - timedelta(days=1),
            data_fim_exclusivo=datetime.utcnow() + timedelta(days=1),
            data_inicio_str="", data_fim_str="",
        )
        return tempo_medio_espera(filtros)


def linha_de(linhas, fila_id):
    encontradas = [l for l in linhas if l["fila_id"] == fila_id]
    assert len(encontradas) == 1, (fila_id, encontradas)
    return encontradas[0]


linhas = relatorio()

# ===========================================================================
# 1) 3 intervalos de espera da senha com 2 realocações, cada um na fila de
#    DESTINO (onde a espera terminou, não de onde a senha veio).
# ===========================================================================
espera_recepcao = linha_de(linhas, ids["fila_recepcao"])
assert espera_recepcao["tempo_medio_segundos"] == 50.0
assert espera_recepcao["quantidade"] == 1
ok("Espera 1 (gerada->1ª chamada, Recepção): 50s, atribuída à fila onde a chamada ocorreu")

espera_triagem = linha_de(linhas, ids["fila_triagem"])
assert espera_triagem["tempo_medio_segundos"] == 30.0
ok("Espera 2 (realocado->2ª chamada, Triagem): 30s - hand-off intermediário que antes NÃO era medido")

espera_coleta = linha_de(linhas, ids["fila_coleta"])
assert espera_coleta["tempo_medio_segundos"] == 20.0
ok("Espera 3 (realocado->3ª chamada via chamada_nome, Coleta): 20s - fechada por chamada_nome, não só chamada_senha (bug 2 corrigido)")

# ===========================================================================
# 2) Senha sem realocação: continua gerando exatamente 1 intervalo.
# ===========================================================================
espera_simples = linha_de(linhas, ids["fila_simples"])
assert espera_simples["tempo_medio_segundos"] == 90.0
assert espera_simples["quantidade"] == 1
ok("Senha sem realocação: 1 único intervalo de espera, exatamente como antes - sem regressão")

# ===========================================================================
# 3) Múltiplos 'chamada_senha' na MESMA fila (voltou_aguardar + re-chamada):
#    os DOIS intervalos (40s e 60s) são somados na média, de forma
#    determinística - não há mais overwrite silencioso de um deles.
# ===========================================================================
espera_rechamada = linha_de(linhas, ids["fila_re_chamada"])
assert espera_rechamada["quantidade"] == 2
assert espera_rechamada["tempo_medio_segundos"] == 50.0  # média de 40s e 60s
ok("Múltiplos 'chamada_senha' na mesma fila: 2 intervalos (40s, 60s) contados - bug do dict-overwrite corrigido")

# ===========================================================================
# 5) Dado histórico (sem snapshot em nenhum evento): fallback de 1 intervalo
#    só (gerada->1ª chamada_senha), fila/unidade do estado ATUAL da Senha.
# ===========================================================================
espera_historica = linha_de(linhas, ids["fila_historica"])
assert espera_historica["unidade_id"] == ids["unidade"]
assert espera_historica["tempo_medio_segundos"] == 70.0
assert espera_historica["quantidade"] == 1
ok("Dado histórico (sem snapshot): fallback de 1 intervalo só (70s), sem erro, fila/unidade do estado atual da Senha")

print()
print(f"TODOS OS {len(passos)} TESTES DE SEGMENTOS DE ESPERA PASSARAM")
