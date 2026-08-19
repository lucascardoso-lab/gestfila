"""
Teste de regressão: correção da causa raiz identificada na investigação do
relatório de tempo de atendimento (seção 7 do BLUEPRINT) - o botão Realocar
(seção 10.2) editava o campo Nome e gravava um log 'chamada_nome' colado ao
'realocado' (mesma request), que _segmentos_intermediarios_realocacao() escolhia por engano
como "início" do segmento de atendimento da fila de origem, medindo esse
segmento como ~0s em vez do tempo real.

Correção: talvez_atualizar_nome() (app/atendimento/helpers.py) ganhou o
parâmetro `registrar_como_chamada=False`, usado só por realocar()/
realocar_local() (chamada.py) - continua gravando nome_paciente/
chamada_nome_em na senha, mas não cria mais o LogAtendimento(evento=
"chamada_nome") nesse ponto. Efeito colateral deliberado (documentado no
BLUEPRINT, seção 7): essa edição de nome no Realocar também para de contar
como uma chamada em ranking_operadores() (volume) e no feed de
ultimas_chamadas() do dashboard.

Cobre dois cenários via requests HTTP reais (não logs forjados direto no ORM),
para provar que o fluxo de produção (rotas -> talvez_atualizar_nome) está
corrigido de fato, não só a query isolada:

1. Cenário original do bug: Chamar -> Realocar (com Nome preenchido) -> Coleta
   chama depois -> Finaliza. Confirma que NENHUM log 'chamada_nome' é gravado
   no Realocar, e que _segmentos_intermediarios_realocacao()/tempo_atendimento() medem o
   tempo real do segmento da Recepção (não ~0s).
2. Cenário de idas-e-vindas (Chamar -> Aguardar com Nome -> Chamar de novo ->
   Realocar com Nome de novo): confirma que a correção NÃO quebrou o
   comportamento existente de pegar a chamada mais recente antes do handoff -
   o 'chamada_nome' do Aguardar (legítimo, não suprimido) fica mais antigo que
   o 'chamada_senha' da re-chamada, e o 'chamada_nome' do Realocar (suprimido
   pela correção) não aparece para competir - o início correto continua sendo
   a re-chamada, não a primeira chamada nem o log do Aguardar.

Os timestamps dos logs são reajustados diretamente via ORM após as chamadas
HTTP reais (que já exercitam o código de produção) só para tornar as
durações medidas determinísticas nas asserções - mesmo princípio de
test_regressao_relatorios_recepcao_coleta.py.

Execução: python -m tests.test_regressao_realocar_nao_conta_como_chamada (a
partir da raiz do projeto, com o virtualenv ativado).
"""
import os
from datetime import datetime, timedelta

os.environ.setdefault("SECRET_KEY", "test")
os.environ.setdefault("FLASK_ENV", "testing")

from app import create_app
from app.extensions import db
from app.models import Empresa, Fila, Local, LogAtendimento, Perfil, Senha, Unidade, Usuario
from app.relatorios.helpers import Filtros
from app.relatorios.queries import _segmentos_intermediarios_realocacao, tempo_atendimento

app = create_app("testing")

passos = []


def ok(msg):
    passos.append(msg)
    print("OK:", msg)


