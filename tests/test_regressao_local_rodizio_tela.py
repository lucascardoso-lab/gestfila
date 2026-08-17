"""
Teste de regressão: tela por-Local, visualização (adendo 10.8) -
/atendimento/<setor>/local/<local_id> (+ /dados).

Cobre: RBAC (setor errado -> 403, sem login -> 401), isolamento multi-tenant/
unidade (reaproveita o mesmo padrão de fila_acessivel_ou_404/senha_acessivel_ou_404
via novo local_acessivel_ou_404), Local com rodízio mostra 'Senhas em espera'
agregadas de várias filas (com coluna Fila), Local sem rodízio configurado renderiza
vazio sem erro, 'Senha chamada' mostra a fila correta, e o endpoint JSON /dados
inclui o nome da fila por item (diferente do endpoint por-fila, que não precisa
disso). Botões de ação e WebSocket são cobertos em
test_regressao_local_rodizio_acoes.py (Fase 10.8-E).

Execução: python -m tests.test_regressao_local_rodizio_tela (a partir da raiz do
projeto, com o virtualenv ativado).
"""
import os
import re
from datetime import datetime

os.environ.setdefault("SECRET_KEY", "test")
os.environ.setdefault("FLASK_ENV", "testing")

from app import create_app
from app.extensions import db
from app.models import Empresa, Fila, Local, LocalRodizioFila, Perfil, Senha, Unidade, Usuario

app = create_app("testing")

passos = []


def ok(msg):
    passos.append(msg)
    print("OK:", msg)


