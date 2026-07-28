"""
Teste de regressão: campo "Nome" não deve exibir a string literal "None"
quando a senha chamada existe mas não tem nome_paciente preenchido (bug
encontrado na tela de Atendimento - painel_fila.html e painel_local.html).

Antes da correção, `value="{{ senha_atual.nome_paciente if senha_atual else '' }}"`
renderizava `None.__str__()` sempre que senha_atual existia e nome_paciente era
nulo, mostrando "None" no input de texto livre. O mesmo padrão foi verificado
(e já estava correto) na lista de "Senhas em espera" e no JS de atualização via
WebSocket - este teste cobre as duas telas (por-fila e por-Local) e os dois
lugares (input "Nome" e tabela de espera).

Execução: python -m tests.test_regressao_nome_paciente_none (a partir da raiz do
projeto, com o virtualenv ativado).
"""
import os
from datetime import datetime

os.environ.setdefault("SECRET_KEY", "test")
os.environ.setdefault("FLASK_ENV", "testing")

from app import create_app
from app.extensions import db
from app.models import Empresa, Fila, Local, Perfil, Senha, Unidade, Usuario

app = create_app("testing")

passos = []


def ok(msg):
    passos.append(msg)
    print("OK:", msg)


with app.app_context():
    db.create_all()

    perfil_recepcao = Perfil(nome="atendente")
    db.session.add(perfil_recepcao)
    db.session.commit()

    empresa_a = Empresa(nome="Clinica A")
    db.session.add(empresa_a)
    db.session.commit()

    unidade_a1 = Unidade(empresa_id=empresa_a.id, nome="Unidade A1")
    db.session.add(unidade_a1)
    db.session.commit()

    fila_a = Fila(empresa_id=empresa_a.id, unidade_id=unidade_a1.id, nome="Fila A", sigla="FA")
    db.session.add(fila_a)
    db.session.commit()

    local_guiche01 = Local(empresa_id=empresa_a.id, unidade_id=unidade_a1.id, nome="Guiche 01")
    db.session.add(local_guiche01)
    db.session.commit()

    def novo_usuario(login, empresa_id, perfis, unidades):
        u = Usuario(empresa_id=empresa_id, nome=login, login=login)
        u.set_senha("senha123")
        u.perfis = perfis
        u.unidades = unidades
        db.session.add(u)
        db.session.commit()
        return u.id

    id_recepcao_a = novo_usuario("recepcao_a_nome_none", empresa_a.id, [perfil_recepcao], [unidade_a1])

    # senha chamada (é a "senha_atual" do posto), sem nome_paciente preenchido -
    # operador_id precisa bater com quem vai consultar (controle de atendimento,
    # senha_atual_no_local agora é escopada por operador, não só por local).
    senha_chamada = Senha(
        empresa_id=empresa_a.id, unidade_id=unidade_a1.id, fila_id=fila_a.id,
        codigo="FA001", sequencial=1, prioridade=False, status="chamada",
        nome_paciente=None, chamada_em=datetime.utcnow(), local_id=local_guiche01.id,
        operador_id=id_recepcao_a,
    )
    # senha em espera, também sem nome_paciente preenchido
    senha_espera = Senha(
        empresa_id=empresa_a.id, unidade_id=unidade_a1.id, fila_id=fila_a.id,
        codigo="FA002", sequencial=2, prioridade=False, status="aguardando",
        nome_paciente=None,
    )
    db.session.add_all([senha_chamada, senha_espera])
    db.session.commit()

    ids = dict(
        empresa_a=empresa_a.id, unidade_a1=unidade_a1.id, fila_a=fila_a.id,
        local_guiche01=local_guiche01.id, recepcao_a=id_recepcao_a,
    )

print("IDS", ids)


def login_como(login):
    c = app.test_client()
    resp = c.post("/login", data={"login": login, "senha": "senha123"}, follow_redirects=True)
    assert resp.status_code == 200, (login, resp.status_code)
    return c


c_recepcao = login_como("recepcao_a_nome_none")
with c_recepcao.session_transaction() as sess:
    sess["atendimento_local_id"] = ids["local_guiche01"]

# ---------------------------------------------------------------------------
# 1) Tela por-fila (painel_fila.html): input "Nome" não deve conter "None".
# ---------------------------------------------------------------------------
resp = c_recepcao.get(f"/atendimento/recepcao/filas/{ids['fila_a']}")
assert resp.status_code == 200
texto = resp.data.decode("utf-8")
html_sem_script = texto.split("<script")[0]

assert 'id="nome" name="nome" value="None"' not in texto
assert "FA001" in html_sem_script  # confirma que a senha_atual foi de fato renderizada
ok("painel_fila: input 'Nome' não exibe 'None' quando senha_atual.nome_paciente é nulo")

assert ">None<" not in html_sem_script
ok("painel_fila: tabela 'Senhas em espera' não exibe 'None' na coluna Nome")

# ---------------------------------------------------------------------------
# 2) Endpoint JSON /dados: nome_paciente nulo deve vir como null, não "None".
# ---------------------------------------------------------------------------
resp = c_recepcao.get(f"/atendimento/recepcao/filas/{ids['fila_a']}/dados")
assert resp.status_code == 200
dados = resp.get_json()
assert dados["senha_atual"]["nome_paciente"] is None
assert dados["espera"][0]["nome_paciente"] is None
ok("GET /filas/<id>/dados: nome_paciente nulo serializado como null (JS trata com '|| \"\"')")

# ---------------------------------------------------------------------------
# 3) Tela por-Local (painel_local.html): mesmo bug, mesma correção.
# ---------------------------------------------------------------------------
resp = c_recepcao.get(f"/atendimento/recepcao/local/{ids['local_guiche01']}")
assert resp.status_code == 200
texto = resp.data.decode("utf-8")
html_sem_script = texto.split("<script")[0]

assert 'id="nome" name="nome" value="None"' not in texto
assert "FA001" in html_sem_script
ok("painel_local: input 'Nome' não exibe 'None' quando senha_atual.nome_paciente é nulo")

print()
print(f"TODOS OS {len(passos)} TESTES DO BUG 'None' NO CAMPO NOME PASSARAM")
