"""
Teste de regressão: som configurável por TV (som_ativo/volume) e texto de chamada
customizável por empresa (texto_chamada_senha_template/texto_chamada_nome_template),
seção 6 do BLUEPRINT.

Limitação de infraestrutura, registrada aqui de propósito: este projeto não tem
runtime de JS/navegador nos testes (sem Playwright/Selenium/jsdom, só Flask test
client) - não é possível chamar de fato `speechSynthesis.speak()` e verificar se foi
invocada. O que este teste verifica é a variável injetada no HTML que a função
`falarChamada()` usa para decidir se fala ou não (`TV_SOM_ATIVO`) e com qual volume/
texto (`TV_VOLUME`, `TEXTO_CHAMADA_*_TEMPLATE`) - a lógica em si
(`if (!TV_SOM_ATIVO) return;` em painel/tv.html) é uma linha só, de leitura direta.

Execução: python -m tests.test_regressao_som_configuravel (a partir da raiz do
projeto, com o virtualenv ativado).
"""
import os

os.environ.setdefault("SECRET_KEY", "test")
os.environ.setdefault("FLASK_ENV", "testing")

from app import create_app
from app.extensions import db
from app.models import Empresa, Perfil, Tv, Unidade, Usuario

app = create_app("testing")

passos = []


def ok(msg):
    passos.append(msg)
    print("OK:", msg)


with app.app_context():
    db.create_all()

    perfil_admin = Perfil(nome="admin")
    db.session.add(perfil_admin)
    db.session.commit()

    # empresa A: fica com o texto padrão (não mexe nos templates) - confirma que o
    # default gravado no model é o texto decidido com o cliente (seção 6).
    empresa_a = Empresa(nome="Clinica A")
    # empresa B: customiza o texto de chamada - confirma que o texto de outra empresa
    # é de fato usado no lugar do default.
    empresa_b = Empresa(
        nome="Clinica B",
        texto_chamada_senha_template="Atenção, senha {senha}, comparecer em {local}.",
        texto_chamada_nome_template="Paciente {nome}, comparecer em {local}.",
    )
    db.session.add_all([empresa_a, empresa_b])
    db.session.commit()

    unidade_a1 = Unidade(empresa_id=empresa_a.id, nome="Unidade A1")
    unidade_b1 = Unidade(empresa_id=empresa_b.id, nome="Unidade B1")
    db.session.add_all([unidade_a1, unidade_b1])
    db.session.commit()

    tv_padrao = Tv(empresa_id=empresa_a.id, unidade_id=unidade_a1.id, nome="TV Padrao", slug="tv-padrao")
    tv_som_off = Tv(
        empresa_id=empresa_a.id, unidade_id=unidade_a1.id, nome="TV Som Off", som_ativo=False, slug="tv-som-off",
    )
    tv_volume_baixo = Tv(
        empresa_id=empresa_a.id, unidade_id=unidade_a1.id, nome="TV Volume Baixo", volume=45, slug="tv-volume-baixo",
    )
    tv_empresa_b = Tv(empresa_id=empresa_b.id, unidade_id=unidade_b1.id, nome="TV Empresa B", slug="tv-empresa-b")
    db.session.add_all([tv_padrao, tv_som_off, tv_volume_baixo, tv_empresa_b])
    db.session.commit()

    def novo_usuario(login, empresa_id, perfis, unidades):
        u = Usuario(empresa_id=empresa_id, nome=login, login=login)
        u.set_senha("senha123")
        u.perfis = perfis
        u.unidades = unidades
        db.session.add(u)
        db.session.commit()
        return u.id

    id_admin_a = novo_usuario("admin_a_som", empresa_a.id, [perfil_admin], [])

    ids = dict(
        empresa_a=empresa_a.id, empresa_b=empresa_b.id, unidade_a1=unidade_a1.id,
        tv_padrao=tv_padrao.id, tv_som_off=tv_som_off.id, tv_volume_baixo=tv_volume_baixo.id,
        tv_empresa_b=tv_empresa_b.id, admin_a=id_admin_a,
    )

print("IDS", ids)


def login_como(login):
    c = app.test_client()
    resp = c.post("/login", data={"login": login, "senha": "senha123"}, follow_redirects=True)
    assert resp.status_code == 200, (login, resp.status_code)
    return c


def db_get(modelo, id_):
    with app.app_context():
        return db.session.get(modelo, id_)


anon = app.test_client()

# ---------------------------------------------------------------------------
# 1) Default do model: texto decidido com o cliente (não quebra empresas existentes
#    que nunca tocaram nesses campos, já que ainda não existe tela admin para editá-los).
# ---------------------------------------------------------------------------
TEXTO_SENHA_PADRAO = "Atenção, senha {senha}. Por favor, dirija-se a {local}."
TEXTO_NOME_PADRAO = "{nome}, por favor, dirija-se a {local}."

