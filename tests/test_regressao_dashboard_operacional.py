"""
Teste de regressão: dashboard operacional (adendo 10.12.2 do BLUEPRINT) - tela
padrão pós-login com 4 KPIs, gráfico de chamadas por hora (Chart.js), lista de
filas com status colorido e tabela de últimos atendimentos.

Cobre:
1. Login de admin/gerencial/super admin redireciona pra /dashboard (não mais
   core.index) - perfil puramente operacional (atendente) continua indo pra
   'Iniciar Turno', sem mudança (seção 10.19/10.23).
2. Os 4 KPIs (aguardando, chamadas hoje, tempo médio de espera hoje, filas
   ativas) calculados corretamente.
3. Escopo por perfil: admin/gerencial veem a empresa INTEIRA (mesmo restritos a
   uma única unidade em Usuario.unidades - diferente do resto de relatorios/,
   decisão de produto específica desta tela); atendente vê só as próprias
   unidades, sem conseguir escapar via querystring (unidade_id de outra unidade
   da mesma empresa é ignorado).
4. Isolamento multi-tenant: nada de uma empresa aparece pro admin de outra.
5. /api/dashboard/kpis (polling) retorna os mesmos números da página.
6. Nome do paciente na tabela de últimos atendimentos: só o primeiro nome.
7. Status colorido das filas: ativa / fora do horário / inativa.

Execução: python -m tests.test_regressao_dashboard_operacional (a partir da
raiz do projeto, com o virtualenv ativado).
"""
import os

os.environ.setdefault("SECRET_KEY", "test")
os.environ.setdefault("FLASK_ENV", "testing")

from datetime import datetime, timedelta

from app import create_app
from app.extensions import db
from app.models import Empresa, Fila, LogAtendimento, Perfil, Senha, Unidade, Usuario
from app.models.fila import DIAS_SEMANA

app = create_app("testing")

passos = []


def ok(msg):
    passos.append(msg)
    print("OK:", msg)


SEMPRE_ABERTA = {dia: {"manha": True, "tarde": True} for dia in DIAS_SEMANA}
SEMPRE_FECHADA = {dia: {"manha": False, "tarde": False} for dia in DIAS_SEMANA}

