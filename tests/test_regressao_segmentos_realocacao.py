"""
Teste de regressão: segmentos de tempo de atendimento para senhas realocadas
múltiplas vezes, com o snapshot novo de fila_id/local_id em LogAtendimento
(adendo 10.62 - generaliza `_pares_inicio_realocado` pra
`_segmentos_intermediarios_realocacao`, medindo TODOS os hand-offs de uma
senha, não só o primeiro).

Diferente de tests/test_regressao_relatorios_recepcao_coleta.py (que cobre o
FALLBACK pra dado histórico, sem o snapshot), este arquivo grava fila_id/
local_id em CADA log de LogAtendimento - simulando dado gravado depois desta
mudança - e confirma a granularidade nova.

Cobre:
1. Senha com 3 realocações (Recepção -> Fila A -> Fila B -> Fila C -> Finalizado):
   4 segmentos no total, cada um com fila_id/local_id/tempo/operador corretos.
2. Senha com 0 realocações (fluxo simples: chamada -> finalizar): continua
   gerando 1 único segmento, exatamente como antes (sem linha extra).
3. Segmento de duração muito curta (poucos segundos): conta normalmente no
   tempo médio, sem nenhum filtro de "provável erro de clique".
4. Adendo 10.63 (achado em revisão do 10.62, não decisão original): MESMO
   operador, MESMA fila, MESMO local - uma vez concluindo (`fim_atendimento`),
   outra vez repassando (`realocado`) - gera DUAS linhas distintas
   (`tipo_fechamento` "conclusao"/"repasse"), cada uma com sua própria média,
   em vez de uma só linha misturando os dois tempos.

Constrói os logs_atendimento diretamente via ORM (não via rotas HTTP) e chama
tempo_atendimento()/Filtros diretamente - mesmo estilo de
test_regressao_relatorios_recepcao_coleta.py.

Execução: python -m tests.test_regressao_segmentos_realocacao (a partir da raiz
do projeto, com o virtualenv ativado).
"""
import os
from datetime import datetime, timedelta

os.environ.setdefault("SECRET_KEY", "test")
os.environ.setdefault("FLASK_ENV", "testing")

from app import create_app
from app.extensions import db
from app.models import Empresa, Fila, Local, LogAtendimento, Senha, Unidade, Usuario
from app.relatorios.helpers import Filtros
from app.relatorios.queries import TIPO_FECHAMENTO_CONCLUSAO, TIPO_FECHAMENTO_REPASSE, tempo_atendimento

app = create_app("testing")

passos = []


def ok(msg):
    passos.append(msg)
    print("OK:", msg)


