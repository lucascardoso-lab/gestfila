"""
Teste de regressão do adendo 10.89: "Too many packets in payload" na tela de
Atendimento por Local com muitas filas.

Causa: a tela emitia um 'entrar_fila' por fila a cada 'connect'. No
long-polling, o 1º saía sozinho e os outros N-1 iam juntos no POST seguinte; o
python-engineio recusa POST com mais de 16 pacotes, engole o erro e responde
200 - com 18 filas ou mais a tela ficava só na sala da 1ª fila e a "Senhas em
espera" das outras não atualizava sozinha.

Cobre:
1. limite efetivo `Payload.max_decode_packets` = 64;
2. `entrar_filas`: isolamento fila a fila (outra empresa, unidade não
   permitida, inexistente, anônimo), repetidos, não inteiros, teto de 100 com
   WARNING; `entrar_fila` com as mesmas respostas de antes; payload de
   'entrou' da TV igual;
3. middleware de diagnóstico: loga acima de 16 com sid/tela/eventos, não loga
   até 16, corpo repassado sem alteração, sem argumentos (dado de paciente) no
   log, CONTENT_LENGTH ausente/inválido/grande segue sem ler;
4. Chromium + servidor REAL em eventlet (tests/_servidor_socketio.py), upgrade
   do WebSocket lento para os emits saírem pelo long-polling: ANTES (JS antigo,
   limite 16) 22 filas = 1 de 22 salas + erro; DEPOIS 22 de 22 com 1 pacote
   'entrar_filas' e fila_atualizada da última fila sem F5; 17/18/64 filas;
   JS antigo (aba aberta antes do deploy) com o limite novo; reconexão = 1
   entrada por connect; TV com 1 'entrar_tv'.

Execução: python -m tests.test_regressao_socketio_muitas_filas (a partir da
raiz do projeto, com o virtualenv ativado).
"""
import copy
import io
import json
import logging
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

os.environ.setdefault("SECRET_KEY", "test")
os.environ.setdefault("FLASK_ENV", "testing")

from engineio.payload import Payload
from playwright.sync_api import sync_playwright

from app import create_app
from app.extensions import MAX_PACOTES_POR_POST, db, socketio
from app.middleware.diagnostico_socketio import MAX_BYTES_PADRAO, DiagnosticoPostSocketIO
from app.models import Empresa, Fila, Perfil, Tv, Unidade, Usuario
from app.sockets.painel import MAX_FILAS_POR_ENTRADA

app = create_app("testing")
NAMESPACE = "/painel"
passos = []


def ok(msg):
    passos.append(msg)
    print("OK:", msg)


class Coletor(logging.Handler):
    def __init__(self):
        super().__init__()
        self.mensagens = []

    def emit(self, record):
        self.mensagens.append(record.getMessage())


# ===========================================================================
# 1) Limite efetivo
# ===========================================================================
assert MAX_PACOTES_POR_POST == 64 and Payload.max_decode_packets == 64, Payload.max_decode_packets
Payload(encoded_payload="\x1e".join(["3"] * 64))
try:
    Payload(encoded_payload="\x1e".join(["3"] * 65))
    raise AssertionError("65 pacotes deveriam ser recusados")
except ValueError as erro:
    assert "Too many packets" in str(erro)
ok("Payload.max_decode_packets efetivo = 64 (64 pacotes aceitos, 65 recusados)")

