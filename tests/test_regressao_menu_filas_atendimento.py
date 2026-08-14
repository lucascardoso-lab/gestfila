"""
Teste de regressão: dois ajustes relacionados no fluxo do perfil operacional
(atendente/executor/atendente_executor):

1. Link "Filas" escondido do menu (nav de app/templates/atendimento/_layout.html)
   pra estes perfis - a tela legada (listar_filas, pré-10.19) não faz parte do
   fluxo deles (Iniciar Turno -> painel_local). Admin continua vendo. Rota
   continua acessível por URL direta (sem bloqueio de RBAC novo).

2. Link "Ir para atendimento (chamada de senhas)" em core/index.html apontava
   pra atendimento.listar_filas (desatualizado desde a 10.19/10.23) - corrigido
   pra atendimento.iniciar_turno, mesmo destino que login() já usa pra estes
   perfis (auth/routes.py::_deve_ir_direto_para_turno). Sem lógica condicional
   de TurnoAtivo: iniciar_turno() sempre mostra o formulário mesmo com turno
   ativo (decisão deliberada da 10.23, "nunca contornado silenciosamente") -
   confirmado explicitamente com o cliente antes de implementar, não
   reintroduzir aqui o comportamento antigo de pular pro painel_local.

3. Mesmo bug do item 2, segunda ocorrência achada depois em uso real: item
   "🎫 Atendimento" do nav em core/dashboard.html também apontava pra
   atendimento.listar_filas - mesma correção, mesmo destino único.

Execução: python -m tests.test_regressao_menu_filas_atendimento (a partir da
raiz do projeto, com o virtualenv ativado).
"""
import os

os.environ.setdefault("SECRET_KEY", "test")
os.environ.setdefault("FLASK_ENV", "testing")

from app import create_app
from app.extensions import db
from app.models import Empresa, Fila, Local, Perfil, TurnoAtivo, Unidade, Usuario

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

    empresa = Empresa(nome="Clinica Menu Filas")
    db.session.add(empresa)
    db.session.commit()

    unidade = Unidade(empresa_id=empresa.id, nome="Unidade 1")
    db.session.add(unidade)
    db.session.commit()

    fila = Fila(empresa_id=empresa.id, unidade_id=unidade.id, nome="Recepcao", sigla="RC")
    db.session.add(fila)
    db.session.commit()

    local = Local(
        empresa_id=empresa.id, unidade_id=unidade.id, nome="Guiche 01", tipo="recepcao",
        nome_exibicao="Guiche 01", identificador_admin="[UNI] Guiche 01",
    )
    db.session.add(local)
    db.session.commit()

    def novo_usuario(login, perfis):
        u = Usuario(empresa_id=empresa.id, nome=login, login=login)
        u.set_senha("senha123")
        u.perfis = perfis
        u.unidades = [unidade]
        db.session.add(u)
        db.session.commit()
        return u.id

    id_atendente = novo_usuario("atendente_menu", [perfil_atendente])
    id_atendente_com_turno = novo_usuario("atendente_com_turno", [perfil_atendente])
    id_admin = novo_usuario("admin_menu", [perfil_admin])

    db.session.add(TurnoAtivo(
        usuario_id=id_atendente_com_turno, local_id=local.id,
        filas_config=[{"fila_id": fila.id, "ordem": 0, "chamadas_seguidas": 0}], ativo=True,
    ))
    db.session.commit()

    ids = dict(
        empresa=empresa.id, unidade=unidade.id, fila=fila.id, local=local.id,
        atendente=id_atendente, atendente_com_turno=id_atendente_com_turno, admin=id_admin,
    )


def login_como(login):
    c = app.test_client()
    resp = c.post("/login", data={"login": login, "senha": "senha123"}, follow_redirects=True)
    assert resp.status_code == 200, (login, resp.status_code)
    return c


c_atendente = login_como("atendente_menu")
c_atendente_com_turno = login_como("atendente_com_turno")
c_admin = login_como("admin_menu")

