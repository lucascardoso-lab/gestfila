"""
Teste de regressão: campo "Sigla" no cadastro de Unidade + atualização
opcional do prefixo dos Locais (adendo 10.71).

Cobre:
1) Formato: 2 a 4 caracteres, só A-Z (sem acento) e dígitos, gravada em
   maiúsculas; vazio continua permitido.
2) Unicidade por empresa, case-insensitive; sigla igual à sigla EFETIVA
   (inclusive automática, com acento: "ANA" x "ANÁ") de OUTRA unidade é
   rejeitada; a própria sigla automática ("MAT" na Matriz) é aceita; editar
   uma unidade que JÁ colide no fallback (sem mexer na sigla) continua
   permitido; UniqueConstraint (empresa_id, sigla) no banco.
3) Listagem: sigla efetiva, "(automática)", colisão entre automáticas e aviso
   passivo de locais com prefixo antigo (com links).
4) Rótulo de fila muda na hora após salvar a sigla.
5) Locais: salvar a sigla leva à prévia, que NÃO altera nada; confirmação com
   uma colisão não grava nenhum local (sigla continua salva); confirmação ok
   troca só o prefixo exato, remove " (id)" só quando é o id do próprio local
   e o resultado continua único, preserva "(sala 2)", outro "(99)" e texto
   editado à mão; locais de outras unidades não mudam.
6) Limpar a sigla: rejeitado se o fallback colidir com outra unidade; aceito
   quando não colide, com o mesmo fluxo de prévia voltando ao prefixo
   automático.
7) RBAC da rota nova igual ao do cadastro de Unidade (403 sem perfil admin,
   404 para unidade de outra empresa).

Execução: python -m tests.test_regressao_sigla_unidade (a partir da raiz do
projeto, com o virtualenv ativado).
"""
import os

os.environ.setdefault("SECRET_KEY", "test")
os.environ.setdefault("FLASK_ENV", "testing")

import html
from urllib.parse import unquote

from sqlalchemy.exc import IntegrityError

from app import create_app
from app.extensions import db
from app.models import Empresa, Fila, Local, Perfil, Unidade, Usuario
from app.utils.rotulos import rotulos_filas

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

    empresa = Empresa(nome="Clinica Siglas")
    empresa_b = Empresa(nome="Clinica B")
    db.session.add_all([empresa, empresa_b])
    db.session.commit()

    def nova_unidade(emp, nome):
        u = Unidade(empresa_id=emp.id, nome=nome)
        db.session.add(u)
        db.session.commit()
        return u

    matriz = nova_unidade(empresa, "Matriz")
    laranjeiras = nova_unidade(empresa, "Laranjeiras")
    ana_cm = nova_unidade(empresa, "Anápolis - Centro Médico")
    ana_lab = nova_unidade(empresa, "Anápolis - Laboratório")
    unidade_b = nova_unidade(empresa_b, "Unidade B")

    coleta_cm = Fila(empresa_id=empresa.id, unidade_id=ana_cm.id, nome="Coleta")
    coleta_lab = Fila(empresa_id=empresa.id, unidade_id=ana_lab.id, nome="Coleta")
    db.session.add_all([coleta_cm, coleta_lab])
    db.session.commit()

    def novo_local(unidade, identificador):
        l = Local(
            empresa_id=unidade.empresa_id, unidade_id=unidade.id, nome="x",
            nome_exibicao="x", identificador_admin=identificador,
        )
        db.session.add(l)
        db.session.commit()
        return l

    l_guiche1 = novo_local(ana_cm, "[ANÁ] Guichê 1")
    l_guiche2 = novo_local(ana_cm, "[ANÁ] Guichê 2 (tmp)")
    l_guiche2.identificador_admin = f"[ANÁ] Guichê 2 ({l_guiche2.id})"  # padrão gerado pela migração 10.32
    # Sem o sufixo viraria "[ACM] Guichê 1" - igual ao Guichê 1 -> o sufixo fica.
    l_guiche1_dup = novo_local(ana_cm, "[ANÁ] Guichê 1 (tmp)")
    l_guiche1_dup.identificador_admin = f"[ANÁ] Guichê 1 ({l_guiche1_dup.id})"
    db.session.commit()
    l_sala = novo_local(ana_cm, "[ANÁ] Recepção (sala 2)")
    l_outro_id = novo_local(ana_cm, "[ANÁ] Triagem (99)")  # parêntese que NÃO é o id do próprio local
    l_mao = novo_local(ana_cm, "[ANÁ] Coleta editado à mão XYZ")
    l_lab = novo_local(ana_lab, "[ANÁ] Guichê 3")
    l_matriz = novo_local(matriz, "[MAT] Guichê 1")
    # Identificador de OUTRA unidade que vai colidir com o novo "[ACM] Guichê 1".
    l_intruso = novo_local(laranjeiras, "[ACM] Guichê 1")

    def novo_usuario(emp, login, perfil):
        u = Usuario(empresa_id=emp.id, nome=login, login=login)
        u.set_senha("senha123")
        u.perfis = [perfil]
        db.session.add(u)
        db.session.commit()

    novo_usuario(empresa, "admin_siglas", perfil_admin)
    novo_usuario(empresa, "atendente_siglas", perfil_atendente)
    novo_usuario(empresa_b, "admin_b_siglas", perfil_admin)

    ids = dict(
        empresa=empresa.id, empresa_b=empresa_b.id, matriz=matriz.id, laranjeiras=laranjeiras.id, ana_cm=ana_cm.id,
        ana_lab=ana_lab.id, unidade_b=unidade_b.id, coleta_cm=coleta_cm.id, coleta_lab=coleta_lab.id,
        guiche1=l_guiche1.id, guiche2=l_guiche2.id, guiche1_dup=l_guiche1_dup.id, sala=l_sala.id,
        outro_id=l_outro_id.id, mao=l_mao.id, lab=l_lab.id, matriz_local=l_matriz.id, intruso=l_intruso.id,
    )


