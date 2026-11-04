"""
Teste de regressão: coluna "Tipo" (tipo de senha escolhido na emissão, seção
10.4) nas tabelas "Senhas em espera" e "Senhas atendidas" da tela de
Atendimento (adendo 10.84).

As duas telas (painel_fila.html e painel_local.html) não compartilham a tabela
(mesma situação do 10.52), e cada tabela é montada por DOIS caminhos: Jinja no
render inicial e JS (linhaEspera/linhaAtendida) a partir do JSON de /dados após
o 'fila_atualizada'. Cobre:

1) Senha com tipo (nome + marcador com a cor do cadastro), sem tipo ("—"), com
   tipo INATIVO (nome continua aparecendo) e com cor inválida gravada fora do
   formulário (nome aparece, sem marcador/style) - nas 2 tabelas x 2 telas, no
   HTML e no JSON de /dados.
2) Paridade Jinja x JS: o htmlTipoSenha() real de cada tela, executado num
   Chromium (Playwright) com o `tipo` do /dados, gera a MESMA célula que o
   macro Jinja - o tipo não muda nem some no primeiro refresh.
3) Largura: com os nomes reais mais longos de produção ("Pessoa idosa – 80
   anos ou mais", "Pessoa com mobilidade reduzida"), nenhuma das 4 tabelas
   estoura a largura do card em 1366x768, nos temas claro e escuro.
4) Payload 'chamada' da TV inalterado: chamar uma senha prioritária com tipo
   leva só as chaves de sempre (+ `cor`, já existente desde a 10.4/10.27),
   nunca o nome do tipo.

Execução: python -m tests.test_regressao_tipo_senha_atendimento (a partir da
raiz do projeto, com o virtualenv ativado).
"""
import os

os.environ.setdefault("SECRET_KEY", "test")
os.environ.setdefault("FLASK_ENV", "testing")

import html
import re
from datetime import datetime, timedelta
from pathlib import Path

from playwright.sync_api import sync_playwright

from app import create_app
from app.extensions import db, socketio
from app.models import Empresa, Fila, Local, LocalRodizioFila, Perfil, Senha, TipoSenha, Unidade, Usuario

app = create_app("testing")

passos = []


def ok(msg):
    passos.append(msg)
    print("OK:", msg)


NOME_IDOSO = "Pessoa idosa – 80 anos ou mais"
NOME_MOBILIDADE = "Pessoa com mobilidade reduzida"
NOME_GESTANTE_INATIVO = "Gestante"
NOME_COR_INVALIDA = "Cor quebrada"

with app.app_context():
    db.create_all()

    perfil_atendente = Perfil(nome="atendente")
    db.session.add(perfil_atendente)
    db.session.commit()

    empresa = Empresa(nome="Clinica Tipos")
    db.session.add(empresa)
    db.session.commit()
    unidade = Unidade(empresa_id=empresa.id, nome="Matriz", sigla="MAT")
    db.session.add(unidade)
    db.session.commit()

    fila = Fila(empresa_id=empresa.id, unidade_id=unidade.id, nome="Recepção")
    db.session.add(fila)
    db.session.commit()

    local = Local(
        empresa_id=empresa.id, unidade_id=unidade.id, nome="Guichê 1",
        nome_exibicao="Guichê 1", identificador_admin="[MAT] Guichê 1",
    )
    db.session.add(local)
    db.session.commit()
    db.session.add(LocalRodizioFila(local_id=local.id, fila_id=fila.id, ordem=0, chamadas_seguidas=1))
    db.session.commit()

    idoso = TipoSenha(empresa_id=empresa.id, nome=NOME_IDOSO, prefixo="PE", cor="#880808", peso_prioridade=2)
    mobilidade = TipoSenha(empresa_id=empresa.id, nome=NOME_MOBILIDADE, prefixo="PM", cor="#880808", peso_prioridade=1)
    gestante = TipoSenha(empresa_id=empresa.id, nome=NOME_GESTANTE_INATIVO, prefixo="G", cor="#1E88E5",
                         peso_prioridade=1, ativo=False)
    # gravado fora do formulário (SQL manual/import): não pode virar CSS arbitrário
    quebrada = TipoSenha(empresa_id=empresa.id, nome=NOME_COR_INVALIDA, cor="red;a:b", peso_prioridade=0)
    db.session.add_all([idoso, mobilidade, gestante, quebrada])
    db.session.commit()

    agora = datetime.utcnow()

    def nova_senha(codigo, tipo, prioridade, status="aguardando", minutos_atras=10, **extra):
        s = Senha(
            empresa_id=empresa.id, unidade_id=unidade.id, fila_id=fila.id, codigo=codigo,
            sequencial=int(codigo[-3:]), prioridade=prioridade, status=status,
            tipo_senha_id=tipo.id if tipo is not None else None,
            criada_em=agora - timedelta(minutes=minutos_atras), **extra,
        )
        db.session.add(s)
        return s

    # "Senhas em espera"
    nova_senha("PE001", idoso, True, minutos_atras=30, nome_paciente="Maria Aparecida dos Santos Oliveira",
               observacoes="Cadeira de rodas, acompanhante")
    nova_senha("REC002", None, False, minutos_atras=20)
    nova_senha("G003", gestante, True, minutos_atras=15)
    nova_senha("REC004", quebrada, False, minutos_atras=12)
    # "Senhas atendidas" (já chamadas neste Local)
    nova_senha("PM005", mobilidade, True, status="finalizada", minutos_atras=60, local_id=local.id,
               chamada_em=agora - timedelta(minutes=50), finalizada_em=agora - timedelta(minutes=45))
    nova_senha("REC006", None, False, status="finalizada", minutos_atras=55, local_id=local.id,
               chamada_em=agora - timedelta(minutes=40), finalizada_em=agora - timedelta(minutes=35))
    db.session.commit()

    atendente = Usuario(empresa_id=empresa.id, nome="Atendente", login="atendente_tipos")
    atendente.set_senha("senha123")
    atendente.perfis = [perfil_atendente]
    atendente.unidades = [unidade]
    db.session.add(atendente)
    db.session.commit()

    ids = dict(fila=fila.id, local=local.id)


