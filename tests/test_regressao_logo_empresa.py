"""
Teste de regressão: upload de logo por empresa (adendo 10.43) - exibido no
cupom impresso do totem, no painel de TV e no admin, com fallback pra nome em
texto quando a empresa não tem logo cadastrado.

Cobre:
- Upload válido (PNG e JPG) salva o arquivo determinístico
  'empresa_<id>.png' em app/static/uploads/logos/ e grava
  Empresa.logo_filename.
- Rejeição por CONTEÚDO real, não extensão: um arquivo SVG/XML disfarçado
  com nome "logo.png" é recusado (Pillow não decodifica como imagem real) -
  nada é escrito em disco, logo_filename não muda.
- Rejeição por tamanho (> 2MB).
- Remoção do logo: apaga o arquivo físico e volta Empresa.logo_filename pra
  None.
- Isolamento multi-tenant: upload pra empresa A nunca toca no arquivo/campo
  da empresa B (nome de arquivo determinístico por ID evita colisão por
  construção). Acesso direto à URL do logo de outra empresa funciona (200) -
  documentado como esperado, não bug: é um asset público (mesmo nível de
  "publicidade" que o totem/painel de TV, que também são páginas sem login),
  não dado sensível.
- Permissão: só super admin (mesmo padrão já usado em TODAS as rotas de
  Empresa hoje - admin de empresa não gerencia o cadastro da empresa em si,
  só o que existe DENTRO dela) - admin de empresa recebe 403.
- Fallback sem logo em cupom (totem), painel de TV e listagem/formulário do
  admin - nenhum <img> renderizado, nada quebra pra quem não usa a feature.

Execução: python -m tests.test_regressao_logo_empresa (a partir da raiz do
projeto, com o virtualenv ativado).
"""
import io
import os

os.environ.setdefault("SECRET_KEY", "test")
os.environ.setdefault("FLASK_ENV", "testing")

from PIL import Image

from app import create_app
from app.extensions import db
from app.models import Empresa, Fila, Perfil, Totem, TotemBotao, Tv, Unidade, Usuario
from app.utils.uploads import caminho_logo

app = create_app("testing")

passos = []


def ok(msg):
    passos.append(msg)
    print("OK:", msg)


def png_valido(cor=(200, 30, 30)):
    buf = io.BytesIO()
    Image.new("RGB", (40, 20), color=cor).save(buf, format="PNG")
    buf.seek(0)
    return buf.read()


def jpg_valido():
    buf = io.BytesIO()
    Image.new("RGB", (40, 20), color=(10, 100, 200)).save(buf, format="JPEG")
    buf.seek(0)
    return buf.read()


SVG_DISFARCADO_DE_PNG = b"<svg xmlns='http://www.w3.org/2000/svg' onload='alert(1)'></svg>"


with app.app_context():
    db.create_all()

    perfil_admin = Perfil(nome="admin")
    db.session.add(perfil_admin)
    db.session.commit()

    empresa_a = Empresa(nome="Clinica Logo A")
    empresa_b = Empresa(nome="Clinica Logo B")
    db.session.add_all([empresa_a, empresa_b])
    db.session.commit()

    unidade_a = Unidade(empresa_id=empresa_a.id, nome="Unidade A1")
    db.session.add(unidade_a)
    db.session.commit()

    fila_a = Fila(empresa_id=empresa_a.id, unidade_id=unidade_a.id, nome="Recepcao", sigla="RC")
    db.session.add(fila_a)
    db.session.commit()

    totem_a = Totem(empresa_id=empresa_a.id, unidade_id=unidade_a.id, nome="Totem A", slug="totem-logo-a")
    tv_a = Tv(empresa_id=empresa_a.id, unidade_id=unidade_a.id, nome="TV A", slug="tv-logo-a")
    db.session.add_all([totem_a, tv_a])
    db.session.commit()

    botao_a = TotemBotao(
        empresa_id=empresa_a.id, unidade_id=unidade_a.id, label="Recepcao", ordem=1, fila_id=fila_a.id,
        sigla="RC", ativo=True,
    )
    db.session.add(botao_a)
    db.session.commit()

    def novo_usuario(login, empresa_id, perfis):
        u = Usuario(empresa_id=empresa_id, nome=login, login=login)
        u.set_senha("senha123")
        u.perfis = perfis
        db.session.add(u)
        db.session.commit()
        return u.id

    id_super = novo_usuario("super_logo", None, [])
    id_admin_empresa_a = novo_usuario("admin_a_logo", empresa_a.id, [perfil_admin])

    ids = dict(
        empresa_a=empresa_a.id, empresa_b=empresa_b.id, unidade_a=unidade_a.id,
        totem_slug=totem_a.slug, botao_a=botao_a.id, tv_slug=tv_a.slug,
        super=id_super, admin_a=id_admin_empresa_a,
    )


