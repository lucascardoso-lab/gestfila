"""
Teste de regressão: recado "Observações" no bloco de cima da tela de
Atendimento (adendo 10.85) - a etapa atual deixa um recado para a próxima
(ex.: recepção -> coleta: "em jejum"), salvo junto com o Nome no Realocar/
Finalizar/Aguardar, nas duas telas (painel_local.html e painel_fila.html).

Cobre:
1) Fluxo combinado: a recepção escreve o recado e realoca -> o recado aparece
   na espera da coleta (tela por Local, HTML e /dados) -> o executor chama e o
   campo vem preenchido -> finaliza (recado mantido).
2) Apagar o texto e salvar remove o recado (None); só espaços também.
3) POST sem o campo (outro lugar, teste antigo, campo desabilitado) NÃO apaga.
4) Acima de 300 caracteres: recusa a ação inteira sem gravar nada (nem nome,
   nem recado, nem status, nem log) - nas 3 ações x 2 telas; 300 é aceito.
5) Nenhum LogAtendimento além do evento da própria ação (sem chamada_nome).
6) TV: nenhum evento enviado para as salas das filas leva o recado; o
   payload 'chamada' mantém as mesmas chaves.
7) HTML/script no recado é escapado no Jinja (espera e campo do bloco de
   cima) e no JS (linhaEspera/renderizarSenhaAtual no refresh).
8) Achado do 10.85 (Nome e Observações): com o cursor no campo, a troca da
   senha chamada sobrescreve o texto antigo (não leva o texto do paciente
   anterior para o seguinte); com a MESMA senha, o que está sendo digitado é
   respeitado. Sem senha chamada, Observações fica vazio e desabilitado.
8b) 2º achado do 10.85: texto digitado em Nome e Observações e o foco levado
   para a busca do Redirecionar - um refresh da MESMA senha (banco ainda
   vazio) NÃO apaga o que foi digitado; o Realocar real pela tela leva os
   dois. Se a senha mudar, os dois são sobrescritos mesmo editados. Campo
   não editado continua acompanhando o banco.
9) Rota de edição inline removida; maxlength do HTML = LIMITE_OBSERVACOES;
   1366x768, temas claro e escuro, fonte padrão e larga: o campo cabe no card,
   sem rolagem horizontal; campo desabilitado usa tokens do tema.

A parte de navegador (7, 8, 9) roda o JS REAL das telas num Chromium
(Playwright): cada requisição da página é respondida pelo próprio Flask (test
client logado) e só o socket.io é trocado por um stub que deixa o teste
disparar 'fila_atualizada'.

Execução: python -m tests.test_regressao_observacoes_recado (a partir da raiz
do projeto, com o virtualenv ativado).
"""
import os

os.environ.setdefault("SECRET_KEY", "test")
os.environ.setdefault("FLASK_ENV", "testing")

import html
import re
from datetime import datetime, timedelta
from urllib.parse import urlsplit

from playwright.sync_api import sync_playwright

from app import create_app
from app.atendimento.helpers import LIMITE_OBSERVACOES
from app.extensions import db, socketio
from app.models import Empresa, Fila, Local, LocalRodizioFila, LogAtendimento, Perfil, Senha, Unidade, Usuario

app = create_app("testing")

passos = []


def ok(msg):
    passos.append(msg)
    print("OK:", msg)


RECADO = "em jejum"
RECADO_HTML = '<script>alert(1)</script>"><img src=x onerror=alert(2)>'

