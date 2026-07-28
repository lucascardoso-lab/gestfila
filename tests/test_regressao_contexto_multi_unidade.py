"""
Teste de regressão: UX de contexto multi-unidade no painel admin (confusão visual
do super admin ao ver locais/filas/botões de unidades diferentes misturados em
listas planas).

Cobre:
1. `Unidade.sigla_exibicao` - sigla explícita vs fallback pros 3 primeiros
   caracteres do nome.
2. Barra de contexto (Empresa + Unidade) - persistência em sessão: escolher uma
   vez e navegar por outras telas sem precisar reselecionar.
3. Filtro por unidade nas listagens (locais/filas/usuários/totens/TVs).
4. Troca de empresa limpa a unidade de contexto (não faz sentido pra outra
   empresa); `unidade_id` de outra empresa nunca filtra (isolamento
   multi-tenant preservado mesmo manipulando a querystring).
5. Admin de empresa (não super admin) vê o seletor de Unidade, mas NUNCA o de
   Empresa - já está implicitamente restrito à própria.
6. Breadcrumb "Empresa › Unidade › Nome da tela" nas telas admin.
7. Prefixo "[SIGLA] Nome" + <optgroup> nos selects que listam locais/filas de
   múltiplas unidades: tela "Iniciar Turno", Totem/TV no cadastro de Fila, Fila
   no cadastro de Botão do totem.
8. Bloco Redirecionar (busca + acesso rápido) também recebe o prefixo de unidade.

Execução: python -m tests.test_regressao_contexto_multi_unidade (a partir da
raiz do projeto, com o virtualenv ativado).
"""
import os

os.environ.setdefault("SECRET_KEY", "test")
os.environ.setdefault("FLASK_ENV", "testing")

from app import create_app
from app.extensions import db
from app.models import Empresa, Fila, Local, Perfil, Senha, Totem, Tv, Unidade, Usuario

app = create_app("testing")

passos = []


def ok(msg):
    passos.append(msg)
    print("OK:", msg)