def login_como(login):
    c = app.test_client()
    resp = c.post("/login", data={"login": login, "senha": "senha123"}, follow_redirects=True)
    assert resp.status_code == 200, (login, resp.status_code)
    return c


def texto(resp):
    return html.unescape(resp.get_data(as_text=True))


def unidade(id_):
    with app.app_context():
        return db.session.get(Unidade, id_)


def identificadores():
    with app.app_context():
        return {l.id: l.identificador_admin for l in Local.query.all()}


def form_unidade(u, **mudancas):
    dados = {"nome": u.nome, "sigla": u.sigla or "", "endereco": u.endereco or "", "telefone": u.telefone or ""}
    dados.update(mudancas)
    return dados


c = login_como("admin_siglas")
URL_EDITAR = "/admin/unidades/{}/editar"

# ===========================================================================
# 1) Formato da sigla.
# ===========================================================================
for invalida in ("A", "ABCDE", "MÁ", "A-B", "A B"):
    r = c.post("/admin/unidades/nova", data={"nome": f"Teste {invalida}", "sigla": invalida})
    assert r.status_code == 200 and "A sigla deve ter de 2 a 4 caracteres" in texto(r), invalida
    assert f'value="{invalida.upper()}"' in texto(r) or invalida == "A B"  # mantém o que foi digitado
with app.app_context():
    assert Unidade.query.filter(Unidade.nome.like("Teste %")).count() == 0
ok("Formato: rejeita 1 caractere, 5 caracteres, acento, hífen e espaço (nada é criado)")

r = c.post("/admin/unidades/nova", data={"nome": "Perimetral", "sigla": " pr9 "})
assert r.status_code == 302, r.status_code
with app.app_context():
    perimetral = Unidade.query.filter_by(nome="Perimetral").one()
    assert perimetral.sigla == "PR9"
    ids["perimetral"] = perimetral.id
r = c.post("/admin/unidades/nova", data={"nome": "Caldas Novas", "sigla": ""})
assert r.status_code == 302
with app.app_context():
    assert Unidade.query.filter_by(nome="Caldas Novas").one().sigla is None
ok("Formato: ' pr9 ' é gravada como 'PR9'; sigla vazia continua permitida (NULL, cai no fallback)")

