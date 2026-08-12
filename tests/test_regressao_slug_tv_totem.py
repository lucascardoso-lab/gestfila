"""
Teste de regressão: slug legível nas URLs públicas de TV e Totem, substituindo o ID
numérico (/painel/tv/<slug>, /totem/<slug>), com as rotas antigas por ID mantidas
como redirect (302, ou 307 para o POST de gerar senha - preserva o corpo do form).

Cobre: geração de slug (acentos/espaços/maiúsculas), colisão gerando sufixo '-2' só
quando necessário (inclusive entre empresas diferentes, já que a unicidade é por
tabela inteira, não por empresa), slug estável após renomear via admin, e o fluxo
completo do totem (tela_raiz -> confirmar -> gerar) funcionando por slug.

Execução: python -m tests.test_regressao_slug_tv_totem (a partir da raiz do
projeto, com o virtualenv ativado).
"""
import os

os.environ.setdefault("SECRET_KEY", "test")
os.environ.setdefault("FLASK_ENV", "testing")

from app import create_app
from app.extensions import db
from app.models import Empresa, Fila, Perfil, Totem, TotemBotao, Tv, Unidade, Usuario
from app.utils.slug import gerar_slug_base, gerar_slug_unico

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

    empresa_a = Empresa(nome="Clinica A")
    empresa_b = Empresa(nome="Clinica B")
    db.session.add_all([empresa_a, empresa_b])
    db.session.commit()

    unidade_a1 = Unidade(empresa_id=empresa_a.id, nome="Unidade A1")
    unidade_b1 = Unidade(empresa_id=empresa_b.id, nome="Unidade B1")
    db.session.add_all([unidade_a1, unidade_b1])
    db.session.commit()

    fila_lab = Fila(empresa_id=empresa_a.id, unidade_id=unidade_a1.id, nome="Laboratorio", sigla="LB")
    db.session.add(fila_lab)
    db.session.commit()

    def novo_usuario(login, empresa_id, perfis):
        u = Usuario(empresa_id=empresa_id, nome=login, login=login)
        u.set_senha("senha123")
        u.perfis = perfis
        db.session.add(u)
        db.session.commit()
        return u.id

    id_admin_a = novo_usuario("admin_a_slug", empresa_a.id, [perfil_admin])

    ids = dict(
        empresa_a=empresa_a.id, empresa_b=empresa_b.id, unidade_a1=unidade_a1.id, unidade_b1=unidade_b1.id,
        fila_lab=fila_lab.id, admin_a=id_admin_a,
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
c_admin_a = login_como("admin_a_slug")

# ---------------------------------------------------------------------------
# 1) Geração de slug: acentos, espaços, maiúsculas, ordinal (º).
# ---------------------------------------------------------------------------
assert gerar_slug_base("1º Andar") == "1-andar"
assert gerar_slug_base("Recepção Principal") == "recepcao-principal"
assert gerar_slug_base("   ") == "item"  # sem nenhum caractere ASCII alfanumérico
ok("gerar_slug_base: acentos, ordinal (º) e espaços tratados corretamente; fallback 'item' se vazio")

assert gerar_slug_unico("Recepção Principal", lambda s: False) == "recepcao-principal"
assert gerar_slug_unico("Recepção Principal", lambda s: s == "recepcao-principal") == "recepcao-principal-2"
assert gerar_slug_unico(
    "Recepção Principal", lambda s: s in ("recepcao-principal", "recepcao-principal-2")
) == "recepcao-principal-3"
ok("gerar_slug_unico: só sufixa quando há colisão real, incrementando a partir de -2")

# ---------------------------------------------------------------------------
# 2) Criação via admin: slug gerado automaticamente a partir do nome.
# ---------------------------------------------------------------------------
resp = c_admin_a.post(
    f"/admin/tvs/nova?empresa_id={ids['empresa_a']}",
    data={"nome": "Recepção Principal", "unidade_id": ids["unidade_a1"], "volume": "100"},
    follow_redirects=True,
)
assert resp.status_code == 200
with app.app_context():
    tv_a = Tv.query.filter_by(nome="Recepção Principal", empresa_id=ids["empresa_a"]).first()
    assert tv_a is not None and tv_a.slug == "recepcao-principal"
    id_tv_a = tv_a.id
ok("Criação de TV via admin: slug 'recepcao-principal' gerado a partir do nome")

# ---------------------------------------------------------------------------
# 3) Colisão entre EMPRESAS DIFERENTES: mesma TV com o mesmo nome na empresa B
#    recebe sufixo '-2', já que a unicidade de slug é por tabela inteira, não por
#    empresa (a URL pública não carrega contexto de empresa). admin_a não é super
#    admin nem administra a empresa B, então simulamos o cadastro de um admin da
#    empresa B diretamente via ORM (mesmo caminho de código de app/admin/tvs.py,
#    sem precisar logar um segundo usuário só para isso).
# ---------------------------------------------------------------------------
with app.app_context():
    tv_b = Tv(empresa_id=ids["empresa_b"], unidade_id=ids["unidade_b1"], nome="Recepção Principal")
    from app.utils.slug import gerar_slug_unico as _gerar

    tv_b.slug = _gerar(tv_b.nome, lambda s: Tv.query.filter_by(slug=s).first() is not None)
    db.session.add(tv_b)
    db.session.commit()
    assert tv_b.slug == "recepcao-principal-2"
    id_tv_b = tv_b.id
ok("Colisão entre empresas diferentes: TV 'Recepção Principal' da empresa B recebe slug 'recepcao-principal-2'")

# ---------------------------------------------------------------------------
# 4) URL pública por slug funciona; URL antiga por ID redireciona (302) para a nova.
# ---------------------------------------------------------------------------
resp = anon.get(f"/painel/tv/{tv_a.slug}")
assert resp.status_code == 200
ok(f"GET /painel/tv/{tv_a.slug} (slug) retorna 200")

resp = anon.get(f"/painel/tv/{id_tv_a}", follow_redirects=False)
assert resp.status_code == 302
assert resp.headers["Location"].endswith(f"/painel/tv/{tv_a.slug}")
ok(f"GET /painel/tv/{id_tv_a} (ID antigo) redireciona (302) para /painel/tv/{tv_a.slug}")

resp = anon.get(f"/painel/tv/{id_tv_a}", follow_redirects=True)
assert resp.status_code == 200
ok("Seguindo o redirect, a URL antiga por ID chega à mesma página (200) da URL por slug")

# ---------------------------------------------------------------------------
# 5) Renomear a TV via admin NÃO recalcula o slug (link já configurado no
#    equipamento físico continua funcionando).
# ---------------------------------------------------------------------------
resp = c_admin_a.post(
    f"/admin/tvs/{id_tv_a}/editar",
    data={"nome": "Recepção Principal (renomeada)", "unidade_id": ids["unidade_a1"], "volume": "100"},
    follow_redirects=True,
)
assert resp.status_code == 200
with app.app_context():
    tv_a_renomeada = db.session.get(Tv, id_tv_a)
    assert tv_a_renomeada.nome == "Recepção Principal (renomeada)"
    assert tv_a_renomeada.slug == "recepcao-principal"  # slug NÃO mudou
ok("Renomear a TV via admin muda o nome mas mantém o slug original (link estável)")

assert anon.get(f"/painel/tv/{tv_a.slug}").status_code == 200
ok("URL pública antiga (mesmo slug) continua funcionando após o rename")

# ---------------------------------------------------------------------------
# 6) Totem: criação via admin gera slug; fluxo completo tela_raiz -> confirmar ->
#    gerar funciona por slug; URLs antigas por unidade_id/totem_id redirecionam.
# ---------------------------------------------------------------------------
resp = c_admin_a.post(
    f"/admin/totens/novo?empresa_id={ids['empresa_a']}",
    data={"nome": "1º Andar", "unidade_id": ids["unidade_a1"]},
    follow_redirects=True,
)
assert resp.status_code == 200
with app.app_context():
    totem = Totem.query.filter_by(nome="1º Andar", empresa_id=ids["empresa_a"]).first()
    assert totem is not None and totem.slug == "1-andar"
    id_totem = totem.id
    slug_totem = totem.slug  # capturado antes do commit abaixo, que expira o objeto

    botao = TotemBotao(
        empresa_id=ids["empresa_a"], unidade_id=ids["unidade_a1"], fila_id=ids["fila_lab"], label="Laboratorio",
        sigla="LB",
    )
    db.session.add(botao)
    db.session.commit()
    id_botao = botao.id
ok("Criação de Totem via admin: slug '1-andar' gerado a partir do nome '1º Andar'")

resp = anon.get(f"/totem/{slug_totem}")
assert resp.status_code == 200
ok(f"GET /totem/{slug_totem} (raiz por slug) retorna 200")

resp = anon.get(f"/totem/{slug_totem}/confirmar/{id_botao}")
assert resp.status_code == 200
ok("GET /totem/<slug>/confirmar/<botao_id> retorna 200")

resp = anon.post(f"/totem/{slug_totem}/gerar/{id_botao}", follow_redirects=True)
assert resp.status_code == 200
assert "LB001" in resp.data.decode("utf-8")
ok("POST /totem/<slug>/gerar/<botao_id> gera a senha LB001 normalmente")

# URLs antigas (unidade_id/totem_id) continuam funcionando via redirect.
resp = anon.get(f"/totem/{ids['unidade_a1']}/{id_totem}", follow_redirects=False)
assert resp.status_code == 302
assert resp.headers["Location"].endswith(f"/totem/{slug_totem}")
ok("GET /totem/<unidade_id>/<totem_id> (URL antiga) redireciona (302) para /totem/<slug>")

resp = anon.get(f"/totem/{ids['unidade_a1']}/{id_totem}/confirmar/{id_botao}", follow_redirects=False)
assert resp.status_code == 302
assert resp.headers["Location"].endswith(f"/totem/{slug_totem}/confirmar/{id_botao}")
ok("GET /totem/<unidade_id>/<totem_id>/confirmar/<botao_id> (URL antiga) redireciona corretamente")

resp = anon.post(f"/totem/{ids['unidade_a1']}/{id_totem}/gerar/{id_botao}", follow_redirects=False)
assert resp.status_code == 307
assert resp.headers["Location"].endswith(f"/totem/{slug_totem}/gerar/{id_botao}")
ok("POST /totem/<unidade_id>/<totem_id>/gerar/<botao_id> (URL antiga) redireciona com 307 (preserva o POST)")

resp = anon.post(f"/totem/{ids['unidade_a1']}/{id_totem}/gerar/{id_botao}", follow_redirects=True)
assert resp.status_code == 200
assert "LB002" in resp.data.decode("utf-8")
ok("Seguindo o redirect 307, a URL antiga de 'gerar' ainda gera a senha corretamente (LB002)")

# ---------------------------------------------------------------------------
# 7) Unidade errada na URL antiga do totem -> 404 (mesma checagem que já existia).
# ---------------------------------------------------------------------------
resp = anon.get(f"/totem/{ids['unidade_b1']}/{id_totem}")
assert resp.status_code == 404
ok("URL antiga do totem com unidade_id errado (de outra empresa) continua dando 404")

print()
print(f"TODOS OS {len(passos)} TESTES DE SLUG EM TV/TOTEM PASSARAM")
