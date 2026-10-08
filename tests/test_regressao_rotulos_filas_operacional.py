"""
Teste de regressão: rótulo de fila "[SIGLA] Nome" fora dos relatórios
(adendo 10.70) - mesma função do 10.69 (app/utils/rotulos.py), migrada para
as telas operacionais e cadastros admin.

Cenário: duas unidades SEM sigla cujo fallback colide ("Anápolis - Centro
Médico"/"Anápolis - Laboratório" -> "[ANÁ]"), cada uma com uma "Coleta", mais
a Matriz (sigla "MAT") com outra "Coleta". Cobre:

1) O mesmo rótulo (com " (id)" nas duas "[ANÁ] Coleta") em cada ponto migrado:
   JSON embutido do Iniciar Turno (A1), JSON do Redirecionar (A2), select de
   Fila do cadastro de Botão do totem (A3), checklists de Local e TV, tela por
   Local (HTML + JSON de /dados) - idêntico ao dos relatórios (10.69).
2) A busca continua pelo nome PURO, executando o JS de verdade (Chromium via
   Playwright, mesmo motor já usado pela exportação em PDF): digitar "coleta"
   no Iniciar Turno e no Redirecionar acha as Coletas, exibidas com o rótulo.
3) O payload de chamada da TV (`_payload_chamada`, evento 'chamada') NÃO
   ganha o rótulo administrativo - nem chave nova, nem prefixo de unidade.
4) rotulo_fila() fora de request (app_context sem test_request_context):
   funciona e não usa cache - reflete na hora uma sigla cadastrada ou uma
   fila renomeada no mesmo app context.

Execução: python -m tests.test_regressao_rotulos_filas_operacional (a partir
da raiz do projeto, com o virtualenv ativado).
"""
import os

os.environ.setdefault("SECRET_KEY", "test")
os.environ.setdefault("FLASK_ENV", "testing")

import html
import json
import re

from playwright.sync_api import sync_playwright

from app import create_app
from app.extensions import db, socketio
from app.models import Empresa, Fila, Local, LocalRodizioFila, Perfil, Senha, Unidade, Usuario
from app.utils.rotulos import rotulos_filas

app = create_app("testing")

passos = []


def ok(msg):
    passos.append(msg)
    print("OK:", msg)


with app.app_context():
    db.create_all()

    perfil_atendente = Perfil(nome="atendente")
    perfil_admin = Perfil(nome="admin")
    db.session.add_all([perfil_atendente, perfil_admin])
    db.session.commit()

    empresa = Empresa(nome="Clinica Operacional")
    db.session.add(empresa)
    db.session.commit()

    ana_cm = Unidade(empresa_id=empresa.id, nome="Anápolis - Centro Médico")
    ana_lab = Unidade(empresa_id=empresa.id, nome="Anápolis - Laboratório")
    matriz = Unidade(empresa_id=empresa.id, nome="Matriz", sigla="MAT")
    db.session.add_all([ana_cm, ana_lab, matriz])
    db.session.commit()

    def nova_fila(unidade, nome):
        f = Fila(empresa_id=empresa.id, unidade_id=unidade.id, nome=nome)
        db.session.add(f)
        db.session.commit()
        return f

    recepcao_cm = nova_fila(ana_cm, "Recepção")
    coleta_cm = nova_fila(ana_cm, "Coleta")
    coleta_lab = nova_fila(ana_lab, "Coleta")
    coleta_mat = nova_fila(matriz, "Coleta")

    local = Local(
        empresa_id=empresa.id, unidade_id=ana_cm.id, nome="Guichê 1",
        nome_exibicao="Guichê 1", identificador_admin="[ANÁ] Guichê 1",
    )
    db.session.add(local)
    db.session.commit()
    db.session.add_all([
        LocalRodizioFila(local_id=local.id, fila_id=recepcao_cm.id, ordem=0, chamadas_seguidas=1),
        LocalRodizioFila(local_id=local.id, fila_id=coleta_cm.id, ordem=1, chamadas_seguidas=1),
    ])
    db.session.commit()

    for fila, codigo in ((recepcao_cm, "REC001"), (coleta_cm, "COL001")):
        db.session.add(Senha(
            empresa_id=empresa.id, unidade_id=fila.unidade_id, fila_id=fila.id,
            codigo=codigo, sequencial=1, prioridade=False, status="aguardando",
        ))
    db.session.commit()

    atendente = Usuario(empresa_id=empresa.id, nome="Atendente", login="atendente_rotulos")
    atendente.set_senha("senha123")
    atendente.perfis = [perfil_atendente]
    atendente.unidades = [ana_cm, matriz]
    admin = Usuario(empresa_id=empresa.id, nome="Admin", login="admin_rotulos_op")
    admin.set_senha("senha123")
    admin.perfis = [perfil_admin]
    db.session.add_all([atendente, admin])
    db.session.commit()

    ids = dict(
        empresa=empresa.id, local=local.id, recepcao_cm=recepcao_cm.id,
        coleta_cm=coleta_cm.id, coleta_lab=coleta_lab.id, coleta_mat=coleta_mat.id,
    )
    ROTULOS = rotulos_filas(empresa.id)

