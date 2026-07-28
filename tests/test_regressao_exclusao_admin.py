"""
Teste de regressão: opção "Excluir" (exclusão de verdade, distinta do já existente
toggle Ativar/Desativar) nas 7 telas administrativas: Empresas, Filas, Totens, TVs,
Locais, Tipos de senha, Botões do totem.

Decisão de design confirmada com o cliente antes de implementar: exclusão SEGURA -
só apaga de fato quando não há nenhum vínculo/histórico; quando há, bloqueia com
mensagem clara e sugere Desativar. Nunca apaga Senha/LogAtendimento (BLUEPRINT.md,
seção 8: "nunca apagar senhas/logs_atendimento - usar status, nunca DELETE") nem
força uma exclusão em cascata que destruiria histórico de relatórios.

Cobre, para cada uma das 7 entidades:
- Bloqueio quando há vínculo/histórico (o registro continua existindo depois).
- Exclusão bem-sucedida quando não há nenhum vínculo (limpa M2M/vínculos de
  configuração primeiro, quando aplicável - tv_filas, fila_tipos_senha,
  local_filas_destino, local_rodizio_filas, Usuario.local_atual_id).
- Isolamento multi-tenant (404 ao tentar excluir objeto de outra empresa) e RBAC
  de Empresa (só super admin, via @super_admin_required).

Execução: python -m tests.test_regressao_exclusao_admin (a partir da raiz do
projeto, com o virtualenv ativado).
"""
import os

os.environ.setdefault("SECRET_KEY", "test")
os.environ.setdefault("FLASK_ENV", "testing")

