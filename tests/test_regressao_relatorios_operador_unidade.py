"""
Teste de regressão: o filtro/dropdown de operador em relatorios/helpers.py deve
respeitar g.unidades_permitidas (isolamento por unidade), igual a unidade_id/fila_id.

Gap de segurança identificado na auditoria de IDOR dos relatórios (item 6 da seção 7
do BLUEPRINT): antes desta correção, resolver_filtros() e opcoes_filtro() listavam/
validavam operador_id contra TODOS os usuários da empresa, sem passar pela mesma
restrição de unidade já aplicada a unidade_id/fila_id via _unidades_ids_visiveis -
um 'gerencial' restrito a uma unidade via a lista completa de nomes de operadores da
empresa inteira no <select>, mesmo de unidades que ele não tem permissão de acessar.

A correção adiciona _operadores_visiveis(empresa), que filtra por
Usuario.unidades.any(Unidade.id.in_(g.unidades_permitidas)) - super admin
(g.unidades_permitidas=None) e perfil 'admin' (g.unidades_permitidas já contém todas
as unidades da empresa, ver middleware/tenant.py) continuam sem restrição, por
construção do próprio g.unidades_permitidas.

Execução: python -m tests.test_regressao_relatorios_operador_unidade (a partir da
raiz do projeto, com o virtualenv ativado).
"""
import os

os.environ.setdefault("SECRET_KEY", "test")
os.environ.setdefault("FLASK_ENV", "testing")

from app import create_app
from app.extensions import db
from app.models import Empresa, Perfil, Unidade, Usuario

app = create_app("testing")

passos = []


def ok(msg):
    passos.append(msg)
    print("OK:", msg)


with app.app_context():
    db.create_all()

    perfil_gerencial = Perfil(nome="gerencial")
    perfil_recepcao = Perfil(nome="atendente")
    perfil_admin = Perfil(nome="admin")
    db.session.add_all([perfil_gerencial, perfil_recepcao, perfil_admin])
    db.session.commit()

    empresa_a = Empresa(nome="Clinica A")
    db.session.add(empresa_a)
    db.session.commit()

    unidade_a1 = Unidade(empresa_id=empresa_a.id, nome="Unidade A1")
    unidade_a2 = Unidade(empresa_id=empresa_a.id, nome="Unidade A2")
    db.session.add_all([unidade_a1, unidade_a2])
    db.session.commit()

    def novo_usuario(login, nome, perfis, unidades):
        u = Usuario(empresa_id=empresa_a.id, nome=nome, login=login)
        u.set_senha("senha123")
        u.perfis = perfis
        u.unidades = unidades
        db.session.add(u)
        db.session.commit()
        return u.id

    id_operador_a1 = novo_usuario("operador_a1_rel", "Fulano da Unidade A1", [perfil_recepcao], [unidade_a1])
    id_operador_a2 = novo_usuario("operador_a2_rel", "Beltrano da Unidade A2", [perfil_recepcao], [unidade_a2])
    id_gerencial_a1 = novo_usuario("gerencial_a1_rel", "Gerencial A1", [perfil_gerencial], [unidade_a1])
    id_admin_a = novo_usuario("admin_a_rel", "Admin A", [perfil_admin], [])

    ids = dict(
        empresa_a=empresa_a.id, unidade_a1=unidade_a1.id, unidade_a2=unidade_a2.id,
        operador_a1=id_operador_a1, operador_a2=id_operador_a2,
        gerencial_a1=id_gerencial_a1, admin_a=id_admin_a,
    )

print("IDS", ids)


def login_como(login):
    c = app.test_client()
    resp = c.post("/login", data={"login": login, "senha": "senha123"}, follow_redirects=True)
    assert resp.status_code == 200, (login, resp.status_code)
    return c


c_gerencial = login_como("gerencial_a1_rel")
c_admin = login_como("admin_a_rel")

URL = "/relatorios/tempo-atendimento"  # única tela que exibe o dropdown de Operador (mostrar_operador=True)

# ---------------------------------------------------------------------------
# 1) Dropdown de operador: gerencial restrito à unidade A1 só vê operadores
#    vinculados à A1, não vê o operador vinculado só à A2 (mesma empresa).
# ---------------------------------------------------------------------------
resp = c_gerencial.get(URL)
assert resp.status_code == 200
texto = resp.data.decode("utf-8")
assert "Fulano da Unidade A1" in texto
assert "Beltrano da Unidade A2" not in texto
ok("Dropdown de operador: gerencial restrito à unidade A1 vê 'Fulano' (A1), não vê 'Beltrano' (A2)")

# ---------------------------------------------------------------------------
# 2) Tentativa de forçar operador_id de outra unidade via querystring é descartada
#    silenciosamente (mesmo padrão de unidade_id/fila_id em resolver_filtros) - o
#    filtro não é aplicado e o link de exportação reconstruído não carrega esse id.
# ---------------------------------------------------------------------------
resp = c_gerencial.get(f"{URL}?operador_id={ids['operador_a2']}")
assert resp.status_code == 200
texto = resp.data.decode("utf-8")
assert "Beltrano da Unidade A2" not in texto
assert f"operador_id={ids['operador_a2']}" not in texto  # não reaparece nos links de exportar CSV/Excel
ok("operador_id de outra unidade forçado na querystring é descartado (filtro ignorado, sem vazar no link de export)")

# ---------------------------------------------------------------------------
# 3) Perfil 'admin' (acesso total à própria empresa via tem_acesso_unidade) continua
#    vendo os operadores de TODAS as unidades - não é afetado pela restrição.
# ---------------------------------------------------------------------------
resp = c_admin.get(URL)
assert resp.status_code == 200
texto = resp.data.decode("utf-8")
assert "Fulano da Unidade A1" in texto and "Beltrano da Unidade A2" in texto
ok("Perfil 'admin' não é afetado pela restrição: vê operadores de todas as unidades da própria empresa")

print()
print(f"TODOS OS {len(passos)} TESTES DE ISOLAMENTO DE OPERADOR POR UNIDADE (RELATÓRIOS) PASSARAM")
