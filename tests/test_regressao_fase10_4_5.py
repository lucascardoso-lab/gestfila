"""
Teste de regressão das seções 10.4 (Tipos de senha configuráveis) e 10.5
(Navegação multi-nível do totem + quantidade de acompanhantes) do BLUEPRINT:

- CRUD administrativo de tipos_senha, com isolamento multi-tenant;
- geração de senha com tipo selecionado (regra de composição do código,
  peso_prioridade alimentando o rodízio de prioridade já existente);
- navegação em 3 níveis no totem (raiz -> submenu -> submenu -> folha);
- geração de senha ainda aceita/valida qtd_acompanhantes no back-end (campo
  informativo, seção 10.5), mesmo com a pergunta removida da tela do totem;
- fluxo combinado completo, sem toque extra de confirmação (pedido do cliente):
  botão-folha já gera direto quando a fila tem 0 ou 1 tipo ativo; com 2+ tipos,
  a escolha do tipo já gera direto - a tela "Confirmar atendimento" continua
  existindo só como fallback de rota (link antigo/bookmarked), não faz mais
  parte do caminho normal da UI.

Execução: python -m tests.test_regressao_fase10_4_5 (a partir da raiz do projeto,
com o virtualenv ativado).
"""
import os

os.environ.setdefault("SECRET_KEY", "test")
os.environ.setdefault("FLASK_ENV", "testing")