# ===========================================================================
# 2) entrar_filas / entrar_fila / entrar_tv (cliente de teste do Flask-SocketIO)
# ===========================================================================
with app.app_context():
    db.create_all()
    perfil = Perfil(nome="atendente")
    emp_a, emp_b = Empresa(nome="A"), Empresa(nome="B")
    db.session.add_all([perfil, emp_a, emp_b])
    db.session.commit()
    a1, a2, b1 = (Unidade(empresa_id=emp_a.id, nome="A1"), Unidade(empresa_id=emp_a.id, nome="A2"),
                  Unidade(empresa_id=emp_b.id, nome="B1"))
    db.session.add_all([a1, a2, b1])
    db.session.commit()
    filas_a1 = [Fila(empresa_id=emp_a.id, unidade_id=a1.id, nome=f"A1-{i}") for i in range(110)]
    fila_a2 = Fila(empresa_id=emp_a.id, unidade_id=a2.id, nome="A2")
    fila_b = Fila(empresa_id=emp_b.id, unidade_id=b1.id, nome="B")
    db.session.add_all(filas_a1 + [fila_a2, fila_b])
    db.session.commit()
    op = Usuario(empresa_id=emp_a.id, nome="op", login="op_1089")
    op.set_senha("senha123")
    op.perfis = [perfil]
    op.unidades = [a1]
    db.session.add(op)
    tv = Tv(empresa_id=emp_a.id, unidade_id=a1.id, nome="TV", slug="tv-1089")
    tv.filas = filas_a1[:22]
    db.session.add_all([op, tv])
    db.session.commit()
    A1 = [f.id for f in filas_a1]
    A2, B, TV_ID = fila_a2.id, fila_b.id, tv.id

http = app.test_client()
assert http.post("/login", data={"login": "op_1089", "senha": "senha123"}).status_code in (200, 302)


def cliente(logado=True):
    ws = socketio.test_client(app, namespace=NAMESPACE, flask_test_client=http if logado else None)
    ws.get_received(NAMESPACE)
    return ws


def salas(ws):
    sid = socketio.server.manager.sid_from_eio_sid(ws.eio_sid, NAMESPACE)
    return sorted(s for s in socketio.server.manager.get_rooms(sid, NAMESPACE) if s.startswith("fila_"))


def recebidos(ws, nome):
    return [r["args"][0] for r in ws.get_received(NAMESPACE) if r["name"] == nome]


ws = cliente()
ws.emit("entrar_filas", {"fila_ids": [A1[0], A2, B, 999999, A1[0], "abc", True, None, 2.5, A1[1]]}, namespace=NAMESPACE)
assert recebidos(ws, "entrou") == [{"fila_ids": [A1[0], A1[1]], "salas": [f"fila_{A1[0]}", f"fila_{A1[1]}"]}]
assert salas(ws) == sorted([f"fila_{A1[0]}", f"fila_{A1[1]}"]), salas(ws)
ok("entrar_filas: entra só nas filas permitidas; outra empresa, unidade não permitida, inexistente, "
   "repetida e não inteira ficam de fora sem derrubar as outras")

anon = cliente(logado=False)
anon.emit("entrar_filas", {"fila_ids": [A1[0]]}, namespace=NAMESPACE)
assert recebidos(anon, "erro") == [{"mensagem": "Não autenticado."}] and salas(anon) == []
ws_inv = cliente()
ws_inv.emit("entrar_filas", {"fila_ids": "1,2"}, namespace=NAMESPACE)
assert recebidos(ws_inv, "erro") == [{"mensagem": "Lista de filas inválida."}] and salas(ws_inv) == []
ok("entrar_filas: anônimo recebe 'Não autenticado.' e lista inválida recebe erro, sem entrar em sala")

coletor = Coletor()
app.logger.addHandler(coletor)
ws_teto = cliente()
ws_teto.emit("entrar_filas", {"fila_ids": A1 + A1}, namespace=NAMESPACE)
entrou = recebidos(ws_teto, "entrou")[0]
assert MAX_FILAS_POR_ENTRADA == 100 and entrou["fila_ids"] == A1[:100] and len(salas(ws_teto)) == 100
assert any("110 filas pedidas, acima do teto de 100" in m for m in coletor.mensagens), coletor.mensagens
app.logger.removeHandler(coletor)
ok("entrar_filas: 220 ids (110 distintos) -> repetidos removidos, entra nas 100 primeiras e loga WARNING do excesso")