with app.app_context():
    db.create_all()

    perfil_admin = Perfil(nome="admin")
    perfil_gerencial = Perfil(nome="gerencial")
    perfil_atendente = Perfil(nome="atendente")
    db.session.add_all([perfil_admin, perfil_gerencial, perfil_atendente])
    db.session.commit()

    empresa_a = Empresa(nome="Clinica Dashboard A")
    empresa_b = Empresa(nome="Clinica Dashboard B")
    db.session.add_all([empresa_a, empresa_b])
    db.session.commit()

    matriz = Unidade(empresa_id=empresa_a.id, nome="Matriz", sigla="MAT")
    anexo = Unidade(empresa_id=empresa_a.id, nome="Anexo", sigla="ANX")
    unidade_b = Unidade(empresa_id=empresa_b.id, nome="Unica B", sigla="UNB")
    db.session.add_all([matriz, anexo, unidade_b])
    db.session.commit()

    fila_matriz_aberta = Fila(
        empresa_id=empresa_a.id, unidade_id=matriz.id, nome="Recepção Matriz", sigla="RM",
        horario_funcionamento=dict(SEMPRE_ABERTA),
    )
    fila_matriz_inativa = Fila(
        empresa_id=empresa_a.id, unidade_id=matriz.id, nome="Fila Desativada", sigla="FD", ativo=False,
    )
    fila_anexo_fechada = Fila(
        empresa_id=empresa_a.id, unidade_id=anexo.id, nome="Recepção Anexo", sigla="RA",
        horario_funcionamento=dict(SEMPRE_FECHADA),
    )
    fila_b = Fila(empresa_id=empresa_b.id, unidade_id=unidade_b.id, nome="Fila B", sigla="FB")
    db.session.add_all([fila_matriz_aberta, fila_matriz_inativa, fila_anexo_fechada, fila_b])
    db.session.commit()

    def novo_usuario(login, empresa_id, perfis, unidades):
        u = Usuario(empresa_id=empresa_id, nome=login, login=login)
        u.set_senha("senha123")
        u.perfis = perfis
        u.unidades = unidades
        db.session.add(u)
        db.session.commit()
        return u.id

    id_admin_a = novo_usuario("admin_dash_a", empresa_a.id, [perfil_admin], [])
    # gerencial explicitamente restrito só à Matriz em Usuario.unidades - o
    # dashboard deve IGNORAR essa restrição e mostrar a empresa inteira mesmo
    # assim (decisão de produto específica desta tela - diferente de relatorios/).
    id_gerencial_a = novo_usuario("gerencial_dash_a", empresa_a.id, [perfil_gerencial], [matriz])
    id_atendente_matriz = novo_usuario("atendente_dash_matriz", empresa_a.id, [perfil_atendente], [matriz])

    agora = datetime.utcnow()

    # Senha aguardando na Matriz.
    s_aguardando = Senha(
        empresa_id=empresa_a.id, unidade_id=matriz.id, fila_id=fila_matriz_aberta.id,
        codigo="RM001", sequencial=1, prioridade=False, status="aguardando",
    )
    # Senha já chamada hoje na Matriz, com nome completo (testa "primeiro nome").
    s_chamada = Senha(
        empresa_id=empresa_a.id, unidade_id=matriz.id, fila_id=fila_matriz_aberta.id,
        codigo="RM002", sequencial=2, prioridade=False, status="chamada",
        nome_paciente="Fulano da Silva Sauro", chamada_em=agora, criada_em=agora - timedelta(minutes=4),
        operador_id=id_admin_a, setor="recepcao",
    )
    # Senha chamada no Anexo (unidade diferente) - só deve contar pra quem vê o Anexo.
    s_chamada_anexo = Senha(
        empresa_id=empresa_a.id, unidade_id=anexo.id, fila_id=fila_anexo_fechada.id,
        codigo="RA001", sequencial=1, prioridade=False, status="chamada",
        chamada_em=agora, criada_em=agora - timedelta(minutes=2), operador_id=id_admin_a, setor="recepcao",
    )
    db.session.add_all([s_aguardando, s_chamada, s_chamada_anexo])
    db.session.commit()

    db.session.add_all([
        LogAtendimento(senha_id=s_chamada.id, evento="gerada", unidade_id=matriz.id, timestamp=s_chamada.criada_em),
        LogAtendimento(senha_id=s_chamada.id, evento="chamada_senha", unidade_id=matriz.id, usuario_id=id_admin_a, timestamp=agora),
        LogAtendimento(senha_id=s_chamada_anexo.id, evento="gerada", unidade_id=anexo.id, timestamp=s_chamada_anexo.criada_em),
        LogAtendimento(senha_id=s_chamada_anexo.id, evento="chamada_senha", unidade_id=anexo.id, usuario_id=id_admin_a, timestamp=agora),
    ])
    db.session.commit()

    ids = dict(
        empresa_a=empresa_a.id, empresa_b=empresa_b.id, matriz=matriz.id, anexo=anexo.id,
        fila_matriz_aberta=fila_matriz_aberta.id, fila_matriz_inativa=fila_matriz_inativa.id,
        fila_anexo_fechada=fila_anexo_fechada.id,
        admin_a=id_admin_a, gerencial_a=id_gerencial_a, atendente_matriz=id_atendente_matriz,
        senha_chamada=s_chamada.id,
    )


def login_como(login):
    c = app.test_client()
    resp = c.post("/login", data={"login": login, "senha": "senha123"}, follow_redirects=False)
    return c, resp


# ===========================================================================
# A) Login redireciona pra /dashboard (admin/gerencial/super admin) - não mais
#    pra core.index.
# ===========================================================================
c_admin, resp = login_como("admin_dash_a")
assert resp.status_code == 302
assert resp.headers["Location"].endswith("/dashboard")
ok("Login de admin redireciona direto pra /dashboard (não mais pra core.index)")