# ===========================================================================
# 2) Unicidade e colisão com a sigla efetiva de outra unidade.
# ===========================================================================
r = c.post("/admin/unidades/nova", data={"nome": "Outra", "sigla": "pr9"})
assert "A sigla PR9 já é usada pela unidade Perimetral." in texto(r)
ok("Unicidade case-insensitive: 'pr9' rejeitada porque 'PR9' já existe (mensagem clara)")

r = c.post(URL_EDITAR.format(ids["laranjeiras"]), data=form_unidade(unidade(ids["laranjeiras"]), sigla="mat"))
assert "A sigla MAT já é usada (automaticamente) pela unidade Matriz. Cadastre primeiro uma sigla para Matriz." in texto(r)
r = c.post(URL_EDITAR.format(ids["matriz"]), data=form_unidade(unidade(ids["matriz"]), sigla="ana"))
assert "A sigla ANA já é usada (automaticamente) pela unidade Anápolis - " in texto(r)
assert unidade(ids["matriz"]).sigla is None and unidade(ids["laranjeiras"]).sigla is None
ok("Rejeita sigla igual à automática de OUTRA unidade, inclusive ignorando acento ('ANA' x 'ANÁ')")

r = c.post(URL_EDITAR.format(ids["matriz"]), data=form_unidade(unidade(ids["matriz"]), sigla="MAT"))
assert r.status_code == 302 and "/admin/unidades" in r.headers["Location"] and "prefixo-locais" not in r.headers["Location"]
assert unidade(ids["matriz"]).sigla == "MAT"
assert identificadores()[ids["matriz_local"]] == "[MAT] Guichê 1"
ok("Cadastrar a própria sigla automática ('MAT' na Matriz) é permitido e não abre prévia de locais")

r = c.post(URL_EDITAR.format(ids["ana_cm"]), data=form_unidade(unidade(ids["ana_cm"]), endereco="Rua 1"))
assert r.status_code == 302 and unidade(ids["ana_cm"]).endereco == "Rua 1"
ok("Editar outro campo de uma unidade que JÁ colide no fallback ('ANÁ' x 'ANÁ') continua permitido")

r = c.post(URL_EDITAR.format(ids["laranjeiras"]), data=form_unidade(unidade(ids["laranjeiras"]), nome="Matriz Sul"))
assert "Sem sigla, esta unidade usaria a sigla automática MAT, que já é usada pela unidade Matriz. Cadastre uma sigla para esta unidade." in texto(r)
assert unidade(ids["laranjeiras"]).nome == "Laranjeiras"
r = c.post(URL_EDITAR.format(ids["laranjeiras"]), data=form_unidade(unidade(ids["laranjeiras"]), nome="Laranjeiras II"))
assert r.status_code == 302 and unidade(ids["laranjeiras"]).nome == "Laranjeiras II"  # fallback continua LAR
c.post(URL_EDITAR.format(ids["laranjeiras"]), data=form_unidade(unidade(ids["laranjeiras"]), nome="Laranjeiras"))
ok("Renomear unidade SEM sigla para um nome cujo fallback colide ('Matriz Sul' -> MAT) é rejeitado")

r = c.post("/admin/unidades/nova", data={"nome": "Anápolis - Norte", "sigla": ""})
assert "Sem sigla, esta unidade usaria a sigla automática ANÁ, que já é usada pela unidade Anápolis - " in texto(r)
ok("Nova unidade sem sigla cujo fallback colidiria é rejeitada (pede para cadastrar uma sigla)")

with app.app_context():
    db.session.add(Unidade(empresa_id=ids["empresa"], nome="Duplicada", sigla="PR9"))
    try:
        db.session.commit()
        raise AssertionError("UniqueConstraint (empresa_id, sigla) não existe")
    except IntegrityError:
        db.session.rollback()
    db.session.add(Unidade(empresa_id=ids["empresa_b"], nome="Mesma sigla em outra empresa", sigla="PR9"))
    db.session.commit()
ok("UniqueConstraint (empresa_id, sigla) no banco; a mesma sigla em OUTRA empresa é permitida")

