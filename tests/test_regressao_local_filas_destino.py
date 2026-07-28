"""
Teste de regressão: restrição opcional de redirecionamento por Local (diagnóstico
item 2) - Local.filas_destino_permitidas (N:N via local_filas_destino).

Cobre: Local sem configuração preserva o comportamento atual (todas as filas ativas
da unidade); Local configurado só mostra/aceita as filas permitidas (na tela E no
POST de Realocar, não só na renderização); tentativa de configurar fila de outra
unidade é rejeitada; isolamento multi-tenant na tela de configuração (que reaproveita
autorizar_objeto/resolver_empresa_contexto de admin/locais.py, sem checagem nova).

Execução: python -m tests.test_regressao_local_filas_destino (a partir da raiz do
projeto, com o virtualenv ativado).
"""
import os

os.environ.setdefault("SECRET_KEY", "test")
os.environ.setdefault("FLASK_ENV", "testing")

from app import create_app
from app.extensions import db
from app.models import Empresa, Fila, Local, Perfil, Senha, Unidade, Usuario
from app.atendimento.helpers import filas_destino

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

    empresa_a = Empresa(nome="Clinica A")
    empresa_b = Empresa(nome="Clinica B")
    db.session.add_all([empresa_a, empresa_b])
    db.session.commit()

    unidade_a1 = Unidade(empresa_id=empresa_a.id, nome="Unidade A1")
    unidade_a2 = Unidade(empresa_id=empresa_a.id, nome="Unidade A2")
    unidade_b1 = Unidade(empresa_id=empresa_b.id, nome="Unidade B1")
    db.session.add_all([unidade_a1, unidade_a2, unidade_b1])
    db.session.commit()

    fila_coleta = Fila(empresa_id=empresa_a.id, unidade_id=unidade_a1.id, nome="Coleta", sigla="CL")
    fila_pcd = Fila(empresa_id=empresa_a.id, unidade_id=unidade_a1.id, nome="PCD", sigla="PC")
    fila_entrega = Fila(empresa_id=empresa_a.id, unidade_id=unidade_a1.id, nome="Entrega de Amostra", sigla="EA")
    fila_consultorio = Fila(empresa_id=empresa_a.id, unidade_id=unidade_a1.id, nome="Consultorio", sigla="CO")
    fila_a2 = Fila(empresa_id=empresa_a.id, unidade_id=unidade_a2.id, nome="Fila Unidade A2", sigla="A2")
    fila_b = Fila(empresa_id=empresa_b.id, unidade_id=unidade_b1.id, nome="Fila B", sigla="FB")
    db.session.add_all([fila_coleta, fila_pcd, fila_entrega, fila_consultorio, fila_a2, fila_b])
    db.session.commit()

    local_aberto = Local(empresa_id=empresa_a.id, unidade_id=unidade_a1.id, nome="Guiche Aberto")
    local_restrito = Local(empresa_id=empresa_a.id, unidade_id=unidade_a1.id, nome="Guiche Restrito")
    db.session.add_all([local_aberto, local_restrito])
    db.session.commit()

    local_restrito.filas_destino_permitidas = [fila_pcd, fila_entrega]
    db.session.commit()

    def novo_usuario(login, empresa_id, perfis, unidades):
        u = Usuario(empresa_id=empresa_id, nome=login, login=login)
        u.set_senha("senha123")
        u.perfis = perfis
        u.unidades = unidades
        db.session.add(u)
        db.session.commit()
        return u.id

    id_admin_a = novo_usuario("admin_a_destino", empresa_a.id, [perfil_admin], [])
    id_admin_b = novo_usuario("admin_b_destino", empresa_b.id, [perfil_admin], [])
    id_recepcao_a1 = novo_usuario("recepcao_a1_destino", empresa_a.id, [perfil_recepcao], [unidade_a1])

    senha_coleta = Senha(
        empresa_id=empresa_a.id, unidade_id=unidade_a1.id, fila_id=fila_coleta.id,
        codigo="CL001", sequencial=1, prioridade=False, status="aguardando",
    )
    db.session.add(senha_coleta)
    db.session.commit()

    ids = dict(
        empresa_a=empresa_a.id, empresa_b=empresa_b.id, unidade_a1=unidade_a1.id, unidade_a2=unidade_a2.id,
        fila_coleta=fila_coleta.id, fila_pcd=fila_pcd.id, fila_entrega=fila_entrega.id,
        fila_consultorio=fila_consultorio.id, fila_a2=fila_a2.id, fila_b=fila_b.id,
        local_aberto=local_aberto.id, local_restrito=local_restrito.id,
        admin_a=id_admin_a, admin_b=id_admin_b, recepcao_a1=id_recepcao_a1,
        senha_coleta=senha_coleta.id,
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


c_admin_a = login_como("admin_a_destino")
c_admin_b = login_como("admin_b_destino")

# ---------------------------------------------------------------------------
# 1) filas_destino() direto: Local SEM configuração preserva o comportamento atual
#    (todas as filas ativas da unidade, exceto a própria fila de origem).
# ---------------------------------------------------------------------------
with app.app_context():
    fila_coleta_db = db.session.get(Fila, ids["fila_coleta"])
    local_aberto_db = db.session.get(Local, ids["local_aberto"])
    destinos = filas_destino(fila_coleta_db, local_aberto_db)
    nomes = {f.nome for f in destinos}
    assert nomes == {"PCD", "Entrega de Amostra", "Consultorio"}
ok("Local sem configuração: filas_destino() retorna todas as filas ativas da unidade (comportamento preservado)")

# ---------------------------------------------------------------------------
# 2) filas_destino() direto: Local COM configuração só mostra as filas permitidas.
# ---------------------------------------------------------------------------
with app.app_context():
    fila_coleta_db = db.session.get(Fila, ids["fila_coleta"])
    local_restrito_db = db.session.get(Local, ids["local_restrito"])
    destinos = filas_destino(fila_coleta_db, local_restrito_db)
    nomes = {f.nome for f in destinos}
    assert nomes == {"PCD", "Entrega de Amostra"}
    assert "Consultorio" not in nomes
ok("Local com configuração: filas_destino() retorna só PCD/Entrega de Amostra, não Consultorio")

# ---------------------------------------------------------------------------
# 3) Local sem Local nenhum (None) - mesmo comportamento de 'sem configuração'.
# ---------------------------------------------------------------------------
with app.app_context():
    fila_coleta_db = db.session.get(Fila, ids["fila_coleta"])
    destinos = filas_destino(fila_coleta_db, None)
    assert {f.nome for f in destinos} == {"PCD", "Entrega de Amostra", "Consultorio"}
ok("local=None: mesmo comportamento de 'sem configuração' (todas as filas ativas da unidade)")

# ---------------------------------------------------------------------------
# 4) Admin: configurar Guiche Restrito com uma fila de OUTRA unidade é rejeitado.
# ---------------------------------------------------------------------------
resp = c_admin_a.post(
    f"/admin/locais/{ids['local_restrito']}/editar",
    data={
        "nome": "Guiche Restrito", "unidade_id": ids["unidade_a1"], "tipo": "",
        "filas_destino": [str(ids["fila_pcd"]), str(ids["fila_a2"])],
    },
)
assert "mesma unidade do local".encode("utf-8") in resp.data
with app.app_context():
    local_restrito_db = db.session.get(Local, ids["local_restrito"])
    nomes_atuais = {f.nome for f in local_restrito_db.filas_destino_permitidas}
    assert nomes_atuais == {"PCD", "Entrega de Amostra"}  # config original intacta
ok("Admin: configurar fila de outra unidade é rejeitado, configuração original permanece intacta")

# ---------------------------------------------------------------------------
# 5) Admin: caminho feliz - reconfigurar Guiche Aberto para restringir a só Coleta.
# ---------------------------------------------------------------------------
resp = c_admin_a.post(
    f"/admin/locais/{ids['local_aberto']}/editar",
    data={
        "nome": "Guiche Aberto", "unidade_id": ids["unidade_a1"], "tipo": "",
        "filas_destino": [str(ids["fila_pcd"])],
    },
    follow_redirects=True,
)
assert resp.status_code == 200
with app.app_context():
    local_aberto_db = db.session.get(Local, ids["local_aberto"])
    assert {f.nome for f in local_aberto_db.filas_destino_permitidas} == {"PCD"}
ok("Admin: caminho feliz grava a configuração (Guiche Aberto agora só permite PCD)")

# desfaz para não afetar os testes HTTP seguintes, que esperam Guiche Aberto sem restrição
with app.app_context():
    local_aberto_db = db.session.get(Local, ids["local_aberto"])
    local_aberto_db.filas_destino_permitidas = []
    db.session.commit()

# ---------------------------------------------------------------------------
# 6) Isolamento multi-tenant: admin de outra empresa recebe 404 (reaproveita
#    autorizar_objeto já existente, nenhuma checagem nova) e não vê as filas da
#    empresa A no multi-select.
# ---------------------------------------------------------------------------
resp = c_admin_b.get(f"/admin/locais/{ids['local_restrito']}/editar")
assert resp.status_code == 404
ok("Isolamento: admin de outra empresa recebe 404 ao tentar configurar Local da empresa A")

resp = c_admin_b.post(
    f"/admin/locais/{ids['local_restrito']}/editar",
    data={"nome": "hack", "unidade_id": ids["unidade_a1"], "filas_destino": [str(ids["fila_pcd"])]},
)
assert resp.status_code == 404
with app.app_context():
    assert db.session.get(Local, ids["local_restrito"]).nome == "Guiche Restrito"  # nada mudou
ok("Isolamento: POST de outra empresa também recebe 404, configuração não é alterada")

resp = c_admin_a.get(f"/admin/locais/{ids['local_restrito']}/editar")
assert resp.status_code == 200
texto = resp.data.decode("utf-8")
assert "Fila B" not in texto  # fila da empresa B nunca aparece no multi-select
ok("Multi-select de filas de destino não vaza filas de outra empresa")

# ---------------------------------------------------------------------------
# 7) Ponta a ponta HTTP: tela de Atendimento respeita a restrição do Local escolhido,
#    tanto na renderização do radio button quanto na validação do POST Realocar.
# ---------------------------------------------------------------------------
c_recepcao = login_como("recepcao_a1_destino")

# escolhe o local via a rota real (não manipula a sessão direto) - mais fiel ao
# fluxo real do operador; filas_destino() usa local_atual() (sessão), gravada aqui.
resp = c_recepcao.post("/atendimento/local", data={"local_id": ids["local_restrito"]}, follow_redirects=True)
assert resp.status_code == 200

resp = c_recepcao.get(f"/atendimento/recepcao/filas/{ids['fila_coleta']}")
assert resp.status_code == 200
texto = resp.data.decode("utf-8")
assert "PCD" in texto and "Entrega de Amostra" in texto
assert "Consultorio" not in texto
ok("Tela de Atendimento (Guiche Restrito): radio 'Redirecionar' só mostra PCD/Entrega de Amostra")

# chama a senha no Guiche Restrito, pra poder testar o Realocar
resp = c_recepcao.post(
    f"/atendimento/recepcao/filas/{ids['fila_coleta']}/chamar",
    data={"codigo": "CL001"}, follow_redirects=True,
)
assert resp.status_code == 200

# tentativa de realocar para uma fila válida da unidade, mas NÃO permitida por este Local
resp = c_recepcao.post(
    f"/atendimento/recepcao/filas/{ids['fila_coleta']}/realocar",
    data={"fila_destino_id": ids["fila_consultorio"]},
    follow_redirects=True,
)
assert "Selecione uma fila de destino válida".encode("utf-8") in resp.data
with app.app_context():
    assert db.session.get(Senha, ids["senha_coleta"]).fila_id == ids["fila_coleta"]  # não moveu
ok("POST Realocar: fila válida da unidade mas fora da lista permitida é rejeitada (não só a UI, o servidor também valida)")

# realocar para uma fila permitida - deve funcionar
resp = c_recepcao.post(
    f"/atendimento/recepcao/filas/{ids['fila_coleta']}/realocar",
    data={"fila_destino_id": ids["fila_pcd"]},
    follow_redirects=True,
)
assert resp.status_code == 200
with app.app_context():
    assert db.session.get(Senha, ids["senha_coleta"]).fila_id == ids["fila_pcd"]
ok("POST Realocar: fila permitida pela configuração do Local funciona normalmente")

print()
print(f"TODOS OS {len(passos)} TESTES DE RESTRIÇÃO DE REDIRECIONAMENTO POR LOCAL PASSARAM")