ws1 = cliente()
ws1.emit("entrar_fila", {"fila_id": A1[5]}, namespace=NAMESPACE)
assert recebidos(ws1, "entrou") == [{"fila_id": A1[5], "salas": [f"fila_{A1[5]}"]}]
for fila_id, mensagem in ((B, "Sem acesso a esta fila."), (A2, "Sem acesso a esta fila."), (999999, "Fila não encontrada."),
                          (None, "Fila não encontrada.")):
    ws1.emit("entrar_fila", {"fila_id": fila_id}, namespace=NAMESPACE)
    assert recebidos(ws1, "erro") == [{"mensagem": mensagem}], fila_id
anon.emit("entrar_fila", {"fila_id": A1[0]}, namespace=NAMESPACE)
assert recebidos(anon, "erro") == [{"mensagem": "Não autenticado."}]
assert salas(ws1) == [f"fila_{A1[5]}"]
ok("entrar_fila (tela por fila): mesmas respostas de antes - entrou, 'Sem acesso a esta fila.', "
   "'Fila não encontrada.', 'Não autenticado.'")

ws_tv = cliente(logado=False)
ws_tv.emit("entrar_tv", {"tv_id": TV_ID}, namespace=NAMESPACE)
entrou_tv = recebidos(ws_tv, "entrou")[0]
assert set(entrou_tv) == {"tv_id", "salas", "historico", "total_hoje"}, entrou_tv
assert len(entrou_tv["salas"]) == 23 and len(salas(ws_tv)) == 22
ok("TV: 'entrou' com as mesmas 4 chaves e as 22 salas de fila, sem login (inalterado)")
for c in (ws, anon, ws_inv, ws_teto, ws1, ws_tv):
    c.disconnect(namespace=NAMESPACE)

# ===========================================================================
# 3) Middleware de diagnóstico
# ===========================================================================
# O teste usa a PRÓPRIA instância instalada por create_app (mesmo max_bytes,
# limite e caminho de produção) - só troca o app de baixo e o logger.
PRODUCAO = app.wsgi_app
assert isinstance(PRODUCAO, DiagnosticoPostSocketIO)
assert PRODUCAO.max_bytes == socketio.server.eio.max_http_buffer_size == MAX_BYTES_PADRAO == 1_000_000, PRODUCAO.max_bytes
assert PRODUCAO.limite == 16 and PRODUCAO.caminho == "/socket.io/"
ok("Middleware instalado em create_app por fora do Flask-SocketIO, com max_bytes = max_http_buffer_size do "
   "servidor (1.000.000 bytes), limite 16 e caminho /socket.io/")


def chamar(corpo, metodo="POST", caminho="/socket.io/", content_length=None, referer="http://x/atendimento/recepcao/local/66"):
    coletor = Coletor()
    logger = logging.getLogger("teste_diagnostico_1089")
    logger.handlers = [coletor]
    logger.setLevel(logging.INFO)
    visto = {}

    def app_final(environ, start_response):
        visto["corpo"] = environ["wsgi.input"].read()
        visto["input"] = environ["wsgi.input"]
        return [b"ok"]

    entrada = io.BytesIO(corpo)
    environ = {"REQUEST_METHOD": metodo, "PATH_INFO": caminho, "QUERY_STRING": "EIO=4&transport=polling&t=x&sid=SID123",
               "HTTP_REFERER": referer, "wsgi.input": entrada,
               "CONTENT_LENGTH": str(len(corpo)) if content_length is None else content_length}
    instancia = copy.copy(PRODUCAO)
    instancia.wsgi_app, instancia.logger = app_final, logger
    instancia(environ, lambda *a: None)
    return coletor.mensagens, visto, entrada


def eventos(n, nome="entrar_fila"):
    return "\x1e".join(f'42/painel,["{nome}",{{"fila_id":{i},"nome_paciente":"Maria da Silva"}}]' for i in range(n)).encode()