ROTULO_COLETA_CM = f"[ANÁ] Coleta ({ids['coleta_cm']})"
ROTULO_COLETA_LAB = f"[ANÁ] Coleta ({ids['coleta_lab']})"
ROTULO_COLETA_MAT = "[MAT] Coleta"
ROTULO_RECEPCAO = "[ANÁ] Recepção"
assert ROTULOS[ids["coleta_cm"]] == ROTULO_COLETA_CM, ROTULOS
assert ROTULOS[ids["coleta_lab"]] == ROTULO_COLETA_LAB, ROTULOS
assert ROTULOS[ids["coleta_mat"]] == ROTULO_COLETA_MAT, ROTULOS
assert ROTULOS[ids["recepcao_cm"]] == ROTULO_RECEPCAO, ROTULOS
ok("rotulos_filas (app/utils/rotulos.py): fallback '[ANÁ]' colidindo ganha ' (id)' nas duas Coletas")


def login_como(login):
    c = app.test_client()
    resp = c.post("/login", data={"login": login, "senha": "senha123"}, follow_redirects=True)
    assert resp.status_code == 200, (login, resp.status_code)
    return c


def pagina(cliente, url):
    resp = cliente.get(url)
    assert resp.status_code == 200, (url, resp.status_code)
    return resp.get_data(as_text=True)


def json_embutido(texto, inicio, fim):
    trecho = texto.split(inicio, 1)[1].split(fim, 1)[0]
    return json.loads(trecho)


def buscar_no_navegador(navegador, conteudo, input_id, dropdown_id, termo):
    """Roda o JS real da página (sem servidor por trás - recursos externos
    simplesmente não carregam) e devolve os itens exibidos no dropdown."""
    pg = navegador.new_page()
    pg.set_content(re.sub(r'<link rel="stylesheet"[^>]*>', "", conteudo))
    pg.fill(f"#{input_id}", termo)
    itens = pg.eval_on_selector_all(f"#{dropdown_id} li", "els => els.map(e => e.textContent)")
    pg.close()
    return itens


c_atendente = login_como("atendente_rotulos")
c_admin = login_como("admin_rotulos_op")

# ===========================================================================
# A1) Iniciar Turno: JSON embutido traz `rotulo` (sem unidade/unidade_sigla).
# ===========================================================================
html_turno = pagina(c_atendente, "/atendimento/iniciar-turno")
filas_turno = {f["id"]: f for f in json_embutido(html_turno, "const FILAS_DISPONIVEIS = ", ";\n")}
assert filas_turno[ids["coleta_cm"]]["rotulo"] == ROTULO_COLETA_CM
assert filas_turno[ids["coleta_mat"]]["rotulo"] == ROTULO_COLETA_MAT
assert filas_turno[ids["coleta_cm"]]["nome"] == "Coleta"  # nome puro continua, para a busca
assert ids["coleta_lab"] not in filas_turno  # unidade que o atendente não acessa
assert all("unidade_sigla" not in f and "unidade" not in f for f in filas_turno.values())
assert "unidade_sigla" not in html_turno
ok("Iniciar Turno: JSON embutido com o rótulo da função única (mesmo '(id)' dos relatórios)")