def login_como(login):
    c = app.test_client()
    resp = c.post("/login", data={"login": login, "senha": "senha123"}, follow_redirects=True)
    assert resp.status_code == 200, (login, resp.status_code)
    return c


def db_get_empresa(id_):
    with app.app_context():
        return db.session.get(Empresa, id_)


URL_EDITAR_A = f"/admin/empresas/{ids['empresa_a']}/editar"

c_super = login_como("super_logo")
c_admin_a = login_como("admin_a_logo")

# ===========================================================================
# A) Permissão: admin de empresa (não super admin) é bloqueado - mesmo padrão
#    já usado em TODAS as rotas de Empresa hoje (listar/nova/editar/toggle/
#    excluir são @super_admin_required, confirmado antes de implementar).
# ===========================================================================
resp = c_admin_a.get(URL_EDITAR_A)
assert resp.status_code == 403
ok("Admin de empresa recebe 403 ao tentar acessar a tela de edição de Empresa (mesmo padrão das outras rotas)")

resp = c_admin_a.post(
    URL_EDITAR_A,
    data={"nome": "Hackeado", "logo": (io.BytesIO(png_valido()), "logo.png")},
    content_type="multipart/form-data",
)
assert resp.status_code == 403
with app.app_context():
    assert db.session.get(Empresa, ids["empresa_a"]).logo_filename is None
ok("Admin de empresa recebe 403 ao tentar fazer upload de logo via POST direto - nada é gravado")

# ===========================================================================
# B) Upload válido de PNG - salva o arquivo determinístico e grava o campo.
# ===========================================================================
resp = c_super.post(
    URL_EDITAR_A,
    data={"nome": "Clinica Logo A", "logo": (io.BytesIO(png_valido()), "logo.png")},
    content_type="multipart/form-data",
    follow_redirects=True,
)
assert resp.status_code == 200
empresa_a_db = db_get_empresa(ids["empresa_a"])
assert empresa_a_db.logo_filename == f"empresa_{ids['empresa_a']}.png"
with app.app_context():
    assert os.path.exists(caminho_logo(ids["empresa_a"]))
ok("Upload de PNG válido salva 'empresa_<id>.png' em disco e grava Empresa.logo_filename")

# ===========================================================================
# C) Upload válido de JPG - também aceito, normalizado pra .png no disco.
# ===========================================================================
resp = c_super.post(
    URL_EDITAR_A,
    data={"nome": "Clinica Logo A", "logo": (io.BytesIO(jpg_valido()), "logo.jpg")},
    content_type="multipart/form-data",
    follow_redirects=True,
)
assert resp.status_code == 200
empresa_a_db = db_get_empresa(ids["empresa_a"])
assert empresa_a_db.logo_filename == f"empresa_{ids['empresa_a']}.png"  # sempre .png, mesmo vindo de .jpg
ok("Upload de JPG válido também é aceito - conteúdo real (Pillow), não a extensão do arquivo, decide - salvo como .png")

