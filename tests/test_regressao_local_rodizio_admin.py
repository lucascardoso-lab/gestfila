"""
Teste de regressão: tela admin de configuração de rodízio por Local (adendo
10.8, Fase 10.8-B) - /admin/locais/<id>/rodizio.

Cobre: só filas da MESMA unidade do Local aparecem como opção (nem chega a
mostrar fila de outra unidade, diferente do padrão de filas_destino_permitidas);
tentativa de forçar fila de outra unidade via form manual é rejeitada mesmo assim
(defesa em profundidade); validação de chamadas_seguidas/ordem; caminho feliz
grava a lista com os valores corretos; remover todas as filas limpa a
configuração; isolamento multi-tenant (reaproveita autorizar_objeto, sem
checagem nova); link "Configurar rodízio" presente na listagem.

Execução: python -m tests.test_regressao_local_rodizio_admin (a partir da raiz
do projeto, com o virtualenv ativado).
"""
import os

os.environ.setdefault("SECRET_KEY", "test")
os.environ.setdefault("FLASK_ENV", "testing")

from app import create_app
from app.extensions import db
from app.models import Empresa, Fila, Local, LocalRodizioFila, Perfil, Unidade, Usuario

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
    unidade_a2 = Unidade(empresa_id=empresa_a.id, nome="Unidade A2")
    unidade_b1 = Unidade(empresa_id=empresa_b.id, nome="Unidade B1")
    db.session.add_all([unidade_a1, unidade_a2, unidade_b1])
    db.session.commit()

    fila_coleta = Fila(empresa_id=empresa_a.id, unidade_id=unidade_a1.id, nome="Coleta", sigla="CL")
    fila_pcd = Fila(empresa_id=empresa_a.id, unidade_id=unidade_a1.id, nome="PCD", sigla="PC")
    fila_a2 = Fila(empresa_id=empresa_a.id, unidade_id=unidade_a2.id, nome="Fila Unidade A2", sigla="A2")
    fila_b = Fila(empresa_id=empresa_b.id, unidade_id=unidade_b1.id, nome="Fila B", sigla="FB")
    db.session.add_all([fila_coleta, fila_pcd, fila_a2, fila_b])
    db.session.commit()

    local = Local(empresa_id=empresa_a.id, unidade_id=unidade_a1.id, nome="Guiche 01")
    db.session.add(local)
    db.session.commit()

    def novo_usuario(login, empresa_id, perfis):
        u = Usuario(empresa_id=empresa_id, nome=login, login=login)
        u.set_senha("senha123")
        u.perfis = perfis
        db.session.add(u)
        db.session.commit()
        return u.id

    id_admin_a = novo_usuario("admin_a_rodizio", empresa_a.id, [perfil_admin])
    id_admin_b = novo_usuario("admin_b_rodizio", empresa_b.id, [perfil_admin])

    ids = dict(
        empresa_a=empresa_a.id, empresa_b=empresa_b.id, unidade_a1=unidade_a1.id, unidade_a2=unidade_a2.id,
        fila_coleta=fila_coleta.id, fila_pcd=fila_pcd.id, fila_a2=fila_a2.id, fila_b=fila_b.id,
        local=local.id, admin_a=id_admin_a, admin_b=id_admin_b,
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


c_admin_a = login_como("admin_a_rodizio")
c_admin_b = login_como("admin_b_rodizio")

URL_RODIZIO = f"/admin/locais/{ids['local']}/rodizio"

# ---------------------------------------------------------------------------
# 1) GET: só as filas da MESMA unidade do Local aparecem - nem a de outra
#    unidade da mesma empresa, nem a de outra empresa.
# ---------------------------------------------------------------------------
resp = c_admin_a.get(URL_RODIZIO)
assert resp.status_code == 200
texto = resp.data.decode("utf-8")
assert "Coleta" in texto and "PCD" in texto
assert "Fila Unidade A2" not in texto
assert "Fila B" not in texto
ok("GET /rodizio: só mostra filas da mesma unidade do Local (Coleta/PCD), nunca de outra unidade/empresa")

# ---------------------------------------------------------------------------
# 2) Link 'Configurar rodízio' presente na listagem.
# ---------------------------------------------------------------------------
resp = c_admin_a.get(f"/admin/locais?empresa_id={ids['empresa_a']}")
texto = resp.data.decode("utf-8")
assert f'/admin/locais/{ids["local"]}/rodizio' in texto
assert "Configurar rodízio" in texto
ok("Listagem de Locais: link 'Configurar rodízio' presente")

# ---------------------------------------------------------------------------
# 3) Validação: forçar fila de outra unidade via form manual é rejeitado, mesmo
#    não sendo uma opção visível na tela (defesa em profundidade).
# ---------------------------------------------------------------------------
resp = c_admin_a.post(
    URL_RODIZIO,
    data={
        "rodizio_filas": [str(ids["fila_coleta"]), str(ids["fila_a2"])],
        f"ordem_{ids['fila_coleta']}": "0", f"chamadas_seguidas_{ids['fila_coleta']}": "1",
        f"ordem_{ids['fila_a2']}": "0", f"chamadas_seguidas_{ids['fila_a2']}": "1",
    },
)
assert "Selecione filas válidas".encode("utf-8") in resp.data
with app.app_context():
    assert db.session.get(Local, ids["local"]).rodizio_ordenado == []
ok("Forçar fila de outra unidade via form manual é rejeitado, nenhuma configuração é gravada")

# ---------------------------------------------------------------------------
# 4) Validação: chamadas_seguidas < 1 e ordem < 0 são rejeitados.
# ---------------------------------------------------------------------------
resp = c_admin_a.post(
    URL_RODIZIO,
    data={
        "rodizio_filas": [str(ids["fila_coleta"])],
        f"ordem_{ids['fila_coleta']}": "0", f"chamadas_seguidas_{ids['fila_coleta']}": "0",
    },
)
assert "chamadas seguidas".encode("utf-8") in resp.data.lower() or "Quantidade de chamadas seguidas".encode("utf-8") in resp.data
ok("chamadas_seguidas=0 é rejeitado (mínimo 1)")

resp = c_admin_a.post(
    URL_RODIZIO,
    data={
        "rodizio_filas": [str(ids["fila_coleta"])],
        f"ordem_{ids['fila_coleta']}": "-1", f"chamadas_seguidas_{ids['fila_coleta']}": "1",
    },
)
assert "Ordem deve ser".encode("utf-8") in resp.data
ok("ordem=-1 é rejeitado (mínimo 0)")

# ---------------------------------------------------------------------------
# 5) Caminho feliz: configura Coleta (ordem 0, 2 chamadas seguidas) e PCD
#    (ordem 1, 1 chamada seguida).
# ---------------------------------------------------------------------------
resp = c_admin_a.post(
    URL_RODIZIO,
    data={
        "rodizio_filas": [str(ids["fila_coleta"]), str(ids["fila_pcd"])],
        f"ordem_{ids['fila_coleta']}": "0", f"chamadas_seguidas_{ids['fila_coleta']}": "2",
        f"ordem_{ids['fila_pcd']}": "1", f"chamadas_seguidas_{ids['fila_pcd']}": "1",
    },
    follow_redirects=True,
)
assert resp.status_code == 200
assert "tela unificada de Atendimento".encode("utf-8") in resp.data
with app.app_context():
    local_db = db.session.get(Local, ids["local"])
    config = {item.fila_id: (item.ordem, item.chamadas_seguidas) for item in local_db.rodizio_ordenado}
    assert config == {ids["fila_coleta"]: (0, 2), ids["fila_pcd"]: (1, 1)}
ok("Caminho feliz: grava a lista com ordem/chamadas_seguidas corretos, avisa sobre a mudança de fluxo")

# ---------------------------------------------------------------------------
# 6) GET novamente: tela recarrega com os checkboxes/valores já preenchidos.
# ---------------------------------------------------------------------------
resp = c_admin_a.get(URL_RODIZIO)
texto = resp.data.decode("utf-8")
assert f'name="ordem_{ids["fila_coleta"]}" min="0" style="width:5rem;" value="0"' in texto
assert f'name="chamadas_seguidas_{ids["fila_coleta"]}" min="1" style="width:5rem;" value="2"' in texto
ok("GET /rodizio após salvar: valores atuais (ordem/chamadas_seguidas) pré-preenchidos corretamente")

# ---------------------------------------------------------------------------
# 7) Desmarcar todas as filas remove a configuração (Local volta ao fluxo atual).
# ---------------------------------------------------------------------------
resp = c_admin_a.post(URL_RODIZIO, data={}, follow_redirects=True)
assert resp.status_code == 200
assert "Rodízio removido".encode("utf-8") in resp.data
with app.app_context():
    assert db.session.get(Local, ids["local"]).rodizio_ordenado == []
ok("Enviar sem nenhuma fila marcada remove toda a configuração (Local volta ao fluxo por fila_id)")

# ---------------------------------------------------------------------------
# 8) Isolamento multi-tenant: admin de outra empresa recebe 404 (reaproveita
#    autorizar_objeto, nenhuma checagem nova).
# ---------------------------------------------------------------------------
assert c_admin_b.get(URL_RODIZIO).status_code == 404
ok("Isolamento: GET /rodizio de outra empresa recebe 404")

resp = c_admin_b.post(
    URL_RODIZIO,
    data={"rodizio_filas": [str(ids["fila_coleta"])], f"ordem_{ids['fila_coleta']}": "0",
          f"chamadas_seguidas_{ids['fila_coleta']}": "5"},
)
assert resp.status_code == 404
with app.app_context():
    assert db.session.get(Local, ids["local"]).rodizio_ordenado == []  # nada foi gravado
ok("Isolamento: POST /rodizio de outra empresa recebe 404, nada é gravado")

print()
print(f"TODOS OS {len(passos)} TESTES DA TELA ADMIN DE RODÍZIO (FASE 10.8-B) PASSARAM")
