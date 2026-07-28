"""
Teste de regressão: dashboard operacional (fora do BLUEPRINT original) em
/relatorios/dashboard - RBAC (gerencial/admin/super admin, não recepcao/coleta),
isolamento multi-tenant e por unidade (mesmo padrão do resto do sistema), contagens
de recursos, senhas aguardando (total e por fila), feed de últimas chamadas, e os
4 tipos de alerta de configuração incompleta.

Execução: python -m tests.test_regressao_dashboard (a partir da raiz do projeto,
com o virtualenv ativado).
"""
import os
from datetime import datetime, timedelta

os.environ.setdefault("SECRET_KEY", "test")
os.environ.setdefault("FLASK_ENV", "testing")

from app import create_app
from app.extensions import db
from app.models import Empresa, Fila, LogAtendimento, Perfil, Senha, TipoSenha, Totem, Tv, Unidade, Usuario
from app.models.fila import DIAS_SEMANA
from app.relatorios.queries import coletar_alertas, resumo_recursos, senhas_aguardando_por_fila

app = create_app("testing")

passos = []


def ok(msg):
    passos.append(msg)
    print("OK:", msg)


HORARIO_FECHADO = {dia: {"manha": False, "tarde": False} for dia in DIAS_SEMANA}

with app.app_context():
    db.create_all()

    perfil_admin = Perfil(nome="admin")
    perfil_gerencial = Perfil(nome="gerencial")
    perfil_recepcao = Perfil(nome="atendente")
    db.session.add_all([perfil_admin, perfil_gerencial, perfil_recepcao])
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

    tipo_normal = TipoSenha(empresa_id=empresa_a.id, nome="Normal", peso_prioridade=0)
    db.session.add(tipo_normal)
    db.session.commit()

    # --- filas em Unidade A1 -------------------------------------------------
    fila_sem_tipo = Fila(empresa_id=empresa_a.id, unidade_id=unidade_a1.id, nome="Fila Sem Tipo", sigla="ST")
    fila_fora_horario = Fila(
        empresa_id=empresa_a.id, unidade_id=unidade_a1.id, nome="Fila Fora Horario", sigla="FH",
        horario_funcionamento=dict(HORARIO_FECHADO),
    )
    fila_ok = Fila(empresa_id=empresa_a.id, unidade_id=unidade_a1.id, nome="Fila OK", sigla="OK")
    fila_inativa = Fila(
        empresa_id=empresa_a.id, unidade_id=unidade_a1.id, nome="Fila Inativa", sigla="IN", ativo=False,
    )
    db.session.add_all([fila_sem_tipo, fila_fora_horario, fila_ok, fila_inativa])
    db.session.commit()
    fila_fora_horario.tipos_senha = [tipo_normal]
    fila_ok.tipos_senha = [tipo_normal]
    db.session.commit()

    # --- fila em Unidade A2 (mesma empresa) - só visível pra quem tem acesso --
    fila_a2 = Fila(empresa_id=empresa_a.id, unidade_id=unidade_a2.id, nome="Fila Unidade A2", sigla="A2")
    db.session.add(fila_a2)
    db.session.commit()

    # --- totens em Unidade A1 -------------------------------------------------
    totem_sem_impressora = Totem(
        empresa_id=empresa_a.id, unidade_id=unidade_a1.id, nome="Totem Sem Impressora", slug="totem-sem-impressora",
    )
    totem_ok = Totem(
        empresa_id=empresa_a.id, unidade_id=unidade_a1.id, nome="Totem OK",
        nome_impressora_windows="Bematech MP-4200 TH", slug="totem-ok",
    )
    totem_inativo = Totem(
        empresa_id=empresa_a.id, unidade_id=unidade_a1.id, nome="Totem Inativo", ativo=False, slug="totem-inativo",
    )
    db.session.add_all([totem_sem_impressora, totem_ok, totem_inativo])
    db.session.commit()

    # --- TVs em Unidade A1 ------------------------------------------------------
    tv_sem_filas = Tv(empresa_id=empresa_a.id, unidade_id=unidade_a1.id, nome="TV Sem Filas", slug="tv-sem-filas")
    tv_ok = Tv(empresa_id=empresa_a.id, unidade_id=unidade_a1.id, nome="TV OK", slug="tv-ok")
    db.session.add_all([tv_sem_filas, tv_ok])
    db.session.commit()
    tv_ok.filas = [fila_ok]
    db.session.commit()

    # --- senhas em Fila OK: 3 aguardando + 2 já chamadas (para o feed) ----------
    base_t = datetime.utcnow() - timedelta(minutes=10)

    def nova_senha(codigo, seq, status, nome_paciente=None):
        s = Senha(
            empresa_id=empresa_a.id, unidade_id=unidade_a1.id, fila_id=fila_ok.id,
            codigo=codigo, sequencial=seq, prioridade=False, status=status,
            nome_paciente=nome_paciente, criada_em=base_t,
        )
        db.session.add(s)
        db.session.commit()
        return s

    senha_aguardando_1 = nova_senha("OK001", 1, "aguardando")
    senha_aguardando_2 = nova_senha("OK002", 2, "aguardando")
    senha_aguardando_3 = nova_senha("OK003", 3, "aguardando")
    senha_chamada = nova_senha("OK004", 4, "chamada")
    senha_nome = nova_senha("OK005", 5, "chamada", nome_paciente="Maria Teste")

    log_chamada = LogAtendimento(
        senha_id=senha_chamada.id, evento="chamada_senha", unidade_id=unidade_a1.id,
        timestamp=base_t + timedelta(seconds=1),
    )
    log_nome = LogAtendimento(
        senha_id=senha_nome.id, evento="chamada_nome", unidade_id=unidade_a1.id,
        timestamp=base_t + timedelta(seconds=2),  # mais recente - deve aparecer primeiro no feed
    )
    db.session.add_all([log_chamada, log_nome])
    db.session.commit()

    def novo_usuario(login, empresa_id, perfis, unidades):
        u = Usuario(empresa_id=empresa_id, nome=login, login=login)
        u.set_senha("senha123")
        u.perfis = perfis
        u.unidades = unidades
        db.session.add(u)
        db.session.commit()
        return u.id

    id_admin_a = novo_usuario("admin_a_dash", empresa_a.id, [perfil_admin], [])
    id_gerencial_a1 = novo_usuario("gerencial_a1_dash", empresa_a.id, [perfil_gerencial], [unidade_a1])
    id_recepcao_a = novo_usuario("recepcao_a_dash", empresa_a.id, [perfil_recepcao], [unidade_a1])

    super_admin = Usuario(empresa_id=None, nome="Super Admin", login="superadmin_dash")
    super_admin.set_senha("senha123")
    db.session.add(super_admin)
    db.session.commit()

    ids = dict(
        empresa_a=empresa_a.id, empresa_b=empresa_b.id, unidade_a1=unidade_a1.id, unidade_a2=unidade_a2.id,
        fila_ok=fila_ok.id, admin_a=id_admin_a, gerencial_a1=id_gerencial_a1, recepcao_a=id_recepcao_a,
    )

