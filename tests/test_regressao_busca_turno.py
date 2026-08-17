"""
Teste de regressão: busca com autocomplete na tela "Iniciar turno" (seção
10.19), aplicando o mesmo padrão da seção 10.10 (atendimento/
_redirecionar_busca.html) aos dois <select> simples que existiam ali - "Fila"
e "Local" - 3º uso do mesmo padrão nesta sessão (redirecionar fila + fila do
turno + local do turno).

Cobre:
- Os <select id="select-fila">/<select id="select-local"> não existem mais;
  entram dois campos de busca independentes (#busca-fila / #busca-local),
  cada um com seu próprio input oculto, dropdown e balão "selecionado".
- Fila: lista completa embutida como JSON (FILAS_DISPONIVEIS) com o marcador
  `aberta` (Fila.aberta_em(), mesmo dado usado na 10.10) e agrupamento
  "Atendendo agora"/"Fora do horário" no dropdown - igual ao widget de
  Redirecionar.
- Local: sem marcador de horário (não se aplica) - busca simples por nome;
  sem indicador de "ocupado por outro operador" porque essa informação nunca
  existiu no <select> simples que este widget substitui (confirmado antes de
  implementar - locais_acessiveis()/_contexto_formulario não trazem esse
  dado) - a checagem de ocupação continua só no POST (turno.py).
- Contrato de formulário inalterado: o input oculto de Local carrega
  `name="local_id"`, o mesmo nome que o <select> antigo usava - a rota POST
  de `iniciar_turno` não mudou uma linha.
- O dropdown de Fila continua listando TODAS as filas, mesmo as já
  adicionadas à tabela "Filas do turno" (confirmado o comportamento ATUAL do
  <select> simples antes de decidir - a mesma fila pode ser adicionada de novo
  só para ajustar o peso no rodízio, o servidor mescla ao salvar).
- Alvos de toque de pelo menos 44px, sem mínimo de caracteres pra abrir o
  dropdown (mesma correção já validada na 10.10, não reintroduzida aqui).
- Fluxo de negócio ponta a ponta inalterado: POST em /iniciar-turno com
  `local_id`/`fila_id`/`chamadas_seguidas` continua criando o TurnoAtivo
  normalmente - o widget novo só troca a UI que gera esses campos.

Execução: python -m tests.test_regressao_busca_turno (a partir da raiz do
projeto, com o virtualenv ativado).
"""
import json
import os

os.environ.setdefault("SECRET_KEY", "test")
os.environ.setdefault("FLASK_ENV", "testing")

from app import create_app
from app.extensions import db
from app.models import Empresa, Fila, Local, Perfil, TurnoAtivo, Unidade, Usuario
from app.models.fila import DIAS_SEMANA

app = create_app("testing")

passos = []


def ok(msg):
    passos.append(msg)
    print("OK:", msg)


SEMPRE_ABERTA = {dia: {"manha": True, "tarde": True} for dia in DIAS_SEMANA}
SEMPRE_FECHADA = {dia: {"manha": False, "tarde": False} for dia in DIAS_SEMANA}

