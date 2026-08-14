"""
Teste de regressão: dois ajustes na tela "Editar local" (admin), achados
independentes na mesma investigação:

1. Checklist "Filas de destino permitidas" (admin/locais/form.html, usado por
   novo_local() E editar_local()) mostrava filas de TODAS as unidades da
   empresa, sem filtro - a regra "mesma unidade" só era validada no submit
   (_validar_local), não refletida na tela. Como o campo "Unidade" é um
   <select> dentro do MESMO formulário (pode mudar antes de salvar, sem
   reload), a correção é um filtro em tempo real no cliente: cada item do
   checklist carrega `data-unidade-id`, e o JS esconde (nunca remove do DOM)
   os que não são da unidade selecionada - desmarcando o checkbox se estiver
   marcado, pra não deixar uma seleção escondida sobreviver até o submit.

2. A barra de contexto (Empresa/Unidade, admin/_layout.html) aparecia,
   interativa, em TODAS as telas de editar um objeto específico (fila, local,
   rodízio de local, tipo de senha, botão do totem, totem, TV, unidade,
   usuário, empresa) - sem nenhum efeito ao ser trocada, já que essas rotas
   sempre derivam a empresa do PRÓPRIO objeto sendo editado (comportamento
   intencional, documentado em app/admin/__init__.py::unidades_do_contexto),
   nunca da querystring/sessão. Levantamento completo de todas as rotas
   `editar_*`/`rodizio_local` do admin (10 no total) - cada uma passou a
   enviar `esconder_seletor_contexto=True` pro template, que agora esconde a
   barra inteira nesse caso em vez de mostrar um controle sem efeito.

Execução: python -m tests.test_regressao_admin_editar_contexto (a partir da
raiz do projeto, com o virtualenv ativado).
"""
import os

os.environ.setdefault("SECRET_KEY", "test")
os.environ.setdefault("FLASK_ENV", "testing")

from app import create_app
from app.extensions import db
from app.models import (
    Empresa, Fila, Local, Perfil, TipoSenha, Totem, TotemBotao, Tv, Unidade, Usuario,
)

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

    empresa = Empresa(nome="Clinica Contexto Editar")
    db.session.add(empresa)
    db.session.commit()

    unidade_1 = Unidade(empresa_id=empresa.id, nome="Matriz")
    unidade_2 = Unidade(empresa_id=empresa.id, nome="Filial")
    db.session.add_all([unidade_1, unidade_2])
    db.session.commit()

    fila_u1 = Fila(empresa_id=empresa.id, unidade_id=unidade_1.id, nome="Fila Matriz", sigla="FM")
    fila_u2 = Fila(empresa_id=empresa.id, unidade_id=unidade_2.id, nome="Fila Filial", sigla="FF")
    db.session.add_all([fila_u1, fila_u2])
    db.session.commit()

    local = Local(
        empresa_id=empresa.id, unidade_id=unidade_1.id, nome="Guiche 01",
        nome_exibicao="Guiche 01", identificador_admin="[MAT] Guiche 01",
    )
    db.session.add(local)
    db.session.commit()

    totem = Totem(empresa_id=empresa.id, unidade_id=unidade_1.id, nome="Totem 1", slug="totem-ctx-editar")
    tv = Tv(empresa_id=empresa.id, unidade_id=unidade_1.id, nome="TV 1", slug="tv-ctx-editar")
    tipo_senha = TipoSenha(empresa_id=empresa.id, nome="Normal", prefixo="N", cor="#000000")
    db.session.add_all([totem, tv, tipo_senha])
    db.session.commit()

    botao = TotemBotao(empresa_id=empresa.id, unidade_id=unidade_1.id, label="Botao 1", fila_id=fila_u1.id, sigla="FM")
    db.session.add(botao)
    db.session.commit()

    def novo_usuario(login, empresa_id, perfis):
        u = Usuario(empresa_id=empresa_id, nome=login, login=login)
        u.set_senha("senha123")
        u.perfis = perfis
        db.session.add(u)
        db.session.commit()
        return u.id

    id_admin = novo_usuario("admin_ctx_editar", empresa.id, [perfil_admin])

    id_super = Usuario(nome="Super Admin Ctx Editar", login="super_ctx_editar", empresa_id=None)
    id_super.set_senha("senha123")
    db.session.add(id_super)
    db.session.commit()

    id_usuario_alvo = novo_usuario("usuario_alvo_ctx", empresa.id, [perfil_admin])

    ids = dict(
        empresa=empresa.id, unidade_1=unidade_1.id, unidade_2=unidade_2.id,
        fila_u1=fila_u1.id, fila_u2=fila_u2.id, local=local.id, totem=totem.id, tv=tv.id,
        tipo_senha=tipo_senha.id, botao=botao.id, usuario_alvo=id_usuario_alvo,
    )


