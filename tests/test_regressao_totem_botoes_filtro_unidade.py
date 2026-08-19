"""
Teste de regressão (adendo 10.64): campos "Botão pai" e "Fila" na tela de
Botões do totem (admin/totem_botoes/form.html) não filtravam por Unidade -
bug real relatado pelo cliente, mesma classe do adendo 10.53 (checklist
"Filas de destino permitidas" de admin/locais/form.html), mas com diferenças
confirmadas antes de corrigir: aqui são dois <select> (não checkboxes) e
havia uma segunda regra de negócio faltando ("Botão pai" precisa ser um
botão-menu, sem fila vinculada - um botão-folha não pode ter filhos).

Cobre:
1. data-unidade-id presente nas opções de "Botão pai" e "Fila", nas duas
   telas (novo_totem_botao E editar_totem_botao, mesmo template) - e o JS de
   filtro em tempo real está no HTML.
2. Um botão-folha (fila_id preenchido) nunca aparece como opção de "Botão
   pai" - nem no HTML renderizado, nem aceito no POST (validação de
   servidor, _validar_totem_botao/_candidatos_pai).
3. Na edição, o próprio botão não aparece como opção de pai dele mesmo
   (auto-referência) - comportamento pré-existente, só confirmado aqui.
4. Fila/pai de outra unidade continua rejeitado no servidor no submit
   (_validar_totem_botao já existia antes desta correção - confirma que não
   regrediu).

Execução: python -m tests.test_regressao_totem_botoes_filtro_unidade (a
partir da raiz do projeto, com o virtualenv ativado).
"""
import os

os.environ.setdefault("SECRET_KEY", "test")
os.environ.setdefault("FLASK_ENV", "testing")

from app import create_app
from app.extensions import db
from app.models import Empresa, Fila, Perfil, TotemBotao, Unidade, Usuario

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

    empresa = Empresa(nome="Clinica Botoes Filtro")
    db.session.add(empresa)
    db.session.commit()

    unidade_1 = Unidade(empresa_id=empresa.id, nome="Matriz")
    unidade_2 = Unidade(empresa_id=empresa.id, nome="Filial")
    db.session.add_all([unidade_1, unidade_2])
    db.session.commit()

    fila_u1 = Fila(empresa_id=empresa.id, unidade_id=unidade_1.id, nome="Fila Matriz")
    fila_u2 = Fila(empresa_id=empresa.id, unidade_id=unidade_2.id, nome="Fila Filial")
    db.session.add_all([fila_u1, fila_u2])
    db.session.commit()

    # Botão-MENU na unidade 1 (candidato válido a pai).
    botao_menu_u1 = TotemBotao(empresa_id=empresa.id, unidade_id=unidade_1.id, label="Menu Matriz", fila_id=None)
    # Botão-FOLHA na unidade 1 (NÃO pode ser pai de ninguém - regra achada nesta investigação).
    botao_folha_u1 = TotemBotao(
        empresa_id=empresa.id, unidade_id=unidade_1.id, label="Folha Matriz", fila_id=fila_u1.id, sigla="MTZ",
    )
    # Botão-MENU na unidade 2 (candidato válido a pai só quando a unidade escolhida for a 2).
    botao_menu_u2 = TotemBotao(empresa_id=empresa.id, unidade_id=unidade_2.id, label="Menu Filial", fila_id=None)
    db.session.add_all([botao_menu_u1, botao_folha_u1, botao_menu_u2])
    db.session.commit()

    # Botão a ser editado (unidade 1, filho do botao_menu_u1) - usado nos
    # casos de auto-referência e de filtro na tela de editar.
    botao_editar = TotemBotao(
        empresa_id=empresa.id, unidade_id=unidade_1.id, label="Botao Para Editar",
        parent_id=botao_menu_u1.id, fila_id=None,
    )
    db.session.add(botao_editar)
    db.session.commit()

    def novo_usuario(login, perfis):
        u = Usuario(empresa_id=empresa.id, nome=login, login=login)
        u.set_senha("senha123")
        u.perfis = perfis
        db.session.add(u)
        db.session.commit()
        return u.id

    id_admin = novo_usuario("admin_botoes_filtro", [perfil_admin])

    ids = dict(
        empresa=empresa.id, unidade_1=unidade_1.id, unidade_2=unidade_2.id,
        fila_u1=fila_u1.id, fila_u2=fila_u2.id,
        botao_menu_u1=botao_menu_u1.id, botao_folha_u1=botao_folha_u1.id,
        botao_menu_u2=botao_menu_u2.id, botao_editar=botao_editar.id,
        admin=id_admin,
    )


def login_como(login):
    c = app.test_client()
    resp = c.post("/login", data={"login": login, "senha": "senha123"}, follow_redirects=True)
    assert resp.status_code == 200, (login, resp.status_code)
    return c


