"""
Teste de regressão do adendo 10.47: a sigla usada na composição do código da
senha (seção 2 do BLUEPRINT) deixa de pertencer à Fila e passa a pertencer ao
Botão do totem (`totem_botoes.sigla`) - permite no futuro dois botões
diferentes apontarem pra mesma fila com siglas diferentes, e a tela de filas
continua permitindo criar fila SEM botão vinculado (fila órfã), com a sigla
ficando pendente até algum botão ser vinculado a ela.

O caso "geração de senha usa a sigla do botão, não da fila" e o caso "dois
botões na mesma fila compartilham sequencial sem colidir" já são cobertos com
profundidade em tests/test_regressao_sequencial_diario.py (seção I) - este
arquivo cobre o resto da superfície do adendo: validação/formato/unicidade da
sigla no CRUD de botões, botão-menu nunca exigindo sigla, fila órfã de sigla e
botão, o script de migração de dados (fila.sigla -> botao.sigla), e RBAC.

Execução: python -m tests.test_regressao_sigla_botao_totem (a partir da raiz
do projeto, com o virtualenv ativado).
"""
import os

os.environ.setdefault("SECRET_KEY", "test")
os.environ.setdefault("FLASK_ENV", "testing")

from app import create_app
from app.extensions import db
from app.models import Empresa, Fila, Perfil, Senha, Totem, TotemBotao, Unidade, Usuario

app = create_app("testing")

passos = []


def ok(msg):
    passos.append(msg)
    print("OK:", msg)


with app.app_context():
    db.create_all()

    perfil_admin = Perfil(nome="admin")
    perfil_atendente = Perfil(nome="atendente")
    db.session.add_all([perfil_admin, perfil_atendente])
    db.session.commit()

    empresa = Empresa(nome="Clinica Sigla Botao")
    db.session.add(empresa)
    db.session.commit()

    unidade = Unidade(empresa_id=empresa.id, nome="Unidade 1")
    db.session.add(unidade)
    db.session.commit()

    # fila com Fila.sigla ANTIGA/deprecada preenchida ("OLD") - o código gerado
    # não deve usá-la em nenhum cenário abaixo.
    fila = Fila(empresa_id=empresa.id, unidade_id=unidade.id, nome="Laboratorio", sigla="OLD")
    db.session.add(fila)
    db.session.commit()

    totem = Totem(empresa_id=empresa.id, unidade_id=unidade.id, nome="Totem 1", slug="totem-sigla-botao")
    db.session.add(totem)
    db.session.commit()

    def novo_usuario(login, perfis, unidades=None):
        u = Usuario(empresa_id=empresa.id, nome=login, login=login)
        u.set_senha("senha123")
        u.perfis = perfis
        u.unidades = unidades or []
        db.session.add(u)
        db.session.commit()
        return u.id

    id_admin = novo_usuario("admin_sigla", [perfil_admin])
    id_atendente = novo_usuario("atendente_sigla", [perfil_atendente], unidades=[unidade])

    ids = dict(
        empresa=empresa.id, unidade=unidade.id, fila=fila.id, totem_slug=totem.slug,
        admin=id_admin, atendente=id_atendente,
    )


def login_como(login):
    c = app.test_client()
    resp = c.post("/login", data={"login": login, "senha": "senha123"}, follow_redirects=True)
    assert resp.status_code == 200, (login, resp.status_code)
    return c


def db_get(modelo, id_):
    with app.app_context():
        return db.session.get(modelo, id_)


c_admin = login_como("admin_sigla")
c_atendente = login_como("atendente_sigla")
c_totem = app.test_client()

# ===========================================================================
# A) Botão-folha novo via admin: sigla obrigatória quando uma fila é
#    selecionada.
# ===========================================================================
resp = c_admin.post(
    f"/admin/totem-botoes/novo?empresa_id={ids['empresa']}",
    data={"label": "Laboratorio", "unidade_id": ids["unidade"], "fila_id": ids["fila"], "sigla": ""},
)
assert "Informe a sigla do botão".encode("utf-8") in resp.data
with app.app_context():
    assert db.session.query(TotemBotao).filter_by(fila_id=ids["fila"]).first() is None