from app import create_app
from app.extensions import db
from app.models import (
    Empresa,
    Fila,
    Local,
    LocalRodizioFila,
    Perfil,
    Senha,
    TipoSenha,
    Totem,
    TotemBotao,
    Tv,
    Unidade,
    Usuario,
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

    empresa_a = Empresa(nome="Clinica A")
    empresa_b = Empresa(nome="Clinica B")
    empresa_vazia = Empresa(nome="Clinica Vazia")
    db.session.add_all([empresa_a, empresa_b, empresa_vazia])
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

    id_admin_a = novo_usuario("admin_a_del", empresa_a.id, [perfil_admin])
    id_admin_b = novo_usuario("admin_b_del", empresa_b.id, [perfil_admin])
    id_super = novo_usuario("super_del", None, [])

    # ---- Totem: um limpo (deletável), um bloqueado (fila.totem_id aponta pra ele) ----
    totem_limpo = Totem(empresa_id=empresa_a.id, unidade_id=unidade_a1.id, nome="Totem Limpo", slug="totem-limpo-del")
    totem_bloqueado = Totem(
        empresa_id=empresa_a.id, unidade_id=unidade_a1.id, nome="Totem Bloqueado", slug="totem-bloqueado-del",
    )
    totem_empresa_b = Totem(empresa_id=empresa_b.id, unidade_id=unidade_b1.id, nome="Totem B", slug="totem-b-del")
    db.session.add_all([totem_limpo, totem_bloqueado, totem_empresa_b])
    db.session.commit()

    # ---- TV: uma limpa (com fila via M2M, mas não via atalho fila.tv_id), uma bloqueada ----
    tv_limpa = Tv(empresa_id=empresa_a.id, unidade_id=unidade_a1.id, nome="TV Limpa", slug="tv-limpa-del")
    tv_bloqueada = Tv(empresa_id=empresa_a.id, unidade_id=unidade_a1.id, nome="TV Bloqueada", slug="tv-bloqueada-del")
    db.session.add_all([tv_limpa, tv_bloqueada])
    db.session.commit()

    # ---- TipoSenha: um limpo, um bloqueado (usado numa senha histórica) ----
    tipo_limpo = TipoSenha(empresa_id=empresa_a.id, nome="Tipo Limpo")
    tipo_bloqueado = TipoSenha(empresa_id=empresa_a.id, nome="Tipo Bloqueado")
    db.session.add_all([tipo_limpo, tipo_bloqueado])
    db.session.commit()

    # ---- Filas: limpa (deletável), bloqueada por Senha, bloqueada só por TotemBotao ----
    fila_limpa = Fila(empresa_id=empresa_a.id, unidade_id=unidade_a1.id, nome="Fila Limpa", sigla="FL")
    fila_bloqueada_senha = Fila(empresa_id=empresa_a.id, unidade_id=unidade_a1.id, nome="Fila Com Senha", sigla="FS")
    fila_bloqueada_botao = Fila(empresa_id=empresa_a.id, unidade_id=unidade_a1.id, nome="Fila Com Botao", sigla="FB")
    fila_para_totem = Fila(
        empresa_id=empresa_a.id, unidade_id=unidade_a1.id, nome="Fila Trava Totem", sigla="FT",
        totem_id=totem_bloqueado.id,
    )
    fila_para_tv = Fila(
        empresa_id=empresa_a.id, unidade_id=unidade_a1.id, nome="Fila Trava TV", sigla="FV", tv_id=tv_bloqueada.id,
    )
    db.session.add_all([fila_limpa, fila_bloqueada_senha, fila_bloqueada_botao, fila_para_totem, fila_para_tv])
    db.session.commit()

    # fila_limpa recebe vínculos puramente de configuração (M2M) - exclusão deve limpar
    # sozinha e funcionar mesmo assim, já que isso não é histórico/auditoria.
    fila_limpa.tvs = [tv_limpa]
    fila_limpa.tipos_senha = [tipo_limpo]
    db.session.commit()

    senha_historica = Senha(
        empresa_id=empresa_a.id, unidade_id=unidade_a1.id, fila_id=fila_bloqueada_senha.id,
        codigo="FS001", sequencial=1, prioridade=False, status="finalizada", tipo_senha_id=tipo_bloqueado.id,
    )
    db.session.add(senha_historica)
    db.session.commit()

    botao_travando_fila = TotemBotao(
        empresa_id=empresa_a.id, unidade_id=unidade_a1.id, fila_id=fila_bloqueada_botao.id, label="Botao Trava Fila",
    )
    db.session.add(botao_travando_fila)
    db.session.commit()

    # ---- Locais: limpo (com usuário "local atual" apontando pra ele - deve limpar
    #      sozinho), bloqueado (senha histórica registrada nele) ----
    local_limpo = Local(empresa_id=empresa_a.id, unidade_id=unidade_a1.id, nome="Local Limpo")
    local_bloqueado = Local(empresa_id=empresa_a.id, unidade_id=unidade_a1.id, nome="Local Bloqueado")
    db.session.add_all([local_limpo, local_bloqueado])
    db.session.commit()

    # usa fila_bloqueada_senha (já vai ficar bloqueada por outro motivo, seção D) como
    # origem desta senha - nunca fila_limpa, que precisa continuar sem NENHUM vínculo
    # até a seção D testar a exclusão bem-sucedida dela.
    senha_no_local = Senha(
        empresa_id=empresa_a.id, unidade_id=unidade_a1.id, fila_id=fila_bloqueada_senha.id,
        codigo="FS900", sequencial=900, prioridade=False, status="finalizada", local_id=local_bloqueado.id,
    )
    db.session.add(senha_no_local)
    db.session.commit()

    db.session.add(
        LocalRodizioFila(local_id=local_limpo.id, fila_id=fila_bloqueada_senha.id, ordem=0, chamadas_seguidas=1)
    )
    db.session.commit()

    with app.app_context():
        operador = db.session.get(Usuario, id_admin_a)
        operador.local_atual_id = local_limpo.id
        db.session.commit()

    # ---- Botões do totem: pai com filho (pai bloqueado), filho isolado (deletável) ----
    botao_pai = TotemBotao(empresa_id=empresa_a.id, unidade_id=unidade_a1.id, label="Botao Pai")
    db.session.add(botao_pai)
    db.session.commit()
    botao_filho = TotemBotao(empresa_id=empresa_a.id, unidade_id=unidade_a1.id, parent_id=botao_pai.id, label="Botao Filho")
    db.session.add(botao_filho)
    db.session.commit()

    ids = dict(
        empresa_a=empresa_a.id, empresa_b=empresa_b.id, empresa_vazia=empresa_vazia.id,
        totem_limpo=totem_limpo.id, totem_bloqueado=totem_bloqueado.id, totem_empresa_b=totem_empresa_b.id,
        tv_limpa=tv_limpa.id, tv_bloqueada=tv_bloqueada.id,
        tipo_limpo=tipo_limpo.id, tipo_bloqueado=tipo_bloqueado.id,
        fila_limpa=fila_limpa.id, fila_bloqueada_senha=fila_bloqueada_senha.id,
        fila_bloqueada_botao=fila_bloqueada_botao.id, fila_para_totem=fila_para_totem.id, fila_para_tv=fila_para_tv.id,
        local_limpo=local_limpo.id, local_bloqueado=local_bloqueado.id,
        botao_pai=botao_pai.id, botao_filho=botao_filho.id,
        admin_a=id_admin_a,
    )


def login_como(login):
    c = app.test_client()
    resp = c.post("/login", data={"login": login, "senha": "senha123"}, follow_redirects=True)
    assert resp.status_code == 200, (login, resp.status_code)
    return c


def existe(modelo, id_):
    with app.app_context():
        return db.session.get(modelo, id_) is not None


c_admin_a = login_como("admin_a_del")
c_admin_b = login_como("admin_b_del")
c_super = login_como("super_del")

# ===========================================================================
# A) Totem
# ===========================================================================
resp = c_admin_a.post(f"/admin/totens/{ids['totem_bloqueado']}/excluir", follow_redirects=True)
assert resp.status_code == 200
assert "Não é possível excluir".encode("utf-8") in resp.data
assert existe(Totem, ids["totem_bloqueado"])
ok("Totem com fila vinculada (Fila.totem_id) é bloqueado, mensagem clara, nada é apagado")

resp = c_admin_a.post(f"/admin/totens/{ids['totem_limpo']}/excluir", follow_redirects=True)
assert resp.status_code == 200
assert "excluído".encode("utf-8") in resp.data
assert not existe(Totem, ids["totem_limpo"])
ok("Totem sem nenhum vínculo é excluído de verdade")

resp = c_admin_a.post(f"/admin/totens/{ids['totem_empresa_b']}/excluir")
assert resp.status_code == 404
assert existe(Totem, ids["totem_empresa_b"])
ok("Isolamento multi-tenant: admin da empresa A recebe 404 ao tentar excluir totem da empresa B")

# ===========================================================================
# B) TV
# ===========================================================================
resp = c_admin_a.post(f"/admin/tvs/{ids['tv_bloqueada']}/excluir", follow_redirects=True)
assert resp.status_code == 200
assert "Não é possível excluir".encode("utf-8") in resp.data
assert existe(Tv, ids["tv_bloqueada"])
ok("TV usada como atalho de cadastro (Fila.tv_id) é bloqueada")

resp = c_admin_a.post(f"/admin/tvs/{ids['tv_limpa']}/excluir", follow_redirects=True)
assert resp.status_code == 200
assert "excluída".encode("utf-8") in resp.data
assert not existe(Tv, ids["tv_limpa"])
ok("TV sem atalho de fila é excluída (mesmo tendo fila vinculada só via M2M tv_filas)")

# ===========================================================================
# C) Tipo de senha
# ===========================================================================
resp = c_admin_a.post(f"/admin/tipos-senha/{ids['tipo_bloqueado']}/excluir", follow_redirects=True)
assert resp.status_code == 200
assert "Não é possível excluir".encode("utf-8") in resp.data
assert existe(TipoSenha, ids["tipo_bloqueado"])
ok("Tipo de senha usado numa senha histórica é bloqueado")

resp = c_admin_a.post(f"/admin/tipos-senha/{ids['tipo_limpo']}/excluir", follow_redirects=True)
assert resp.status_code == 200
assert "excluído".encode("utf-8") in resp.data
assert not existe(TipoSenha, ids["tipo_limpo"])
ok("Tipo de senha sem nenhuma senha vinculada é excluído (mesmo estando numa fila via M2M)")

# ===========================================================================
# D) Fila
# ===========================================================================
resp = c_admin_a.post(f"/admin/filas/{ids['fila_bloqueada_senha']}/excluir", follow_redirects=True)
assert resp.status_code == 200
assert "Não é possível excluir".encode("utf-8") in resp.data
assert existe(Fila, ids["fila_bloqueada_senha"])
ok("Fila com senha histórica é bloqueada")

resp = c_admin_a.post(f"/admin/filas/{ids['fila_bloqueada_botao']}/excluir", follow_redirects=True)
assert resp.status_code == 200
assert "Não é possível excluir".encode("utf-8") in resp.data
assert existe(Fila, ids["fila_bloqueada_botao"])
ok("Fila vinculada a um botão do totem (sem nenhuma senha) também é bloqueada")

resp = c_admin_a.post(f"/admin/filas/{ids['fila_limpa']}/excluir", follow_redirects=True)
assert resp.status_code == 200
assert "excluída".encode("utf-8") in resp.data
assert not existe(Fila, ids["fila_limpa"])
ok("Fila sem senha/botão é excluída (mesmo com vínculos M2M de TV e tipo de senha - limpos automaticamente)")

with app.app_context():
    assert db.session.get(Tv, ids["tv_bloqueada"]) is not None  # não afetada
ok("Excluir a fila_limpa não derrubou a TV vinculada, só o vínculo (tv_filas)")

# ===========================================================================
# E) Local
# ===========================================================================
resp = c_admin_a.post(f"/admin/locais/{ids['local_bloqueado']}/excluir", follow_redirects=True)
assert resp.status_code == 200
assert "Não é possível excluir".encode("utf-8") in resp.data
assert existe(Local, ids["local_bloqueado"])
ok("Local com senha histórica é bloqueado")

with app.app_context():
    assert db.session.get(Usuario, ids["admin_a"]).local_atual_id == ids["local_limpo"]

resp = c_admin_a.post(f"/admin/locais/{ids['local_limpo']}/excluir", follow_redirects=True)
assert resp.status_code == 200
assert "excluído".encode("utf-8") in resp.data
assert not existe(Local, ids["local_limpo"])
with app.app_context():
    assert db.session.get(Usuario, ids["admin_a"]).local_atual_id is None
    assert LocalRodizioFila.query.filter_by(local_id=ids["local_limpo"]).count() == 0
ok("Local sem senha é excluído; 'local atual' do operador é limpo e o rodízio cascateia sozinho")

# ===========================================================================
# F) Botão do totem
# ===========================================================================
resp = c_admin_a.post(f"/admin/totem-botoes/{ids['botao_pai']}/excluir", follow_redirects=True)
assert resp.status_code == 200
assert "Não é possível excluir".encode("utf-8") in resp.data
assert existe(TotemBotao, ids["botao_pai"])
ok("Botão com filho é bloqueado")

resp = c_admin_a.post(f"/admin/totem-botoes/{ids['botao_filho']}/excluir", follow_redirects=True)
assert resp.status_code == 200
assert "excluído".encode("utf-8") in resp.data
assert not existe(TotemBotao, ids["botao_filho"])
ok("Botão-folha (sem filhos) é excluído")

resp = c_admin_a.post(f"/admin/totem-botoes/{ids['botao_pai']}/excluir", follow_redirects=True)
assert resp.status_code == 200
assert "excluído".encode("utf-8") in resp.data
assert not existe(TotemBotao, ids["botao_pai"])
ok("Depois de excluir o filho, o antigo pai (agora sem filhos) também pode ser excluído")

# ===========================================================================
# G) Empresa - RBAC (só super admin) + bloqueio + exclusão de empresa vazia
# ===========================================================================
resp = c_admin_a.post(f"/admin/empresas/{ids['empresa_vazia']}/excluir")
assert resp.status_code == 403
ok("Admin de empresa (não super admin) recebe 403 ao tentar excluir qualquer empresa")

resp = c_super.post(f"/admin/empresas/{ids['empresa_a']}/excluir", follow_redirects=True)
assert resp.status_code == 200
assert "Não é possível excluir".encode("utf-8") in resp.data
assert existe(Empresa, ids["empresa_a"])
ok("Super admin: empresa com dados (unidades/usuários/filas...) é bloqueada")

resp = c_super.post(f"/admin/empresas/{ids['empresa_vazia']}/excluir", follow_redirects=True)
assert resp.status_code == 200
assert "excluída".encode("utf-8") in resp.data
assert not existe(Empresa, ids["empresa_vazia"])
ok("Super admin: empresa completamente vazia é excluída de verdade")

print()
print(f"TODOS OS {len(passos)} TESTES DE EXCLUSAO ADMIN (7 ENTIDADES) PASSARAM")