def login_como(login):
    c = app.test_client()
    resp = c.post("/login", data={"login": login, "senha": "senha123"}, follow_redirects=True)
    assert resp.status_code == 200, (login, resp.status_code)
    return c


c_super = login_como("super_ctx_editar")
c_admin = login_como("admin_ctx_editar")

# ===========================================================================
# 1) Checklist "Filas de destino permitidas": data-unidade-id presente pra
#    filas das DUAS unidades, e o JS de filtro em tempo real está no HTML -
#    tanto em novo_local quanto em editar_local (mesmo template).
# ===========================================================================
resp_editar = c_super.get(f"/admin/locais/{ids['local']}/editar?empresa_id={ids['empresa']}")
assert resp_editar.status_code == 200
texto_editar = resp_editar.data.decode("utf-8")

assert f'data-unidade-id="{ids["unidade_1"]}"' in texto_editar
assert f'data-unidade-id="{ids["unidade_2"]}"' in texto_editar
ok("editar_local: checklist carrega data-unidade-id pras filas das DUAS unidades")

assert "function filtrarFilasPorUnidade(" in texto_editar
assert 'elUnidade.addEventListener("change", filtrarFilasPorUnidade)' in texto_editar
assert "checkbox.checked = false" in texto_editar
ok("editar_local: JS de filtro em tempo real (esconde + desmarca) presente")

resp_novo = c_super.get(f"/admin/locais/novo?empresa_id={ids['empresa']}")
assert resp_novo.status_code == 200
texto_novo = resp_novo.data.decode("utf-8")
assert f'data-unidade-id="{ids["unidade_1"]}"' in texto_novo
assert f'data-unidade-id="{ids["unidade_2"]}"' in texto_novo
assert "function filtrarFilasPorUnidade(" in texto_novo
ok("novo_local: MESMO template, mesmo filtro presente (não só em editar_local)")

# ===========================================================================
# 2) Barra de contexto (Empresa/Unidade) escondida em TODAS as rotas
#    editar_*/rodizio_local levantadas - super admin normalmente VÊ o
#    seletor de Empresa (id="contexto-empresa") em telas de listagem; nas
#    telas de editar um objeto, não deve aparecer de jeito nenhum.
# ===========================================================================
rotas_editar = {
    "editar_fila": f"/admin/filas/{ids['fila_u1']}/editar",
    "editar_local": f"/admin/locais/{ids['local']}/editar",
    "rodizio_local": f"/admin/locais/{ids['local']}/rodizio",
    "editar_tipo_senha": f"/admin/tipos-senha/{ids['tipo_senha']}/editar",
    "editar_totem_botao": f"/admin/totem-botoes/{ids['botao']}/editar",
    "editar_totem": f"/admin/totens/{ids['totem']}/editar",
    "editar_tv": f"/admin/tvs/{ids['tv']}/editar",
    "editar_unidade": f"/admin/unidades/{ids['unidade_1']}/editar",
    "editar_usuario": f"/admin/usuarios/{ids['usuario_alvo']}/editar",
    "editar_empresa": f"/admin/empresas/{ids['empresa']}/editar",
}

for nome_rota, url in rotas_editar.items():
    resp = c_super.get(url)
    assert resp.status_code == 200, (nome_rota, resp.status_code)
    texto = resp.data.decode("utf-8")
    assert 'id="contexto-empresa"' not in texto, nome_rota
    assert 'class="barra-contexto"' not in texto, nome_rota
    ok(f"{nome_rota}: barra de contexto (Empresa/Unidade) NÃO aparece")

# ---------------------------------------------------------------------------
# Caso de controle: uma tela de LISTAGEM comum (não editar um objeto) continua
# mostrando a barra normalmente pro super admin - a mudança não escondeu o
# seletor globalmente, só nas telas de editar.
# ---------------------------------------------------------------------------
resp = c_super.get(f"/admin/locais?empresa_id={ids['empresa']}")
assert resp.status_code == 200
texto = resp.data.decode("utf-8")
assert 'id="contexto-empresa"' in texto
ok("Controle: listar_locais (listagem, não edição) continua mostrando a barra de contexto normalmente")

print()
print(f"TODOS OS {len(passos)} TESTES DE CONTEXTO EM TELAS DE EDITAR PASSARAM")