# ===========================================================================
# A2 + tela por Local: chama a 1ª senha do rodízio (REC001, Recepção) e
# confere HTML, JSON do Redirecionar e /dados.
# ===========================================================================
NAMESPACE = "/painel"
ws_tv = socketio.test_client(app, namespace=NAMESPACE, flask_test_client=c_atendente)
ws_tv.get_received(NAMESPACE)
ws_tv.emit("entrar_fila", {"fila_id": ids["recepcao_cm"]}, namespace=NAMESPACE)
ws_tv.get_received(NAMESPACE)

URL_LOCAL = f"/atendimento/recepcao/local/{ids['local']}"
resp = c_atendente.post(f"{URL_LOCAL}/chamar", data={}, follow_redirects=True)
assert resp.status_code == 200

html_local = pagina(c_atendente, URL_LOCAL)
destinos = json_embutido(html_local, '<script type="application/json" id="dados-filas-destino">', "</script>")
destino_coleta = next(d for d in destinos if d["id"] == ids["coleta_cm"])
assert destino_coleta["nome_exibicao"] == ROTULO_COLETA_CM
assert destino_coleta["nome"] == "Coleta"  # busca do Redirecionar continua pelo nome puro
ok("Redirecionar: JSON embutido com o rótulo da função única em nome_exibicao (nome puro mantido)")

texto_local = html.unescape(html_local)
assert f'id="senha-chamada-fila" class="subtitulo" style="margin:0;">{ROTULO_RECEPCAO}</span>' in texto_local
assert f"<td>{ROTULO_COLETA_CM}</td>" in texto_local  # COL001 em "Senhas em espera"
ok("Tela por Local (HTML): senha chamada e 'Senhas em espera' com o rótulo")

dados = c_atendente.get(f"{URL_LOCAL}/dados").get_json()
assert dados["senha_atual"]["fila"] == ROTULO_RECEPCAO
assert [s["fila"] for s in dados["espera"]] == [ROTULO_COLETA_CM]
ok("Tela por Local (JSON /dados, refetch após 'fila_atualizada'): mesmo rótulo")

# ===========================================================================
# 3) Payload da TV: inalterado, sem rótulo administrativo.
# ===========================================================================
recebidos = ws_tv.get_received(NAMESPACE)
payload_tv = next(ev for ev in recebidos if ev["name"] == "chamada")["args"][0]
assert set(payload_tv) == {"tipo", "local", "senha", "local_audio_slug"}, payload_tv
assert payload_tv["senha"] == "REC001" and payload_tv["local"] == "Guichê 1"
assert not any("[ANÁ]" in str(v) or "Recepção" in str(v) for v in payload_tv.values()), payload_tv
atualizada = next(ev for ev in recebidos if ev["name"] == "fila_atualizada")["args"][0]
assert atualizada == {"fila_id": ids["recepcao_cm"]}
ok("Payload 'chamada' da TV sem rótulo/fila (mesmas chaves de antes); 'fila_atualizada' só leva o id")

# ===========================================================================
# A3 + checklists admin: Botão do totem, Local e TV.
# ===========================================================================
html_botao = html.unescape(pagina(c_admin, f"/admin/totem-botoes/novo?empresa_id={ids['empresa']}"))
for fila_id, rotulo in ((ids["coleta_cm"], ROTULO_COLETA_CM), (ids["coleta_lab"], ROTULO_COLETA_LAB),
                        (ids["coleta_mat"], ROTULO_COLETA_MAT)):
    assert re.search(rf'<option value="{fila_id}" data-unidade-id="\d+"\s*>{re.escape(rotulo)}</option>', html_botao), rotulo