corpo17 = eventos(16) + b"\x1e3"
log, visto, _ = chamar(corpo17)
assert visto["corpo"] == corpo17
assert len(log) == 1, log
assert all(t in log[0] for t in ("17 pacotes", "sid=SID123", "tela=/atendimento/recepcao/local/66",
                                 "entrar_fila: 16", "engineio:pong: 1")), log[0]
assert "Maria" not in log[0] and "fila_id" not in log[0] and "nome_paciente" not in log[0]
ok("Middleware: 17 pacotes -> WARNING com sid, tela e eventos com contagem; sem argumentos/dado de paciente; corpo intacto")

log, visto, _ = chamar(eventos(16))
assert log == [] and visto["corpo"] == eventos(16)
corpo64 = eventos(64)  # o maior POST que o engineio aceita agora (64 pacotes) - alguns KB
log, visto, _ = chamar(corpo64)
assert len(corpo64) < PRODUCAO.max_bytes and visto["corpo"] == corpo64 and "64 pacotes" in log[0] and "entrar_fila: 64" in log[0]
log, visto, _ = chamar(eventos(30, nome='x"]; drop'))
assert len(log) == 1 and "drop" not in log[0]
ok("Middleware: 16 pacotes não loga (corpo intacto); 64 pacotes (%d bytes) lidos e registrados com o max_bytes de "
   "produção; nome de evento fora do padrão não vai para o log" % len(corpo64))

for metodo, caminho, cl in (("GET", "/socket.io/", None), ("POST", "/login", None), ("POST", "/socket.io/", "abc"),
                            ("POST", "/socket.io/", ""), ("POST", "/socket.io/", "0"), ("POST", "/socket.io/", str(PRODUCAO.max_bytes + 1))):
    corpo = eventos(20)
    log, visto, entrada = chamar(corpo, metodo=metodo, caminho=caminho, content_length=cl)
    assert log == [] and visto["input"] is entrada, (metodo, caminho, cl)
ok("Middleware: GET, outra rota, CONTENT_LENGTH inválido/ausente/zero e acima do limite seguem sem ler o corpo nem logar")

# ===========================================================================
# 4) Chromium + servidor real em eventlet (upgrade lento)
# ===========================================================================
RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PASTA = tempfile.mkdtemp(prefix="gestfila_1089_")
ANTIGO = 'socket.emit("entrar_filas", { fila_ids: FILA_IDS });'
CODIGO_ANTIGO = 'FILA_IDS.forEach((filaId) => socket.emit("entrar_fila", { fila_id: filaId }));'


def porta_livre():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def subir_servidor(limite=None):
    porta = porta_livre()
    amb = dict(os.environ, TESTE_ATRASO_WS="1.5", PYTHONIOENCODING="utf-8")
    amb.pop("TESTE_LIMITE_PACOTES", None)
    if limite:
        amb["TESTE_LIMITE_PACOTES"] = str(limite)
    # saída num arquivo (um PIPE não lido enche e trava o servidor)
    saida = open(os.path.join(PASTA, f"saida_{porta}.txt"), "w", encoding="utf-8")
    proc = subprocess.Popen([sys.executable, os.path.join(RAIZ, "tests", "_servidor_socketio.py"), str(porta), PASTA],
                            cwd=RAIZ, env=amb, stdout=saida, stderr=subprocess.STDOUT)
    base = f"http://127.0.0.1:{porta}"
    for _ in range(120):
        try:
            urllib.request.urlopen(base + "/login", timeout=1)
            return proc, base
        except Exception:
            if proc.poll() is not None:
                raise RuntimeError(open(saida.name, encoding="utf-8", errors="replace").read())
            time.sleep(0.25)
    proc.kill()
    raise RuntimeError("servidor de teste não subiu")


def log_servidor():
    caminho = os.path.join(PASTA, "servidor.log")
    return open(caminho, encoding="utf-8").read() if os.path.exists(caminho) else ""


