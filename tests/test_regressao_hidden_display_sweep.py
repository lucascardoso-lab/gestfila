"""
Teste de regressão: varredura completa do padrão "display de origem AUTOR
vence [hidden] de origem UA" (achado 3ª e 4ª vez neste projeto - já
corrigido antes em atendimento/_layout.html::.aviso-encerramento e
painel/tv.html::.chamada-badge-prioritario).

Mecanismo do bug: `[hidden] { display: none; }` é uma declaração da folha de
estilo do NAVEGADOR (origem UA). QUALQUER declaração `display:` de origem
AUTOR que combine com o elemento sempre vence uma declaração UA normal na
cascata, independente da especificidade relativa entre os dois seletores -
setar `elemento.hidden = true` via JS segue marcando o atributo certo no DOM,
mas o elemento continua visível se existir um `display` de autor competindo.

Dois casos achados NOVOS nesta varredura (além dos dois já corrigidos antes):
1. `.checkboxes label` (admin/_layout.html, `display: flex`) - quebrava o
   filtro por unidade do checklist "Filas de destino permitidas"
   (admin/locais/form.html, adendo 10.53 item 1: item.hidden = true não
   escondia nada de verdade).
2. `.selecionado-redirecionar` (atendimento/_redirecionar_busca.html,
   `display: flex`) - quebrava o balão "→ Redirecionando para: X" do widget
   de busca, incluído tanto em painel_fila.html quanto painel_local.html.

Execução: python -m tests.test_regressao_hidden_display_sweep (a partir da
raiz do projeto, com o virtualenv ativado).
"""
import os

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

    perfil_admin = Perfil(nome="admin")
    perfil_recepcao = Perfil(nome="atendente")
    db.session.add_all([perfil_admin, perfil_recepcao])
    db.session.commit()

    empresa = Empresa(nome="Clinica Hidden Sweep")
    db.session.add(empresa)
    db.session.commit()

    unidade = Unidade(empresa_id=empresa.id, nome="Unidade 1")
    db.session.add(unidade)
    db.session.commit()

    # 2 filas na MESMA unidade - a segunda garante filas_destino não-vazio
    # (senão painel_fila.html nem chega a incluir _redirecionar_busca.html).
    fila_a = Fila(empresa_id=empresa.id, unidade_id=unidade.id, nome="Fila A", sigla="FA")
    fila_b = Fila(empresa_id=empresa.id, unidade_id=unidade.id, nome="Fila B", sigla="FB")
    db.session.add_all([fila_a, fila_b])
    db.session.commit()

    local = Local(
        empresa_id=empresa.id, unidade_id=unidade.id, nome="Guiche 01",
        nome_exibicao="Guiche 01", identificador_admin="[UNI] Guiche 01",
    )
    db.session.add(local)
    db.session.commit()

    # painel_local.html só inclui _redirecionar_busca.html quando há uma
    # senha_atual chamada NESTE Local (diferente de painel_fila.html, que
    # calcula filas_destino incondicionalmente) - rodízio + senha aguardando
    # pra poder chamá-la de verdade antes de checar a tela.
    db.session.add(LocalRodizioFila(local_id=local.id, fila_id=fila_a.id, ordem=0, chamadas_seguidas=1))
    senha_fa = Senha(
        empresa_id=empresa.id, unidade_id=unidade.id, fila_id=fila_a.id,
        codigo="FA001", sequencial=1, prioridade=False, status="aguardando",
    )
    db.session.add(senha_fa)
    db.session.commit()

    def novo_usuario(login, perfis, unidades=None):
        u = Usuario(empresa_id=empresa.id, nome=login, login=login)
        u.set_senha("senha123")
        u.perfis = perfis
        u.unidades = unidades or []
        db.session.add(u)
        db.session.commit()
        return u.id

    id_admin = novo_usuario("admin_hidden_sweep", [perfil_admin])
    id_recepcao = novo_usuario("recepcao_hidden_sweep", [perfil_recepcao], [unidade])

    ids = dict(empresa=empresa.id, unidade=unidade.id, fila_a=fila_a.id, fila_b=fila_b.id, local=local.id)


def login_como(login):
    c = app.test_client()
    resp = c.post("/login", data={"login": login, "senha": "senha123"}, follow_redirects=True)
    assert resp.status_code == 200, (login, resp.status_code)
    with c.session_transaction() as sess:
        sess["atendimento_local_id"] = ids["local"]
    return c


def corpo_da_regra_css(texto, seletor):
    """Extrai o corpo de UMA regra CSS específica pelo seletor exato -
    mesmo padrão já usado em tests/test_regressao_painel_tv_redesign.py
    (evita falso-positivo de outro `display: none` qualquer no arquivo)."""
    inicio = texto.index(seletor)
    abre = texto.index("{", inicio)
    fecha = texto.index("}", abre)
    return texto[abre:fecha + 1]


c_admin = login_como("admin_hidden_sweep")
c_recepcao = login_como("recepcao_hidden_sweep")

# ===========================================================================
# 1) .checkboxes label[hidden] { display: none; } presente em admin/_layout.html
#    (compartilhado por toda tela admin que usa .checkboxes, não só locais).
# ===========================================================================
resp = c_admin.get(f"/admin/locais/{ids['local']}/editar")
assert resp.status_code == 200
texto = resp.data.decode("utf-8")

assert ".checkboxes label[hidden]" in texto
regra = corpo_da_regra_css(texto, ".checkboxes label[hidden]")
assert "display: none" in regra
ok("admin/_layout.html: regra própria '.checkboxes label[hidden] { display: none; }' presente e com display:none no corpo certo")

# ===========================================================================
# 2) .selecionado-redirecionar[hidden] { display: none; } presente no widget
#    de busca (_redirecionar_busca.html) - confirmado nas DUAS telas que o
#    incluem (painel_fila E painel_local), mesmo cuidado já usado no adendo
#    do destaque de tempo de espera (10.52) e no de esconder a barra de
#    contexto (10.53): achado num lugar, corrigido nos dois.
# ===========================================================================
resp_fila = c_recepcao.get(f"/atendimento/recepcao/filas/{ids['fila_a']}")
assert resp_fila.status_code == 200
texto_fila = resp_fila.data.decode("utf-8")

# painel_local.html só inclui o widget quando há senha_atual chamada NESTE
# Local (ver fixture acima) - chama a FA001 de verdade antes de checar.
resp_chamar = c_recepcao.post(
    f"/atendimento/recepcao/local/{ids['local']}/chamar",
    data={"codigo": "FA001"}, follow_redirects=True,
)
assert resp_chamar.status_code == 200

resp_local = c_recepcao.get(f"/atendimento/recepcao/local/{ids['local']}")
assert resp_local.status_code == 200
texto_local = resp_local.data.decode("utf-8")

for nome_tela, texto_tela in [("painel_fila", texto_fila), ("painel_local", texto_local)]:
    assert ".selecionado-redirecionar[hidden]" in texto_tela, nome_tela
    regra = corpo_da_regra_css(texto_tela, ".selecionado-redirecionar[hidden]")
    assert "display: none" in regra, nome_tela
    ok(f"{nome_tela}: regra própria '.selecionado-redirecionar[hidden]' (display: none) presente (widget de busca incluído nas duas telas)")

print()
print(f"TODOS OS {len(passos)} TESTES DA VARREDURA DE HIDDEN/DISPLAY PASSARAM")
