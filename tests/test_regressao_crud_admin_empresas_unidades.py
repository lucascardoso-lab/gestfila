"""
Teste de regressão: CRUD administrativo "cru" de empresas/unidades/perfis
(app/admin/empresas.py, app/admin/unidades.py, app/admin/perfis.py).

Gap de cobertura identificado na auditoria de testes: esses três CRUDs nunca
tinham um teste HTTP dedicado, só apareciam indiretamente como fixtures de
setup de outros testes (criar Empresa/Unidade direto no banco, sem passar
pela rota). Este teste cobre:

1) Empresas: só super admin acessa (@super_admin_required); admin de empresa
   recebe 403. Criar/editar via POST funciona; CNPJ duplicado é bloqueado
   (_validar_empresa); exclusão é bloqueada se a empresa tiver qualquer
   unidade/usuário/fila cadastrada (mesmo padrão de exclusão segura de
   admin/filas.py), e só é permitida quando ela está vazia.

2) Unidades: escopo de tenant via autorizar_objeto() (app/admin/helpers.py) -
   um admin de uma empresa não consegue editar unidade de outra empresa
   (404, não 403, pra não revelar que o ID existe - mesmo padrão documentado
   no docstring de autorizar_objeto).

2.1) Bug real reportado em produção: campos opcionais nulos (Unidade.
   endereco/telefone, Empresa.cnpj) apareciam como a string literal "None"
   no formulário de edição - `{{ campo if objeto else '' }}` só tratava
   "objeto é None" (tela de criação), não "objeto existe mas o campo em si
   é None". Salvar sem tocar no campo persistia "None" como valor de
   verdade no banco (sobrevive ao `.strip() or None` do backend por ser
   string não-vazia) - o cupom impresso do totem passava a mostrar "None"
   como endereço/telefone real. Corrigido nos templates (`(campo if objeto
   else '') or ''`); mesmo padrão de bug corrigido em admin/totens/form.html
   (nome_impressora_windows) e admin/filas.py/admin/tipos_senha.py (campos
   numéricos, só cosmético - o backend usa type=int e descarta "None" como
   texto inválido), fora do escopo deste arquivo.

3) Perfis: tela somente-leitura (não existe CRUD de perfil pela web, ver
   README) - só confere que lista os perfis do seed e que é restrita a
   super admin.

Execução: python -m tests.test_regressao_crud_admin_empresas_unidades (a
partir da raiz do projeto, com o virtualenv ativado).
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

    perfil_admin = Perfil(nome="admin")
    perfil_gerencial = Perfil(nome="gerencial")
    db.session.add_all([perfil_admin, perfil_gerencial])
    db.session.commit()

    empresa_a = Empresa(nome="Clinica A", cnpj="11.111.111/0001-11")
    empresa_b = Empresa(nome="Clinica B", cnpj="22.222.222/0001-22")
    db.session.add_all([empresa_a, empresa_b])
    db.session.commit()

    unidade_a1 = Unidade(empresa_id=empresa_a.id, nome="Unidade A1")
    unidade_b1 = Unidade(empresa_id=empresa_b.id, nome="Unidade B1")
    db.session.add_all([unidade_a1, unidade_b1])
    db.session.commit()

    def novo_usuario(login, empresa_id, perfis):
        u = Usuario(empresa_id=empresa_id, nome=login, login=login)
        u.set_senha("senha123")
        u.perfis = perfis
        db.session.add(u)
        db.session.commit()
        return u.id

    id_super = novo_usuario("super_crud_admin", None, [])
    id_admin_a = novo_usuario("admin_a_crud", empresa_a.id, [perfil_admin])
    id_admin_b = novo_usuario("admin_b_crud", empresa_b.id, [perfil_admin])

    ids = dict(
        empresa_a=empresa_a.id, empresa_b=empresa_b.id,
        unidade_a1=unidade_a1.id, unidade_b1=unidade_b1.id,
        super=id_super, admin_a=id_admin_a, admin_b=id_admin_b,
    )


def login_como(login):
    c = app.test_client()
    resp = c.post("/login", data={"login": login, "senha": "senha123"}, follow_redirects=True)
    assert resp.status_code == 200, (login, resp.status_code)
    return c


c_super = login_como("super_crud_admin")
c_admin_a = login_como("admin_a_crud")
c_admin_b = login_como("admin_b_crud")

# ===========================================================================
# A) Empresas: acesso restrito a super admin.
# ===========================================================================
resp = c_admin_a.get("/admin/empresas")
assert resp.status_code == 403
ok("Admin de empresa recebe 403 ao tentar listar /admin/empresas (rota exclusiva de super admin)")

resp = c_super.get("/admin/empresas")
assert resp.status_code == 200
assert "Clinica A" in resp.data.decode("utf-8")
ok("Super admin lista /admin/empresas normalmente")

# ===========================================================================
# B) Empresas: criação via POST, com validação de CNPJ duplicado.
# ===========================================================================
resp = c_super.post(
    "/admin/empresas/nova",
    data={"nome": "Clinica Nova", "cnpj": "33.333.333/0001-33"},
    follow_redirects=True,
)
assert resp.status_code == 200
assert "Clinica Nova" in resp.data.decode("utf-8")
ok("Super admin cria uma nova empresa via POST /admin/empresas/nova")

resp = c_super.post(
    "/admin/empresas/nova",
    data={"nome": "Clinica Duplicada", "cnpj": "11.111.111/0001-11"},  # mesmo CNPJ da empresa A
    follow_redirects=True,
)
assert resp.status_code == 200
texto = resp.data.decode("utf-8")
assert "Já existe uma empresa cadastrada com esse CNPJ" in texto
with app.app_context():
    assert Empresa.query.filter_by(nome="Clinica Duplicada").first() is None
ok("Criar empresa com CNPJ já usado por outra é bloqueado, nada é persistido")

# ===========================================================================
# C) Empresas: exclusão segura - bloqueada se houver unidade/usuário/fila.
# ===========================================================================
resp = c_super.post(f"/admin/empresas/{ids['empresa_a']}/excluir", follow_redirects=True)
assert resp.status_code == 200
texto = resp.data.decode("utf-8")
assert "Não é possível excluir" in texto
with app.app_context():
    assert db.session.get(Empresa, ids["empresa_a"]) is not None
ok("Exclusão de empresa com unidades/usuários cadastrados é bloqueada (empresa continua existindo)")

with app.app_context():
    empresa_vazia = Empresa(nome="Clinica Vazia")
    db.session.add(empresa_vazia)
    db.session.commit()
    id_empresa_vazia = empresa_vazia.id

resp = c_super.post(f"/admin/empresas/{id_empresa_vazia}/excluir", follow_redirects=True)
assert resp.status_code == 200
with app.app_context():
    assert db.session.get(Empresa, id_empresa_vazia) is None
ok("Empresa sem nenhum vínculo é excluída de verdade")

# ===========================================================================
# D) Unidades: isolamento de tenant via autorizar_objeto() - 404, não 403.
# ===========================================================================
resp = c_admin_a.get(f"/admin/unidades/{ids['unidade_b1']}/editar")
assert resp.status_code == 404
ok("Admin da empresa A recebe 404 (não 403) ao tentar editar unidade da empresa B (não revela existência)")

resp = c_admin_b.get(f"/admin/unidades/{ids['unidade_b1']}/editar")
assert resp.status_code == 200
ok("Admin da empresa B edita normalmente sua própria unidade")

resp = c_admin_a.post(
    f"/admin/unidades/{ids['unidade_a1']}/editar",
    data={"nome": "Unidade A1 Renomeada", "endereco": "", "telefone": ""},
    follow_redirects=True,
)
assert resp.status_code == 200
with app.app_context():
    assert db.session.get(Unidade, ids["unidade_a1"]).nome == "Unidade A1 Renomeada"
ok("Admin da empresa A renomeia sua própria unidade via POST")

resp = c_admin_a.post(f"/admin/unidades/{ids['unidade_b1']}/toggle-ativo", follow_redirects=True)
assert resp.status_code == 404
with app.app_context():
    assert db.session.get(Unidade, ids["unidade_b1"]).ativo is True
ok("Admin da empresa A não consegue alternar ativo/inativo de unidade da empresa B (404, nada alterado)")

# ===========================================================================
# D.1) Bug real reportado em produção: campos opcionais nulos (endereco/
#      telefone de Unidade, cnpj de Empresa) apareciam como a STRING literal
#      "None" no formulário de edição - `{{ campo if objeto else '' }}` só
#      tratava "objeto é None" (tela de criação), não "objeto existe mas o
#      campo em si é None". Se o admin salvasse sem tocar nesse campo, o
#      texto "None" (não-vazio) sobrevivia ao `.strip() or None` do backend e
#      virava o valor de VERDADE no banco - o cupom impresso do totem passava
#      a mostrar "None" como se fosse o endereço/telefone real. unidade_a1
#      already tem endereco/telefone=None (setados assim no bloco D acima).
# ===========================================================================
resp = c_admin_a.get(f"/admin/unidades/{ids['unidade_a1']}/editar")
assert resp.status_code == 200
texto_form_unidade = resp.data.decode("utf-8")
# extrai o conteúdo real do <textarea id="endereco"> e do value="" do telefone
trecho_endereco = texto_form_unidade[texto_form_unidade.index('id="endereco"'):texto_form_unidade.index("</textarea>", texto_form_unidade.index('id="endereco"'))]
assert ">None<" not in trecho_endereco and trecho_endereco.rstrip().endswith(">")  # textarea vazia, sem "None" antes do fechamento
trecho_telefone = texto_form_unidade[texto_form_unidade.index('id="telefone"'):texto_form_unidade.index('id="telefone"') + 200]
assert 'value="None"' not in trecho_telefone
ok("Formulário de edição de Unidade com endereco/telefone=None NÃO renderiza a string 'None' nos campos")

# round-trip completo: "admin abre o formulário e salva sem tocar em nada" -
# confirma que o banco continua None de verdade, não a string "None".
resp = c_admin_a.post(
    f"/admin/unidades/{ids['unidade_a1']}/editar",
    data={"nome": "Unidade A1 Renomeada", "endereco": "", "telefone": ""},
    follow_redirects=True,
)
assert resp.status_code == 200
with app.app_context():
    unidade_pos_save = db.session.get(Unidade, ids["unidade_a1"])
    assert unidade_pos_save.endereco is None
    assert unidade_pos_save.telefone is None
ok("Salvar o formulário sem alterar endereco/telefone mantém None de verdade no banco (não a string 'None')")

# mesmo bug, mesma correção: Empresa.cnpj (nullable) - empresa nova sem CNPJ.
resp = c_super.post(
    "/admin/empresas/nova",
    data={"nome": "Clinica Sem CNPJ", "cnpj": ""},
    follow_redirects=True,
)
assert resp.status_code == 200
with app.app_context():
    empresa_sem_cnpj = Empresa.query.filter_by(nome="Clinica Sem CNPJ").first()
    assert empresa_sem_cnpj is not None and empresa_sem_cnpj.cnpj is None
    id_empresa_sem_cnpj = empresa_sem_cnpj.id

resp = c_super.get(f"/admin/empresas/{id_empresa_sem_cnpj}/editar")
assert resp.status_code == 200
texto_form_empresa = resp.data.decode("utf-8")
assert 'name="cnpj" value="None"' not in texto_form_empresa
ok("Formulário de edição de Empresa com cnpj=None NÃO renderiza a string 'None' no campo")

# ===========================================================================
# E) Perfis: tela somente-leitura, restrita a super admin.
# ===========================================================================
resp = c_admin_a.get("/admin/perfis")
assert resp.status_code == 403
ok("Admin de empresa recebe 403 ao tentar acessar /admin/perfis")

resp = c_super.get("/admin/perfis")
assert resp.status_code == 200
texto = resp.data.decode("utf-8")
assert "admin" in texto and "gerencial" in texto
ok("Super admin lista os perfis cadastrados em /admin/perfis")

print()
print(f"TODOS OS {len(passos)} TESTES DE CRUD ADMIN (EMPRESAS/UNIDADES/PERFIS) PASSARAM")
