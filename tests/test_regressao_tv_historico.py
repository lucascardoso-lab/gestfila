"""
Teste de regressão: correção de dois bugs relatados no painel/TV (seção 6 do
BLUEPRINT) e uma melhoria pedida junto:

1. "Tocar" duplicava a senha na lista da TV - cada repetição era tratada como
   uma chamada nova (empilhava mais uma linha idêntica, inflava "chamadas
   hoje"). Corrigido com o campo `repetir: True` no payload do WebSocket
   (registrar_toque, app/atendimento/helpers.py), que o JS agora usa pra só
   re-destacar a linha do topo em vez de duplicar.
2. O histórico só existia na memória do navegador - reload ou queda de rede
   zerava a lista, mesmo com chamadas reais tendo acontecido. Corrigido com
   `historico_chamadas_tv()` (mesmo arquivo), consultado a cada (re)conexão
   via `entrar_tv` (app/sockets/painel.py) e devolvido no payload de "entrou".
3. "resete apenas ao final do dia": a consulta filtra por `chamada_em` de
   HOJE (UTC, mesma convenção já usada no reset diário do sequencial em
   totem/routes.py) - chamadas de dias anteriores não aparecem mais.

Limitação de infraestrutura (mesmo motivo já registrado em
test_regressao_som_configuravel.py): sem runtime de navegador nos testes, não
dá pra verificar de fato que o áudio toca - só que a lógica/dados corretos
chegam até o JS.

Execução: python -m tests.test_regressao_tv_historico (a partir da raiz do
projeto, com o virtualenv ativado).
"""
import os
from datetime import datetime, timedelta

os.environ.setdefault("SECRET_KEY", "test")
os.environ.setdefault("FLASK_ENV", "testing")

from app import create_app
from app.extensions import db, socketio
from app.models import Empresa, Fila, Local, Perfil, Senha, Tv, Unidade, Usuario

app = create_app("testing")

passos = []


def ok(msg):
    passos.append(msg)
    print("OK:", msg)


def _sem_slug_audio(payload):
    """Ignora local_audio_slug (adendo do áudio pré-gravado) nos asserts de payload
    deste teste, que são sobre tipo/local/senha - o valor do slug depende de quais
    .wav estão no repo e é coberto por test_regressao_audio_pregravado.py."""
    return {k: v for k, v in payload.items() if k != "local_audio_slug"}


NAMESPACE = "/painel"