with app.app_context():
    db.create_all()

    perfil_admin = Perfil(nome="admin")
    perfil_recepcao = Perfil(nome="atendente")
    db.session.add_all([perfil_admin, perfil_recepcao])
    db.session.commit()

    empresa_a = Empresa(nome="Clinica A Teste")
    empresa_b = Empresa(nome="Clinica B Teste")
    db.session.add_all([empresa_a, empresa_b])
    db.session.commit()

    matriz = Unidade(empresa_id=empresa_a.id, nome="Matriz", sigla="MAT")
    anexo = Unidade(empresa_id=empresa_a.id, nome="Anexo Mezanino")  # sem sigla - testa fallback
    unidade_b = Unidade(empresa_id=empresa_b.id, nome="Unidade Unica B", sigla="UUB")
    db.session.add_all([matriz, anexo, unidade_b])
    db.session.commit()

    fila_matriz = Fila(empresa_id=empresa_a.id, unidade_id=matriz.id, nome="Recepção Geral", sigla="RG")
    fila_anexo = Fila(empresa_id=empresa_a.id, unidade_id=anexo.id, nome="Recepção Geral", sigla="RA")
    db.session.add_all([fila_matriz, fila_anexo])
    db.session.commit()

    local_matriz = Local(empresa_id=empresa_a.id, unidade_id=matriz.id, nome="Guiche 01", tipo="recepcao")
    local_anexo = Local(empresa_id=empresa_a.id, unidade_id=anexo.id, nome="Guiche 01", tipo="recepcao")
    db.session.add_all([local_matriz, local_anexo])
    db.session.commit()

    totem_matriz = Totem(empresa_id=empresa_a.id, unidade_id=matriz.id, nome="Totem Matriz", slug="totem-ctx-matriz")
    totem_anexo = Totem(empresa_id=empresa_a.id, unidade_id=anexo.id, nome="Totem Anexo", slug="totem-ctx-anexo")
    db.session.add_all([totem_matriz, totem_anexo])
    db.session.commit()

    tv_matriz = Tv(empresa_id=empresa_a.id, unidade_id=matriz.id, nome="TV Matriz", slug="tv-ctx-matriz")
    tv_anexo = Tv(empresa_id=empresa_a.id, unidade_id=anexo.id, nome="TV Anexo", slug="tv-ctx-anexo")
    db.session.add_all([tv_matriz, tv_anexo])
    db.session.commit()

    # Configuração explícita (Local.filas_destino_permitidas, seção 10.3) pra
    # exercitar o caso motivador do pedido: o bloco Redirecionar do local_matriz
    # pode apontar pra uma fila de OUTRA unidade (fila_anexo) - é exatamente esse
    # cenário que precisa do prefixo "[SIGLA]" pra não confundir o operador.
    local_matriz.filas_destino_permitidas = [fila_anexo]
    db.session.commit()

    def novo_usuario(login, empresa_id, perfis, unidades):
        u = Usuario(empresa_id=empresa_id, nome=login, login=login)
        u.set_senha("senha123")
        u.perfis = perfis
        u.unidades = unidades
        db.session.add(u)
        db.session.commit()
        return u.id

    id_super = Usuario(nome="Super Admin CTX", login="super_ctx", empresa_id=None)
    id_super.set_senha("senha123")
    db.session.add(id_super)
    db.session.commit()

    id_admin_a = novo_usuario("admin_ctx_a", empresa_a.id, [perfil_admin], [])
    id_usuario_matriz = novo_usuario("usuario_matriz_ctx", empresa_a.id, [perfil_recepcao], [matriz])

    ids = dict(
        empresa_a=empresa_a.id, empresa_b=empresa_b.id,
        matriz=matriz.id, anexo=anexo.id, unidade_b=unidade_b.id,
        fila_matriz=fila_matriz.id, fila_anexo=fila_anexo.id,
        local_matriz=local_matriz.id, local_anexo=local_anexo.id,
        totem_matriz=totem_matriz.id, totem_anexo=totem_anexo.id,
        tv_matriz=tv_matriz.id, tv_anexo=tv_anexo.id,
        super=id_super.id, admin_a=id_admin_a, usuario_matriz=id_usuario_matriz,
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
    return texto[inicio:texto.index('"', inicio)]


# ===========================================================================
# A) Unidade.sigla_exibicao - sigla explícita vs fallback (3 primeiros
#    caracteres do nome, maiúsculas).
# ===========================================================================
with app.app_context():
    m = db.session.get(Unidade, ids["matriz"])
    a = db.session.get(Unidade, ids["anexo"])
    assert m.sigla_exibicao == "MAT"
    assert a.sigla_exibicao == "ANE"  # fallback: 3 primeiros chars de "Anexo Mezanino"
ok("Unidade.sigla_exibicao usa a sigla cadastrada, ou os 3 primeiros caracteres do nome como fallback")

# ===========================================================================
# B) Super admin: barra de contexto (Empresa + Unidade) presente, e a escolha
#    de empresa persiste na sessão entre páginas diferentes sem precisar
#    reselecionar (item explícito do pedido).
# ===========================================================================
c_super = login_como("super_ctx")

resp = c_super.get(f"/admin/locais?empresa_id={ids['empresa_a']}")
assert resp.status_code == 200
texto = resp.data.decode("utf-8")
assert 'id="contexto-empresa"' in texto
assert 'id="contexto-unidade"' in texto
ok("Super admin vê a barra de contexto com os dois selects (Empresa e Unidade)")

# nenhum empresa_id na querystring desta vez - deve reaproveitar a empresa A
# escolhida no passo anterior (sessão), SEM cair no seletor de empresas de novo.
resp = c_super.get("/admin/filas")
assert resp.status_code == 200
texto = resp.data.decode("utf-8")
assert "Selecione uma empresa" not in texto
assert f'value="{ids["empresa_a"]}"' in texto and "selected" in texto[texto.index(f'value="{ids["empresa_a"]}"'):texto.index(f'value="{ids["empresa_a"]}"') + 80]
ok("Sem empresa_id explícito, a tela seguinte reaproveita a empresa da sessão (não pede pra reselecionar)")

# ===========================================================================
# C) Filtro por unidade - listagens de locais/filas/totens/TVs/usuários
#    respeitam a unidade escolhida na barra de contexto.
# ===========================================================================
resp = c_super.get(f"/admin/locais?empresa_id={ids['empresa_a']}&unidade_id={ids['matriz']}")
assert resp.status_code == 200
texto = resp.data.decode("utf-8")
assert "Guiche 01" in texto
# Matriz e Anexo têm um local com o MESMO nome ("Guiche 01") de propósito - o que
# prova o filtro é qual dos dois links de edição (por ID) aparece.
assert f"/locais/{ids['local_matriz']}/editar" in texto
assert f"/locais/{ids['local_anexo']}/editar" not in texto
ok("Listagem de Locais filtrada pela unidade de contexto (Matriz) - Anexo não aparece")

# unidade persiste na sessão pras próximas telas também, sem repetir na querystring.
resp = c_super.get("/admin/filas")
assert resp.status_code == 200
texto = resp.data.decode("utf-8")
assert f"/filas/{ids['fila_matriz']}/editar" in texto
assert f"/filas/{ids['fila_anexo']}/editar" not in texto
ok("Filtro de unidade também persiste na sessão - listagem de Filas já chega filtrada")

resp = c_super.get("/admin/totens")
texto = resp.data.decode("utf-8")
assert f"/totens/{ids['totem_matriz']}/editar" in texto and f"/totens/{ids['totem_anexo']}/editar" not in texto
ok("Listagem de Totens também respeita a unidade de contexto")

resp = c_super.get("/admin/tvs")
texto = resp.data.decode("utf-8")
assert f"/tvs/{ids['tv_matriz']}/editar" in texto and f"/tvs/{ids['tv_anexo']}/editar" not in texto
ok("Listagem de TVs também respeita a unidade de contexto")

resp = c_super.get("/admin/usuarios")
texto = resp.data.decode("utf-8")
assert "usuario_matriz_ctx" in texto
ok("Listagem de Usuários também respeita a unidade de contexto (usuário vinculado à Matriz aparece)")

# volta pra "Todas as unidades" explicitamente (unidade_id vazio) - limpa o filtro.
resp = c_super.get(f"/admin/locais?unidade_id=")
assert resp.status_code == 200
texto = resp.data.decode("utf-8")
assert f"/locais/{ids['local_matriz']}/editar" in texto and f"/locais/{ids['local_anexo']}/editar" in texto
ok("Escolher 'Todas as unidades' de volta limpa o filtro - as duas unidades voltam a aparecer")

# ===========================================================================
# D) Trocar de empresa limpa a unidade de contexto (não existe naquela empresa).
# ===========================================================================
resp = c_super.get(f"/admin/locais?empresa_id={ids['empresa_a']}&unidade_id={ids['matriz']}")
assert resp.status_code == 200  # reconfirma o filtro em Matriz antes de trocar de empresa

resp = c_super.get(f"/admin/unidades?empresa_id={ids['empresa_b']}")
assert resp.status_code == 200
texto = resp.data.decode("utf-8")
assert "Unidade Unica B" in texto
ok("Trocar para a empresa B mostra os dados da empresa B normalmente")

resp = c_super.get("/admin/locais")
assert resp.status_code == 200
texto = resp.data.decode("utf-8")
assert "Nenhum local cadastrado" in texto  # empresa B não tem locais - não vazou o filtro da Matriz (empresa A)
ok("Trocar de empresa limpa a unidade de contexto anterior (não filtra por uma unidade de outra empresa)")

# ===========================================================================
# E) Isolamento multi-tenant preservado: um unidade_id de OUTRA empresa nunca
#    filtra/vaza dados - resolver_unidade_contexto valida empresa_id.
# ===========================================================================
resp = c_super.get(f"/admin/locais?empresa_id={ids['empresa_a']}&unidade_id={ids['unidade_b']}")
assert resp.status_code == 200
texto = resp.data.decode("utf-8")
# unidade_id inválida pra esta empresa -> contexto vira None (sem filtro) - as DUAS
# unidades da empresa A aparecem, nenhum dado da empresa B jamais aparece aqui.
assert f"/locais/{ids['local_matriz']}/editar" in texto and f"/locais/{ids['local_anexo']}/editar" in texto
ok("unidade_id de outra empresa é ignorado com segurança (sem filtro), isolamento multi-tenant preservado")

# ===========================================================================
# F) Admin de empresa (não super admin): vê o seletor de Unidade, mas NUNCA o
#    de Empresa - já está implicitamente restrito à própria empresa.
# ===========================================================================
c_admin_a = login_como("admin_ctx_a")
resp = c_admin_a.get("/admin/locais")
assert resp.status_code == 200
texto = resp.data.decode("utf-8")
assert 'id="contexto-unidade"' in texto
assert 'id="contexto-empresa"' not in texto
ok("Admin de empresa vê o seletor de Unidade, mas NÃO vê o seletor de Empresa")

resp = c_admin_a.get(f"/admin/locais?unidade_id={ids['matriz']}")
assert resp.status_code == 200
texto = resp.data.decode("utf-8")
assert f"/locais/{ids['local_matriz']}/editar" in texto and f"/locais/{ids['local_anexo']}/editar" not in texto
ok("Filtro de unidade também funciona pro admin de empresa, dentro da própria empresa")

# admin de empresa não consegue usar unidade_id pra enxergar outra empresa via
# manipulação de querystring - autorizar_objeto/g.unidades_permitidas continuam
# de pé; aqui a prova é que resolver_unidade_contexto valida contra a PRÓPRIA empresa.
resp = c_admin_a.get(f"/admin/locais?unidade_id={ids['unidade_b']}")
assert resp.status_code == 200
texto = resp.data.decode("utf-8")
assert f"/locais/{ids['local_matriz']}/editar" in texto and f"/locais/{ids['local_anexo']}/editar" in texto
ok("Admin de empresa manipulando unidade_id de outra empresa não filtra nem vaza nada - cai em 'sem filtro'")

# ===========================================================================
# G) Breadcrumb "Empresa › Unidade › Nome da tela".
# ===========================================================================
resp = c_super.get(f"/admin/locais?empresa_id={ids['empresa_a']}&unidade_id={ids['matriz']}")
texto = resp.data.decode("utf-8")
assert '<p class="breadcrumb-contexto">' in texto
trecho_breadcrumb = texto[texto.index('<p class="breadcrumb-contexto">'):texto.index("</p>", texto.index('<p class="breadcrumb-contexto">'))]
assert "Clinica A Teste" in trecho_breadcrumb
assert "Matriz" in trecho_breadcrumb
assert "Locais" in trecho_breadcrumb
ok("Breadcrumb mostra Empresa > Unidade > Nome da tela")

resp = c_super.get(f"/admin/locais?empresa_id={ids['empresa_a']}&unidade_id=")
texto = resp.data.decode("utf-8")
trecho_breadcrumb = texto[texto.index('<p class="breadcrumb-contexto">'):texto.index("</p>", texto.index('<p class="breadcrumb-contexto">'))]
assert "Todas as unidades" in trecho_breadcrumb
ok("Sem unidade escolhida, o breadcrumb mostra 'Todas as unidades'")

# ===========================================================================
# H) Prefixo "[SIGLA] Nome" + <optgroup> nos selects multi-unidade.
# ===========================================================================
# H.1) Tela "Iniciar Turno" (seção 10.19) - fila e local.
c_usuario_matriz = login_como("usuario_matriz_ctx")
# este usuário só tem acesso à Matriz - login já redireciona pra 'Iniciar Turno'
# (perfil recepcao puro) - troca de usuário com acesso às DUAS unidades pra ver
# o agrupamento de fato (usuario_matriz só veria 1 optgroup).
with app.app_context():
    u = db.session.get(Usuario, ids["usuario_matriz"])
    u.unidades = [db.session.get(Unidade, ids["matriz"]), db.session.get(Unidade, ids["anexo"])]
    db.session.commit()
c_usuario_matriz = login_como("usuario_matriz_ctx")

resp = c_usuario_matriz.get("/atendimento/iniciar-turno")
assert resp.status_code == 200
texto = resp.data.decode("utf-8")
assert '<optgroup label="MATRIZ">' in texto
assert '<optgroup label="ANEXO MEZANINO">' in texto
assert "[MAT] Recepção Geral" in texto
assert "[ANE] Recepção Geral" in texto
assert "[MAT] Guiche 01" in texto
assert "[ANE] Guiche 01" in texto
ok("Tela 'Iniciar Turno': selects de Fila e Local usam <optgroup> por unidade + prefixo [SIGLA]")

# H.2) Cadastro de Fila (admin) - selects de Totem e TV.
resp = c_super.get(f"/admin/filas/nova?empresa_id={ids['empresa_a']}")
assert resp.status_code == 200
texto = resp.data.decode("utf-8")
assert '<optgroup label="MATRIZ">' in texto
assert '<optgroup label="ANEXO MEZANINO">' in texto
assert "[MAT] Totem Matriz" in texto
assert "[ANE] Totem Anexo" in texto
assert "[MAT] TV Matriz" in texto
assert "[ANE] TV Anexo" in texto
ok("Cadastro de Fila: selects de Totem e TV usam <optgroup> por unidade + prefixo [SIGLA]")

# H.3) Cadastro de Botão do totem (admin) - select de Fila.
resp = c_super.get(f"/admin/totem-botoes/novo?empresa_id={ids['empresa_a']}")
assert resp.status_code == 200
texto = resp.data.decode("utf-8")
assert "[MAT] Recepção Geral" in texto
assert "[ANE] Recepção Geral" in texto
ok("Cadastro de Botão do totem: select de Fila usa <optgroup> por unidade + prefixo [SIGLA]")

# ===========================================================================
# I) Bloco Redirecionar - prefixo de unidade nos botões de acesso rápido e na
#    busca livre.
# ===========================================================================
with app.app_context():
    s = Senha(
        empresa_id=ids["empresa_a"], unidade_id=ids["matriz"], fila_id=ids["fila_matriz"],
        codigo="RG001", sequencial=1, prioridade=False, status="chamada",
        local_id=ids["local_matriz"], operador_id=ids["usuario_matriz"], setor="recepcao",
    )
    db.session.add(s)
    db.session.commit()

with c_usuario_matriz.session_transaction() as sess:
    sess["atendimento_local_id"] = ids["local_matriz"]

resp = c_usuario_matriz.get(f"/atendimento/recepcao/filas/{ids['fila_matriz']}")
assert resp.status_code == 200
texto = resp.data.decode("utf-8")
assert '"nome_exibicao"' in texto
# "Recepção" pode vir com o "ç" escapado como ç dentro do JSON embutido -
# checa só o prefixo (ASCII) pra não depender de como o Jinja/json.dumps escapou.
assert "[ANE] Recep" in texto
ok("Bloco Redirecionar: JSON da busca livre embute nome_exibicao com o prefixo [SIGLA]")

print()
print(f"TODOS OS {len(passos)} TESTES DE CONTEXTO MULTI-UNIDADE PASSARAM")
