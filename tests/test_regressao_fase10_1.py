"""
Teste de regressão da seção 10.1 do BLUEPRINT (fusão de Serviço em Fila):

- botão do totem aponta direto para fila_id (sem indireção por servico_id);
- geração de senha usa sigla/digitos_sequencial/zera_diariamente/prioridade_padrao
  direto da Fila;
- horario_funcionamento é respeitado (fora do horário, recusa gerar senha);
- fluxo completo geração -> chamada continua emitindo o evento WebSocket
  'chamada' no painel da TV, exatamente como antes da fusão.

Execução: python -m tests.test_regressao_fase10_1 (a partir da raiz do projeto, com o
virtualenv ativado).
"""
import os

os.environ.setdefault("SECRET_KEY", "test")
os.environ.setdefault("FLASK_ENV", "testing")

from datetime import datetime

from app import create_app
from app.extensions import db, socketio
from app.models import Empresa, Fila, Local, LogAtendimento, Perfil, Senha, Totem, TotemBotao, Tv, Unidade, Usuario

app = create_app("testing")

passos = []


def ok(msg):
    passos.append(msg)
    print("OK:", msg)


HORARIO_ABERTO = {
    dia: {"manha": True, "tarde": True}
    for dia in ("domingo", "segunda", "terca", "quarta", "quinta", "sexta", "sabado")
}
HORARIO_FECHADO = {
    dia: {"manha": False, "tarde": False}
    for dia in ("domingo", "segunda", "terca", "quarta", "quinta", "sexta", "sabado")
}

with app.app_context():
    db.create_all()

    perfil_recepcao = Perfil(nome="atendente")
    db.session.add(perfil_recepcao)
    db.session.commit()

    empresa = Empresa(nome="Clinica Fusao")
    db.session.add(empresa)
    db.session.commit()

    unidade = Unidade(empresa_id=empresa.id, nome="Unidade 1")
    db.session.add(unidade)
    db.session.commit()

    # fila já nasce com os campos que antes viviam em Servico - nenhuma tabela
    # `servicos` é tocada neste fluxo, confirmando que ela deixou de ser necessária.
    fila = Fila(
        empresa_id=empresa.id, unidade_id=unidade.id, nome="Laboratorio",
        sigla="LAB", digitos_sequencial=3, zera_diariamente=True, prioridade_padrao=0,
        horario_funcionamento=dict(HORARIO_ABERTO),
    )
    db.session.add(fila)
    db.session.commit()

    # botão-folha vinculado direto à fila (fila_id), sem servico_id
    botao = TotemBotao(empresa_id=empresa.id, unidade_id=unidade.id, label="Laboratorio", fila_id=fila.id)
    db.session.add(botao)
    db.session.commit()

    tv = Tv(empresa_id=empresa.id, unidade_id=unidade.id, nome="TV Recepção", slug="tv-recepcao")
    tv.filas = [fila]
    db.session.add(tv)
    db.session.commit()

    usuario = Usuario(empresa_id=empresa.id, nome="recepcao_1", login="recepcao_1")
    usuario.set_senha("senha123")
    usuario.perfis = [perfil_recepcao]
    usuario.unidades = [unidade]
    db.session.add(usuario)
    db.session.commit()

    local = Local(
        empresa_id=empresa.id, unidade_id=unidade.id, nome="Guichê 01",
        nome_exibicao="Guichê 01", identificador_admin="[UNI] Guichê 01",
    )
    db.session.add(local)
    db.session.commit()

    totem = Totem(empresa_id=empresa.id, unidade_id=unidade.id, nome="Totem Recepção", slug="totem-recepcao")
    db.session.add(totem)
    db.session.commit()

    ids = dict(unidade=unidade.id, botao=botao.id, fila=fila.id, tv=tv.id, local=local.id, totem=totem.id)