with app.app_context():
    db.create_all()

    perfil_recepcao = Perfil(nome="atendente")
    db.session.add(perfil_recepcao)
    db.session.commit()

    empresa = Empresa(nome="Clinica Busca Turno")
    db.session.add(empresa)
    db.session.commit()

    unidade = Unidade(empresa_id=empresa.id, nome="Unidade 1")
    db.session.add(unidade)
    db.session.commit()

    fila_aberta = Fila(
        empresa_id=empresa.id, unidade_id=unidade.id, nome="Dr. Aberto Agora", sigla="AB",
        horario_funcionamento=dict(SEMPRE_ABERTA),
    )
    fila_fechada = Fila(
        empresa_id=empresa.id, unidade_id=unidade.id, nome="Dr. Fora Horario", sigla="FH",
        horario_funcionamento=dict(SEMPRE_FECHADA),
    )
    db.session.add_all([fila_aberta, fila_fechada])
    db.session.commit()

    local_1 = Local(
        empresa_id=empresa.id, unidade_id=unidade.id, nome="Guiche 01",
        nome_exibicao="Guiche 01", identificador_admin="[UNI] Guiche 01",
    )
    local_2 = Local(
        empresa_id=empresa.id, unidade_id=unidade.id, nome="Guiche 02",
        nome_exibicao="Guiche 02", identificador_admin="[UNI] Guiche 02",
    )
    db.session.add_all([local_1, local_2])
    db.session.commit()

    def novo_usuario(login, perfis):
        u = Usuario(empresa_id=empresa.id, nome=login, login=login)
        u.set_senha("senha123")
        u.perfis = perfis
        u.unidades = [unidade]
        db.session.add(u)
        db.session.commit()
        return u.id

    id_operador = novo_usuario("operador_busca_turno", [perfil_recepcao])

    ids = dict(
        empresa=empresa.id, unidade=unidade.id,
        fila_aberta=fila_aberta.id, fila_fechada=fila_fechada.id,
        local_1=local_1.id, local_2=local_2.id, operador=id_operador,
    )


def login_como(login):
    c = app.test_client()
    resp = c.post("/login", data={"login": login, "senha": "senha123"}, follow_redirects=True)
    assert resp.status_code == 200, (login, resp.status_code)
    return c


def csrf_de(c, url):
    resp = c.get(url)
    texto = resp.data.decode("utf-8")
    inicio = texto.index('name="csrf_token" value="') + len('name="csrf_token" value="')
    return texto[inicio:texto.index('"', inicio)], resp


c = login_como("operador_busca_turno")

URL_INICIAR_TURNO = "/atendimento/iniciar-turno"

_, resp = csrf_de(c, URL_INICIAR_TURNO)
texto = resp.data.decode("utf-8")

# ===========================================================================
# A) Os <select> simples não existem mais; entram os dois campos de busca.
# ===========================================================================
assert 'id="select-fila"' not in texto and 'id="select-local"' not in texto
ok("Os <select> simples de Fila e Local não existem mais na tela")

assert 'id="busca-fila"' in texto and 'id="fila-busca-id"' in texto
assert 'id="busca-local"' in texto
assert '<input type="hidden" name="local_id" id="local-busca-id"' in texto
ok("Campos de busca (#busca-fila/#busca-local) e input oculto #local-busca-id (name=local_id) presentes")

# Enter não pode submeter o form implicitamente (mesmo padrão de #busca-redirecionar).
trecho_fila = texto[texto.index('id="busca-fila"'):texto.index('id="busca-fila"') + 400]
trecho_local = texto[texto.index('id="busca-local"'):texto.index('id="busca-local"') + 400]
assert "onkeydown=\"if (event.key === 'Enter') event.preventDefault();\"" in trecho_fila
assert "onkeydown=\"if (event.key === 'Enter') event.preventDefault();\"" in trecho_local
ok("Ambos os campos de busca bloqueiam submissão implícita via Enter")

# ===========================================================================
# B) Lista de filas embutida como JSON, com marcador `aberta` (Fila.aberta_em()).
# ===========================================================================
inicio = texto.index("FILAS_DISPONIVEIS = ") + len("FILAS_DISPONIVEIS = ")
fim = texto.index(";", inicio)
filas_disponiveis = json.loads(texto[inicio:fim])
por_id = {item["id"]: item for item in filas_disponiveis}

assert por_id[ids["fila_aberta"]]["aberta"] is True
ok("Fila com horário de funcionamento sempre aberto -> aberta=true no JSON embutido")

assert por_id[ids["fila_fechada"]]["aberta"] is False
ok("Fila com horário de funcionamento sempre fechado -> aberta=false no JSON embutido")

# ===========================================================================
# C) Marcadores visuais/agrupamento (reaproveitados da 10.10) presentes no JS.
# ===========================================================================
assert "Atendendo agora" in texto and "Fora do horário" in texto
ok("Marcadores 'Atendendo agora'/'Fora do horário' presentes no widget de Fila")

assert "divisor-dropdown-turno" in texto
ok("Divisor visual entre filas ativas/demais está presente")