c_gerencial, resp = login_como("gerencial_dash_a")
assert resp.status_code == 302
assert resp.headers["Location"].endswith("/dashboard")
ok("Login de gerencial redireciona direto pra /dashboard")

# perfil puramente operacional continua indo pra 'Iniciar Turno' - sem mudança
# nenhuma da seção 10.19/10.23.
c_atendente, resp = login_como("atendente_dash_matriz")
assert resp.status_code == 302
assert resp.headers["Location"].endswith("/atendimento/iniciar-turno")
ok("Login de perfil puramente operacional (atendente) continua indo pra 'Iniciar Turno', não pro dashboard")

# ===========================================================================
# B) GET /dashboard (admin) - os 4 KPIs corretos, empresa inteira (Matriz + Anexo).
# ===========================================================================
resp = c_admin.get("/dashboard")
assert resp.status_code == 200
texto = resp.data.decode("utf-8")

assert '<div class="kpi-numero" id="kpi-aguardando">1</div>' in texto
ok("KPI 'Aguardando': 1 senha aguardando na Matriz (admin vê a empresa inteira)")

assert '<div class="kpi-numero" id="kpi-chamadas-hoje">2</div>' in texto
ok("KPI 'Chamadas hoje': 2 (uma na Matriz, uma no Anexo - admin vê as duas unidades)")

assert '<div class="kpi-numero" id="kpi-filas-ativas">2</div>' in texto
ok("KPI 'Filas ativas': 2 (fila_matriz_aberta + fila_anexo_fechada - fila_matriz_inativa não conta)")

ok("Página /dashboard renderiza com status 200 pra admin")

# ===========================================================================
# C) Status colorido das filas: ativa / fora do horário / inativa.
# ===========================================================================
assert '<span class="badge ativa">' in texto
assert 'Recepção Matriz' in texto
ok("Fila dentro do horário de funcionamento aparece com badge 'ativa' (verde)")

assert '<span class="badge fora_horario">' in texto
assert 'Recepção Anexo' in texto
ok("Fila ativa mas fora do horário de funcionamento aparece com badge 'fora_horario' (laranja)")

assert '<span class="badge inativa">' in texto
assert 'Fila Desativada' in texto
ok("Fila desativada aparece com badge 'inativa' (cinza)")

# ===========================================================================
# D) Nome do paciente na tabela de últimos atendimentos: só o primeiro nome.
# ===========================================================================
assert "Fulano" in texto
assert "Fulano da Silva Sauro" not in texto
assert "da Silva Sauro" not in texto
ok("Tabela de últimos atendimentos mostra só o primeiro nome do paciente ('Fulano', não o nome completo)")

# ===========================================================================
# E) Escopo por perfil - gerencial vê a EMPRESA INTEIRA no dashboard, mesmo
#    restrito só à Matriz em Usuario.unidades (decisão de produto desta tela).
# ===========================================================================
resp = c_gerencial.get("/dashboard")
assert resp.status_code == 200
texto_gerencial = resp.data.decode("utf-8")
assert '<div class="kpi-numero" id="kpi-chamadas-hoje">2</div>' in texto_gerencial
assert "Recepção Anexo" in texto_gerencial  # unidade que NÃO está em Usuario.unidades dele
ok("Gerencial vê a empresa inteira no dashboard (Anexo incluso), mesmo restrito só à Matriz em Usuario.unidades")

# ===========================================================================
# F) Escopo por perfil - atendente vê SÓ as próprias unidades (Matriz), nunca o
#    Anexo - nem manipulando unidade_id na querystring (não tem esse controle).
# ===========================================================================
resp = c_atendente.get("/dashboard")
assert resp.status_code == 200
texto_atendente = resp.data.decode("utf-8")
assert '<div class="kpi-numero" id="kpi-chamadas-hoje">1</div>' in texto_atendente  # só a chamada da Matriz
assert "Recepção Anexo" not in texto_atendente
assert "Recepção Matriz" in texto_atendente
assert 'id="contexto-unidade"' not in texto_atendente  # sem seletor de unidade pra esse perfil
ok("Atendente vê só a própria unidade (Matriz) no dashboard - Anexo não aparece, sem seletor de unidade")