# ===========================================================================
# 3) Listagem: sigla efetiva, "(automática)", colisão entre automáticas.
# ===========================================================================
lista = " ".join(texto(c.get(f"/admin/unidades?empresa_id={ids['empresa']}")).split())
assert '<td class="sigla-unidade"> MAT </td>' in lista  # cadastrada: sem "(automática)"
assert "ANÁ <span class=\"subtitulo\">(automática)</span>" in lista
assert "LAR <span class=\"subtitulo\">(automática)</span>" in lista
assert "igual à de Anápolis - Laboratório - cadastre uma sigla" in lista
assert "igual à de Anápolis - Centro Médico - cadastre uma sigla" in lista
ok("Listagem: sigla efetiva, '(automática)' quando é fallback e aviso das duas 'ANÁ' iguais")

# ===========================================================================
# 4) + 5) Cadastrar a sigla do Centro Médico: rótulo muda na hora; prévia.
# ===========================================================================
with app.app_context():
    assert rotulos_filas(ids["empresa"])[ids["coleta_cm"]] == f"[ANÁ] Coleta ({ids['coleta_cm']})"
antes = identificadores()

r = c.post(URL_EDITAR.format(ids["ana_cm"]), data=form_unidade(unidade(ids["ana_cm"]), sigla="acm"))
assert r.status_code == 302
destino = unquote(r.headers["Location"])
assert f"/admin/unidades/{ids['ana_cm']}/prefixo-locais" in destino and "prefixo_antigo=ANÁ" in destino, destino
assert unidade(ids["ana_cm"]).sigla == "ACM"
assert identificadores() == antes
ok("Salvar a sigla grava a unidade e redireciona para a prévia, sem tocar em nenhum local")

with app.app_context():
    rotulos = rotulos_filas(ids["empresa"])
assert rotulos[ids["coleta_cm"]] == "[ACM] Coleta" and rotulos[ids["coleta_lab"]] == "[ANÁ] Coleta", rotulos
rel = texto(c.get("/relatorios/volume"))
assert ">[ACM] Coleta</option>" in rel and ">[ANÁ] Coleta</option>" in rel
ok("Rótulo de fila muda na hora: '[ACM] Coleta' e a outra perde o ' (id)' (não colide mais)")

previa = texto(c.get(r.headers["Location"]))
assert "[ANÁ] Guichê 1" in previa and "[ACM] Guichê 1" in previa
assert "já usado pelo local '[ACM] Guichê 1'" in previa  # colisão já sinalizada na prévia
assert identificadores() == antes
ok("Prévia (GET) mostra antes -> depois e a colisão, sem alterar nenhum local")

lista = texto(c.get(f"/admin/unidades?empresa_id={ids['empresa']}"))
assert "6 locais ainda usam o prefixo [ANÁ]" in lista
assert f"/admin/locais/{ids['guiche1']}/editar" in lista
assert f"/admin/unidades/{ids['ana_cm']}/prefixo-locais?prefixo_antigo=" in lista
assert "1 local ainda usa o prefixo [ACM]" in lista  # o intruso na Laranjeiras
form_edicao = texto(c.get(URL_EDITAR.format(ids["ana_cm"])))
assert "6 locais ainda usam o prefixo [ANÁ]" in form_edicao
ok("Aviso passivo ('Agora não'): listagem e edição mostram os locais com prefixo antigo, com links")

URL_PREFIXO = f"/admin/unidades/{ids['ana_cm']}/prefixo-locais"
r = c.post(URL_PREFIXO, data={"prefixo_antigo": "ANÁ"})
assert r.status_code == 200 and "Nenhum local foi atualizado" in texto(r) and "'[ACM] Guichê 1' já é usado pelo local '[ACM] Guichê 1'" in texto(r)
assert identificadores() == antes and unidade(ids["ana_cm"]).sigla == "ACM"
ok("Confirmação com uma colisão: NENHUM local é gravado (atômico) e a sigla continua salva")

with app.app_context():
    db.session.get(Local, ids["intruso"]).identificador_admin = "[LAR] Guichê 9"
    db.session.commit()