with app.app_context():
    db.create_all()

    perfil_atendente = Perfil(nome="atendente")
    perfil_executor = Perfil(nome="executor")
    db.session.add_all([perfil_atendente, perfil_executor])
    db.session.commit()

    empresa = Empresa(nome="Clinica Recado")
    db.session.add(empresa)
    db.session.commit()
    unidade = Unidade(empresa_id=empresa.id, nome="Matriz", sigla="MAT")
    db.session.add(unidade)
    db.session.commit()

    fila_rec = Fila(empresa_id=empresa.id, unidade_id=unidade.id, nome="Recepção")
    fila_col = Fila(empresa_id=empresa.id, unidade_id=unidade.id, nome="Coleta")
    db.session.add_all([fila_rec, fila_col])
    db.session.commit()

    def novo_local(nome):
        loc = Local(empresa_id=empresa.id, unidade_id=unidade.id, nome=nome,
                    nome_exibicao=nome, identificador_admin=f"[MAT] {nome}")
        db.session.add(loc)
        db.session.commit()
        return loc

    guiche = novo_local("Guichê 1")
    sala = novo_local("Sala 1")
    db.session.add_all([
        LocalRodizioFila(local_id=guiche.id, fila_id=fila_rec.id, ordem=0, chamadas_seguidas=1),
        LocalRodizioFila(local_id=sala.id, fila_id=fila_col.id, ordem=0, chamadas_seguidas=1),
    ])
    db.session.commit()

    agora = datetime.utcnow()
    seq = iter(range(1, 100))
    base_senha = dict(empresa_id=empresa.id, unidade_id=unidade.id)

    def nova_senha(fila_id, prefixo, **extra):
        n = next(seq)
        with app.app_context():
            s = Senha(**base_senha, fila_id=fila_id, codigo=f"{prefixo}{n:03d}", sequencial=n,
                      prioridade=False, status="aguardando",
                      criada_em=agora - timedelta(minutes=100 - n), **extra)
            db.session.add(s)
            db.session.commit()
            return s.id, s.codigo

    def novo_usuario(login, perfil):
        u = Usuario(empresa_id=empresa.id, nome=login, login=login)
        u.set_senha("senha123")
        u.perfis = [perfil]
        u.unidades = [unidade]
        db.session.add(u)
        db.session.commit()

    novo_usuario("atendente_recado", perfil_atendente)
    novo_usuario("executor_recado", perfil_executor)

    ids = dict(fila_rec=fila_rec.id, fila_col=fila_col.id, guiche=guiche.id, sala=sala.id)


def login_como(login, local_id):
    c = app.test_client()
    resp = c.post("/login", data={"login": login, "senha": "senha123"}, follow_redirects=True)
    assert resp.status_code == 200, (login, resp.status_code)
    with c.session_transaction() as sess:
        sess["atendimento_local_id"] = local_id
    return c


def senha(senha_id):
    with app.app_context():
        s = db.session.get(Senha, senha_id)
        db.session.expunge(s)
        return s


def eventos_log(senha_id):
    with app.app_context():
        return [l.evento for l in LogAtendimento.query.filter_by(senha_id=senha_id).order_by(LogAtendimento.id)]


def pagina(cliente, url):
    resp = cliente.get(url)
    assert resp.status_code == 200, (url, resp.status_code)
    return resp.get_data(as_text=True)


def corpo_espera(texto):
    return texto.split('<tbody id="corpo-espera">', 1)[1].split("</tbody>", 1)[0]


def input_observacoes(texto):
    return re.search(r'<input type="text" id="observacoes"[^>]*>', texto).group(0)


c_rec = login_como("atendente_recado", ids["guiche"])
c_col = login_como("executor_recado", ids["sala"])
URL_REC_LOCAL = f"/atendimento/recepcao/local/{ids['guiche']}"
URL_COL_LOCAL = f"/atendimento/coleta/local/{ids['sala']}"
URL_REC_FILA = f"/atendimento/recepcao/filas/{ids['fila_rec']}"

# TV "ouvindo" as duas filas durante o teste inteiro (seção 6)
NAMESPACE = "/painel"
ws_tv = socketio.test_client(app, namespace=NAMESPACE, flask_test_client=c_rec)
for fid in (ids["fila_rec"], ids["fila_col"]):
    ws_tv.emit("entrar_fila", {"fila_id": fid}, namespace=NAMESPACE)
ws_tv.get_received(NAMESPACE)
eventos_tv = []


def coletar_tv():
    eventos_tv.extend(ws_tv.get_received(NAMESPACE))