with app.app_context():
    db.create_all()

    perfil_recepcao = Perfil(nome="atendente")
    db.session.add(perfil_recepcao)
    db.session.commit()

    empresa_a = Empresa(nome="Clinica A")
    empresa_b = Empresa(nome="Clinica B")
    db.session.add_all([empresa_a, empresa_b])
    db.session.commit()

    unidade_a1 = Unidade(empresa_id=empresa_a.id, nome="Unidade A1")
    unidade_b1 = Unidade(empresa_id=empresa_b.id, nome="Unidade B1")
    db.session.add_all([unidade_a1, unidade_b1])
    db.session.commit()

    fila_a = Fila(empresa_id=empresa_a.id, unidade_id=unidade_a1.id, nome="Fila A", sigla="FA")
    fila_a2 = Fila(empresa_id=empresa_a.id, unidade_id=unidade_a1.id, nome="Fila A2", sigla="F2")
    fila_b = Fila(empresa_id=empresa_b.id, unidade_id=unidade_b1.id, nome="Fila B", sigla="FB")
    db.session.add_all([fila_a, fila_a2, fila_b])
    db.session.commit()

    local_a = Local(
        empresa_id=empresa_a.id, unidade_id=unidade_a1.id, nome="Guiche 01",
        nome_exibicao="Guiche 01", identificador_admin="[UNI] Guiche 01",
    )
    db.session.add(local_a)
    db.session.commit()

    tv_a = Tv(empresa_id=empresa_a.id, unidade_id=unidade_a1.id, nome="TV A", slug="tv-hist-a")
    tv_a.filas = [fila_a, fila_a2]
    tv_b = Tv(empresa_id=empresa_b.id, unidade_id=unidade_b1.id, nome="TV B", slug="tv-hist-b")
    tv_b.filas = [fila_b]
    db.session.add_all([tv_a, tv_b])
    db.session.commit()

    # senha de ONTEM já chamada, na mesma fila da TV A - prova que o histórico não
    # "vaza" pro dia seguinte (item 3: reset só ao final do dia).
    senha_ontem = Senha(
        empresa_id=empresa_a.id, unidade_id=unidade_a1.id, fila_id=fila_a.id,
        codigo="FA900", sequencial=900, prioridade=False, status="finalizada",
        chamada_em=datetime.utcnow() - timedelta(days=1), local_id=local_a.id,
    )
    # senha de outra empresa, chamada hoje - prova isolamento multi-tenant.
    senha_empresa_b = Senha(
        empresa_id=empresa_b.id, unidade_id=unidade_b1.id, fila_id=fila_b.id,
        codigo="FB001", sequencial=1, prioridade=False, status="chamada",
        chamada_em=datetime.utcnow(),
    )
    db.session.add_all([senha_ontem, senha_empresa_b])
    db.session.commit()

    senha1 = Senha(
        empresa_id=empresa_a.id, unidade_id=unidade_a1.id, fila_id=fila_a.id,
        codigo="FA001", sequencial=1, prioridade=False, status="aguardando",
    )
    senha2 = Senha(
        empresa_id=empresa_a.id, unidade_id=unidade_a1.id, fila_id=fila_a2.id,
        codigo="F2001", sequencial=1, prioridade=False, status="aguardando",
    )
    db.session.add_all([senha1, senha2])
    db.session.commit()

    def novo_usuario(login, empresa_id, perfis, unidades):
        u = Usuario(empresa_id=empresa_id, nome=login, login=login)
        u.set_senha("senha123")
        u.perfis = perfis
        u.unidades = unidades
        db.session.add(u)
        db.session.commit()
        return u.id

    id_recepcao_a = novo_usuario("recepcao_a_tv", empresa_a.id, [perfil_recepcao], [unidade_a1])

    ids = dict(
        empresa_a=empresa_a.id, fila_a=fila_a.id, fila_a2=fila_a2.id, local_a=local_a.id,
        tv_a=tv_a.id, tv_b=tv_b.id, senha1=senha1.id, senha2=senha2.id,
    )


def login_como(login):
    c = app.test_client()
    resp = c.post("/login", data={"login": login, "senha": "senha123"}, follow_redirects=True)
    assert resp.status_code == 200, (login, resp.status_code)
    return c


c_recepcao = login_como("recepcao_a_tv")
with c_recepcao.session_transaction() as sess:
    sess["atendimento_local_id"] = ids["local_a"]

# ===========================================================================
# A) Ao conectar pela primeira vez, com só a senha de ONTEM chamada (mesma
#    fila), o histórico "de hoje" vem vazio - prova o reset por dia (item 3).
# ===========================================================================
ws = socketio.test_client(app, namespace=NAMESPACE)
ws.get_received(NAMESPACE)
ws.emit("entrar_tv", {"tv_id": ids["tv_a"]}, namespace=NAMESPACE)
recebidos = ws.get_received(NAMESPACE)
ev_entrou = next(ev for ev in recebidos if ev["name"] == "entrou")
assert ev_entrou["args"][0]["historico"] == []
assert ev_entrou["args"][0]["total_hoje"] == 0
ok("'entrou' não inclui chamada de ontem no histórico/contador de hoje (reset diário)")

# ===========================================================================
# B) 'Chamar' FA001 -> reconectar -> o histórico de hoje já reflete a chamada,
#    mesmo sem o cliente ter recebido o evento 'chamada' ao vivo (simula reload/
#    reconexão da TV - item 2, sobrevive ao reload).
# ===========================================================================
resp = c_recepcao.post(
    f"/atendimento/recepcao/filas/{ids['fila_a']}/chamar",
    data={"codigo": "FA001"},
    follow_redirects=True,
)
assert resp.status_code == 200
ws.get_received(NAMESPACE)  # descarta o evento 'chamada' ao vivo, já coberto noutros testes