with app.app_context():
    db.create_all()

    perfil_recepcao = Perfil(nome="atendente")
    perfil_coleta = Perfil(nome="executor")
    db.session.add_all([perfil_recepcao, perfil_coleta])
    db.session.commit()

    empresa = Empresa(nome="Clinica A")
    db.session.add(empresa)
    db.session.commit()

    unidade = Unidade(empresa_id=empresa.id, nome="Unidade A1")
    db.session.add(unidade)
    db.session.commit()

    fila_recepcao = Fila(empresa_id=empresa.id, unidade_id=unidade.id, nome="Fila Recepcao", sigla="RC")
    fila_coleta = Fila(empresa_id=empresa.id, unidade_id=unidade.id, nome="Fila Coleta", sigla="CL")
    db.session.add_all([fila_recepcao, fila_coleta])
    db.session.commit()

    local_recepcao = Local(
        empresa_id=empresa.id, unidade_id=unidade.id, nome="Guiche Recepcao",
        nome_exibicao="Guiche Recepcao", identificador_admin="[UNI] Guiche Recepcao",
    )
    local_coleta = Local(
        empresa_id=empresa.id, unidade_id=unidade.id, nome="Guiche Coleta",
        nome_exibicao="Guiche Coleta", identificador_admin="[UNI] Guiche Coleta",
    )
    db.session.add_all([local_recepcao, local_coleta])
    db.session.commit()

    senha1 = Senha(
        empresa_id=empresa.id, unidade_id=unidade.id, fila_id=fila_recepcao.id,
        codigo="RC001", sequencial=1, prioridade=False, status="aguardando",
    )
    senha2 = Senha(
        empresa_id=empresa.id, unidade_id=unidade.id, fila_id=fila_recepcao.id,
        codigo="RC002", sequencial=2, prioridade=False, status="aguardando",
    )
    db.session.add_all([senha1, senha2])
    db.session.commit()

    def novo_usuario(login, perfis):
        u = Usuario(empresa_id=empresa.id, nome=login, login=login)
        u.set_senha("senha123")
        u.perfis = perfis
        u.unidades = [unidade]
        db.session.add(u)
        db.session.commit()
        return u.id

    id_recepcao = novo_usuario("recepcao_realoc_nome", [perfil_recepcao])
    id_coleta = novo_usuario("coleta_realoc_nome", [perfil_coleta])

    ids = dict(
        empresa=empresa.id, fila_recepcao=fila_recepcao.id, fila_coleta=fila_coleta.id,
        local_recepcao=local_recepcao.id, local_coleta=local_coleta.id,
        senha1=senha1.id, senha2=senha2.id, recepcao=id_recepcao, coleta=id_coleta,
    )

print("IDS", ids)


def login_como(login):
    c = app.test_client()
    resp = c.post("/login", data={"login": login, "senha": "senha123"}, follow_redirects=True)
    assert resp.status_code == 200, (login, resp.status_code)
    return c


c_recepcao = login_como("recepcao_realoc_nome")
with c_recepcao.session_transaction() as sess:
    sess["atendimento_local_id"] = ids["local_recepcao"]

c_coleta = login_como("coleta_realoc_nome")
with c_coleta.session_transaction() as sess:
    sess["atendimento_local_id"] = ids["local_coleta"]


def logs_da_senha(senha_id):
    with app.app_context():
        return (
            LogAtendimento.query.filter_by(senha_id=senha_id)
            .order_by(LogAtendimento.id.asc())
            .all()
        )


def _filtros():
    return Filtros(
        empresa_id=ids["empresa"], unidades_permitidas=None, unidade_id=None,
        fila_id=None, operador_id=None,
        data_inicio=datetime.utcnow() - timedelta(days=1),
        data_fim_exclusivo=datetime.utcnow() + timedelta(days=1),
        data_inicio_str="", data_fim_str="",
    )


# ---------------------------------------------------------------------------
# 1) Cenário original do bug: Chamar -> Realocar (com Nome) -> Coleta chama ->
#    Finaliza. Confirma que 'chamada_nome' NÃO é gravado no Realocar, e que o
#    tempo do segmento da Recepção é medido corretamente (não ~0s).
# ---------------------------------------------------------------------------
resp = c_recepcao.post(
    f"/atendimento/recepcao/filas/{ids['fila_recepcao']}/chamar",
    data={"codigo": "RC001"}, follow_redirects=True,
)
assert resp.status_code == 200