with app.app_context():
    db.create_all()

    perfil_recepcao = Perfil(nome="atendente")
    perfil_admin = Perfil(nome="admin")
    db.session.add_all([perfil_recepcao, perfil_admin])
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

    fila_x = Fila(empresa_id=empresa_a.id, unidade_id=unidade_a1.id, nome="Fila X", sigla="FX")
    fila_y = Fila(empresa_id=empresa_a.id, unidade_id=unidade_a1.id, nome="Fila Y", sigla="FY")
    db.session.add_all([fila_x, fila_y])
    db.session.commit()

    local_com_rodizio = Local(
        empresa_id=empresa_a.id, unidade_id=unidade_a1.id, nome="Local Com Rodizio",
        nome_exibicao="Local Com Rodizio", identificador_admin="[UNI] Local Com Rodizio",
    )
    local_sem_rodizio = Local(
        empresa_id=empresa_a.id, unidade_id=unidade_a1.id, nome="Local Sem Rodizio",
        nome_exibicao="Local Sem Rodizio", identificador_admin="[UNI] Local Sem Rodizio",
    )
    db.session.add_all([local_com_rodizio, local_sem_rodizio])
    db.session.commit()

    db.session.add_all([
        LocalRodizioFila(local_id=local_com_rodizio.id, fila_id=fila_x.id, ordem=0, chamadas_seguidas=1),
        LocalRodizioFila(local_id=local_com_rodizio.id, fila_id=fila_y.id, ordem=1, chamadas_seguidas=1),
    ])
    db.session.commit()

    senha_x1 = Senha(
        empresa_id=empresa_a.id, unidade_id=unidade_a1.id, fila_id=fila_x.id,
        codigo="FX001", sequencial=1, prioridade=False, status="aguardando",
    )
    senha_y1 = Senha(
        empresa_id=empresa_a.id, unidade_id=unidade_a1.id, fila_id=fila_y.id,
        codigo="FY001", sequencial=1, prioridade=True, status="aguardando",
    )
    db.session.add_all([senha_x1, senha_y1])
    db.session.commit()

    def novo_usuario(login, empresa_id, perfis, unidades):
        u = Usuario(empresa_id=empresa_id, nome=login, login=login)
        u.set_senha("senha123")
        u.perfis = perfis
        u.unidades = unidades
        db.session.add(u)
        db.session.commit()
        return u.id

    id_recepcao_a1 = novo_usuario("recepcao_a1_tela", empresa_a.id, [perfil_recepcao], [unidade_a1])
    id_recepcao_a2 = novo_usuario("recepcao_a2_tela", empresa_a.id, [perfil_recepcao], [unidade_a2])
    id_admin_b = novo_usuario("admin_b_tela", empresa_b.id, [perfil_admin], [])

    ids = dict(
        empresa_a=empresa_a.id, unidade_a1=unidade_a1.id, fila_x=fila_x.id, fila_y=fila_y.id,
        local_com_rodizio=local_com_rodizio.id, local_sem_rodizio=local_sem_rodizio.id,
        senha_x1=senha_x1.id, senha_y1=senha_y1.id,
        recepcao_a1=id_recepcao_a1, recepcao_a2=id_recepcao_a2, admin_b=id_admin_b,
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
c_recepcao_a1 = login_como("recepcao_a1_tela")
c_recepcao_a2 = login_como("recepcao_a2_tela")
c_admin_b = login_como("admin_b_tela")

URL_PAINEL = f"/atendimento/recepcao/local/{ids['local_com_rodizio']}"
URL_DADOS = f"/atendimento/recepcao/local/{ids['local_com_rodizio']}/dados"

# ---------------------------------------------------------------------------
# 1) RBAC: sem login -> 401; setor incompatível com o perfil -> 403.
# ---------------------------------------------------------------------------
assert anon.get(URL_PAINEL).status_code == 401
ok("RBAC: sem login recebe 401 em /atendimento/<setor>/local/<id>")

resp = c_recepcao_a1.get(f"/atendimento/coleta/local/{ids['local_com_rodizio']}")
assert resp.status_code == 403
ok("RBAC: perfil só 'atendente' recebe 403 na rota de setor 'coleta' (mesma checagem de exigir_acesso_setor)")

# ---------------------------------------------------------------------------
# 2) Isolamento por unidade: recepcao_a2 (restrito à unidade A2) recebe 404 ao
#    tentar acessar Local da unidade A1 (mesma empresa).
# ---------------------------------------------------------------------------
resp = c_recepcao_a2.get(URL_PAINEL)
assert resp.status_code == 404
ok("Isolamento por unidade: recepcao_a2 (unidade A2) recebe 404 ao acessar Local da unidade A1")

# ---------------------------------------------------------------------------
# 3) Isolamento multi-tenant: admin de outra empresa recebe 404.
# ---------------------------------------------------------------------------
resp = c_admin_b.get(URL_PAINEL)
assert resp.status_code == 404
ok("Isolamento multi-tenant: admin de outra empresa recebe 404 ao acessar Local da empresa A")

# ---------------------------------------------------------------------------
# 4) GET painel_local: mostra 'Senhas em espera' agregadas das DUAS filas do
#    rodízio (Fila X e Fila Y), com a coluna Fila identificando cada uma.
# ---------------------------------------------------------------------------
resp = c_recepcao_a1.get(URL_PAINEL)
assert resp.status_code == 200
texto = resp.data.decode("utf-8")
# Fora de QUALQUER <script> - lá dentro, o JS carrega o mesmo texto da
# mensagem de estado vazio como string literal (fallback do render
# client-side), o que faria um "not in texto" simples dar falso positivo
# mesmo com a tabela cheia de dados. Remove TODOS os blocos <script>...
# </script> (não só corta a partir do primeiro) - dark mode (etapa 2,
# BLUEPRINT.md) passou a injetar um <script> de tema no <head>, antes de
# qualquer conteúdo real da página; cortar só a partir do primeiro
# <script> (como antes) removeria o corpo inteiro da página por engano.
html_sem_script = re.sub(r"<script[^>]*>.*?</script>", "", texto, flags=re.DOTALL)
assert "FX001" in html_sem_script and "Fila X" in html_sem_script
assert "FY001" in html_sem_script and "Fila Y" in html_sem_script
assert "Nenhuma senha aguardando neste Local." not in html_sem_script
ok("GET /local/<id>: 'Senhas em espera' agrega candidatas de Fila X e Fila Y, com coluna Fila")

# ---------------------------------------------------------------------------
# 5) 'Senha chamada' mostra a fila correta quando há uma senha em atendimento.
# ---------------------------------------------------------------------------
with app.app_context():
    senha_y1_db = db.session.get(Senha, ids["senha_y1"])
    senha_y1_db.status = "chamada"
    senha_y1_db.chamada_em = datetime.utcnow()
    senha_y1_db.local_id = ids["local_com_rodizio"]
    # operador_id precisa bater com quem vai consultar (controle de atendimento,
    # senha_atual_no_local agora é escopada por operador, não só por local).
    senha_y1_db.operador_id = ids["recepcao_a1"]
    db.session.commit()

resp = c_recepcao_a1.get(URL_PAINEL)
texto = resp.data.decode("utf-8")
assert "FY001" in texto
ok("'Senha chamada' exibe a senha atual (FY001) com o nome da fila (Fila Y)")

# ---------------------------------------------------------------------------
# 6) GET /dados (JSON): inclui o nome da fila por item - diferente do endpoint
#    por-fila, que não precisa repetir isso (já sabe de qual fila é).
# ---------------------------------------------------------------------------
resp = c_recepcao_a1.get(URL_DADOS)
assert resp.status_code == 200
dados = resp.get_json()
assert dados["senha_atual"]["codigo"] == "FY001"
codigos_fila = {item["codigo"]: item["fila"] for item in dados["espera"]}
assert codigos_fila == {"FX001": "Fila X"}  # FY001 já foi chamada, não aparece mais em espera
ok("GET /local/<id>/dados: JSON inclui 'fila' por item em 'espera'/'senha_atual' corretamente")

# ---------------------------------------------------------------------------
# 7) Local SEM rodízio configurado: renderiza vazio, sem erro.
# ---------------------------------------------------------------------------
resp = c_recepcao_a1.get(f"/atendimento/recepcao/local/{ids['local_sem_rodizio']}")
assert resp.status_code == 200
texto = resp.data.decode("utf-8")
assert "Nenhuma senha aguardando neste Local." in texto
assert "Nenhuma senha atendida neste Local ainda." in texto
ok("Local sem rodízio configurado: tela renderiza normalmente (200), com mensagens de estado vazio")

print()
print(f"TODOS OS {len(passos)} TESTES DA TELA POR-LOCAL (ADENDO 10.8) PASSARAM")
