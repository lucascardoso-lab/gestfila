"""
Teste de regressão: gerar uma senha no totem deve avisar em tempo real a tela de
atendimento (evento WebSocket 'fila_atualizada') - bug relatado pelo cliente: no
perfil recepção, uma senha nova só aparecia em "Senhas em espera" depois de um F5
manual. Causa raiz: `totem/routes.py::_emitir_senha` nunca chamava
`emitir_atualizacao_fila()` - só as ações do próprio atendente (Chamar/Realocar/
Finalizar/Aguardar, `atendimento/helpers.py`) disparavam esse evento. Como as duas
telas de atendimento (por-fila e por-Local/rodízio, adendo 10.8) escutam o mesmo
evento 'fila_atualizada', a falta afetava ambas igualmente - o fix (um único
`emitir_atualizacao_fila(fila.id)` em `_emitir_senha`) cobre as duas de uma vez.

Execução: python -m tests.test_regressao_totem_avisa_fila (a partir da raiz do
projeto, com o virtualenv ativado).
"""
import os

os.environ.setdefault("SECRET_KEY", "test")
os.environ.setdefault("FLASK_ENV", "testing")

from app import create_app
from app.extensions import db, socketio
from app.models import Empresa, Fila, Local, LocalRodizioFila, Perfil, Totem, Unidade, Usuario

app = create_app("testing")

passos = []


def ok(msg):
    passos.append(msg)
    print("OK:", msg)


NAMESPACE = "/painel"

with app.app_context():
    db.create_all()

    perfil_recepcao = Perfil(nome="atendente")
    db.session.add(perfil_recepcao)
    db.session.commit()

    empresa = Empresa(nome="Clinica A")
    db.session.add(empresa)
    db.session.commit()

    unidade = Unidade(empresa_id=empresa.id, nome="Unidade A1")
    db.session.add(unidade)
    db.session.commit()

    fila_recepcao = Fila(empresa_id=empresa.id, unidade_id=unidade.id, nome="Recepção Geral", sigla="RG")
    fila_coleta = Fila(empresa_id=empresa.id, unidade_id=unidade.id, nome="Coleta Geral", sigla="CG")
    db.session.add_all([fila_recepcao, fila_coleta])
    db.session.commit()

    totem = Totem(empresa_id=empresa.id, unidade_id=unidade.id, nome="Totem 1", slug="totem-avisa-fila")
    db.session.add(totem)
    db.session.commit()

    botao_recepcao_id = None
    from app.models import TotemBotao

    botao_recepcao = TotemBotao(
        empresa_id=empresa.id, unidade_id=unidade.id, label="Recepção", ordem=1,
        fila_id=fila_recepcao.id, ativo=True,
    )
    db.session.add(botao_recepcao)
    db.session.commit()
    botao_recepcao_id = botao_recepcao.id

    local_coleta = Local(empresa_id=empresa.id, unidade_id=unidade.id, nome="Guiche Coleta", tipo="coleta")
    db.session.add(local_coleta)
    db.session.commit()
    db.session.add(LocalRodizioFila(local_id=local_coleta.id, fila_id=fila_coleta.id, ordem=0, chamadas_seguidas=1))
    db.session.commit()

    u = Usuario(empresa_id=empresa.id, nome="recepcao_totem", login="recepcao_totem")
    u.set_senha("senha123")
    u.perfis = [perfil_recepcao]
    u.unidades = [unidade]
    db.session.add(u)
    db.session.commit()

    ids = dict(
        empresa=empresa.id, unidade=unidade.id,
        fila_recepcao=fila_recepcao.id, fila_coleta=fila_coleta.id,
        totem_slug=totem.slug, botao_recepcao=botao_recepcao_id,
        local_coleta=local_coleta.id, usuario="recepcao_totem",
    )


def login_como(login):
    c = app.test_client()
    resp = c.post("/login", data={"login": login, "senha": "senha123"}, follow_redirects=True)
    assert resp.status_code == 200, (login, resp.status_code)
    return c


# ===========================================================================
# A) Tela por-fila (recepção, seção 10.2): operador conectado na sala da fila via
#    'entrar_fila' - gerar uma senha nova no totem deve emitir 'fila_atualizada'
#    para essa fila, SEM que o operador precise dar F5.
# ===========================================================================
c1 = login_como(ids["usuario"])
ws = socketio.test_client(app, namespace=NAMESPACE, flask_test_client=c1)
ws.get_received(NAMESPACE)
ws.emit("entrar_fila", {"fila_id": ids["fila_recepcao"]}, namespace=NAMESPACE)
ws.get_received(NAMESPACE)

c_totem = app.test_client()
resp = c_totem.get(f"/totem/{ids['totem_slug']}/confirmar/{ids['botao_recepcao']}")
assert resp.status_code == 200

resp = c_totem.post(
    f"/totem/{ids['totem_slug']}/gerar/{ids['botao_recepcao']}",
    data={"qtd_acompanhantes": "0"},
    follow_redirects=True,
)
assert resp.status_code == 200

recebidos = ws.get_received(NAMESPACE)
eventos_fila_atualizada = [ev for ev in recebidos if ev["name"] == "fila_atualizada"]
assert len(eventos_fila_atualizada) == 1, recebidos
assert eventos_fila_atualizada[0]["args"][0]["fila_id"] == ids["fila_recepcao"]
ok("Gerar senha no totem emite 'fila_atualizada' pra tela por-fila (recepção) - não precisa mais de F5")

# ===========================================================================
# B) Tela por-Local (coleta, adendo 10.8): mesma verificação, agora numa fila que
#    participa do rodízio de um Local (a tela por-Local entra na sala da FILA da
#    mesma forma, um 'entrar_fila' por fila do rodízio).
# ===========================================================================
ws2 = socketio.test_client(app, namespace=NAMESPACE, flask_test_client=c1)
ws2.get_received(NAMESPACE)
ws2.emit("entrar_fila", {"fila_id": ids["fila_coleta"]}, namespace=NAMESPACE)
ws2.get_received(NAMESPACE)

# gera diretamente via helper do totem (sem passar por um TotemBotao de coleta,
# que não foi necessário criar pra este teste) - mesma função `_emitir_senha`
# exercitada no cenário A, então cobre a mesma correção.
with app.app_context():
    from app.totem.routes import _emitir_senha

    fila_coleta_obj = db.session.get(Fila, ids["fila_coleta"])
    unidade_obj = db.session.get(Unidade, ids["unidade"])
    _emitir_senha(fila_coleta_obj, unidade_obj)

recebidos2 = ws2.get_received(NAMESPACE)
eventos_fila_atualizada2 = [ev for ev in recebidos2 if ev["name"] == "fila_atualizada"]
assert len(eventos_fila_atualizada2) == 1, recebidos2
assert eventos_fila_atualizada2[0]["args"][0]["fila_id"] == ids["fila_coleta"]
ok("Gerar senha no totem emite 'fila_atualizada' pra tela por-Local (coleta/rodízio) igualmente")

print()
print(f"TODOS OS {len(passos)} TESTES DE AVISO DE FILA (TOTEM) PASSARAM")