from app import create_app
from app.extensions import db
from app.models import Empresa, Fila, Perfil, Senha, TipoSenha, Totem, TotemBotao, Unidade, Usuario

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
    db.session.add(unidade_a1)
    db.session.commit()

    def novo_usuario(login, empresa_id, perfis):
        u = Usuario(empresa_id=empresa_id, nome=login, login=login)
        u.set_senha("senha123")
        u.perfis = perfis
        db.session.add(u)
        db.session.commit()
        return u.id

    id_admin_a = novo_usuario("admin_a4", empresa_a.id, [perfil_admin])
    id_admin_b = novo_usuario("admin_b4", empresa_b.id, [perfil_admin])

    # catálogo de tipos de senha (empresa A) - pesos distintos para testar o
    # desempate por peso dentro do rodízio de prioridade.
    tipo_idoso = TipoSenha(empresa_id=empresa_a.id, nome="Idoso", prefixo="I", cor="#E53935", peso_prioridade=5)
    tipo_gestante = TipoSenha(empresa_id=empresa_a.id, nome="Gestante", prefixo="G", cor="#8E24AA", peso_prioridade=3)
    tipo_normal = TipoSenha(empresa_id=empresa_a.id, nome="Normal", prefixo="N", peso_prioridade=0)
    db.session.add_all([tipo_idoso, tipo_gestante, tipo_normal])
    db.session.commit()

    # Fila simples, sem tipos vinculados - deve gerar direto, sem tela de escolha.
    fila_lab = Fila(
        empresa_id=empresa_a.id, unidade_id=unidade_a1.id, nome="Laboratorio", sigla="LAB",
        digitos_sequencial=3, prioridade_rodizio=0,
    )
    # Fila com 3 tipos ativos vinculados - deve mostrar a tela de escolha.
    fila_consulta = Fila(
        empresa_id=empresa_a.id, unidade_id=unidade_a1.id, nome="Consulta", sigla="CON",
        digitos_sequencial=3, prioridade_rodizio=1,
    )
    # Fila com só 1 tipo ativo vinculado - deve pular a tela de escolha, mas ainda
    # assim atribuir esse tipo automaticamente à senha.
    fila_unico = Fila(
        empresa_id=empresa_a.id, unidade_id=unidade_a1.id, nome="Exame Unico", sigla="UNI",
        digitos_sequencial=3,
    )
    db.session.add_all([fila_lab, fila_consulta, fila_unico])
    db.session.commit()

    fila_consulta.tipos_senha = [tipo_idoso, tipo_gestante, tipo_normal]
    fila_unico.tipos_senha = [tipo_gestante]
    db.session.commit()

    # hierarquia de 3 níveis no totem: raiz (menu) -> submenu (menu) -> folha (gera senha).
    nivel1 = TotemBotao(empresa_id=empresa_a.id, unidade_id=unidade_a1.id, label="CONSULTORIOS", ordem=1)
    db.session.add(nivel1)
    db.session.commit()

    nivel2 = TotemBotao(
        empresa_id=empresa_a.id, unidade_id=unidade_a1.id, label="QUIMIO/MEZANINO1", parent_id=nivel1.id, ordem=1,
    )
    db.session.add(nivel2)
    db.session.commit()

    nivel3_folha = TotemBotao(
        empresa_id=empresa_a.id, unidade_id=unidade_a1.id, label="ADMINISTRACAO M1",
        parent_id=nivel2.id, fila_id=fila_lab.id, sigla="LAB", ordem=1,
    )
    db.session.add(nivel3_folha)
    db.session.commit()

    # botões-folha diretos na raiz, para as filas com tipos.
    botao_consulta = TotemBotao(
        empresa_id=empresa_a.id, unidade_id=unidade_a1.id, label="Atendimento Geral",
        fila_id=fila_consulta.id, sigla="CON", ordem=2,
    )
    botao_unico = TotemBotao(
        empresa_id=empresa_a.id, unidade_id=unidade_a1.id, label="Exame Unico",
        fila_id=fila_unico.id, sigla="UNI", ordem=3,
    )
    db.session.add_all([botao_consulta, botao_unico])
    db.session.commit()

    # seção 10.6: o totem físico agora é identificado na própria URL pública
    totem_a1 = Totem(empresa_id=empresa_a.id, unidade_id=unidade_a1.id, nome="Totem A1", slug="totem-a1")
    db.session.add(totem_a1)
    db.session.commit()

    ids = dict(
        empresa_a=empresa_a.id, empresa_b=empresa_b.id, unidade_a1=unidade_a1.id, totem_a1=totem_a1.id,
        admin_a=id_admin_a, admin_b=id_admin_b,
        tipo_idoso=tipo_idoso.id, tipo_gestante=tipo_gestante.id, tipo_normal=tipo_normal.id,
        fila_lab=fila_lab.id, fila_consulta=fila_consulta.id, fila_unico=fila_unico.id,
        nivel1=nivel1.id, nivel2=nivel2.id, nivel3_folha=nivel3_folha.id,
        botao_consulta=botao_consulta.id, botao_unico=botao_unico.id,
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


c_admin_a = login_como("admin_a4")
c_admin_b = login_como("admin_b4")
c_totem = app.test_client()

# ===========================================================================
# A) CRUD administrativo de tipos_senha (isolamento multi-tenant)
# ===========================================================================
resp = c_admin_a.get("/admin/tipos-senha")
assert resp.status_code == 200
texto = resp.data.decode("utf-8")
assert "Idoso" in texto and "Gestante" in texto and "Normal" in texto
ok("Listagem: admin da empresa A vê os tipos de senha da própria empresa")

resp = c_admin_a.post(
    f"/admin/tipos-senha/novo?empresa_id={ids['empresa_a']}",
    data={"nome": "", "prefixo": "P", "cor": "#000000", "peso_prioridade": "1"},
)
assert "Informe o nome".encode("utf-8") in resp.data
ok("Criação: nome vazio é rejeitado")

resp = c_admin_a.post(
    f"/admin/tipos-senha/novo?empresa_id={ids['empresa_a']}",
    data={"nome": "PCD", "prefixo": "P", "cor": "vermelho", "peso_prioridade": "2"},
)
assert "formato hexadecimal".encode("utf-8") in resp.data
ok("Criação: cor fora do formato hexadecimal é rejeitada")

resp = c_admin_a.post(
    f"/admin/tipos-senha/novo?empresa_id={ids['empresa_a']}",
    data={"nome": "PCD", "prefixo": "PCD", "cor": "#1E88E5", "peso_prioridade": "2"},
)
assert "1 ou 2 letras".encode("utf-8") in resp.data
ok("Criação: prefixo com mais de 2 letras é rejeitado")

resp = c_admin_a.post(
    f"/admin/tipos-senha/novo?empresa_id={ids['empresa_a']}",
    data={"nome": "PCD", "prefixo": "p", "cor": "#1E88E5", "peso_prioridade": "4"},
    follow_redirects=True,
)
assert resp.status_code == 200
with app.app_context():
    tipo_pcd = TipoSenha.query.filter_by(empresa_id=ids["empresa_a"], nome="PCD").first()
    assert tipo_pcd is not None and tipo_pcd.prefixo == "P" and tipo_pcd.ativo is True
    id_tipo_pcd = tipo_pcd.id
ok("Criação: caminho feliz grava nome/prefixo (maiusculizado)/cor/peso, ativo por padrão")

resp = c_admin_b.get(f"/admin/tipos-senha/{ids['tipo_idoso']}/editar")
assert resp.status_code == 404
ok("Isolamento: admin de outra empresa recebe 404 ao editar tipo de senha da empresa A")

resp = c_admin_b.post(f"/admin/tipos-senha/{ids['tipo_idoso']}/toggle-ativo")
assert resp.status_code == 404
with app.app_context():
    assert db.session.get(TipoSenha, ids["tipo_idoso"]).ativo is True
ok("Isolamento: admin de outra empresa recebe 404 ao ativar/desativar tipo de senha da empresa A, nada muda")

resp = c_admin_a.post(f"/admin/tipos-senha/{id_tipo_pcd}/toggle-ativo", follow_redirects=True)
assert resp.status_code == 200
with app.app_context():
    assert db.session.get(TipoSenha, id_tipo_pcd).ativo is False
ok("Toggle: admin da própria empresa desativa o tipo de senha")

print()

# ===========================================================================
# B) Navegação multi-nível no totem (3 níveis: raiz -> submenu -> submenu -> folha)
# ===========================================================================
resp = c_totem.get(f"/totem/totem-a1")
assert resp.status_code == 200
texto_raiz = resp.data.decode("utf-8")
assert "CONSULTORIOS" in texto_raiz
assert "ADMINISTRACAO M1" not in texto_raiz
ok("Nível 1 (raiz): mostra o menu CONSULTORIOS, não revela folhas de níveis mais profundos")

# Pedido do cliente: tirar o toque extra de "confirmar" do fluxo do totem.
# Botão-folha de fila com 1 único tipo ativo (Exame Unico -> fila_unico,
# só "Gestante") já gera direto (POST pra gerar_senha, com o tipo fixado
# como hidden input) - nunca mais linka pra /confirmar/.
assert f'action="/totem/totem-a1/gerar/{ids["botao_unico"]}"' in texto_raiz
assert f'value="{ids["tipo_gestante"]}"' in texto_raiz
assert f'/totem/totem-a1/confirmar/{ids["botao_unico"]}' not in texto_raiz
# Botão-folha de fila com 2+ tipos ativos (Atendimento Geral -> fila_consulta)
# continua linkando pra /confirmar/ - aí SIM precisa de uma escolha real.
assert f'href="/totem/totem-a1/confirmar/{ids["botao_consulta"]}"' in texto_raiz
ok("Menu raiz: botão de fila com 1 tipo já gera direto (form POST); botão com 2+ tipos continua linkando pra escolha")

resp = c_totem.get(f"/totem/totem-a1/menu/{ids['nivel1']}")
assert resp.status_code == 200
assert "QUIMIO/MEZANINO1" in resp.data.decode("utf-8")
ok("Nível 2 (submenu): mostra QUIMIO/MEZANINO1 dentro de CONSULTORIOS")

resp = c_totem.get(f"/totem/totem-a1/menu/{ids['nivel2']}")
assert resp.status_code == 200
texto = resp.data.decode("utf-8")
assert "ADMINISTRACAO M1" in texto
ok("Nível 3 (submenu de submenu): mostra o botão-folha ADMINISTRACAO M1")

# fila_lab não tem NENHUM tipo de senha vinculado - botão também já gera direto
# (form POST, sem hidden de tipo_senha_id nenhum) - sem tela de confirmação.
assert f'action="/totem/totem-a1/gerar/{ids["nivel3_folha"]}"' in texto
assert f'/totem/totem-a1/confirmar/{ids["nivel3_folha"]}' not in texto
ok("Botão de fila sem tipo de senha algum também já gera direto (form POST), sem tela de confirmação")

print()

# ===========================================================================
# C) Fluxo simples (fila sem tipo de senha) - a rota /confirmar/ (fallback,
#    ver seção B acima pro caminho normal via form POST) continua resolvendo
#    sem tela de escolha de tipo.
# ===========================================================================
resp = c_totem.get(f"/totem/totem-a1/confirmar/{ids['nivel3_folha']}")
assert resp.status_code == 200
texto = resp.data.decode("utf-8")
assert "Confirmar atendimento" in texto
assert "Escolha o tipo" not in texto.replace("Escolha o tipo de atendimento", "")
ok("Fallback /confirmar/: fila sem tipo de senha vinculado também resolve direto, sem tela de escolha")

resp = c_totem.post(
    f"/totem/totem-a1/gerar/{ids['nivel3_folha']}",
    data={"qtd_acompanhantes": "4"},
    follow_redirects=True,
)
assert resp.status_code == 200
assert "LAB001" in resp.data.decode("utf-8")
with app.app_context():
    senha_lab = Senha.query.filter_by(codigo="LAB001").first()
    assert senha_lab is not None and senha_lab.tipo_senha_id is None and senha_lab.qtd_acompanhantes == 4
ok("Geração sem tipo: código permanece 'LAB001', qtd_acompanhantes=4 é capturado")

resp = c_totem.post(
    f"/totem/totem-a1/gerar/{ids['nivel3_folha']}", data={"qtd_acompanhantes": "7"}
)
assert resp.status_code == 400
ok("Geração: qtd_acompanhantes fora da faixa 0-6 é rejeitada (400)")

print()

# ===========================================================================
# D) Fluxo combinado completo - fila com 1 único tipo ativo (pula a tela de
#    escolha, mas atribui o tipo automaticamente).
# ===========================================================================
resp = c_totem.get(f"/totem/totem-a1/confirmar/{ids['botao_unico']}")
assert resp.status_code == 200
texto = resp.data.decode("utf-8")
assert "Confirmar atendimento" in texto
assert "Gestante" in texto  # tipo único já é mostrado/confirmado automaticamente
ok("Fallback /confirmar/: fila com 1 único tipo ativo também resolve direto, com o tipo já atribuído")

resp = c_totem.post(
    f"/totem/totem-a1/gerar/{ids['botao_unico']}",
    data={"tipo_senha_id": str(ids["tipo_gestante"]), "qtd_acompanhantes": "1"},
    follow_redirects=True,
)
assert resp.status_code == 200
with app.app_context():
    senha_unico = Senha.query.filter_by(fila_id=ids["fila_unico"]).first()
    assert senha_unico.codigo == "UNIG001"  # peso > 0 -> prefixo sempre aparece
    assert senha_unico.tipo_senha_id == ids["tipo_gestante"]
    assert senha_unico.prioridade is True  # peso_prioridade do tipo (3) > 0
    assert senha_unico.qtd_acompanhantes == 1
ok("Geração com tipo único: código 'UNIG001', prioridade=True (peso>0), acompanhantes=1 gravados")

print()

# ===========================================================================
# E) Fluxo combinado completo - fila com 2+ tipos ativos (mostra a tela de
#    escolha; escolher o tipo já gera a senha direto, sem tela de confirmação
#    - pedido do cliente pra tirar o toque extra do fluxo do totem).
# ===========================================================================
resp = c_totem.get(f"/totem/totem-a1/confirmar/{ids['botao_consulta']}")
assert resp.status_code == 200
texto = resp.data.decode("utf-8")
assert "Escolha o tipo de atendimento" in texto or "tipo de atendimento" in texto
assert "Idoso" in texto and "Gestante" in texto and "Normal" in texto
assert "Confirmar atendimento" not in texto
ok("Fila com 2+ tipos ativos: mostra a tela de escolha de tipo, NÃO a confirmação ainda")

# cada tipo, na tela de escolha, é um form POST direto pra gerar_senha (com o
# tipo já fixado como hidden input) - nunca mais um link pra /confirmar/.
assert f'action="/totem/totem-a1/gerar/{ids["botao_consulta"]}"' in texto
assert texto.count(f'action="/totem/totem-a1/gerar/{ids["botao_consulta"]}"') == 3  # Idoso, Gestante, Normal
assert f'value="{ids["tipo_idoso"]}"' in texto
assert f'value="{ids["tipo_gestante"]}"' in texto
assert f'value="{ids["tipo_normal"]}"' in texto
assert f'/totem/totem-a1/confirmar/{ids["botao_consulta"]}?tipo_senha_id=' not in texto
ok("Tela de escolha de tipo: cada opção já é um form POST direto pra gerar_senha, sem link pra /confirmar/")

# a rota /confirmar/ com ?tipo_senha_id= continua funcionando (fallback/robustez -
# ex. link antigo salvo/favoritado), mesmo não sendo mais o caminho que a UI usa.
resp = c_totem.get(
    f"/totem/totem-a1/confirmar/{ids['botao_consulta']}?tipo_senha_id={ids['tipo_idoso']}"
)
assert resp.status_code == 200
texto = resp.data.decode("utf-8")
assert "Confirmar atendimento" in texto
assert "Idoso" in texto
ok("Ao escolher 'Idoso', a próxima tela é a confirmação, com o tipo já fixado")

resp = c_totem.post(
    f"/totem/totem-a1/gerar/{ids['botao_consulta']}",
    data={"tipo_senha_id": str(ids["tipo_idoso"]), "qtd_acompanhantes": "2"},
    follow_redirects=True,
)
assert resp.status_code == 200
with app.app_context():
    senha_idoso = Senha.query.filter_by(codigo="CONI001").first()
    assert senha_idoso is not None
    assert senha_idoso.tipo_senha_id == ids["tipo_idoso"]
    assert senha_idoso.prioridade is True
    assert senha_idoso.qtd_acompanhantes == 2
ok("Geração combinada (tipo 'Idoso' + 2 acompanhantes): código 'CONI001', prioridade=True, dados corretos")

# tipo "Normal" (peso 0): sem exibir_prefixo_tipo_quando_normal (default False) -> sem prefixo.
resp = c_totem.post(
    f"/totem/totem-a1/gerar/{ids['botao_consulta']}",
    data={"tipo_senha_id": str(ids["tipo_normal"]), "qtd_acompanhantes": "0"},
    follow_redirects=True,
)
assert resp.status_code == 200
with app.app_context():
    senha_normal = Senha.query.filter_by(fila_id=ids["fila_consulta"], tipo_senha_id=ids["tipo_normal"]).first()
    assert senha_normal.codigo == "CON002"  # sem o prefixo "N" - tipo padrão, flag desligada
    assert senha_normal.prioridade is False
ok("Tipo 'Normal' (peso 0) sem exibir_prefixo_tipo_quando_normal: código sem prefixo ('CON002')")

# liga a flag e gera de novo - agora o prefixo "N" aparece.
with app.app_context():
    fila_consulta_obj = db.session.get(Fila, ids["fila_consulta"])
    fila_consulta_obj.exibir_prefixo_tipo_quando_normal = True
    db.session.commit()

resp = c_totem.post(
    f"/totem/totem-a1/gerar/{ids['botao_consulta']}",
    data={"tipo_senha_id": str(ids["tipo_normal"]), "qtd_acompanhantes": "0"},
    follow_redirects=True,
)
assert resp.status_code == 200
with app.app_context():
    senhas_normais = Senha.query.filter_by(fila_id=ids["fila_consulta"], tipo_senha_id=ids["tipo_normal"]).all()
    assert any(s.codigo == "CONN003" for s in senhas_normais)
ok("Com exibir_prefixo_tipo_quando_normal=True, o prefixo 'N' do tipo padrão passa a aparecer ('CONN003')")

# tipo_senha_id de outra fila/inexistente é rejeitado (não pertence à fila_consulta).
resp = c_totem.post(
    f"/totem/totem-a1/gerar/{ids['botao_consulta']}",
    data={"tipo_senha_id": "999999", "qtd_acompanhantes": "0"},
)
assert resp.status_code == 400
ok("Geração: tipo_senha_id que não pertence à fila (ou inexistente) é rejeitado (400)")

print()

# ===========================================================================
# F) peso_prioridade alimenta o rodízio de prioridade já existente (seção 10.4) -
#    dentro do grupo de prioritárias, quem pesa mais é chamado primeiro, mesmo
#    tendo sido gerado depois (desempate por peso, não só FIFO).
# ===========================================================================
from app.atendimento.helpers import selecionar_proxima_senha  # noqa: E402

with app.app_context():
    fila_rodizio = Fila(
        empresa_id=ids["empresa_a"], unidade_id=ids["unidade_a1"], nome="Rodizio Peso", sigla="ROD",
        digitos_sequencial=3, prioridade_rodizio=5,
    )
    db.session.add(fila_rodizio)
    db.session.commit()
    fila_rodizio.tipos_senha = [
        db.session.get(TipoSenha, ids["tipo_idoso"]), db.session.get(TipoSenha, ids["tipo_gestante"]),
    ]
    db.session.commit()
    id_fila_rodizio = fila_rodizio.id

    from datetime import datetime, timedelta

    base_t = datetime.utcnow() - timedelta(minutes=5)
    senha_gestante_primeiro = Senha(
        empresa_id=ids["empresa_a"], unidade_id=ids["unidade_a1"], fila_id=id_fila_rodizio,
        codigo="ROD001", sequencial=1, prioridade=True, status="aguardando",
        tipo_senha_id=ids["tipo_gestante"], criada_em=base_t,
    )
    senha_idoso_depois = Senha(
        empresa_id=ids["empresa_a"], unidade_id=ids["unidade_a1"], fila_id=id_fila_rodizio,
        codigo="ROD002", sequencial=2, prioridade=True, status="aguardando",
        tipo_senha_id=ids["tipo_idoso"], criada_em=base_t + timedelta(seconds=30),
    )
    db.session.add_all([senha_gestante_primeiro, senha_idoso_depois])
    db.session.commit()

    fila_rodizio_obj = db.session.get(Fila, id_fila_rodizio)
    proxima = selecionar_proxima_senha(fila_rodizio_obj)
    assert proxima.codigo == "ROD002"  # Idoso (peso 5) furou a frente de Gestante (peso 3), apesar de ter chegado depois
ok("peso_prioridade alimenta o rodízio: dentro das prioritárias, maior peso é chamado primeiro (não só FIFO)")

print()
print(f"TODOS OS {len(passos)} TESTES DAS SEÇÕES 10.4 E 10.5 (TIPOS DE SENHA + MULTI-NÍVEL/ACOMPANHANTES) PASSARAM")