# ===========================================================================
# 9a) Estrutura: rota inline removida, campo nas 2 telas, maxlength = limite.
# ===========================================================================
regras = {r.rule for r in app.url_map.iter_rules()}
assert not any(r.endswith("/observacoes") for r in regras), [r for r in regras if "observ" in r]
assert "salvar_observacoes" not in {r.endpoint.split(".")[-1] for r in app.url_map.iter_rules()}
for url in (URL_REC_LOCAL, URL_REC_FILA):
    texto = pagina(c_rec, url)
    campo = input_observacoes(texto)
    assert f'maxlength="{LIMITE_OBSERVACOES}"' in campo and LIMITE_OBSERVACOES == 300, (url, campo)
    assert " disabled" in campo and 'value=""' in campo, (url, campo)  # sem senha chamada
    assert "event.preventDefault()" in campo, (url, campo)  # Enter bloqueado, igual ao Nome
    # dentro do form das ações (vai junto no Realocar/Finalizar/Aguardar)
    form = texto.split('<form id="form-acoes"', 1)[1].split("</form>", 1)[0]
    assert 'id="observacoes"' in form, url
    assert "inline-observacoes" not in texto and "salvar_observacoes" not in texto, url
    assert "<th>Observações</th>" in texto, url
ok("Rota inline removida; campo Observações no form das ações das 2 telas, maxlength=300, Enter bloqueado, desabilitado sem senha")

# ===========================================================================
# 1) Fluxo combinado recepção -> coleta (tela por Local) + 5) logs + 6) TV.
# ===========================================================================
r1_id, r1_cod = nova_senha(ids["fila_rec"], "REC")

resp = c_rec.post(f"{URL_REC_LOCAL}/chamar", data={"codigo": r1_cod}, follow_redirects=True)
assert resp.status_code == 200 and senha(r1_id).status == "chamada"
texto = pagina(c_rec, URL_REC_LOCAL)
campo = input_observacoes(texto)
assert " disabled" not in campo and 'value=""' in campo, campo
coletar_tv()
logs_antes = eventos_log(r1_id)

resp = c_rec.post(f"{URL_REC_LOCAL}/realocar", data={
    "fila_destino_id": ids["fila_col"], "nome": "Maria Souza", "observacoes": f"  {RECADO}  ",
}, follow_redirects=True)
assert resp.status_code == 200
s = senha(r1_id)
assert s.fila_id == ids["fila_col"] and s.status == "aguardando", (s.fila_id, s.status)
assert s.observacoes == RECADO and s.nome_paciente == "Maria Souza", (s.observacoes, s.nome_paciente)
assert eventos_log(r1_id) == logs_antes + ["realocado"], eventos_log(r1_id)
ok("Recepção: recado (sem espaços nas pontas) salvo junto com o Nome no Realocar; só o log 'realocado'")

texto = pagina(c_col, URL_COL_LOCAL)
assert f"<td class=\"col-observacoes\">{RECADO}</td>" in corpo_espera(texto), corpo_espera(texto)
assert " disabled" in input_observacoes(texto)  # coleta ainda sem senha chamada
dados = c_col.get(f"{URL_COL_LOCAL}/dados").get_json()
assert dados["senha_atual"] is None
assert {x["codigo"]: x["observacoes"] for x in dados["espera"]} == {r1_cod: RECADO}, dados["espera"]
ok("Coleta (tela por Local): recado aparece na espera, no HTML e no /dados, antes de chamar")

resp = c_col.post(f"{URL_COL_LOCAL}/chamar", data={"codigo": r1_cod}, follow_redirects=True)
assert resp.status_code == 200 and senha(r1_id).status == "chamada"
texto = pagina(c_col, URL_COL_LOCAL)
campo = input_observacoes(texto)
assert f'value="{RECADO}"' in campo and " disabled" not in campo, campo
dados = c_col.get(f"{URL_COL_LOCAL}/dados").get_json()
assert dados["senha_atual"]["observacoes"] == RECADO, dados["senha_atual"]
ok("Coleta: o executor chama e o campo vem preenchido com o recado (HTML e /dados)")

logs_antes = eventos_log(r1_id)
s_antes = senha(r1_id)
resp = c_col.post(f"{URL_COL_LOCAL}/finalizar", data={"nome": "Maria Souza", "observacoes": RECADO},
                  follow_redirects=True)
assert resp.status_code == 200
s = senha(r1_id)
assert s.status == "finalizada" and s.observacoes == RECADO, (s.status, s.observacoes)
assert s.chamada_nome_em == s_antes.chamada_nome_em and s.criada_em == s_antes.criada_em
assert eventos_log(r1_id) == logs_antes + ["fim_atendimento"], eventos_log(r1_id)
ok("Coleta: Finalizar mantém o recado; só o log 'fim_atendimento' (sem chamada_nome), timestamps intactos")