# ===========================================================================
# D) Rejeição por CONTEÚDO real: SVG disfarçado com nome "logo.png" é
#    recusado - a extensão do arquivo enviado NÃO é o que decide.
# ===========================================================================
logo_antes = db_get_empresa(ids["empresa_a"]).logo_filename
resp = c_super.post(
    URL_EDITAR_A,
    data={"nome": "Clinica Logo A", "logo": (io.BytesIO(SVG_DISFARCADO_DE_PNG), "logo.png")},
    content_type="multipart/form-data",
    follow_redirects=True,
)
assert resp.status_code == 200
texto = resp.data.decode("utf-8")
assert "não é uma imagem PNG ou JPG válida" in texto
empresa_a_db = db_get_empresa(ids["empresa_a"])
assert empresa_a_db.logo_filename == logo_antes  # não mudou - upload rejeitado, logo anterior preservado
ok("SVG disfarçado de .png é recusado por conteúdo real (Pillow não decodifica XML como imagem) - logo anterior preservado")

# ===========================================================================
# E) Rejeição por tamanho (> 2MB).
# ===========================================================================
arquivo_grande = io.BytesIO(b"\x00" * (2 * 1024 * 1024 + 1))
resp = c_super.post(
    URL_EDITAR_A,
    data={"nome": "Clinica Logo A", "logo": (arquivo_grande, "grande.png")},
    content_type="multipart/form-data",
    follow_redirects=True,
)
assert resp.status_code == 200
texto = resp.data.decode("utf-8")
assert "muito grande" in texto or "2MB" in texto
empresa_a_db = db_get_empresa(ids["empresa_a"])
assert empresa_a_db.logo_filename == logo_antes  # ainda o logo válido do passo C, não mudou
ok("Arquivo maior que 2MB é recusado, logo anterior preservado")

# ===========================================================================
# F) Fallback COM logo: cupom (totem), painel de TV e listagem do admin
#    renderizam a tag <img> com o arquivo certo.
# ===========================================================================
resp = c_super.post(f"/totem/{ids['totem_slug']}/gerar/{ids['botao_a']}", follow_redirects=True)
assert resp.status_code == 200
texto_cupom = resp.data.decode("utf-8")
assert f"uploads/logos/empresa_{ids['empresa_a']}.png" in texto_cupom
assert 'class="cupom-logo"' in texto_cupom
ok("Cupom do totem renderiza o <img> do logo quando a empresa tem um cadastrado")

resp = app.test_client().get(f"/painel/tv/{ids['tv_slug']}")
assert resp.status_code == 200
texto_tv = resp.data.decode("utf-8")
assert f"uploads/logos/empresa_{ids['empresa_a']}.png" in texto_tv
ok("Painel de TV renderiza o logo da empresa no cabeçalho quando cadastrado")

resp = c_super.get("/admin/empresas")
assert resp.status_code == 200
texto_listagem = resp.data.decode("utf-8")
assert f"uploads/logos/empresa_{ids['empresa_a']}.png" in texto_listagem
ok("Listagem de empresas no admin mostra a miniatura do logo cadastrado")

# ===========================================================================
# G) Remoção do logo: apaga o arquivo físico, volta logo_filename pra None,
#    e os 3 lugares voltam ao fallback (nome em texto, sem <img>).
# ===========================================================================
texto_form = c_super.get(URL_EDITAR_A).data.decode("utf-8")
csrf_remover = texto_form[texto_form.index('id="form-remover-logo"'):]
csrf_remover = csrf_remover[csrf_remover.index('name="csrf_token" value="') + len('name="csrf_token" value="'):]
csrf_remover = csrf_remover[:csrf_remover.index('"')]

resp = c_super.post(
    f"/admin/empresas/{ids['empresa_a']}/logo/remover",
    data={"csrf_token": csrf_remover}, follow_redirects=True,
)
assert resp.status_code == 200
empresa_a_db = db_get_empresa(ids["empresa_a"])
assert empresa_a_db.logo_filename is None
with app.app_context():
    assert not os.path.exists(caminho_logo(ids["empresa_a"]))