c_admin = login_como("admin_botoes_filtro")

# ===========================================================================
# 1) data-unidade-id presente nas opções de "Botão pai" e "Fila", e o JS de
#    filtro em tempo real está no HTML - em novo_totem_botao E
#    editar_totem_botao (mesmo template).
# ===========================================================================
resp_novo = c_admin.get(f"/admin/totem-botoes/novo?empresa_id={ids['empresa']}")
assert resp_novo.status_code == 200
texto_novo = resp_novo.data.decode("utf-8")

assert f'data-unidade-id="{ids["unidade_1"]}"' in texto_novo
assert f'data-unidade-id="{ids["unidade_2"]}"' in texto_novo
ok("novo_totem_botao: data-unidade-id presente nas opções de Botão pai/Fila das DUAS unidades")

assert "function filtrarSelectPorUnidade(" in texto_novo
assert 'elUnidade.addEventListener("change", filtrarTudoPorUnidade)' in texto_novo
assert 'select.value = ""' in texto_novo
ok("novo_totem_botao: JS de filtro em tempo real (esconde + reseta seleção inválida) presente")

resp_editar = c_admin.get(f"/admin/totem-botoes/{ids['botao_editar']}/editar?empresa_id={ids['empresa']}")
assert resp_editar.status_code == 200
texto_editar = resp_editar.data.decode("utf-8")
assert f'data-unidade-id="{ids["unidade_1"]}"' in texto_editar
assert f'data-unidade-id="{ids["unidade_2"]}"' in texto_editar
assert "function filtrarSelectPorUnidade(" in texto_editar
ok("editar_totem_botao: MESMO template, mesmo filtro presente (Unidade é editável de verdade na edição)")

def regiao_select_parent_id(texto):
    """Recorta só o HTML do <select id="parent_id">...</select> - comparar contra
    a página inteira arriscaria falso-positivo/negativo com o value de outro
    <option> coincidindo numericamente (ex.: o próprio select "Unidade")."""
    inicio = texto.index('id="parent_id"')
    fim = texto.index("</select>", inicio)
    return texto[inicio:fim]


# ===========================================================================
# 2) Botão-folha nunca aparece como opção de "Botão pai" - nem no HTML, nem
#    aceito no POST (regra achada nesta investigação, não existia antes).
# ===========================================================================
assert f'value="{ids["botao_folha_u1"]}"' not in regiao_select_parent_id(texto_novo)
ok("novo_totem_botao: botão-folha (fila_id preenchido) não aparece como opção de Botão pai no HTML")

resp = c_admin.post(
    f"/admin/totem-botoes/novo?empresa_id={ids['empresa']}",
    data={"label": "Filho Invalido", "unidade_id": ids["unidade_1"], "parent_id": ids["botao_folha_u1"], "fila_id": ""},
)
assert "Selecione um botão pai válido".encode("utf-8") in resp.data
with app.app_context():
    assert TotemBotao.query.filter_by(label="Filho Invalido").first() is None
ok("Servidor rejeita um botão-folha como Botão pai, mesmo enviado via POST direto (bypass do form)")

# ===========================================================================
# 3) Na edição, o próprio botão não aparece como opção de pai dele mesmo
#    (auto-referência) - comportamento pré-existente, confirmado aqui.
# ===========================================================================
assert f'value="{ids["botao_editar"]}"' not in regiao_select_parent_id(texto_editar)
ok("editar_totem_botao: o próprio botão não aparece como opção de Botão pai dele mesmo")

# ===========================================================================
# 4) Fila/pai de outra unidade continua rejeitado no servidor no submit
#    (_validar_totem_botao já existia - confirma que não regrediu).
# ===========================================================================
resp = c_admin.post(
    f"/admin/totem-botoes/novo?empresa_id={ids['empresa']}",
    data={"label": "Fila Errada", "unidade_id": ids["unidade_1"], "parent_id": "", "fila_id": ids["fila_u2"], "sigla": "ERR"},
)
assert "A fila selecionada pertence a outra unidade".encode("utf-8") in resp.data
ok("Servidor continua rejeitando uma fila de outra unidade no submit (comportamento pré-existente, não regrediu)")

resp = c_admin.post(
    f"/admin/totem-botoes/novo?empresa_id={ids['empresa']}",
    data={"label": "Pai Errado", "unidade_id": ids["unidade_1"], "parent_id": ids["botao_menu_u2"], "fila_id": ""},
)
assert "O botão pai selecionado pertence a outra unidade".encode("utf-8") in resp.data
ok("Servidor continua rejeitando um Botão pai de outra unidade no submit (comportamento pré-existente, não regrediu)")

print()
print(f"TODOS OS {len(passos)} TESTES DE FILTRO POR UNIDADE NA TELA DE BOTOES DO TOTEM PASSARAM")