# ===========================================================================
# 1) Link "Filas" escondido do menu (nav atendimento/_layout.html) pra
#    atendente, presente pra admin - rota continua acessível por URL direta
#    pros dois (sem bloqueio de RBAC novo).
# ===========================================================================
resp = c_atendente.get("/atendimento/filas")
assert resp.status_code == 200
texto = resp.data.decode("utf-8")
assert "🎫 Filas" not in texto
ok("Atendente: link 'Filas' ausente do menu (nav de atendimento/_layout.html)")

resp = c_admin.get("/atendimento/filas")
assert resp.status_code == 200
texto = resp.data.decode("utf-8")
assert "🎫 Filas" in texto
ok("Admin: link 'Filas' continua presente no menu")

# rota em si continua 200 por URL direta pro atendente, mesmo sem o link no menu.
resp = c_atendente.get("/atendimento/filas")
assert resp.status_code == 200
ok("Rota /atendimento/filas continua acessível por URL direta pro atendente (sem RBAC novo)")

# outra tela do mesmo blueprint (iniciar_turno, real ponto de entrada do
# atendente) também não deve mostrar o link.
resp = c_atendente.get("/atendimento/iniciar-turno")
assert resp.status_code == 200
assert "🎫 Filas" not in resp.data.decode("utf-8")
ok("Link 'Filas' também ausente em outras telas do blueprint atendimento (iniciar_turno)")

# ===========================================================================
# 2) Link "Ir para atendimento" em core.index aponta pra iniciar_turno, não
#    mais pra listar_filas - MESMO destino pra atendente com ou sem
#    TurnoAtivo (sem lógica condicional, confirmado com o cliente: 10.23
#    diz que iniciar_turno() sempre mostra o formulário).
# ===========================================================================
resp = c_atendente.get("/")
assert resp.status_code == 200
texto = resp.data.decode("utf-8")
assert 'href="/atendimento/iniciar-turno"' in texto
assert 'href="/atendimento/filas"' not in texto
ok("core.index (atendente SEM turno ativo): link aponta pra /atendimento/iniciar-turno, não mais /atendimento/filas")

resp = c_atendente_com_turno.get("/")
assert resp.status_code == 200
texto = resp.data.decode("utf-8")
assert 'href="/atendimento/iniciar-turno"' in texto
assert 'href="/atendimento/filas"' not in texto
ok("core.index (atendente COM turno ativo): link aponta pro MESMO destino (/atendimento/iniciar-turno), sem pular pro painel_local")

# admin também vê o link (já cobria antes, continua cobrindo com o novo href).
resp = c_admin.get("/")
assert resp.status_code == 200
texto = resp.data.decode("utf-8")
assert 'href="/atendimento/iniciar-turno"' in texto
ok("core.index (admin): link 'Ir para atendimento' continua presente, com o href corrigido")

# ===========================================================================
# 3) Segunda ocorrência do mesmo bug, achada depois em uso real: item
#    "🎫 Atendimento" do nav em core/dashboard.html tinha o MESMO problema
#    (apontava pra listar_filas) - mesmo destino corrigido, mesmo padrão.
# ===========================================================================
resp = c_atendente.get("/dashboard")
assert resp.status_code == 200
texto = resp.data.decode("utf-8")
assert 'href="/atendimento/iniciar-turno"' in texto
assert 'href="/atendimento/filas"' not in texto
ok("core.dashboard (atendente): item 'Atendimento' do nav aponta pra /atendimento/iniciar-turno, não mais /atendimento/filas")

resp = c_atendente_com_turno.get("/dashboard")
assert resp.status_code == 200
texto = resp.data.decode("utf-8")
assert 'href="/atendimento/iniciar-turno"' in texto
ok("core.dashboard (atendente COM turno ativo): mesmo destino, sem pular pro painel_local")

print()
print(f"TODOS OS {len(passos)} TESTES DE MENU FILAS/ATENDIMENTO PASSARAM")