resp = c_recepcao.post(
    f"/atendimento/recepcao/filas/{ids['fila_recepcao']}/realocar",
    data={"fila_destino_id": ids["fila_coleta"], "nome": "Maria Realocada"},
    follow_redirects=True,
)
assert resp.status_code == 200

with app.app_context():
    senha1_db = db.session.get(Senha, ids["senha1"])
    assert senha1_db.nome_paciente == "Maria Realocada"
    assert senha1_db.fila_id == ids["fila_coleta"]
ok("'Realocar' com Nome preenchido: nome_paciente gravado normalmente, senha move para a Fila Coleta")

logs1 = logs_da_senha(ids["senha1"])
eventos1 = [log.evento for log in logs1]
assert eventos1.count("chamada_nome") == 0
assert "realocado" in eventos1
ok("'Realocar' com Nome preenchido NÃO grava log 'chamada_nome' (correção da causa raiz) - só 'realocado'")

resp = c_coleta.post(
    f"/atendimento/coleta/filas/{ids['fila_coleta']}/chamar",
    data={"codigo": "RC001"}, follow_redirects=True,
)
assert resp.status_code == 200

resp = c_coleta.post(f"/atendimento/coleta/filas/{ids['fila_coleta']}/finalizar", data={}, follow_redirects=True)
assert resp.status_code == 200

# Reajusta os timestamps (na MESMA ordem cronológica em que os logs reais foram
# criados) para tornar a duração do segmento determinística: chamada_senha
# (Recepção) -> realocado 120s depois -> chamada_senha (Coleta) 80s depois ->
# fim_atendimento 180s depois. O segmento da Recepção esperado = 120s.
with app.app_context():
    logs1 = (
        LogAtendimento.query.filter_by(senha_id=ids["senha1"])
        .order_by(LogAtendimento.id.asc())
        .all()
    )
    assert [log.evento for log in logs1] == ["chamada_senha", "realocado", "chamada_senha", "fim_atendimento"], (
        [log.evento for log in logs1]
    )
    t0 = datetime.utcnow() - timedelta(hours=1)
    offsets = [0, 120, 200, 380]
    for log, offset in zip(logs1, offsets):
        log.timestamp = t0 + timedelta(seconds=offset)
    db.session.commit()

with app.app_context():
    segmentos = _segmentos_intermediarios_realocacao(_filtros())
    assert len(segmentos) == 1
    inicio, realocado_log, fila_id, local_id, _unidade_id = segmentos[0]
    assert inicio.evento == "chamada_senha"
    assert fila_id == ids["fila_recepcao"]  # snapshot real (adendo 10.62) - log gravado via rota HTTP
    assert local_id == ids["local_recepcao"]
    duracao = (realocado_log.timestamp - inicio.timestamp).total_seconds()
    assert duracao == 120.0, duracao
ok("_segmentos_intermediarios_realocacao: 'início' é o chamada_senha genuíno da Recepção (não o chamada_nome do Realocar, que nem existe mais) - segmento medido = 120s, não ~0s, com fila/local reais")

with app.app_context():
    linhas = tempo_atendimento(_filtros())
    linha_recepcao = next(l for l in linhas if l["operador_id"] == ids["recepcao"] and l["setor"] == "recepcao")
    assert linha_recepcao["tempo_medio_segundos"] == 120.0
    linha_coleta = next(l for l in linhas if l["operador_id"] == ids["coleta"] and l["setor"] == "coleta")
    assert linha_coleta["tempo_medio_segundos"] == 180.0
ok("tempo_atendimento(): segmento da Recepção = 120s (real) e segmento da Coleta = 180s (real) - bug original corrigido")


# ---------------------------------------------------------------------------
# 2) Cenário de idas-e-vindas: Chamar -> Aguardar (com Nome, log LEGÍTIMO,
#    continua sendo gravado) -> Chamar de novo -> Realocar (com Nome, log
#    SUPRIMIDO pela correção). O 'início' deve continuar sendo a RE-chamada
#    (mais recente), não a 1ª chamada nem o chamada_nome do Aguardar.
# ---------------------------------------------------------------------------
resp = c_recepcao.post(
    f"/atendimento/recepcao/filas/{ids['fila_recepcao']}/chamar",
    data={"codigo": "RC002"}, follow_redirects=True,
)
assert resp.status_code == 200