# ===========================================================================
# D) Local: sem marcador de horário (não se aplica) - busca simples por nome.
# ===========================================================================
inicio_local = texto.index("LOCAIS_DISPONIVEIS = ") + len("LOCAIS_DISPONIVEIS = ")
fim_local = texto.index(";", inicio_local)
locais_disponiveis = json.loads(texto[inicio_local:fim_local])
assert {item["id"] for item in locais_disponiveis} == {ids["local_1"], ids["local_2"]}
assert "aberta" not in locais_disponiveis[0]
ok("Locais embutidos como JSON, sem o campo 'aberta' (não se aplica a Local)")

trecho_busca_local_config = texto[texto.index("buscaLocal = criarBuscaAutocomplete"):texto.index("buscaLocal = criarBuscaAutocomplete") + 500]
assert "aberta: null" in trecho_busca_local_config
ok("Widget de Local configurado sem marcador de horário (aberta: null)")

# ===========================================================================
# E) Alvos de toque de pelo menos 44px (mesmo padrão de acessibilidade da 10.10).
# ===========================================================================
assert "min-height: 44px" in texto
ok("CSS dos widgets usa alvos de toque de pelo menos 44px")

# ===========================================================================
# F) Sem mínimo de caracteres pra abrir o dropdown (correção já validada na
#    10.10, não reintroduzida aqui) - focar/clicar já lista tudo.
# ===========================================================================
assert "termoNormalizado.length" not in texto
ok("Nenhum widget exige mínimo de caracteres digitados pra abrir a caixa de seleção")

trecho_fabrica = texto[texto.index('elBusca.addEventListener("input"'):]
assert 'elBusca.addEventListener("focus", () => renderizarDropdown(elBusca.value));' in trecho_fabrica
assert 'elBusca.addEventListener("click", () => renderizarDropdown(elBusca.value));' in trecho_fabrica
ok("Focar OU clicar no campo dispara renderizarDropdown, mesmo padrão da 10.10")

# ===========================================================================
# G) Dropdown de Fila continua listando TODAS as filas, mesmo as já
#    adicionadas à tabela "Filas do turno" - comportamento IDÊNTICO ao
#    <select> simples anterior (confirmado antes de implementar): a mesma
#    fila pode ser adicionada de novo só pra ajustar o peso no rodízio.
# ===========================================================================
csrf, _ = csrf_de(c, URL_INICIAR_TURNO)
resp = c.post(
    URL_INICIAR_TURNO,
    data={
        "csrf_token": csrf,
        "fila_id": [str(ids["fila_aberta"])],
        "chamadas_seguidas": ["2"],
        "local_id": str(ids["local_1"]),
    },
    follow_redirects=False,
)
assert resp.status_code == 302
ok("Turno iniciado com uma fila + local válido, via os campos ocultos produzidos pelo widget novo")

resp_get = c.get(URL_INICIAR_TURNO)
texto_get = resp_get.data.decode("utf-8")
inicio = texto_get.index("FILAS_DISPONIVEIS = ") + len("FILAS_DISPONIVEIS = ")
fim = texto_get.index(";", inicio)
filas_disponiveis_apos = json.loads(texto_get[inicio:fim])
assert {item["id"] for item in filas_disponiveis_apos} == {ids["fila_aberta"], ids["fila_fechada"]}
ok("Fila já adicionada ao turno continua aparecendo no dropdown de Fila (não é filtrada/escondida)")

# ===========================================================================
# H) Reconfigurar o turno: GET recupera o Local salvo como `valorInicial` do
#    widget de busca (pré-seleção que o <select required> antigo fazia via
#    'selected' - aqui replicada via JS, já que hidden inputs não são
#    validados nativamente pelo navegador).
# ===========================================================================
assert f"valorInicial: {ids['local_1']}," in texto_get
ok("GET recupera o Local salvo como valorInicial do widget de busca (pré-seleção)")

print()
print(f"TODOS OS {len(passos)} TESTES DA BUSCA COM AUTOCOMPLETE EM 'INICIAR TURNO' PASSARAM")