# ===========================================================================
# 2) Apagar remove / 3) campo ausente mantém - nas 2 telas.
# ===========================================================================
for tela, url in (("painel_local", URL_REC_LOCAL), ("painel_fila", URL_REC_FILA)):
    sid, cod = nova_senha(ids["fila_rec"], "REC")

    def chamar():
        r = c_rec.post(f"{url}/chamar", data={"codigo": cod}, follow_redirects=True)
        assert r.status_code == 200 and senha(sid).status == "chamada", (tela, senha(sid).status)

    chamar()
    c_rec.post(f"{url}/aguardar", data={"observacoes": "pedido incompleto"})
    assert senha(sid).observacoes == "pedido incompleto" and senha(sid).status == "aguardando", tela

    chamar()
    c_rec.post(f"{url}/aguardar", data={})  # campo AUSENTE (POST antigo / campo desabilitado)
    assert senha(sid).observacoes == "pedido incompleto", (tela, senha(sid).observacoes)

    chamar()
    c_rec.post(f"{url}/aguardar", data={"nome": "Fulano"})  # só o Nome, sem o campo
    assert senha(sid).observacoes == "pedido incompleto" and senha(sid).nome_paciente == "Fulano", tela

    chamar()
    c_rec.post(f"{url}/aguardar", data={"observacoes": "   "})  # enviado só com espaços = vazio
    assert senha(sid).observacoes is None, (tela, senha(sid).observacoes)

    chamar()
    c_rec.post(f"{url}/aguardar", data={"observacoes": "de novo"})
    chamar()
    c_rec.post(f"{url}/finalizar", data={"observacoes": ""})  # enviado vazio
    assert senha(sid).observacoes is None and senha(sid).status == "finalizada", tela

    assert "chamada_nome" not in eventos_log(sid), (tela, eventos_log(sid))
ok("Nas 2 telas: campo enviado vazio (ou só espaços) apaga; campo ausente mantém o recado; nenhum chamada_nome")

# ===========================================================================
# 4) Acima do limite: recusa a ação inteira, nada gravado - 3 ações x 2 telas.
# ===========================================================================
for tela, url in (("painel_local", URL_REC_LOCAL), ("painel_fila", URL_REC_FILA)):
    for acao in ("realocar", "finalizar", "aguardar"):
        sid, cod = nova_senha(ids["fila_rec"], "REC", observacoes="recado antigo")
        c_rec.post(f"{url}/chamar", data={"codigo": cod})
        antes = senha(sid)
        logs_antes = eventos_log(sid)
        coletar_tv()
        dados_post = {"nome": "Nome Novo", "observacoes": "a" * (LIMITE_OBSERVACOES + 1)}
        if acao == "realocar":
            dados_post["fila_destino_id"] = ids["fila_col"]
        resp = c_rec.post(f"{url}/{acao}", data=dados_post, follow_redirects=True)
        assert resp.status_code == 200, (tela, acao)
        assert f"mais de {LIMITE_OBSERVACOES} caracteres" in resp.get_data(as_text=True), (tela, acao)
        depois = senha(sid)
        for campo_nome in ("status", "fila_id", "local_id", "operador_id", "nome_paciente", "chamada_nome_em",
                           "chamada_em", "finalizada_em", "observacoes"):
            assert getattr(depois, campo_nome) == getattr(antes, campo_nome), (tela, acao, campo_nome)
        assert depois.nome_paciente is None and depois.observacoes == "recado antigo", (tela, acao)
        assert eventos_log(sid) == logs_antes, (tela, acao, eventos_log(sid))
        assert ws_tv.get_received(NAMESPACE) == [], (tela, acao)  # nem 'chamada' por nome, nem fila_atualizada

        # exatamente no limite: aceito (e libera o operador para a próxima rodada)
        dados_post["observacoes"] = "b" * LIMITE_OBSERVACOES
        c_rec.post(f"{url}/{acao}", data=dados_post)
        assert senha(sid).observacoes == "b" * LIMITE_OBSERVACOES, (tela, acao)
        assert senha(sid).status in ("aguardando", "finalizada"), (tela, acao, senha(sid).status)
        coletar_tv()