resp = c_recepcao.post(
    f"/atendimento/recepcao/filas/{ids['fila_recepcao']}/aguardar",
    data={"nome": "Joao Preliminar"}, follow_redirects=True,
)
assert resp.status_code == 200

with app.app_context():
    senha2_db = db.session.get(Senha, ids["senha2"])
    assert senha2_db.status == "aguardando"
    assert senha2_db.nome_paciente == "Joao Preliminar"
ok("'Aguardar' com Nome preenchido: continua gravando nome_paciente e log 'chamada_nome' normalmente (não afetado pela correção)")

resp = c_recepcao.post(
    f"/atendimento/recepcao/filas/{ids['fila_recepcao']}/chamar",
    data={"codigo": "RC002"}, follow_redirects=True,
)
assert resp.status_code == 200

resp = c_recepcao.post(
    f"/atendimento/recepcao/filas/{ids['fila_recepcao']}/realocar",
    data={"fila_destino_id": ids["fila_coleta"], "nome": "Joao Realocado"},
    follow_redirects=True,
)
assert resp.status_code == 200

with app.app_context():
    logs2 = (
        LogAtendimento.query.filter_by(senha_id=ids["senha2"])
        .order_by(LogAtendimento.id.asc())
        .all()
    )
    eventos2 = [log.evento for log in logs2]
    assert eventos2 == [
        "chamada_senha",   # 1ª chamada
        "chamada_nome",    # Aguardar (legítimo, não suprimido)
        "voltou_aguardar",
        "chamada_senha",   # RE-chamada
        "realocado",       # Realocar - SEM 'chamada_nome' (suprimido pela correção)
    ], eventos2
ok("Idas-e-vindas: 'chamada_nome' do Aguardar continua gravado; 'chamada_nome' do Realocar continua suprimido")

with app.app_context():
    logs2 = (
        LogAtendimento.query.filter_by(senha_id=ids["senha2"])
        .order_by(LogAtendimento.id.asc())
        .all()
    )
    # timestamps determinísticos: chamada(1a)=0s, chamada_nome(aguardar)=30s,
    # voltou_aguardar=30s, chamada(RE-chamada)=90s, realocado=150s.
    # 'início' esperado = RE-chamada (90s) -> segmento = 150-90 = 60s.
    t0 = datetime.utcnow() - timedelta(hours=1)
    offsets = [0, 30, 30, 90, 150]
    for log, offset in zip(logs2, offsets):
        log.timestamp = t0 + timedelta(seconds=offset)
    db.session.commit()

with app.app_context():
    segmentos = _segmentos_intermediarios_realocacao(_filtros())
    segmento_senha2 = next(s for s in segmentos if s[0].senha_id == ids["senha2"])
    inicio, realocado_log, fila_id, local_id, _unidade_id = segmento_senha2
    assert inicio.evento == "chamada_senha"
    assert inicio.timestamp == t0 + timedelta(seconds=90), inicio.timestamp
    assert fila_id == ids["fila_recepcao"]
    assert local_id == ids["local_recepcao"]
    duracao = (realocado_log.timestamp - inicio.timestamp).total_seconds()
    assert duracao == 60.0, duracao
ok("Idas-e-vindas: 'início' correto continua sendo a RE-chamada (90s), não a 1ª chamada (0s) nem o chamada_nome do Aguardar (30s) - segmento = 60s, comportamento pré-existente preservado, fila/local reais")

print()
print(f"TODOS OS {len(passos)} TESTES DE 'REALOCAR NÃO CONTA COMO CHAMADA' PASSARAM")