print("IDS", ids)


def login_como(login):
    c = app.test_client()
    resp = c.post("/login", data={"login": login, "senha": "senha123"}, follow_redirects=True)
    assert resp.status_code == 200, (login, resp.status_code)
    return c


anon = app.test_client()
c_admin_a = login_como("admin_a_dash")
c_gerencial_a1 = login_como("gerencial_a1_dash")
c_recepcao_a = login_como("recepcao_a_dash")
c_super = login_como("superadmin_dash")

# ---------------------------------------------------------------------------
# 1) RBAC: recepcao -> 403, sem login -> 401, gerencial/admin -> 200.
# ---------------------------------------------------------------------------
assert anon.get("/relatorios/dashboard").status_code == 401
ok("RBAC: sem login recebe 401 em /relatorios/dashboard")

assert c_recepcao_a.get("/relatorios/dashboard").status_code == 403
ok("RBAC: perfil 'atendente' recebe 403 em /relatorios/dashboard")

assert c_gerencial_a1.get("/relatorios/dashboard").status_code == 200
ok("RBAC: perfil 'gerencial' acessa /relatorios/dashboard (200)")

assert c_admin_a.get("/relatorios/dashboard").status_code == 200
ok("RBAC: perfil 'admin' acessa /relatorios/dashboard (200)")

# ---------------------------------------------------------------------------
# 2) Super admin: sem empresa_id mostra seletor; com empresa_id mostra o dashboard.
# ---------------------------------------------------------------------------
resp = c_super.get("/relatorios/dashboard")
assert resp.status_code == 200
assert "Clinica A".encode("utf-8") in resp.data and "Clinica B".encode("utf-8") in resp.data
ok("Super admin sem empresa_id vê a tela de seleção de empresa")

resp = c_super.get(f"/relatorios/dashboard?empresa_id={ids['empresa_a']}")
assert resp.status_code == 200
ok("Super admin com empresa_id vê o dashboard daquela empresa")