r = c.post(URL_PREFIXO, data={"prefixo_antigo": "ANÁ"})
assert r.status_code == 302, r.status_code
depois = identificadores()
assert depois[ids["guiche1"]] == "[ACM] Guichê 1"
assert depois[ids["guiche2"]] == "[ACM] Guichê 2"  # " (id)" do próprio local removido
assert depois[ids["guiche1_dup"]] == f"[ACM] Guichê 1 ({ids['guiche1_dup']})"  # sem ele colidiria -> mantido
assert depois[ids["sala"]] == "[ACM] Recepção (sala 2)"
assert depois[ids["outro_id"]] == "[ACM] Triagem (99)"
assert depois[ids["mao"]] == "[ACM] Coleta editado à mão XYZ"
assert depois[ids["lab"]] == "[ANÁ] Guichê 3" and depois[ids["matriz_local"]] == "[MAT] Guichê 1"
ok("Confirmação ok: só o prefixo muda; ' (id)' sai só quando é o próprio id e o resultado é único")
ok("'(sala 2)', '(99)' de outro id e texto editado à mão preservados; outras unidades intactas")

lista = texto(c.get(f"/admin/unidades?empresa_id={ids['empresa']}"))
assert "aviso-prefixo-locais" not in lista  # (o flash "...para o prefixo [ACM]" da confirmação pode aparecer)
ok("Depois de atualizar, o aviso passivo some")

# Recalculado no POST: um POST sem nada a fazer (prefixo já trocado) não altera nada.
r = c.post(URL_PREFIXO, data={"prefixo_antigo": "ANÁ"})
assert r.status_code == 302 and identificadores() == depois
ok("POST de confirmação recalcula do banco: repetido, não encontra nada e não altera nada")

# ===========================================================================
# 6) Limpar a sigla.
# ===========================================================================
r = c.post(URL_EDITAR.format(ids["ana_cm"]), data=form_unidade(unidade(ids["ana_cm"]), sigla=""))
assert "Não é possível remover a sigla: sem ela, esta unidade usaria a sigla automática ANÁ, que já é usada pela unidade Anápolis - Laboratório." in texto(r)
assert unidade(ids["ana_cm"]).sigla == "ACM"
ok("Limpar a sigla é rejeitado quando o fallback colidiria com outra unidade")

r = c.post(URL_EDITAR.format(ids["ana_lab"]), data=form_unidade(unidade(ids["ana_lab"]), sigla="ALB"))
assert "prefixo-locais" in r.headers["Location"]  # "Agora não": não confirma
r = c.post(URL_EDITAR.format(ids["ana_cm"]), data=form_unidade(unidade(ids["ana_cm"]), sigla=""))
assert r.status_code == 302 and "prefixo_antigo=ACM" in unquote(r.headers["Location"])
assert unidade(ids["ana_cm"]).sigla is None and unidade(ids["ana_cm"]).sigla_exibicao == "ANÁ"
assert identificadores()[ids["guiche1"]] == "[ACM] Guichê 1"  # nada muda sem confirmar
r = c.post(URL_PREFIXO, data={"prefixo_antigo": "ACM"})
assert r.status_code == 302
final = identificadores()
assert final[ids["guiche1"]] == "[ANÁ] Guichê 1" and final[ids["sala"]] == "[ANÁ] Recepção (sala 2)"
assert final[ids["lab"]] == "[ANÁ] Guichê 3"  # Laboratório recusou ("Agora não") -> intocado
ok("Limpar a sigla (sem colisão) cai no mesmo fluxo: prévia e, confirmado, prefixo volta ao automático")

# ===========================================================================
# 7) RBAC da rota nova.
# ===========================================================================
c_atendente = login_como("atendente_siglas")
assert c_atendente.get(URL_PREFIXO + "?prefixo_antigo=ANÁ").status_code == 403
assert c_atendente.post(URL_PREFIXO, data={"prefixo_antigo": "ANÁ"}).status_code == 403
c_b = login_como("admin_b_siglas")
assert c_b.get(URL_PREFIXO + "?prefixo_antigo=ANÁ").status_code == 404
assert c_b.post(URL_PREFIXO, data={"prefixo_antigo": "ANÁ"}).status_code == 404
assert identificadores() == final
ok("RBAC: 403 sem perfil admin e 404 para unidade de outra empresa (GET e POST da prévia)")

print(f"\n{len(passos)} verificações OK")