def login_como(login):
    c = app.test_client()
    resp = c.post("/login", data={"login": login, "senha": "senha123"}, follow_redirects=True)
    assert resp.status_code == 200, (login, resp.status_code)
    with c.session_transaction() as sess:
        sess["atendimento_local_id"] = ids["local"]
    return c


def pagina(cliente, url):
    resp = cliente.get(url)
    assert resp.status_code == 200, (url, resp.status_code)
    return resp.get_data(as_text=True)


def normalizar(trecho):
    return re.sub(r">\s+<", "><", " ".join(trecho.split()))


def corpo_tabela(texto, corpo_id):
    return texto.split(f'<tbody id="{corpo_id}">', 1)[1].split("</tbody>", 1)[0]


def celulas_tipo_por_codigo(texto, corpo_id):
    """{codigo: <td class="col-tipo">...</td> normalizado} das linhas da tabela."""
    resultado = {}
    for linha in re.findall(r"<tr data-senha-id=.*?</tr>", corpo_tabela(texto, corpo_id), re.S):
        codigo = re.search(r"<td>\s*([A-Z]+\d{3})", linha).group(1)
        celula = re.search(r'<td class="col-tipo">.*?</td>', linha, re.S).group(0)
        resultado[codigo] = normalizar(celula)
    return resultado


ESPERADO_ESPERA = {"PE001", "REC002", "G003", "REC004"}
ESPERADO_ATENDIDAS = {"PM005", "REC006"}

MARCADOR_880808 = '<span class="tipo-senha-marcador" style="background-color: #880808" aria-hidden="true"></span>'


def conferir_celulas(celulas, tela):
    for codigo in celulas:
        texto = html.unescape(celulas[codigo])
        if codigo == "PE001":
            assert texto == f'<td class="col-tipo"><span class="tipo-senha">{MARCADOR_880808}{NOME_IDOSO}</span></td>', (tela, texto)
        elif codigo == "PM005":
            assert texto == f'<td class="col-tipo"><span class="tipo-senha">{MARCADOR_880808}{NOME_MOBILIDADE}</span></td>', (tela, texto)
        elif codigo in ("REC002", "REC006"):
            assert texto == '<td class="col-tipo"><span class="tipo-senha-vazio">—</span></td>', (tela, texto)
        elif codigo == "G003":
            assert NOME_GESTANTE_INATIVO in texto and "#1E88E5" in texto, (tela, texto)  # inativo: nome e cor mantidos
        elif codigo == "REC004":
            assert NOME_COR_INVALIDA in texto, (tela, texto)
            assert "style=" not in texto and "tipo-senha-marcador" not in texto and "red;" not in texto, (tela, texto)


c = login_como("atendente_tipos")
URL_LOCAL = f"/atendimento/recepcao/local/{ids['local']}"
URL_FILA = f"/atendimento/recepcao/filas/{ids['fila']}"
TELAS = {"painel_local": URL_LOCAL, "painel_fila": URL_FILA}