ok("Botão-folha (fila selecionada) sem sigla: bloqueado, nada é criado")

# ===========================================================================
# B) Formato de sigla inválido (fora de 2-4 letras A-Z) é rejeitado.
# ===========================================================================
for sigla_invalida in ("L", "LABORAT", "l4", "12"):
    resp = c_admin.post(
        f"/admin/totem-botoes/novo?empresa_id={ids['empresa']}",
        data={"label": "Laboratorio", "unidade_id": ids["unidade"], "fila_id": ids["fila"], "sigla": sigla_invalida},
    )
    assert "2 a 4 letras".encode("utf-8") in resp.data, sigla_invalida
ok("Formato de sigla inválido (não 2-4 letras A-Z) é rejeitado em todos os casos testados")

# ===========================================================================
# C) Botão-folha com sigla válida ("NEW", deliberadamente diferente da
#    Fila.sigla deprecada "OLD") é criado, e a geração de senha no totem usa a
#    sigla do BOTÃO, não a da fila.
# ===========================================================================
resp = c_admin.post(
    f"/admin/totem-botoes/novo?empresa_id={ids['empresa']}",
    data={"label": "Laboratorio", "unidade_id": ids["unidade"], "fila_id": ids["fila"], "sigla": "new"},
    follow_redirects=True,
)
assert resp.status_code == 200
with app.app_context():
    botao_lab = db.session.query(TotemBotao).filter_by(fila_id=ids["fila"]).first()
    assert botao_lab is not None
    assert botao_lab.sigla == "NEW"  # normalizado para maiúsculas
    id_botao_lab = botao_lab.id
ok("Botão-folha com sigla válida ('new' -> 'NEW') é criado")

resp = c_totem.post(f"/totem/{ids['totem_slug']}/gerar/{id_botao_lab}", data={"qtd_acompanhantes": "0"}, follow_redirects=True)
assert resp.status_code == 200
texto = resp.data.decode("utf-8")
assert "NEW001" in texto
assert "OLD001" not in texto
with app.app_context():
    assert Senha.query.filter_by(codigo="NEW001").first() is not None
    assert Senha.query.filter_by(codigo="OLD001").first() is None
ok("Geração de senha usa a sigla do BOTÃO ('NEW001'), não a sigla deprecada da Fila ('OLD')")

# ===========================================================================
# D) Botão-menu (fila_id vazio) nunca exige sigla - mesmo se o form enviar um
#    valor de sigla, ele é descartado (não faz sentido pra um botão puramente
#    de navegação).
# ===========================================================================
resp = c_admin.post(
    f"/admin/totem-botoes/novo?empresa_id={ids['empresa']}",
    data={"label": "MEDICOS", "unidade_id": ids["unidade"], "fila_id": "", "sigla": "XX"},
    follow_redirects=True,
)
assert resp.status_code == 200
with app.app_context():
    botao_menu = db.session.query(TotemBotao).filter_by(label="MEDICOS").first()
    assert botao_menu is not None
    assert botao_menu.fila_id is None
    assert botao_menu.sigla is None  # descartada - botão-menu nunca tem sigla
ok("Botão-menu (sem fila vinculada) nunca tem sigla, mesmo se o form enviar uma")

# ===========================================================================
# E) Unicidade de sigla por unidade, agora escopada em TotemBotao: dois
#    botões-folha na MESMA unidade com a MESMA sigla é bloqueado.
# ===========================================================================
fila2 = Fila(empresa_id=ids["empresa"], unidade_id=ids["unidade"], nome="Consulta")
with app.app_context():
    db.session.add(fila2)
    db.session.commit()
    id_fila2 = fila2.id

resp = c_admin.post(
    f"/admin/totem-botoes/novo?empresa_id={ids['empresa']}",
    data={"label": "Consulta", "unidade_id": ids["unidade"], "fila_id": id_fila2, "sigla": "NEW"},
)
assert "Já existe um botão com essa sigla nessa unidade".encode("utf-8") in resp.data
with app.app_context():
    assert db.session.query(TotemBotao).filter_by(fila_id=id_fila2).first() is None