def abrir(nav, base, cen, codigo_antigo=False):
    ctx = nav.new_context()
    pg = ctx.new_page()
    enviados = []
    pg.on("request", lambda r: enviados.append(r.post_data or "") if "/socket.io/" in r.url and r.method == "POST" else None)
    pg.on("websocket", lambda w: w.on("framesent", lambda f: enviados.append(str(f))))
    if codigo_antigo:  # o JS de antes do 10.89, como numa aba aberta antes do deploy
        def antigo(rota):
            resposta = rota.fetch()
            corpo = resposta.text()
            assert ANTIGO in corpo
            rota.fulfill(response=resposta, body=corpo.replace(ANTIGO, CODIGO_ANTIGO))
        pg.route("**/atendimento/recepcao/local/*", antigo)
    pg.goto(base + "/login")
    pg.fill("input[name=login]", cen["login"])
    pg.fill("input[name=senha]", "senha123")
    pg.click("button[type=submit]")
    pg.wait_for_load_state("networkidle")
    pg.goto(f"{base}/atendimento/recepcao/local/{cen['local_id']}")
    pg.wait_for_function("() => window.__socketAtendimento && window.__socketAtendimento.connected", timeout=20000)
    return ctx, pg, enviados


def salas_da_pagina(pg, base, esperado, prazo=8.0):
    fim = time.time() + prazo
    while True:
        sid = pg.evaluate("window.__socketAtendimento.id")
        lista = json.loads(urllib.request.urlopen(f"{base}/__teste/salas?sid={sid}").read())
        if len(lista) >= esperado or time.time() > fim:
            return lista
        time.sleep(0.25)


def recebe_fila_atualizada(pg, base, fila_id):
    antes = int(pg.text_content("#contagem-espera") or 0)
    urllib.request.urlopen(urllib.request.Request(f"{base}/__teste/senha/{fila_id}", method="POST"))
    try:
        pg.wait_for_function(f"() => Number(document.getElementById('contagem-espera').textContent) > {antes}", timeout=5000)
        return True
    except Exception:
        return False


def contar(enviados, evento):
    return sum(e.count(f'["{evento}"') for e in enviados)