htmls = {}
for tela, url in TELAS.items():
    texto = pagina(c, url)
    htmls[tela] = texto

    # cabeçalhos: "Tipo" logo depois de "Senha", nas duas tabelas
    cab_espera = texto.split('<table id="tabela-espera">', 1)[1].split("</thead>", 1)[0]
    cab_atendidas = texto.split('<table id="tabela-atendidas">', 1)[1].split("</thead>", 1)[0]
    assert "<th>Senha</th><th>Tipo</th>" in cab_espera, (tela, cab_espera)
    assert "<th>Senha</th><th>Tipo</th>" in cab_atendidas, (tela, cab_atendidas)
    # colspan das linhas "nenhuma senha" acompanhou a coluna nova (JS de refresh)
    assert 'colspan="7"' in texto and 'colspan="4"' in texto and 'colspan="6"' not in texto and 'colspan="3"' not in texto, tela

    celulas_espera = celulas_tipo_por_codigo(texto, "corpo-espera")
    celulas_atendidas = celulas_tipo_por_codigo(texto, "corpo-atendidas")
    assert set(celulas_espera) == ESPERADO_ESPERA, (tela, celulas_espera)
    assert set(celulas_atendidas) == ESPERADO_ATENDIDAS, (tela, celulas_atendidas)
    conferir_celulas(celulas_espera, tela)
    conferir_celulas(celulas_atendidas, tela)
ok("HTML (render inicial): coluna Tipo nas 2 tabelas x 2 telas - com tipo, sem tipo ('—'), inativo, cor inválida sem style")

dados = {}
for tela, url in TELAS.items():
    d = c.get(f"{url}/dados").get_json()
    dados[tela] = d
    espera = {s["codigo"]: s["tipo"] for s in d["espera"]}
    atendidas = {s["codigo"]: s["tipo"] for s in d["atendidas"]}
    assert set(espera) == ESPERADO_ESPERA and set(atendidas) == ESPERADO_ATENDIDAS, (tela, espera, atendidas)
    assert espera["PE001"] == {"nome": NOME_IDOSO, "cor": "#880808"}, (tela, espera)
    assert espera["REC002"] is None and atendidas["REC006"] is None, tela
    assert espera["G003"] == {"nome": NOME_GESTANTE_INATIVO, "cor": "#1E88E5"}, (tela, espera)  # tipo inativo
    assert espera["REC004"] == {"nome": NOME_COR_INVALIDA, "cor": None}, (tela, espera)
    assert atendidas["PM005"] == {"nome": NOME_MOBILIDADE, "cor": "#880808"}, (tela, atendidas)
    # badge "P" continua separado do tipo (item 5 aprovado): vem de Senha.prioridade
    assert {s["codigo"]: s["prioridade"] for s in d["espera"]} == {
        "PE001": True, "REC002": False, "G003": True, "REC004": False}, tela
ok("JSON de /dados (refresh após 'fila_atualizada'): `tipo` {nome, cor} ou null nas 2 tabelas x 2 telas")


def extrair_funcao(texto, nome):
    inicio = texto.index(f"function {nome}(")
    profundidade, i = 0, texto.index("{", inicio)
    while True:
        if texto[i] == "{":
            profundidade += 1
        elif texto[i] == "}":
            profundidade -= 1
            if profundidade == 0:
                return texto[inicio:i + 1]
        i += 1


CSS_TEMA = Path(app.static_folder, "css", "gestfila.css").read_text(encoding="utf-8")


def com_css_embutido(texto):
    """Sem servidor por trás: troca o <link> do gestfila.css pelo conteúdo e tira
    os <script src> externos (socket.io), que não carregariam de qualquer forma."""
    texto = re.sub(r'<link rel="stylesheet"[^>]*>', f"<style>{CSS_TEMA}</style>", texto)
    return re.sub(r'<script src="[^"]*"></script>', "", texto)