ok("Cadastro de Botão do totem: select 'Fila' com o rótulo da função única")

for url in (f"/admin/locais/novo?empresa_id={ids['empresa']}", f"/admin/tvs/nova?empresa_id={ids['empresa']}"):
    texto = " ".join(html.unescape(pagina(c_admin, url)).split())
    for fila_id, rotulo in ((ids["coleta_cm"], ROTULO_COLETA_CM), (ids["coleta_lab"], ROTULO_COLETA_LAB)):
        assert re.search(rf'value="{fila_id}"\s*> {re.escape(rotulo)} </label>', texto), (url, rotulo)
    assert "Coleta (Anápolis" not in texto  # formato antigo "Nome (Unidade)"
ok("Checklists de Local e TV: rótulo da função única no lugar de 'Nome (Unidade)'")

# Mesmo rótulo que os relatórios (10.69) para a mesma fila.
html_rel = html.unescape(pagina(c_admin, "/relatorios/volume"))
assert f'<option value="{ids["coleta_cm"]}" data-unidade-id' in html_rel
assert re.search(rf'<option value="{ids["coleta_cm"]}"[^>]*>{re.escape(ROTULO_COLETA_CM)}</option>', html_rel)
ok("Relatórios mostram exatamente o mesmo rótulo para a mesma fila")

# ===========================================================================
# 2) Busca pelo nome puro, com o JS real da página.
# ===========================================================================
with sync_playwright() as pw:
    navegador = pw.chromium.launch()
    itens = buscar_no_navegador(navegador, html_turno, "busca-fila", "dropdown-fila", "coleta")
    assert any(ROTULO_COLETA_CM in i for i in itens) and any(ROTULO_COLETA_MAT in i for i in itens), itens
    assert not any("Recepção" in i for i in itens), itens
    itens_sigla = buscar_no_navegador(navegador, html_turno, "busca-fila", "dropdown-fila", "[ANÁ]")
    assert not any("Coleta" in i for i in itens_sigla), itens_sigla  # busca é pelo nome, não pelo rótulo
    ok("Iniciar Turno: digitar 'coleta' acha todas as Coletas (exibidas com rótulo); a busca é pelo nome puro")

    # Redirecionar a partir da Recepção chamada: destinos = filas ativas da
    # mesma unidade (só a Coleta do Centro Médico).
    itens = buscar_no_navegador(navegador, html_local, "busca-redirecionar", "dropdown-redirecionar", "coleta")
    assert any(ROTULO_COLETA_CM in i for i in itens), itens
    ok("Redirecionar: digitar 'coleta' acha a Coleta pelo nome puro, exibida com o rótulo")
    navegador.close()

# ===========================================================================
# 4) rotulo_fila() fora de request (flask shell, scripts, seeds): funciona e
#    NÃO usa cache - `g` é do app context, que num shell pode durar a sessão
#    inteira; um cache ali devolveria rótulo velho depois de uma alteração.
# ===========================================================================
from flask import has_request_context

from app.utils.rotulos import rotulo_fila

with app.app_context():
    assert not has_request_context()
    coleta_cm = db.session.get(Fila, ids["coleta_cm"])
    assert rotulo_fila(coleta_cm) == ROTULO_COLETA_CM

    # Cadastrar a sigla (o passo da nota de deploy) desfaz a colisão "[ANÁ]" -
    # a chamada seguinte, no MESMO app context, já reflete isso.
    coleta_cm.unidade.sigla = "ACM"
    db.session.commit()
    assert rotulo_fila(coleta_cm) == "[ACM] Coleta", rotulo_fila(coleta_cm)
    coleta_cm.nome = "Coleta Adulto"
    db.session.commit()
    assert rotulo_fila(coleta_cm) == "[ACM] Coleta Adulto", rotulo_fila(coleta_cm)
ok("rotulo_fila() em app_context() sem request: funciona e reflete alterações na hora (sem cache)")

print(f"\n{len(passos)} verificações OK")