with app.app_context():
    db.create_all()

    empresa = Empresa(nome="Clinica B")
    db.session.add(empresa)
    db.session.commit()

    unidade = Unidade(empresa_id=empresa.id, nome="Unidade B1")
    db.session.add(unidade)
    db.session.commit()

    def nova_fila(nome, sigla):
        f = Fila(empresa_id=empresa.id, unidade_id=unidade.id, nome=nome, sigla=sigla)
        db.session.add(f)
        db.session.commit()
        return f

    fila_recepcao = nova_fila("Recepção", "RC")
    fila_a = nova_fila("Fila A", "FA")
    fila_b = nova_fila("Fila B", "FB")
    fila_c = nova_fila("Fila C", "FC")
    fila_simples = nova_fila("Fila Simples", "FS")

    def novo_local(nome):
        l = Local(
            empresa_id=empresa.id, unidade_id=unidade.id, nome=nome,
            nome_exibicao=nome, identificador_admin=f"[UNI] {nome}",
        )
        db.session.add(l)
        db.session.commit()
        return l

    local_recepcao = novo_local("Guichê Recepção")
    local_a = novo_local("Guichê A")
    local_b = novo_local("Guichê B")
    local_c = novo_local("Guichê C")
    local_simples = novo_local("Guichê Simples")

    def novo_usuario(login):
        u = Usuario(empresa_id=empresa.id, nome=login, login=login)
        u.set_senha("senha123")
        u.unidades = [unidade]
        db.session.add(u)
        db.session.commit()
        return u.id

    fila_mista = nova_fila("Fila Mista", "FM")
    local_misto = novo_local("Guichê Misto")

    id_op_recepcao = novo_usuario("op_recepcao_seg")
    id_op_fila_a = novo_usuario("op_fila_a_seg")
    id_op_fila_b = novo_usuario("op_fila_b_seg")
    id_op_fila_c = novo_usuario("op_fila_c_seg")
    id_op_simples = novo_usuario("op_simples_seg")
    id_op_misto = novo_usuario("op_misto_seg")

    t0 = datetime.utcnow() - timedelta(hours=2)

    def nova_senha(fila_id, codigo, local_id=None):
        s = Senha(
            empresa_id=empresa.id, unidade_id=unidade.id, fila_id=fila_id,
            codigo=codigo, sequencial=1, prioridade=False, status="finalizada", local_id=local_id,
        )
        db.session.add(s)
        db.session.commit()
        return s.id

    def log(senha_id, evento, usuario_id, setor, timestamp, fila_id, local_id):
        db.session.add(
            LogAtendimento(
                senha_id=senha_id, evento=evento, usuario_id=usuario_id,
                unidade_id=unidade.id, setor=setor, timestamp=timestamp,
                fila_id=fila_id, local_id=local_id,
            )
        )

    # -------------------------------------------------------------------
    # Senha com 3 realocações: Recepção -> Fila A -> Fila B -> Fila C -> Finalizado.
    # 4 segmentos: 3 intermediários (via _segmentos_intermediarios_realocacao,
    # snapshot no próprio log 'realocado') + 1 final (via _pares_inicio_fim,
    # estado atual da Senha - que já é fila_c/local_c).
    # -------------------------------------------------------------------
    id_multi = nova_senha(fila_c.id, "M0001", local_id=local_c.id)
    log(id_multi, "chamada_senha", id_op_recepcao, "recepcao", t0, fila_recepcao.id, local_recepcao.id)
    log(id_multi, "realocado", id_op_recepcao, "recepcao", t0 + timedelta(seconds=100), fila_recepcao.id, local_recepcao.id)  # seg1: 100s
    log(id_multi, "chamada_senha", id_op_fila_a, "coleta", t0 + timedelta(seconds=110), fila_a.id, local_a.id)
    log(id_multi, "realocado", id_op_fila_a, "coleta", t0 + timedelta(seconds=190), fila_a.id, local_a.id)  # seg2: 80s
    log(id_multi, "chamada_senha", id_op_fila_b, "coleta", t0 + timedelta(seconds=200), fila_b.id, local_b.id)
    log(id_multi, "realocado", id_op_fila_b, "coleta", t0 + timedelta(seconds=203), fila_b.id, local_b.id)  # seg3: 3s (bem curto)
    log(id_multi, "chamada_senha", id_op_fila_c, "coleta", t0 + timedelta(seconds=210), fila_c.id, local_c.id)
    log(id_multi, "fim_atendimento", id_op_fila_c, "coleta", t0 + timedelta(seconds=270), fila_c.id, local_c.id)  # seg4: 60s

    # -------------------------------------------------------------------
    # Senha simples (0 realocações): chamada -> finalizar. Deve continuar
    # gerando exatamente 1 segmento, sem nenhuma linha extra.
    # -------------------------------------------------------------------
    id_simples = nova_senha(fila_simples.id, "S0001", local_id=local_simples.id)
    log(id_simples, "chamada_senha", id_op_simples, "recepcao", t0, fila_simples.id, local_simples.id)
    log(id_simples, "fim_atendimento", id_op_simples, "recepcao", t0 + timedelta(seconds=45), fila_simples.id, local_simples.id)

    # -------------------------------------------------------------------
    # Adendo 10.63: MESMO operador, MESMA fila, MESMO local - uma senha ele
    # CONCLUI (fim_atendimento) e outra ele REPASSA (realocado). Sem
    # tipo_fechamento na chave de agrupamento, essas duas médias (200s de
    # conclusão vs. 10s de repasse) cairiam juntas numa única linha.
    # -------------------------------------------------------------------
    id_misto_conclui = nova_senha(fila_mista.id, "FM001", local_id=local_misto.id)
    log(id_misto_conclui, "chamada_senha", id_op_misto, "recepcao", t0, fila_mista.id, local_misto.id)
    log(id_misto_conclui, "fim_atendimento", id_op_misto, "recepcao", t0 + timedelta(seconds=200), fila_mista.id, local_misto.id)

    id_misto_repassa = nova_senha(fila_c.id, "FM002", local_id=local_c.id)
    log(id_misto_repassa, "chamada_senha", id_op_misto, "recepcao", t0, fila_mista.id, local_misto.id)
    log(id_misto_repassa, "realocado", id_op_misto, "recepcao", t0 + timedelta(seconds=10), fila_mista.id, local_misto.id)

    db.session.commit()

    ids = dict(
        empresa=empresa.id, unidade=unidade.id,
        fila_recepcao=fila_recepcao.id, fila_a=fila_a.id, fila_b=fila_b.id, fila_c=fila_c.id,
        fila_simples=fila_simples.id, fila_mista=fila_mista.id,
        local_recepcao=local_recepcao.id, local_a=local_a.id, local_b=local_b.id, local_c=local_c.id,
        local_simples=local_simples.id, local_misto=local_misto.id,
        op_recepcao=id_op_recepcao, op_fila_a=id_op_fila_a, op_fila_b=id_op_fila_b,
        op_fila_c=id_op_fila_c, op_simples=id_op_simples, op_misto=id_op_misto,
    )

