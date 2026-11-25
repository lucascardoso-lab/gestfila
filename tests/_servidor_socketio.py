"""Servidor REAL (Flask-SocketIO em eventlet) para tests/test_regressao_socketio_muitas_filas.py
(adendo 10.89) - não é um teste (fora do padrão test_regressao_*).

Uso: python tests/_servidor_socketio.py <porta> <pasta>
Ambiente:
  TESTE_LIMITE_PACOTES  sobrescreve Payload.max_decode_packets (16 = antes do 10.89)
  TESTE_ATRASO_WS       atrasa o handshake do WebSocket (s) - upgrade lento, para os
                        entrar_* saírem pelo long-polling de forma determinística

Na <pasta>: banco SQLite (semeado aqui), cenarios.json (logins/locais/filas) e
servidor.log (erros do engineio e WARNINGs do app). Rotas só de teste:
/__teste/salas?sid= (salas do socket) e POST /__teste/senha/<fila_id> (senha nova
+ o mesmo sinal 'fila_atualizada' do totem).
"""
import os
import sys

import eventlet

eventlet.monkey_patch()

PORTA = int(sys.argv[1])
PASTA = sys.argv[2]
RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)
os.environ["SECRET_KEY"] = "teste"
os.environ["FLASK_ENV"] = "testing"
os.environ["TEST_DATABASE_URL"] = "sqlite:///" + os.path.join(PASTA, "banco.db").replace("\\", "/")

# app/extensions.py usa threading no Windows (dev local); produção é eventlet.
# O módulo vê "linux" só durante o import, para o servidor rodar em eventlet aqui.
_plataforma = sys.platform
sys.platform = "linux"
import app.extensions  # noqa: E402,F401
sys.platform = _plataforma

import json  # noqa: E402
import logging  # noqa: E402

import engineio.server as eio_server  # noqa: E402
from engineio.payload import Payload  # noqa: E402
from flask import jsonify, request  # noqa: E402

from app import create_app  # noqa: E402
from app.atendimento.helpers import emitir_atualizacao_fila  # noqa: E402
from app.extensions import db, socketio  # noqa: E402
from app.models import Empresa, Fila, Local, Perfil, Senha, TurnoAtivo, Tv, Unidade, Usuario  # noqa: E402

app = create_app("testing")
assert socketio.server.eio.async_mode == "eventlet", socketio.server.eio.async_mode
if os.environ.get("TESTE_LIMITE_PACOTES"):
    Payload.max_decode_packets = int(os.environ["TESTE_LIMITE_PACOTES"])

registro = logging.FileHandler(os.path.join(PASTA, "servidor.log"), encoding="utf-8")
registro.setFormatter(logging.Formatter("%(levelname)s %(name)s %(message)s"))
for nome in ("engineio.server", app.logger.name):
    logging.getLogger(nome).addHandler(registro)
app.logger.setLevel(logging.INFO)

ATRASO_WS = float(os.environ.get("TESTE_ATRASO_WS", "0"))
_handle_request = eio_server.Server.handle_request


def handle_request_com_atraso(self, environ, start_response):
    if ATRASO_WS and "transport=websocket" in environ.get("QUERY_STRING", ""):
        eventlet.sleep(ATRASO_WS)
    return _handle_request(self, environ, start_response)


eio_server.Server.handle_request = handle_request_com_atraso


@app.route("/__teste/salas")
def salas():
    return jsonify(sorted(s for s in socketio.server.manager.get_rooms(request.args["sid"], "/painel")
                          if s.startswith("fila_")))


@app.route("/__teste/senha/<int:fila_id>", methods=["POST"])
def senha_nova(fila_id):
    fila = db.session.get(Fila, fila_id)
    seq = (db.session.query(db.func.max(Senha.sequencial)).scalar() or 0) + 1
    db.session.add(Senha(empresa_id=fila.empresa_id, unidade_id=fila.unidade_id, fila_id=fila.id,
                         codigo=f"T{seq:03d}", sequencial=seq, status="aguardando"))
    db.session.commit()
    emitir_atualizacao_fila(fila_id)
    return jsonify(ok=True)


def semear():
    db.create_all()
    perfil = Perfil(nome="atendente")
    empresa = Empresa(nome="Clinica 10.89")
    db.session.add_all([perfil, empresa])
    db.session.commit()
    unidade = Unidade(empresa_id=empresa.id, nome="Matriz")
    db.session.add(unidade)
    db.session.commit()
    filas = [Fila(empresa_id=empresa.id, unidade_id=unidade.id, nome=f"Fila {i:02d}") for i in range(1, 71)]
    db.session.add_all(filas)
    db.session.commit()
    cenarios = {}
    for n in (3, 17, 18, 22, 64):
        local = Local(empresa_id=empresa.id, unidade_id=unidade.id, nome=f"Guiche {n}", nome_exibicao=f"Guiche {n}",
                      identificador_admin=f"[M] Guiche {n}", tipo="recepcao")
        operador = Usuario(empresa_id=empresa.id, nome=f"op{n}", login=f"op{n}")
        operador.set_senha("senha123")
        operador.perfis = [perfil]
        operador.unidades = [unidade]
        db.session.add_all([local, operador])
        db.session.commit()
        db.session.add(TurnoAtivo(usuario_id=operador.id, local_id=local.id, ativo=True, filas_config=[
            {"fila_id": f.id, "ordem": i, "chamadas_seguidas": 1} for i, f in enumerate(filas[:n])]))
        db.session.commit()
        cenarios[n] = {"login": f"op{n}", "local_id": local.id, "fila_ids": [f.id for f in filas[:n]]}
    tv = Tv(empresa_id=empresa.id, unidade_id=unidade.id, nome="TV 22", slug="tv-22")
    tv.filas = filas[:22]
    db.session.add(tv)
    db.session.commit()
    cenarios["tv"] = {"slug": "tv-22", "id": tv.id}
    with open(os.path.join(PASTA, "cenarios.json"), "w", encoding="utf-8") as f:
        json.dump(cenarios, f)


with app.app_context():
    if not os.path.exists(os.path.join(PASTA, "cenarios.json")):
        semear()

print("PRONTO", flush=True)
socketio.run(app, host="127.0.0.1", port=PORTA, debug=False, use_reloader=False, log_output=False)