ok("Acima de 300 caracteres: Realocar/Finalizar/Aguardar recusados nas 2 telas sem gravar nome, recado, status, log ou TV; 300 aceito")

# ===========================================================================
# 6) TV: nenhum evento das salas das filas carregou recado nenhum.
# ===========================================================================
coletar_tv()
assert eventos_tv, "a TV deveria ter recebido eventos (chamadas/atualizações)"
for ev in eventos_tv:
    bruto = repr(ev)
    assert RECADO not in bruto and "pedido incompleto" not in bruto and "observ" not in bruto, ev
    assert "recado antigo" not in bruto and "bbbb" not in bruto, ev
chamadas = [ev["args"][0] for ev in eventos_tv if ev["name"] == "chamada"]
assert chamadas, eventos_tv
for p in chamadas:
    assert set(p) <= {"tipo", "local", "nome_paciente", "senha", "local_audio_slug", "cor", "repetir"}, p
ok(f"TV: {len(eventos_tv)} eventos recebidos nas salas das filas, nenhum com o recado; payload 'chamada' com as chaves de sempre")

# ===========================================================================
# 7a) Escape no Jinja: espera e campo do bloco de cima.
# ===========================================================================
x_id, x_cod = nova_senha(ids["fila_col"], "COL", observacoes=RECADO_HTML, nome_paciente="Paciente X")
texto = pagina(c_col, URL_COL_LOCAL)
assert RECADO_HTML not in texto and "<img src=x" not in texto, "recado cru no HTML"
celulas = [html.unescape(t) for t in re.findall(r"<td class=\"col-observacoes\">([^<]*alert\(1\)[^<]*)</td>", corpo_espera(texto))]
assert celulas == [RECADO_HTML], corpo_espera(texto)  # escapado (sem "<" cru) e, desescapado, o texto original
c_col.post(f"{URL_COL_LOCAL}/chamar", data={"codigo": x_cod})
texto = pagina(c_col, URL_COL_LOCAL)
campo = input_observacoes(texto)
valor_campo = re.search(r'value="([^"]*)"', campo).group(1)
assert RECADO_HTML not in texto and "<" not in valor_campo and html.unescape(valor_campo) == RECADO_HTML, campo
ok("Jinja: recado com HTML/script é escapado na espera e no value do campo do bloco de cima")

# ===========================================================================
# Navegador: 7b) escape no JS, 8) troca de senha com o campo em foco, 9b) layout.
# ===========================================================================
STUB_SOCKET = """
window.__handlers = {};
window.io = function () {
  return { on(ev, fn) { (window.__handlers[ev] = window.__handlers[ev] || []).push(fn); }, emit() {} };
};
"""
# conta respostas de /dados já RENDERIZADAS (o setTimeout roda depois do
# .then(renderizarDados) da tela)
INIT_FETCH = """
window.__renders = 0;
const __fetch = window.fetch;
window.fetch = (...args) => __fetch(...args).then((r) => {
  const json = r.json.bind(r);
  r.json = () => json().then((d) => { setTimeout(() => { window.__renders++; }, 0); return d; });
  return r;
});
"""
BASE = "http://gestfila.teste"


def abrir(navegador, cliente, url, viewport=None):
    pg = navegador.new_page(viewport=viewport or {"width": 1366, "height": 768})
    pg.add_init_script(INIT_FETCH)

    def responder(route, request):
        partes = urlsplit(request.url)
        if partes.path.endswith("socket.io.min.js"):
            return route.fulfill(status=200, content_type="application/javascript", body=STUB_SOCKET)
        caminho = partes.path + (f"?{partes.query}" if partes.query else "")
        if request.method == "POST":  # botões do form (ex.: Finalizar real pela tela)
            r = cliente.post(caminho, data=request.post_data_buffer,
                             content_type=request.headers.get("content-type"))
        else:
            r = cliente.get(caminho)
        cabecalhos = {"content-type": r.headers.get("Content-Type", "text/html")}
        if "Location" in r.headers:
            cabecalhos["location"] = r.headers["Location"]
        route.fulfill(status=r.status_code, headers=cabecalhos, body=r.get_data())

    pg.route(f"{BASE}/**", responder)
    pg.goto(BASE + url)
    return pg