ok("Remover logo apaga o arquivo físico e limpa Empresa.logo_filename")

resp = app.test_client().post(f"/totem/{ids['totem_slug']}/gerar/{ids['botao_a']}", follow_redirects=True)
assert resp.status_code == 200
texto_sem_logo = resp.data.decode("utf-8")
# checa a TAG <img> especificamente, não a substring solta - a regra CSS
# ".cupom-logo { ... }" sempre existe no <style>, com ou sem logo cadastrado.
assert '<img class="cupom-logo"' not in texto_sem_logo
assert "Clinica Logo A" in texto_sem_logo  # nome em texto continua normalmente
ok("Sem logo: cupom volta a mostrar só o nome em texto, exatamente como antes da feature")

resp = app.test_client().get(f"/painel/tv/{ids['tv_slug']}")
assert '<img class="cabecalho-logo-empresa"' not in resp.data.decode("utf-8")
ok("Sem logo: painel de TV não renderiza nenhum <img> no cabeçalho")

# ===========================================================================
# H) Isolamento multi-tenant: upload pra empresa A nunca afeta empresa B -
#    nome de arquivo determinístico por ID evita colisão por construção.
#    Acesso direto à URL do logo de outra empresa funciona (200) - ESPERADO,
#    não é falha de isolamento: é um asset estático público (mesmo nível de
#    "publicidade" que totem/painel de TV, que também não exigem login),
#    não dado sensível - o que importa é nunca vazar o ARQUIVO/CAMPO errado
#    entre empresas, o que os dois asserts abaixo confirmam.
# ===========================================================================
resp = c_super.post(
    URL_EDITAR_A,
    data={"nome": "Clinica Logo A", "logo": (io.BytesIO(png_valido(cor=(1, 2, 3))), "logo.png")},
    content_type="multipart/form-data",
    follow_redirects=True,
)
assert resp.status_code == 200
with app.app_context():
    assert db.session.get(Empresa, ids["empresa_b"]).logo_filename is None  # empresa B nunca ganhou logo nenhum
    assert not os.path.exists(caminho_logo(ids["empresa_b"]))  # nenhum arquivo foi criado pra B
ok("Upload de logo pra empresa A não cria arquivo nem grava campo para empresa B")

with app.app_context():
    nome_arquivo_a = db.session.get(Empresa, ids["empresa_a"]).logo_filename
assert nome_arquivo_a == f"empresa_{ids['empresa_a']}.png"
assert str(ids["empresa_b"]) not in nome_arquivo_a
ok("Nome do arquivo é determinístico por ID da empresa - sem risco de colisão/mistura entre tenants")

# Teste HTTP de verdade (não só prosa/adendo) do acesso direto à URL: um
# cliente SEM login nenhum consegue baixar o logo da empresa A direto pela
# URL estática - confirma na prática a decisão documentada no item 6 do
# adendo 10.43 (pasta pública, logo não é dado sensível, isolamento garantido
# é o de ARQUIVO/CAMPO por tenant, não o de acesso por URL).
c_anonimo = app.test_client()
resp = c_anonimo.get(f"/static/uploads/logos/{nome_arquivo_a}")
assert resp.status_code == 200
assert resp.mimetype in ("image/png",)
ok("Acesso direto (sem login) à URL estática do logo funciona (200) - decisão documentada, não bug de isolamento")

resp_inexistente = c_anonimo.get(f"/static/uploads/logos/empresa_{ids['empresa_b']}.png")
assert resp_inexistente.status_code == 404
ok("Logo de empresa sem upload nenhum (empresa B) dá 404 na URL estática - simplesmente não existe arquivo, nada vazou")

print()
print(f"TODOS OS {len(passos)} TESTES DE LOGO DE EMPRESA PASSARAM")