ok("Dois botões-folha na mesma unidade com a mesma sigla ('NEW'): segundo bloqueado")

# editar o próprio botão mantendo a mesma sigla continua permitido (exclusão
# do próprio id na checagem de unicidade).
resp = c_admin.post(
    f"/admin/totem-botoes/{id_botao_lab}/editar",
    data={"label": "Laboratorio", "unidade_id": ids["unidade"], "fila_id": ids["fila"], "sigla": "NEW", "ordem": "0"},
    follow_redirects=True,
)
assert resp.status_code == 200
with app.app_context():
    assert db.session.get(TotemBotao, id_botao_lab).sigla == "NEW"
ok("Editar o próprio botão mantendo a mesma sigla não é bloqueado pela checagem de unicidade")

# ===========================================================================
# F) Fila pode ser criada e salva SEM sigla e sem botão vinculado (órfã) - o
#    formulário de fila não pede mais o campo.
# ===========================================================================
resp = c_admin.post(
    f"/admin/filas/nova?empresa_id={ids['empresa']}",
    data={
        "nome": "Fila Orfa", "unidade_id": ids["unidade"], "digitos_sequencial": "3",
        "prioridade_padrao": "0", "prioridade_rodizio": "0",
    },
    follow_redirects=True,
)
assert resp.status_code == 200
with app.app_context():
    fila_orfa = Fila.query.filter_by(nome="Fila Orfa").first()
    assert fila_orfa is not None
    assert fila_orfa.sigla is None
    assert TotemBotao.query.filter_by(fila_id=fila_orfa.id).first() is None
ok("Fila criada sem sigla e sem botão vinculado (órfã) - tela de filas não quebra nem exige o campo")

# ===========================================================================
# G) Migração de dados (script da revision 7a2e1c4f9b3d) simulada: um cenário
#    "pré-migração" (botão-folha já existente com sigla=None, fila com sigla
#    antiga preenchida) tem sua sigla copiada corretamente da fila.
# ===========================================================================
with app.app_context():
    fila_legado = Fila(empresa_id=ids["empresa"], unidade_id=ids["unidade"], nome="Fila Legado", sigla="LEG")
    db.session.add(fila_legado)
    db.session.commit()

    # simula o estado ANTES da migration 7a2e1c4f9b3d rodar: botão-folha já
    # vinculado à fila, mas sem sigla própria ainda (coluna nova, NULL).
    botao_legado = TotemBotao(
        empresa_id=ids["empresa"], unidade_id=ids["unidade"], label="Legado",
        fila_id=fila_legado.id, sigla=None,
    )
    db.session.add(botao_legado)
    db.session.commit()
    id_botao_legado = botao_legado.id

    # --- mesma lógica de cópia da migration (fila.sigla -> botao.sigla para
    # cada botão-folha existente) ---------------------------------------
    for botao in TotemBotao.query.filter(TotemBotao.fila_id.isnot(None)).all():
        if botao.sigla is None:
            fila_de_origem = db.session.get(Fila, botao.fila_id)
            if fila_de_origem is not None and fila_de_origem.sigla is not None:
                botao.sigla = fila_de_origem.sigla
    db.session.commit()

    assert db.session.get(TotemBotao, id_botao_legado).sigla == "LEG"
ok("Migração simulada: botão-folha existente sem sigla recebe a sigla antiga da fila vinculada ('LEG')")

# ===========================================================================
# H) RBAC: editar sigla de botão segue o mesmo padrão de permissão já usado
#    no CRUD de botões (admin_bp.before_request) - perfil sem 'admin'/super
#    admin recebe 403.
# ===========================================================================
resp = c_atendente.post(
    f"/admin/totem-botoes/{id_botao_lab}/editar",
    data={"label": "Laboratorio", "unidade_id": ids["unidade"], "fila_id": ids["fila"], "sigla": "NEW", "ordem": "0"},
)
assert resp.status_code == 403
ok("RBAC: usuário sem perfil 'admin' recebe 403 ao tentar editar a sigla de um botão")

print()
print(f"TODOS OS {len(passos)} TESTES DE SIGLA NO BOTAO DO TOTEM PASSARAM")