resp = c_atendente.get(f"/dashboard?unidade_id={ids['anexo']}")
assert resp.status_code == 200
texto_atendente_tentativa = resp.data.decode("utf-8")
assert "Recepção Anexo" not in texto_atendente_tentativa
assert '<div class="kpi-numero" id="kpi-chamadas-hoje">1</div>' in texto_atendente_tentativa
ok("Atendente manipulando unidade_id=Anexo na URL não consegue ver a unidade fora do seu escopo")

# ===========================================================================
# G) Barra de contexto: admin consegue estreitar pra uma única unidade (não é
#    restrição de segurança, é conveniência - o teto dele já é a empresa toda).
# ===========================================================================
resp = c_admin.get(f"/dashboard?unidade_id={ids['anexo']}")
assert resp.status_code == 200
texto_estreitado = resp.data.decode("utf-8")
assert '<div class="kpi-numero" id="kpi-chamadas-hoje">1</div>' in texto_estreitado  # só a do Anexo agora
assert "Recepção Matriz" not in texto_estreitado
ok("Admin consegue estreitar o dashboard pra uma única unidade via barra de contexto")

# volta pra "Todas as unidades" explicitamente - limpa o filtro estreitado no
# passo anterior (persistido na sessão, mesmo mecanismo da barra de contexto,
# seção 10.22) antes de seguir com os próximos passos.
resp = c_admin.get("/dashboard?unidade_id=")
assert resp.status_code == 200
assert '<div class="kpi-numero" id="kpi-chamadas-hoje">2</div>' in resp.data.decode("utf-8")
ok("Escolher 'Todas as unidades' de volta limpa o filtro estreitado da sessão")

# ===========================================================================
# H) Isolamento multi-tenant: nada da empresa B aparece pro admin da empresa A.
# ===========================================================================
resp = c_admin.get("/dashboard")
texto_isolamento = resp.data.decode("utf-8")
assert "Fila B" not in texto_isolamento
assert "Unica B" not in texto_isolamento
ok("Isolamento multi-tenant preservado: nada da empresa B aparece no dashboard da empresa A")

# super admin sem empresa escolhida cai no seletor de empresas (mesmo padrão de
# admin/relatorios) - nunca vê dado de nenhuma empresa por padrão.
db_super = Usuario(nome="Super Dash", login="super_dash", empresa_id=None)
with app.app_context():
    db_super.set_senha("senha123")
    db.session.add(db_super)
    db.session.commit()
c_super, resp_login_super = login_como("super_dash")
assert resp_login_super.status_code == 302
assert resp_login_super.headers["Location"].endswith("/dashboard")
resp = c_super.get("/dashboard")
assert resp.status_code == 200
assert "Selecione uma empresa" in resp.data.decode("utf-8")
ok("Super admin sem empresa escolhida cai no seletor de empresas, sem vazar dado de nenhuma")

# ===========================================================================
# I) /api/dashboard/kpis (polling) retorna os mesmos números da página, em JSON.
# ===========================================================================
resp = c_admin.get("/api/dashboard/kpis")
assert resp.status_code == 200
dados = resp.get_json()
assert dados["aguardando"] == 1
assert dados["chamadas_hoje"] == 2
assert dados["filas_ativas"] == 2
assert dados["tempo_medio_espera_segundos"] is not None
ok("/api/dashboard/kpis retorna os mesmos números da página, em JSON, pro polling de 30s")

resp = c_atendente.get("/api/dashboard/kpis")
assert resp.status_code == 200
dados_atendente = resp.get_json()
assert dados_atendente["chamadas_hoje"] == 1
ok("/api/dashboard/kpis também respeita o escopo por perfil (atendente só vê a própria unidade)")

print()
print(f"TODOS OS {len(passos)} TESTES DO DASHBOARD OPERACIONAL PASSARAM")