def refresh(pg):
    n = pg.evaluate("window.__renders")
    pg.evaluate("window.__handlers.fila_atualizada.forEach((f) => f())")
    pg.wait_for_function(f"window.__renders > {n}")


def valor(pg, campo_id):
    return pg.eval_on_selector(f"#{campo_id}", "e => ({v: e.value, d: e.disabled})")


with sync_playwright() as pw:
    navegador = pw.chromium.launch()

    # 7b) Escape no JS: linhaEspera e renderizarSenhaAtual (refresh via /dados).
    y_id, y_cod = nova_senha(ids["fila_col"], "COL", observacoes=RECADO_HTML)
    pg = abrir(navegador, c_col, URL_COL_LOCAL)
    refresh(pg)
    linha = pg.eval_on_selector(f'#corpo-espera tr[data-senha-id="{y_id}"]',
                                "tr => ({obs: tr.children[5].textContent, perigosos: tr.querySelectorAll('img, script').length})")
    assert linha == {"obs": RECADO_HTML, "perigosos": 0}, linha
    assert valor(pg, "observacoes") == {"v": RECADO_HTML, "d": False}, valor(pg, "observacoes")  # X chamada
    assert pg.evaluate("document.querySelectorAll('#corpo-espera img, #corpo-espera script').length") == 0
    pg.close()
    ok("JS (refresh via /dados): recado com HTML/script vira texto na espera e no campo, nenhum elemento injetado")

    # libera a coleta: finaliza X sem o campo (mantém o recado)
    c_col.post(f"{URL_COL_LOCAL}/finalizar", data={})
    assert senha(x_id).observacoes == RECADO_HTML

    # 8) Troca da senha chamada com o cursor no campo - Nome e Observações.
    for tela, url_base in (("painel_local", URL_COL_LOCAL), ("painel_fila", f"/atendimento/coleta/filas/{ids['fila_col']}")):
        for campo_id in ("nome", "observacoes"):
            a_id, a_cod = nova_senha(ids["fila_col"], "COL", nome_paciente="Paciente A", observacoes="recado A")
            b_id, b_cod = nova_senha(ids["fila_col"], "COL", nome_paciente="Paciente B", observacoes="recado B")
            c_id, c_cod = nova_senha(ids["fila_col"], "COL", nome_paciente="Paciente C", observacoes="recado C")
            c_col.post(f"{url_base}/chamar", data={"codigo": a_cod})
            pg = abrir(navegador, c_col, url_base)
            assert valor(pg, "nome")["v"] == "Paciente A" and valor(pg, "observacoes") == {"v": "recado A", "d": False}

            # MESMA senha + campo em foco: o que está sendo digitado fica
            pg.fill(f"#{campo_id}", "digitando para A")
            refresh(pg)
            assert pg.evaluate("document.activeElement.id") == campo_id
            assert valor(pg, campo_id)["v"] == "digitando para A", (tela, campo_id, valor(pg, campo_id))

            # senha MUDA (A -> B direto) com o campo em foco: sobrescreve
            c_col.post(f"{url_base}/finalizar", data={})
            c_col.post(f"{url_base}/chamar", data={"codigo": b_cod})
            refresh(pg)
            assert pg.evaluate("document.activeElement.id") == campo_id
            assert valor(pg, "nome")["v"] == "Paciente B", (tela, campo_id, valor(pg, "nome"))
            assert valor(pg, "observacoes") == {"v": "recado B", "d": False}, (tela, campo_id, valor(pg, "observacoes"))

            # B -> (sem senha) -> C, com o campo em foco o tempo todo
            pg.fill(f"#{campo_id}", "digitando para B")
            c_col.post(f"{url_base}/finalizar", data={})
            refresh(pg)
            assert valor(pg, "observacoes") == {"v": "", "d": True}, (tela, campo_id, valor(pg, "observacoes"))
            c_col.post(f"{url_base}/chamar", data={"codigo": c_cod})
            refresh(pg)
            assert valor(pg, "nome")["v"] == "Paciente C", (tela, campo_id, valor(pg, "nome"))
            assert valor(pg, "observacoes") == {"v": "recado C", "d": False}, (tela, campo_id, valor(pg, "observacoes"))

            # e o que o navegador enviaria agora é o da senha C (Finalizar real pela tela)
            pg.click("button[formaction$='/finalizar']")
            pg.wait_for_load_state()
            assert senha(c_id).status == "finalizada", (tela, campo_id, senha(c_id).status)
            assert senha(c_id).nome_paciente == "Paciente C" and senha(c_id).observacoes == "recado C", (tela, campo_id)
            assert senha(b_id).nome_paciente == "Paciente B" and senha(b_id).observacoes == "recado B", (tela, campo_id)
            pg.close()
    ok("Achado 10.85: com o cursor em Nome ou Observações, a troca da senha chamada sobrescreve o texto antigo "
       "(nas 2 telas); mesma senha respeita o que está sendo digitado; sem senha, Observações vazio e desabilitado")

    # 8b) 2º achado: digitou, tirou o foco (busca do Redirecionar), chegou um
    # refresh da MESMA senha com nome/recado ainda vazios no banco.
    for tela, url_base in (("painel_local", URL_COL_LOCAL), ("painel_fila", f"/atendimento/coleta/filas/{ids['fila_col']}")):
        d_id, d_cod = nova_senha(ids["fila_col"], "COL")  # sem nome e sem recado no banco
        e_id, e_cod = nova_senha(ids["fila_col"], "COL", nome_paciente="Paciente E", observacoes="recado E")
        c_col.post(f"{url_base}/chamar", data={"codigo": d_cod})
        pg = abrir(navegador, c_col, url_base)
        assert valor(pg, "nome")["v"] == "" and valor(pg, "observacoes") == {"v": "", "d": False}, tela

        pg.fill("#nome", "Digitado D")
        pg.fill("#observacoes", "recado digitado D")
        pg.focus("#busca-redirecionar")
        assert pg.evaluate("document.activeElement.id") == "busca-redirecionar", tela
        contagem_antes = int(pg.inner_text("#contagem-espera"))
        nova_senha(ids["fila_col"], "COL")  # "nova senha no totem" -> fila_atualizada
        refresh(pg)
        assert int(pg.inner_text("#contagem-espera")) == contagem_antes + 1, tela  # o refresh rodou mesmo
        dados = c_col.get(f"{url_base}/dados").get_json()["senha_atual"]
        assert dados["id"] == d_id and dados["nome_paciente"] is None and dados["observacoes"] is None, dados
        assert valor(pg, "nome")["v"] == "Digitado D", (tela, valor(pg, "nome"))
        assert valor(pg, "observacoes") == {"v": "recado digitado D", "d": False}, (tela, valor(pg, "observacoes"))

        # um 2º refresh (outra senha no totem) também não apaga
        nova_senha(ids["fila_col"], "COL")
        refresh(pg)
        assert valor(pg, "nome")["v"] == "Digitado D" and valor(pg, "observacoes")["v"] == "recado digitado D", tela

        # a senha MUDA: os dois são sobrescritos mesmo editados (e a marca é limpa)
        c_col.post(f"{url_base}/finalizar", data={})
        c_col.post(f"{url_base}/chamar", data={"codigo": e_cod})
        refresh(pg)
        assert valor(pg, "nome")["v"] == "Paciente E", (tela, valor(pg, "nome"))
        assert valor(pg, "observacoes") == {"v": "recado E", "d": False}, (tela, valor(pg, "observacoes"))
        with app.app_context():
            db.session.get(Senha, e_id).observacoes = "recado E corrigido no banco"
            db.session.commit()
        refresh(pg)  # E não foi editada nesta tela -> segue o banco
        assert valor(pg, "observacoes")["v"] == "recado E corrigido no banco", (tela, valor(pg, "observacoes"))
        pg.close()

        # fluxo real pela tela: digita, vai à busca do Redirecionar, chega um
        # refresh, escolhe a fila e clica Realocar -> nome e recado vão juntos
        c_col.post(f"{url_base}/finalizar", data={})
        f_id, f_cod = nova_senha(ids["fila_col"], "COL")
        c_col.post(f"{url_base}/chamar", data={"codigo": f_cod})
        pg = abrir(navegador, c_col, url_base)
        pg.fill("#nome", "Digitado F")
        pg.fill("#observacoes", "pedido incompleto")
        pg.focus("#busca-redirecionar")
        nova_senha(ids["fila_col"], "COL")
        refresh(pg)
        pg.evaluate("""(id) => { const sel = document.querySelector("#form-acoes [name='fila_destino_id']");
                       sel.value = String(id); }""", ids["fila_rec"])
        pg.click("button[formaction$='/realocar']")
        pg.wait_for_load_state()
        s = senha(f_id)
        assert s.fila_id == ids["fila_rec"] and s.status == "aguardando", (tela, s.fila_id, s.status)
        assert s.nome_paciente == "Digitado F" and s.observacoes == "pedido incompleto", (tela, s.nome_paciente, s.observacoes)
        pg.close()
    ok("2º achado 10.85: Nome e Observações digitados e fora de foco (busca do Redirecionar) sobrevivem a refreshes "
       "da MESMA senha e vão no Realocar real; senha nova sobrescreve mesmo editados; campo não editado segue o banco")

    # 9b) Layout 1366x768: com senha chamada e recado de 300 caracteres, o campo
    # cabe no card e não há rolagem horizontal; sem senha, campo desabilitado em tokens.
    l_id, l_cod = nova_senha(ids["fila_col"], "COL", nome_paciente="Maria Aparecida dos Santos Oliveira",
                             observacoes="x" * LIMITE_OBSERVACOES)
    # recados longos também na espera: 300 sem espaço (código colado) e 300 com espaços
    nova_senha(ids["fila_col"], "COL", observacoes="y" * LIMITE_OBSERVACOES)
    nova_senha(ids["fila_col"], "COL", observacoes=("pedido incompleto, em jejum " * 12)[:LIMITE_OBSERVACOES])
    FONTE_LARGA = "body, input, button { font-family: Verdana, 'DejaVu Sans', sans-serif !important; }"
    url_fila_col = f"/atendimento/coleta/filas/{ids['fila_col']}"
    for com_senha in (False, True):
        if com_senha:
            c_col.post(f"{URL_COL_LOCAL}/chamar", data={"codigo": l_cod})
        for tema in ("light", "dark"):
            for fonte in (None, FONTE_LARGA):
                for url in (URL_COL_LOCAL, url_fila_col):
                    pg = abrir(navegador, c_col, url)
                    if fonte:
                        pg.add_style_tag(content=fonte)
                    pg.evaluate(f"document.documentElement.setAttribute('data-theme', '{tema}')")
                    m = pg.evaluate("""() => {
                        const el = document.getElementById('observacoes');
                        const card = el.closest('.card');
                        const ec = card.getBoundingClientRect(), ei = el.getBoundingClientRect();
                        const pad = parseFloat(getComputedStyle(card).paddingRight);
                        const sonda = document.createElement('div');
                        sonda.style.background = 'var(--gf-borda)';
                        document.body.appendChild(sonda);
                        const borda = getComputedStyle(sonda).backgroundColor;
                        sonda.remove();
                        const espera = document.getElementById('tabela-espera');
                        const cardEspera = espera.closest('.card');
                        const ce = getComputedStyle(cardEspera);
                        return {dentro: ei.right <= ec.right - pad + 1, largura: ei.width,
                                pagina: document.documentElement.scrollWidth,
                                disabled: el.disabled, fundo: getComputedStyle(el).backgroundColor, borda,
                                altura_card: ec.height,
                                espera_cabe: espera.scrollWidth <= cardEspera.clientWidth - parseFloat(ce.paddingLeft) - parseFloat(ce.paddingRight) + 1};
                    }""")
                    contexto = (com_senha, tema, bool(fonte), url, m)
                    assert m["dentro"] and m["largura"] > 300 and m["pagina"] <= 1366, contexto
                    assert m["espera_cabe"], contexto
                    assert m["disabled"] == (not com_senha), contexto
                    if not com_senha:
                        assert m["fundo"] == m["borda"], contexto  # token do tema, claro e escuro
                    if com_senha and not fonte and tema == "light":
                        print(f"   medida 1366x768 {url.split('/')[3]}: campo {m['largura']:.0f}px, "
                              f"card do bloco de cima {m['altura_card']:.0f}px de altura")
                    pg.close()
    ok("1366x768, temas claro e escuro, fonte padrão e larga, 2 telas: campo cabe no card (recado de 300), "
       "sem rolagem horizontal, espera cabe; desabilitado usa --gf-borda")
    navegador.close()

print(f"\n{len(passos)} verificações OK")