print("IDS", ids)


def linhas_relatorio():
    with app.app_context():
        filtros = Filtros(
            empresa_id=ids["empresa"], unidades_permitidas=None, unidade_id=None,
            fila_id=None, operador_id=None,
            data_inicio=datetime.utcnow() - timedelta(days=1),
            data_fim_exclusivo=datetime.utcnow() + timedelta(days=1),
            data_inicio_str="", data_fim_str="",
        )
        return tempo_atendimento(filtros)


def linha_de(linhas, operador_id):
    encontradas = [l for l in linhas if l["operador_id"] == operador_id]
    assert len(encontradas) == 1, (operador_id, encontradas)
    return encontradas[0]


def linha_de_tipo(linhas, operador_id, tipo_fechamento):
    encontradas = [
        l for l in linhas if l["operador_id"] == operador_id and l["tipo_fechamento"] == tipo_fechamento
    ]
    assert len(encontradas) == 1, (operador_id, tipo_fechamento, encontradas)
    return encontradas[0]


linhas = linhas_relatorio()

# ---------------------------------------------------------------------------
# 1) Senha com 3 realocações: 4 segmentos no total, cada um com fila/local/
#    tempo/operador corretos - inclusive o de 3 segundos (sem filtro de duração
#    mínima).
# ---------------------------------------------------------------------------
seg1 = linha_de(linhas, ids["op_recepcao"])
assert seg1["setor"] == "recepcao"
assert seg1["fila_id"] == ids["fila_recepcao"]
assert seg1["local_id"] == ids["local_recepcao"]
assert seg1["tipo_fechamento"] == TIPO_FECHAMENTO_REPASSE
assert seg1["tempo_medio_segundos"] == 100.0
assert seg1["quantidade"] == 1
ok("Segmento 1 (Recepção, chamada->1ª realocação): fila/local reais, 100s, operador correto, tipo_fechamento=repasse")

seg2 = linha_de(linhas, ids["op_fila_a"])
assert seg2["fila_id"] == ids["fila_a"]
assert seg2["local_id"] == ids["local_a"]
assert seg2["tipo_fechamento"] == TIPO_FECHAMENTO_REPASSE
assert seg2["tempo_medio_segundos"] == 80.0
ok("Segmento 2 (Fila A, 1ª->2ª realocação): fila/local reais, 80s, operador correto - hand-off intermediário que antes NÃO era medido")