NAMESPACE = "/painel"
ws = socketio.test_client(app, namespace=NAMESPACE)
ws.get_received(NAMESPACE)
ws.emit("entrar_tv", {"tv_id": ids["tv"]}, namespace=NAMESPACE)
ws.get_received(NAMESPACE)

c_totem = app.test_client()

# ---------------------------------------------------------------------------
# 1) geração via botão ligado direto a fila_id (sem servico_id) -> LAB001.
# ---------------------------------------------------------------------------
resp = c_totem.post(f"/totem/{ids['unidade']}/{ids['totem']}/gerar/{ids['botao']}", follow_redirects=True)
assert resp.status_code == 200
assert "LAB001" in resp.data.decode("utf-8")
ok("Botão do totem ligado direto a fila_id gera senha LAB001 (sigla/sequencial vêm da Fila)")

with app.app_context():
    senha1 = Senha.query.filter_by(codigo="LAB001").first()
    assert senha1 is not None and senha1.fila_id == ids["fila"]
    assert senha1.servico_id is None  # nenhuma linha de servico envolvida nesta geração
ok("Senha gravada com fila_id e sem depender de servico_id (coluna fica nula em linhas novas)")

resp = c_totem.post(f"/totem/{ids['unidade']}/{ids['totem']}/gerar/{ids['botao']}", follow_redirects=True)
assert "LAB002" in resp.data.decode("utf-8")
ok("Segunda geração incrementa o sequencial corretamente (LAB002)")

# ---------------------------------------------------------------------------
# 2) horario_funcionamento fechado -> recusa gerar senha, sem criar linha nova.
# ---------------------------------------------------------------------------
with app.app_context():
    f = db.session.get(Fila, ids["fila"])
    f.horario_funcionamento = dict(HORARIO_FECHADO)
    db.session.commit()
    total_antes = Senha.query.filter_by(fila_id=ids["fila"]).count()

resp = c_totem.post(f"/totem/{ids['unidade']}/{ids['totem']}/gerar/{ids['botao']}", follow_redirects=True)
assert resp.status_code == 200
assert "fora do horário de funcionamento".encode("utf-8") in resp.data
ok("Fora do horário de funcionamento configurado, o totem recusa gerar senha com mensagem apropriada")

with app.app_context():
    total_depois = Senha.query.filter_by(fila_id=ids["fila"]).count()
    assert total_depois == total_antes
ok("Nenhuma senha nova é criada quando a fila está fora do horário")

with app.app_context():
    f = db.session.get(Fila, ids["fila"])
    f.horario_funcionamento = dict(HORARIO_ABERTO)
    db.session.commit()

# ---------------------------------------------------------------------------
# 3) reabrindo o horário, geração + chamada continuam disparando o WebSocket
#    do painel da TV exatamente como antes da fusão de Serviço em Fila.
# ---------------------------------------------------------------------------
resp = c_totem.post(f"/totem/{ids['unidade']}/{ids['totem']}/gerar/{ids['botao']}", follow_redirects=True)
assert "LAB003" in resp.data.decode("utf-8")

c = app.test_client()
resp = c.post("/login", data={"login": "recepcao_1", "senha": "senha123"}, follow_redirects=True)
assert resp.status_code == 200

with c.session_transaction() as sess:
    sess["atendimento_local_id"] = ids["local"]

resp = c.post(
    f"/atendimento/recepcao/filas/{ids['fila']}/chamar",
    data={},
    follow_redirects=True,
)
assert resp.status_code == 200

recebidos = ws.get_received(NAMESPACE)
evento_chamada = next(ev for ev in recebidos if ev["name"] == "chamada")
assert evento_chamada["args"][0]["tipo"] == "senha"
assert evento_chamada["args"][0]["senha"] == "LAB001"
ok("Painel da TV (WebSocket) continua recebendo o evento 'chamada' normalmente após a fusão")

print()
print(f"TODOS OS {len(passos)} TESTES DA SEÇÃO 10.1 (FUSÃO SERVIÇO->FILA) PASSARAM")