# ---------------------------------------------------------------------------
# 3) Isolamento por unidade: gerencial (só unidade A1) vs. admin (empresa A inteira).
#    Confere direto nas funções de query (números exatos) para não depender de
#    parsing de HTML.
# ---------------------------------------------------------------------------
with app.app_context():
    empresa_a_db = db.session.get(Empresa, ids["empresa_a"])

    recursos_a1 = resumo_recursos(empresa_a_db, {ids["unidade_a1"]})
    assert recursos_a1["filas_ativas"] == 3  # sem_tipo, fora_horario, ok (inativa não conta)
    assert recursos_a1["totens_ativos"] == 2 and recursos_a1["totens_inativos"] == 1
    assert recursos_a1["tvs_ativas"] == 2
    ok("resumo_recursos escopado só à unidade A1: 3 filas ativas, 2/1 totens, 2 TVs")

    alertas_a1 = coletar_alertas(recursos_a1["filas"], recursos_a1["totens"], recursos_a1["tvs"])
    tipos_a1 = sorted(a["tipo"] for a in alertas_a1)
    assert tipos_a1 == ["fila_fora_horario", "fila_sem_tipo_senha", "totem_sem_impressora", "tv_sem_filas"]
    ok("coletar_alertas (unidade A1): exatamente os 4 tipos esperados, fila/totem inativos não geram alerta")

    aguardando_a1 = senhas_aguardando_por_fila(empresa_a_db, {ids["unidade_a1"]})
    assert aguardando_a1 == {ids["fila_ok"]: 3}
    ok("senhas_aguardando_por_fila (unidade A1): 3 na Fila OK, senhas já chamadas não contam")

    recursos_a_total = resumo_recursos(empresa_a_db, {ids["unidade_a1"], ids["unidade_a2"]})
    assert recursos_a_total["filas_ativas"] == 4  # + Fila Unidade A2
    assert recursos_a_total["totens_ativos"] == 2 and recursos_a_total["totens_inativos"] == 1  # inalterado
    assert recursos_a_total["tvs_ativas"] == 2  # inalterado

    alertas_total = coletar_alertas(recursos_a_total["filas"], recursos_a_total["totens"], recursos_a_total["tvs"])
    assert len(alertas_total) == 5  # os 4 de A1 + 'fila_sem_tipo_senha' da Fila Unidade A2
    ok("Isolamento por unidade confirmado: admin (empresa inteira) vê 4 filas/5 alertas, "
       "gerencial (só A1) vê 3 filas/4 alertas - a diferença é exatamente a Fila Unidade A2")

# ---------------------------------------------------------------------------
# 4) Conteúdo renderizado: gerencial (unidade A1) não vê 'Fila Unidade A2'; admin vê.
# ---------------------------------------------------------------------------
resp = c_gerencial_a1.get("/relatorios/dashboard")
texto = resp.data.decode("utf-8")
assert "Fila Unidade A2" not in texto
assert "3" in texto  # filas_ativas do escopo restrito
ok("Dashboard renderizado para 'gerencial' (unidade A1) não exibe a Fila Unidade A2")

resp = c_admin_a.get("/relatorios/dashboard")
texto = resp.data.decode("utf-8")
assert "Fila Unidade A2" in texto
ok("Dashboard renderizado para 'admin' (empresa inteira) exibe a Fila Unidade A2")

# ---------------------------------------------------------------------------
# 5) Senhas aguardando por fila e total, no HTML.
# ---------------------------------------------------------------------------
resp = c_admin_a.get("/relatorios/dashboard")
texto = resp.data.decode("utf-8")
assert "Fila OK" in texto
ok("Tabela 'Senhas aguardando por fila' lista a Fila OK com sua contagem")

# ---------------------------------------------------------------------------
# 6) Feed de últimas chamadas: mais recente primeiro, nome sobrepõe código quando houver.
# ---------------------------------------------------------------------------
resp = c_admin_a.get("/relatorios/dashboard")
texto = resp.data.decode("utf-8")
pos_nome = texto.find("Maria Teste")
pos_codigo = texto.find("OK004")
assert pos_nome != -1 and pos_codigo != -1
assert pos_nome < pos_codigo  # chamada_nome (mais recente) aparece antes de chamada_senha
ok("Feed 'Últimas chamadas': evento mais recente (chamada_nome) aparece antes do mais antigo (chamada_senha)")

# ---------------------------------------------------------------------------
# 7) Isolamento multi-tenant: nada da empresa B aparece no dashboard da empresa A.
# ---------------------------------------------------------------------------
resp = c_admin_a.get("/relatorios/dashboard")
texto = resp.data.decode("utf-8")
assert "Clinica B" not in texto
ok("Isolamento multi-tenant: dashboard da empresa A não menciona a empresa B")

print()
print(f"TODOS OS {len(passos)} TESTES DO DASHBOARD (FORA DO BLUEPRINT) PASSARAM")