ws2 = socketio.test_client(app, namespace=NAMESPACE)
ws2.get_received(NAMESPACE)
ws2.emit("entrar_tv", {"tv_id": ids["tv_a"]}, namespace=NAMESPACE)
recebidos = ws2.get_received(NAMESPACE)
ev_entrou2 = next(ev for ev in recebidos if ev["name"] == "entrou")
assert ev_entrou2["args"][0]["total_hoje"] == 1
assert [_sem_slug_audio(x) for x in ev_entrou2["args"][0]["historico"]] == [{"tipo": "senha", "local": "Guiche 01", "senha": "FA001"}]
ok("Reconectar após 'Chamar' reconstrói o histórico/contador a partir do banco (sobrevive a reload)")

# ===========================================================================
# C) 'Tocar' (repetir a chamada atual) emite 'repetir: True' e NÃO deve inflar
#    o histórico persistido - reconectar de novo continua mostrando total=1.
# ===========================================================================
resp = c_recepcao.post(f"/atendimento/recepcao/filas/{ids['fila_a']}/tocar", data={})
assert resp.status_code == 302
recebidos = ws.get_received(NAMESPACE)
ev_toque = next(ev for ev in recebidos if ev["name"] == "chamada")
assert ev_toque["args"][0]["repetir"] is True
ok("'Tocar' emite o evento 'chamada' com repetir=True (JS usa isso pra não duplicar a linha)")

ws3 = socketio.test_client(app, namespace=NAMESPACE)
ws3.get_received(NAMESPACE)
ws3.emit("entrar_tv", {"tv_id": ids["tv_a"]}, namespace=NAMESPACE)
recebidos = ws3.get_received(NAMESPACE)
ev_entrou3 = next(ev for ev in recebidos if ev["name"] == "entrou")
assert ev_entrou3["args"][0]["total_hoje"] == 1
ok("'Tocar' não altera chamada_em, então o histórico persistido continua com total_hoje=1")

# ===========================================================================
# D) Uma segunda chamada, em outra fila da MESMA TV, soma no histórico
#    agregado (ordem cronológica: mais antiga primeiro).
# ===========================================================================
resp = c_recepcao.post(
    f"/atendimento/recepcao/filas/{ids['fila_a2']}/chamar",
    data={"codigo": "F2001"},
    follow_redirects=True,
)
assert resp.status_code == 200

ws4 = socketio.test_client(app, namespace=NAMESPACE)
ws4.get_received(NAMESPACE)
ws4.emit("entrar_tv", {"tv_id": ids["tv_a"]}, namespace=NAMESPACE)
recebidos = ws4.get_received(NAMESPACE)
ev_entrou4 = next(ev for ev in recebidos if ev["name"] == "entrou")
assert ev_entrou4["args"][0]["total_hoje"] == 2
codigos_historico = [item.get("senha") for item in ev_entrou4["args"][0]["historico"]]
assert codigos_historico == ["FA001", "F2001"]
ok("Histórico agrega as duas filas da mesma TV, em ordem cronológica (mais antiga primeiro)")

# ===========================================================================
# E) Isolamento multi-tenant: a TV da empresa B (fila_b, senha chamada hoje)
#    nunca aparece no histórico da TV A, e vice-versa.
# ===========================================================================
ws_b = socketio.test_client(app, namespace=NAMESPACE)
ws_b.get_received(NAMESPACE)
ws_b.emit("entrar_tv", {"tv_id": ids["tv_b"]}, namespace=NAMESPACE)
recebidos = ws_b.get_received(NAMESPACE)
ev_entrou_b = next(ev for ev in recebidos if ev["name"] == "entrou")
codigos_b = [item.get("senha") for item in ev_entrou_b["args"][0]["historico"]]
assert codigos_b == ["FB001"]
assert "FA001" not in codigos_b and "F2001" not in codigos_b
ok("Isolamento multi-tenant: histórico da TV B só tem a própria senha, nunca as da empresa A")

# ===========================================================================
# F) HTML da TV carrega a lógica corrigida (presença do código, já que não há
#    runtime de navegador nos testes - mesmo padrão de test_regressao_som_configuravel.py).
# ===========================================================================
resp = app.test_client().get("/painel/tv/tv-hist-a")
assert resp.status_code == 200
texto = resp.data.decode("utf-8")
assert "dados.repetir" in texto
assert "function recarregarHistorico" in texto
ok("HTML da TV inclui a correção de 'Tocar' e a reconstrução de histórico")

print()
print(f"TODOS OS {len(passos)} TESTES DE HISTORICO/DUPLICACAO DA TV PASSARAM")
