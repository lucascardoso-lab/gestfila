"""
Teste de regressão: destaque visual por tempo de espera na tabela "Senhas em
espera" - escala de 3 níveis (normal / atenção 70% / estourado 100% do
Fila.tempo_espera_maximo_min).

Precedente já registrado no BLUEPRINT (botão "Chamar" por linha,
tests/test_regressao_botao_chamar_por_linha.py): a tabela "Senhas em espera"
existe em painel_fila.html (tela legada por-fila) E painel_local.html (tela
atual por-Local, a que atendente/executor realmente usa desde a 10.19/10.23) -
os dois templates NÃO compartilham um include pra essa tabela (confirmado por
leitura direta antes de implementar), então a feature precisa estar nos DOIS
explicitamente. Este teste cobre EXPLICITAMENTE os dois lugares, não só "a
feature funciona" - é exatamente o tipo de buraco que já aconteceu antes.

Limitação de infraestrutura, mesmo motivo já aceito em outros testes de JS do
projeto: sem runtime de navegador aqui, o cálculo em si (classeDestaqueEspera,
que roda a cada segundo junto com o cronômetro "T. Espera") não é executado -
o que este teste confirma é que os DADOS certos chegam até o HTML/JSON (o
limite de cada fila, no lugar certo - constante única em painel_fila.html,
atributo por linha em painel_local.html, já que ali o rodízio agrega
candidatas de várias filas com limites potencialmente diferentes) e que a
função/CSS da técnica estão presentes nos dois templates.

Execução: python -m tests.test_regressao_destaque_tempo_espera (a partir da
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

    perfil_recepcao = Perfil(nome="atendente")
    db.session.add(perfil_recepcao)
    db.session.commit()

    empresa = Empresa(nome="Clinica Destaque Espera")
    db.session.add(empresa)
    db.session.commit()

    unidade = Unidade(empresa_id=empresa.id, nome="Unidade 1")
    db.session.add(unidade)
    db.session.commit()

    # Fila A: limite configurado (30 min) - usada pra provar que o valor certo
    # chega no HTML/JSON. Fila B: SEM limite (tempo_espera_maximo_min=None) -
    # prova o fallback (nunca destaca, mesmo com fila diferente no mesmo Local).
    fila_a = Fila(
        empresa_id=empresa.id, unidade_id=unidade.id, nome="Fila Com Limite", sigla="FL",
        tempo_espera_maximo_min=30,
    )
    fila_b = Fila(
        empresa_id=empresa.id, unidade_id=unidade.id, nome="Fila Sem Limite", sigla="FS",
    )
    db.session.add_all([fila_a, fila_b])
    db.session.commit()
    assert fila_b.tempo_espera_maximo_min is None  # sanity: default do model é NULL

    local = Local(
        empresa_id=empresa.id, unidade_id=unidade.id, nome="Guiche 01",
        nome_exibicao="Guiche 01", identificador_admin="[UNI] Guiche 01",
    )
    db.session.add(local)
    db.session.commit()

    # rodízio do Local agrega as DUAS filas - é o cenário que exige o limite
    # calculado POR LINHA (não uma constante única), já que cada fila tem o seu.
    db.session.add_all([
        LocalRodizioFila(local_id=local.id, fila_id=fila_a.id, ordem=0, chamadas_seguidas=1),
        LocalRodizioFila(local_id=local.id, fila_id=fila_b.id, ordem=1, chamadas_seguidas=1),
    ])
    db.session.commit()

    senha_a = Senha(
        empresa_id=empresa.id, unidade_id=unidade.id, fila_id=fila_a.id,
        codigo="FL001", sequencial=1, prioridade=False, status="aguardando",
    )
    senha_b = Senha(
        empresa_id=empresa.id, unidade_id=unidade.id, fila_id=fila_b.id,
        codigo="FS001", sequencial=1, prioridade=False, status="aguardando",
    )
    db.session.add_all([senha_a, senha_b])
    db.session.commit()

    def novo_usuario(login, perfis):
        u = Usuario(empresa_id=empresa.id, nome=login, login=login)
        u.set_senha("senha123")
        u.perfis = perfis
        u.unidades = [unidade]
        db.session.add(u)
        db.session.commit()
        return u.id

    id_recepcao = novo_usuario("recepcao_destaque", [perfil_recepcao])

    ids = dict(
        empresa=empresa.id, unidade=unidade.id, fila_a=fila_a.id, fila_b=fila_b.id,
        local=local.id, senha_a=senha_a.id, senha_b=senha_b.id,
    )


def login_como(login):
    c = app.test_client()
    resp = c.post("/login", data={"login": login, "senha": "senha123"}, follow_redirects=True)
    assert resp.status_code == 200, (login, resp.status_code)
    with c.session_transaction() as sess:
        sess["atendimento_local_id"] = ids["local"]
    return c


c = login_como("recepcao_destaque")

# ===========================================================================
# A) CSS da escala de 3 níveis (compartilhado via atendimento/_layout.html,
#    que os DOIS templates estendem) presente nas DUAS telas.
# ===========================================================================
resp_fila = c.get(f"/atendimento/recepcao/filas/{ids['fila_a']}")
assert resp_fila.status_code == 200
texto_fila = resp_fila.data.decode("utf-8")

resp_local = c.get(f"/atendimento/recepcao/local/{ids['local']}")
assert resp_local.status_code == 200
texto_local = resp_local.data.decode("utf-8")

for nome_tela, texto in [("painel_fila", texto_fila), ("painel_local", texto_local)]:
    assert "espera-atencao" in texto, nome_tela
    assert "espera-estourada" in texto, nome_tela
    assert "function classeDestaqueEspera(" in texto, nome_tela
    ok(f"{nome_tela}: CSS/função da escala de 3 níveis presentes")

# ===========================================================================
# B) painel_fila.html: constante ÚNICA (mesma fila em todas as linhas) com o
#    valor certo de tempo_espera_maximo_min.
# ===========================================================================
assert "const TEMPO_ESPERA_MAXIMO_MIN = 30;" in texto_fila
ok("painel_fila (fila COM limite): TEMPO_ESPERA_MAXIMO_MIN = 30")

resp_fila_sem_limite = c.get(f"/atendimento/recepcao/filas/{ids['fila_b']}")
assert resp_fila_sem_limite.status_code == 200
texto_fila_sem_limite = resp_fila_sem_limite.data.decode("utf-8")
assert "const TEMPO_ESPERA_MAXIMO_MIN = null;" in texto_fila_sem_limite
ok("painel_fila (fila SEM limite): TEMPO_ESPERA_MAXIMO_MIN = null (fallback - nunca destaca)")

# ===========================================================================
# C) painel_local.html: atributo POR LINHA (rodízio agrega as duas filas, cada
#    uma com seu limite) - a linha da senha de fila_a traz 30, a de fila_b
#    vem vazia (fallback). Confirma que o valor certo acompanha a linha certa,
#    não uma constante global (que estaria errada pra pelo menos uma delas).
# ===========================================================================
indice_fl001 = texto_local.index('data-senha-id="{}"'.format(ids["senha_a"]))
trecho_fl001 = texto_local[indice_fl001:indice_fl001 + 120]
assert 'data-tempo-maximo-min="30"' in trecho_fl001
ok("painel_local: linha da senha de fila COM limite traz data-tempo-maximo-min=\"30\"")

indice_fs001 = texto_local.index('data-senha-id="{}"'.format(ids["senha_b"]))
trecho_fs001 = texto_local[indice_fs001:indice_fs001 + 120]
assert 'data-tempo-maximo-min=""' in trecho_fs001
ok("painel_local: linha da senha de fila SEM limite traz data-tempo-maximo-min=\"\" (fallback)")

# ===========================================================================
# D) Endpoint JSON de painel_local (dados_local, usado no refresh via
#    WebSocket) também carrega tempo_espera_maximo_min por item - senão o
#    destaque sumiria a cada atualização em tempo real.
# ===========================================================================
resp_json = c.get(f"/atendimento/recepcao/local/{ids['local']}/dados")
assert resp_json.status_code == 200
dados = resp_json.get_json()
por_id = {item["id"]: item for item in dados["espera"]}
assert por_id[ids["senha_a"]]["tempo_espera_maximo_min"] == 30
assert por_id[ids["senha_b"]]["tempo_espera_maximo_min"] is None
ok("dados_local (JSON): tempo_espera_maximo_min por item, correto pra cada fila")

print()
print(f"TODOS OS {len(passos)} TESTES DE DESTAQUE DE TEMPO DE ESPERA PASSARAM")