with sync_playwright() as pw:
    navegador = pw.chromium.launch()

    # 2) Paridade Jinja x JS com o JS REAL de cada tela.
    pg = navegador.new_page()
    for tela, texto in htmls.items():
        codigo_js = extrair_funcao(texto, "escapeHtml") + "\n" + extrair_funcao(texto, "htmlTipoSenha")
        for tabela, corpo_id in (("espera", "corpo-espera"), ("atendidas", "corpo-atendidas")):
            jinja = celulas_tipo_por_codigo(texto, corpo_id)
            for item in dados[tela][tabela]:
                gerado = pg.evaluate(f"(tipo) => {{ {codigo_js}; return htmlTipoSenha(tipo); }}", item["tipo"])
                assert html.unescape(normalizar(gerado)) == html.unescape(jinja[item["codigo"]]), (
                    tela, tabela, item["codigo"], gerado, jinja[item["codigo"]])
        # JS também barra cor inválida mesmo que chegasse do servidor
        gerado = pg.evaluate(f"(tipo) => {{ {codigo_js}; return htmlTipoSenha(tipo); }}",
                             {"nome": "<b>x</b>", "cor": "red;a:b"})
        assert "style=" not in gerado and "<b>" not in gerado, (tela, gerado)
    pg.close()
    ok("JS htmlTipoSenha() (refresh) gera a MESMA célula que o macro Jinja, nas 2 telas; escapa nome e barra cor inválida")

    # 3) Largura em 1366x768, temas claro e escuro, nomes reais mais longos.
    # A tela usa `system-ui`, que muda com o SO (Segoe UI no Windows é estreita;
    # o Linux da CI é bem mais largo - foi a CI que pegou o estouro da 1ª versão).
    # Além da fonte padrão, força uma fonte larga que exista nos dois ambientes
    # (Verdana no Windows, DejaVu Sans no Linux) para não depender de qual SO roda.
    FONTE_LARGA = "body, input, button { font-family: Verdana, 'DejaVu Sans', sans-serif !important; }"
    for tema, fonte in (("light", None), ("dark", None), ("light", FONTE_LARGA), ("dark", FONTE_LARGA)):
        for tela, texto in htmls.items():
            pg = navegador.new_page(viewport={"width": 1366, "height": 768})
            pg.set_content(com_css_embutido(texto))
            if fonte:
                pg.add_style_tag(content=fonte)
            pg.evaluate(f"document.documentElement.setAttribute('data-theme', '{tema}')")
            for tabela_id in ("tabela-espera", "tabela-atendidas"):
                medida = pg.evaluate(
                    """(id) => { const t = document.getElementById(id); const card = t.closest('.card');
                       const estilo = getComputedStyle(card);
                       const util = card.clientWidth - parseFloat(estilo.paddingLeft) - parseFloat(estilo.paddingRight);
                       return {tabela: t.scrollWidth, util, pagina: document.documentElement.scrollWidth}; }""",
                    tabela_id,
                )
                assert medida["tabela"] <= medida["util"] + 1, (tema, bool(fonte), tela, tabela_id, medida)
                assert medida["pagina"] <= 1366, (tema, bool(fonte), tela, tabela_id, medida)
            # marcador visível: tem tamanho e o anel em token do tema
            caixa = pg.eval_on_selector(".tipo-senha-marcador", "e => { const r = e.getBoundingClientRect(); "
                                        "return {w: r.width, h: r.height, anel: getComputedStyle(e).boxShadow}; }")
            assert caixa["w"] > 0 and caixa["h"] > 0 and caixa["anel"] != "none", (tema, tela, caixa)
            pg.close()
    ok("1366x768, temas claro e escuro, fonte padrão e fonte larga: nenhuma das 4 tabelas estoura o card")
    navegador.close()

# ===========================================================================
# 4) Payload 'chamada' da TV: inalterado, sem nome de tipo.
# ===========================================================================
NAMESPACE = "/painel"
ws_tv = socketio.test_client(app, namespace=NAMESPACE, flask_test_client=c)
ws_tv.get_received(NAMESPACE)
ws_tv.emit("entrar_fila", {"fila_id": ids["fila"]}, namespace=NAMESPACE)
ws_tv.get_received(NAMESPACE)

resp = c.post(f"{URL_LOCAL}/chamar", data={"codigo": "PE001"}, follow_redirects=True)
assert resp.status_code == 200
recebidos = ws_tv.get_received(NAMESPACE)
payload_tv = next(ev for ev in recebidos if ev["name"] == "chamada")["args"][0]
# `cor` já existia (10.4, gate Senha.prioridade da 10.27); nada novo além dela
# (PE001 tem nome de paciente gravado -> chamada por nome, contrato da seção 10.2)
assert set(payload_tv) == {"tipo", "local", "nome_paciente", "local_audio_slug", "cor"}, payload_tv
assert payload_tv["tipo"] == "nome" and payload_tv["cor"] == "#880808", payload_tv
assert not any("idosa" in str(v).lower() for v in payload_tv.values()), payload_tv
ok("Payload 'chamada' da TV: mesmas chaves de antes, sem o nome do tipo")

print(f"\n{len(passos)} verificações OK")