with app.app_context():
    empresa_a_db = db.session.get(Empresa, ids["empresa_a"])
    assert empresa_a_db.texto_chamada_senha_template == TEXTO_SENHA_PADRAO
    assert empresa_a_db.texto_chamada_nome_template == TEXTO_NOME_PADRAO
ok("Default de Empresa.texto_chamada_*_template é o texto decidido com o cliente (seção 6)")

# ---------------------------------------------------------------------------
# 2) TV com som_ativo=False injeta TV_SOM_ATIVO=false no HTML - é essa variável que
#    falarChamada() checa antes de chamar speechSynthesis.speak().
# ---------------------------------------------------------------------------
resp = anon.get("/painel/tv/tv-som-off")
assert resp.status_code == 200
texto = resp.data.decode("utf-8")
assert "const TV_SOM_ATIVO = false;" in texto
ok("TV com som_ativo=False injeta 'const TV_SOM_ATIVO = false;' (falarChamada() retorna sem falar)")

resp = anon.get("/painel/tv/tv-padrao")
texto = resp.data.decode("utf-8")
assert "const TV_SOM_ATIVO = true;" in texto
ok("TV com som_ativo=True (default) injeta 'const TV_SOM_ATIVO = true;'")

# ---------------------------------------------------------------------------
# 3) Volume é aplicado corretamente (TV_VOLUME injetado, usado em fala.volume = TV_VOLUME/100).
# ---------------------------------------------------------------------------
resp = anon.get("/painel/tv/tv-volume-baixo")
texto = resp.data.decode("utf-8")
assert "const TV_VOLUME = 45;" in texto
ok("TV com volume=45 injeta 'const TV_VOLUME = 45;'")

resp = anon.get("/painel/tv/tv-padrao")
texto = resp.data.decode("utf-8")
assert "const TV_VOLUME = 100;" in texto
ok("TV sem customização usa o volume default (100)")

# ---------------------------------------------------------------------------
# 4) Texto customizado da empresa é usado no lugar do hardcoded.
# ---------------------------------------------------------------------------
resp = anon.get("/painel/tv/tv-empresa-b")
texto = resp.data.decode("utf-8")
assert "Aten\\u00e7\\u00e3o, senha {senha}, comparecer em {local}." in texto or \
    "Atenção, senha {senha}, comparecer em {local}." in texto
assert "Paciente {nome}, comparecer em {local}." in texto
ok("TV de empresa com template customizado usa o texto da empresa (TEXTO_CHAMADA_*_TEMPLATE), não o default")

resp = anon.get("/painel/tv/tv-padrao")
texto = resp.data.decode("utf-8")
assert (
    "Aten\\u00e7\\u00e3o, senha {senha}. Por favor, dirija-se a {local}." in texto
    or TEXTO_SENHA_PADRAO in texto
)
assert TEXTO_NOME_PADRAO in texto
ok("TV de empresa sem customização usa o texto default nos consts TEXTO_CHAMADA_*_TEMPLATE")

# ---------------------------------------------------------------------------
# 5) CRUD admin: criar TV com som_ativo desmarcado e volume customizado grava certo.
# ---------------------------------------------------------------------------
c_admin = login_como("admin_a_som")

resp = c_admin.post(
    f"/admin/tvs/nova?empresa_id={ids['empresa_a']}",
    data={"nome": "TV Criada no Form", "unidade_id": ids["unidade_a1"], "volume": "30"},
    follow_redirects=True,
)
assert resp.status_code == 200
with app.app_context():
    criada = Tv.query.filter_by(nome="TV Criada no Form").first()
    assert criada is not None
    assert criada.som_ativo is False  # checkbox não marcado no POST (ausente = "on" não enviado)
    assert criada.volume == 30
    id_tv_criada = criada.id
ok("Criação via admin: checkbox 'som_ativo' desmarcado grava False, volume '30' grava 30")

resp = c_admin.get(f"/admin/tvs/{id_tv_criada}/editar")
assert resp.status_code == 200
texto = resp.data.decode("utf-8")
assert 'name="som_ativo"' in texto and "checked" not in texto.split('name="som_ativo"')[1].split(">")[0]
assert 'value="30"' in texto
ok("Formulário de edição recarrega com som_ativo desmarcado e volume=30 preenchidos")

# ---------------------------------------------------------------------------
# 6) Validação: volume fora de 0-100 é rejeitado, nenhuma TV é criada.
# ---------------------------------------------------------------------------
resp = c_admin.post(
    f"/admin/tvs/nova?empresa_id={ids['empresa_a']}",
    data={"nome": "TV Volume Invalido", "unidade_id": ids["unidade_a1"], "volume": "150", "som_ativo": "on"},
)
assert "Volume deve ser um número entre 0 e 100".encode("utf-8") in resp.data
with app.app_context():
    assert Tv.query.filter_by(nome="TV Volume Invalido").first() is None
ok("Criação via admin: volume fora de 0-100 é rejeitado, nenhuma TV é criada")

print()
print(f"TODOS OS {len(passos)} TESTES DE SOM CONFIGURÁVEL (SEÇÃO 6) PASSARAM")