seg3 = linha_de(linhas, ids["op_fila_b"])
assert seg3["fila_id"] == ids["fila_b"]
assert seg3["local_id"] == ids["local_b"]
assert seg3["tipo_fechamento"] == TIPO_FECHAMENTO_REPASSE
assert seg3["tempo_medio_segundos"] == 3.0
assert seg3["quantidade"] == 1
ok("Segmento 3 (Fila B, 2ª->3ª realocação): duração de só 3 segundos, contada normalmente - sem filtro de 'provável erro de clique'")

seg4 = linha_de(linhas, ids["op_fila_c"])
assert seg4["fila_id"] == ids["fila_c"]
assert seg4["local_id"] == ids["local_c"]
assert seg4["tipo_fechamento"] == TIPO_FECHAMENTO_CONCLUSAO
assert seg4["tempo_medio_segundos"] == 60.0
ok("Segmento 4 (Fila C, última chamada->fim_atendimento): fila/local reais, 60s, tipo_fechamento=conclusao - segmento final, via _pares_inicio_fim, sem mudança")

assert len({(l["operador_id"], l["fila_id"]) for l in linhas if l["operador_id"] in (
    ids["op_recepcao"], ids["op_fila_a"], ids["op_fila_b"], ids["op_fila_c"]
)}) == 4
ok("Os 4 segmentos são 4 linhas distintas (nenhum evento reaproveitado em dois segmentos)")

# ---------------------------------------------------------------------------
# 2) Senha simples (0 realocações): continua gerando exatamente 1 segmento.
# ---------------------------------------------------------------------------
seg_simples = linha_de(linhas, ids["op_simples"])
assert seg_simples["fila_id"] == ids["fila_simples"]
assert seg_simples["local_id"] == ids["local_simples"]
assert seg_simples["tipo_fechamento"] == TIPO_FECHAMENTO_CONCLUSAO
assert seg_simples["tempo_medio_segundos"] == 45.0
assert seg_simples["quantidade"] == 1
ok("Senha com 0 realocações: 1 único segmento, exatamente como antes - nenhuma linha extra gerada")

# ---------------------------------------------------------------------------
# 3) Adendo 10.63: mesmo operador, mesma fila, mesmo local - uma vez concluindo,
#    outra repassando - gera DUAS linhas distintas, não uma média misturada.
# ---------------------------------------------------------------------------
linhas_misto = [l for l in linhas if l["operador_id"] == ids["op_misto"]]
assert len(linhas_misto) == 2, linhas_misto
ok("Mesmo operador/fila/local com os dois tipos de fechamento: exatamente 2 linhas (não 1 linha misturada)")

misto_conclusao = linha_de_tipo(linhas, ids["op_misto"], TIPO_FECHAMENTO_CONCLUSAO)
assert misto_conclusao["fila_id"] == ids["fila_mista"]
assert misto_conclusao["local_id"] == ids["local_misto"]
assert misto_conclusao["tempo_medio_segundos"] == 200.0
assert misto_conclusao["quantidade"] == 1
ok("Linha 'conclusao' do operador misto: 200s - não contaminada pelo repasse de 10s")

misto_repasse = linha_de_tipo(linhas, ids["op_misto"], TIPO_FECHAMENTO_REPASSE)
assert misto_repasse["fila_id"] == ids["fila_mista"]
assert misto_repasse["local_id"] == ids["local_misto"]
assert misto_repasse["tempo_medio_segundos"] == 10.0
assert misto_repasse["quantidade"] == 1
ok("Linha 'repasse' do operador misto: 10s - não contaminada pela conclusão de 200s (bug do adendo 10.62 corrigido pelo 10.63)")

print()
print(f"TODOS OS {len(passos)} TESTES DE SEGMENTOS DE REALOCAÇÃO (DADO NOVO, ADENDOS 10.62/10.63) PASSARAM")