servidores = []
try:
    # --- ANTES: limite 16 e o JS antigo (N 'entrar_fila')
    proc, base = subir_servidor(limite=16)
    servidores.append(proc)
    cen = json.load(open(os.path.join(PASTA, "cenarios.json"), encoding="utf-8"))
    with sync_playwright() as pw:
        nav = pw.chromium.launch()
        ctx, pg, enviados = abrir(nav, base, cen["22"], codigo_antigo=True)
        lista = salas_da_pagina(pg, base, 22, prazo=4)
        assert len(lista) == 1, lista
        assert contar(enviados, "entrar_fila") == 22
        assert not recebe_fila_atualizada(pg, base, cen["22"]["fila_ids"][-1])
        texto = log_servidor()
        assert "Too many packets in payload" in texto, texto[-2000:]
        assert f"POST com 21 pacotes (acima de 16)" in texto and f"tela=/atendimento/recepcao/local/{cen['22']['local_id']}" in texto
        assert "entrar_fila: 21" in texto
        ctx.close()
        nav.close()
    ok("ANTES (limite 16 + JS antigo), 22 filas: 1 de 22 salas, 'Too many packets in payload', sem fila_atualizada "
       "da última fila; o middleware registrou o POST de 21 pacotes com a tela")
    proc.kill()
    proc.wait()

    # --- DEPOIS: limite 64 e o JS novo ('entrar_filas')
    proc, base = subir_servidor()
    servidores.append(proc)
    inicio_log = len(log_servidor())
    with sync_playwright() as pw:
        nav = pw.chromium.launch()
        for n in ("22", "17", "18", "64"):
            ctx, pg, enviados = abrir(nav, base, cen[n])
            lista = salas_da_pagina(pg, base, int(n))
            assert len(lista) == int(n), (n, len(lista))
            assert contar(enviados, "entrar_filas") == 1 and contar(enviados, "entrar_fila") == 0, enviados
            if n == "22":
                assert recebe_fila_atualizada(pg, base, cen[n]["fila_ids"][-1])
                assert recebe_fila_atualizada(pg, base, cen[n]["fila_ids"][0])
            ctx.close()
        trecho = log_servidor()[inicio_log:]
        assert "Too many packets" not in trecho and "POST com" not in trecho, trecho[-2000:]
        ok("DEPOIS (JS novo): 22, 17, 18 e 64 filas entram em todas as salas com 1 pacote 'entrar_filas', sem "
           "nenhum erro nem WARNING do middleware; 22 filas recebe fila_atualizada da última e da 1ª fila sem F5")

        inicio_log = len(log_servidor())

        for n in ("18", "22", "64"):
            ctx, pg, enviados = abrir(nav, base, cen[n], codigo_antigo=True)
            assert len(salas_da_pagina(pg, base, int(n))) == int(n), n
            assert contar(enviados, "entrar_fila") == int(n)
            ctx.close()
        trecho = log_servidor()[inicio_log:]
        avisos = [l for l in trecho.splitlines() if "POST com" in l]
        assert "Too many packets" not in trecho and len(avisos) == 3, trecho[-2000:]
        assert all("eventos=entrar_fila: " in l for l in avisos), avisos
        ok("Aba aberta antes do deploy (JS antigo, N 'entrar_fila') com o limite 64: 18, 22 e 64 filas entram em todas "
           "as salas, sem 'Too many packets'; o middleware registra os 3 POSTs grandes (17, 21 e 63 entrar_fila)")

        inicio_log = len(log_servidor())

        ctx, pg, enviados = abrir(nav, base, cen["22"])
        for i in range(2):
            sid_antes = pg.evaluate("window.__socketAtendimento.id")
            pg.evaluate("window.__socketAtendimento.io.engine.close()")
            pg.wait_for_function(f"() => window.__socketAtendimento.connected && window.__socketAtendimento.id !== {json.dumps(sid_antes)}",
                                 timeout=30000)
            assert len(salas_da_pagina(pg, base, 22)) == 22
        time.sleep(1)
        assert contar(enviados, "entrar_filas") == 3, contar(enviados, "entrar_filas")
        assert recebe_fila_atualizada(pg, base, cen["22"]["fila_ids"][-1])
        ctx.close()
        ok("Reconexão: 2 quedas da conexão -> exatamente 1 'entrar_filas' por connect (3 no total), 22 salas de novo "
           "e fila_atualizada da última fila")

        ctx = nav.new_context()
        pg = ctx.new_page()
        enviados = []
        pg.on("request", lambda r: enviados.append(r.post_data or "") if "/socket.io/" in r.url and r.method == "POST" else None)
        pg.on("websocket", lambda w: w.on("framesent", lambda f: enviados.append(str(f))))
        pg.goto(f"{base}/painel/tv/{cen['tv']['slug']}")
        pg.wait_for_function("() => document.getElementById('status-conexao').textContent.trim().length > 0 "
                             "&& document.getElementById('status-conexao').classList.contains('conectado')", timeout=20000)
        time.sleep(1)
        assert contar(enviados, "entrar_tv") == 1 and contar(enviados, "entrar_fila") == 0 and contar(enviados, "entrar_filas") == 0
        ctx.close()
        nav.close()
    ok("TV (22 filas): 1 pacote 'entrar_tv' por conexão, nenhum entrar_fila/entrar_filas")
    trecho = log_servidor()[inicio_log:]
    assert "Too many packets" not in trecho and "POST com" not in trecho, trecho[-2000:]
    ok("Reconexão e TV: nenhum 'Too many packets' e nenhum WARNING do middleware")
finally:
    for proc in servidores:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
    shutil.rmtree(PASTA, ignore_errors=True)

print()
print(f"TODOS OS {len(passos)} TESTES DO SOCKET.IO COM MUITAS FILAS PASSARAM")
