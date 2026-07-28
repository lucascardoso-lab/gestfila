"""
Teste de regressão: busca com autocomplete no bloco "Redirecionar" da tela de
Atendimento (seção 10.2 do BLUEPRINT), substituindo a lista estática de radio
buttons - dor de usabilidade com 30-50 médicos cadastrados.

Cobre:
- A lista de radio buttons (`type="radio" name="fila_destino_id"`) não existe
  mais no HTML; entra um campo de busca (#busca-redirecionar) + input oculto
  (#fila-destino-id) que carrega o mesmo nome de campo `fila_destino_id`.
- A lista completa de filas candidatas é embutida no HTML como JSON
  (#dados-filas-destino), para filtro 100% local em JavaScript (sem requisição
  ao servidor a cada tecla) - cada item traz `aberta` (Fila.aberta_em(), seção
  10.1/10.5), usado para destacar "Atendendo agora" vs "Fora do horário".
- Isolamento multi-tenant inalterado: só filas da mesma empresa/unidade
  aparecem no JSON embutido (a própria fila de origem nunca aparece como seu
  próprio destino).
- Estilos de alvo de toque (min-height 44px) presentes, para uso em totem/tablet.
- O fluxo de negócio de "Realocar" (mover a senha para outra fila) continua
  funcionando de ponta a ponta enviando o mesmo campo de formulário
  `fila_destino_id` que o widget novo produz - nenhuma mudança de backend.

Execução: python -m tests.test_regressao_redirecionar_busca (a partir da raiz
do projeto, com o virtualenv ativado).
"""
import json
import os
from datetime import datetime

os.environ.setdefault("SECRET_KEY", "test")
os.environ.setdefault("FLASK_ENV", "testing")

from app import create_app
from app.extensions import db
from app.models import Empresa, Fila, Local, Perfil, Senha, Unidade, Usuario
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

    perfil_recepcao = Perfil(nome="atendente")
    db.session.add(perfil_recepcao)
    db.session.commit()

    empresa_a = Empresa(nome="Clinica A")
    empresa_b = Empresa(nome="Clinica B")
    db.session.add_all([empresa_a, empresa_b])
    db.session.commit()

    unidade_a1 = Unidade(empresa_id=empresa_a.id, nome="Unidade A1")
    unidade_b1 = Unidade(empresa_id=empresa_b.id, nome="Unidade B1")
    db.session.add_all([unidade_a1, unidade_b1])
    db.session.commit()

    fila_origem = Fila(empresa_id=empresa_a.id, unidade_id=unidade_a1.id, nome="Recepção Geral", sigla="RG")
    fila_aberta = Fila(
        empresa_id=empresa_a.id, unidade_id=unidade_a1.id, nome="Dr. Rodrigo Brum", sigla="RB",
        horario_funcionamento=dict(SEMPRE_ABERTA),
    )
    fila_fechada = Fila(
        empresa_id=empresa_a.id, unidade_id=unidade_a1.id, nome="Dr. Leandro Gonçalves", sigla="LG",
        horario_funcionamento=dict(SEMPRE_FECHADA),
    )
    fila_outra_empresa = Fila(empresa_id=empresa_b.id, unidade_id=unidade_b1.id, nome="Fila Sigilosa B", sigla="FB")
    db.session.add_all([fila_origem, fila_aberta, fila_fechada, fila_outra_empresa])
    db.session.commit()

    local_guiche01 = Local(empresa_id=empresa_a.id, unidade_id=unidade_a1.id, nome="Guichê 01")
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

    id_recepcao_a = novo_usuario("recepcao_a_busca", empresa_a.id, [perfil_recepcao], [unidade_a1])

    # operador_id precisa bater com quem vai consultar (controle de atendimento,
    # senha_atual_no_local agora é escopada por operador, não só por local).
    senha_chamada = Senha(
        empresa_id=empresa_a.id, unidade_id=unidade_a1.id, fila_id=fila_origem.id,
        codigo="RG001", sequencial=1, prioridade=False, status="chamada",
        chamada_em=datetime.utcnow(), local_id=local_guiche01.id, operador_id=id_recepcao_a,
    )
    db.session.add(senha_chamada)
    db.session.commit()

    ids = dict(
        fila_origem=fila_origem.id, fila_aberta=fila_aberta.id, fila_fechada=fila_fechada.id,
        fila_outra_empresa=fila_outra_empresa.id, local_guiche01=local_guiche01.id,
        senha_chamada=senha_chamada.id,
    )


def login_como(login):
    c = app.test_client()
    resp = c.post("/login", data={"login": login, "senha": "senha123"}, follow_redirects=True)
    assert resp.status_code == 200, (login, resp.status_code)
    return c


c_recepcao = login_como("recepcao_a_busca")
with c_recepcao.session_transaction() as sess:
    sess["atendimento_local_id"] = ids["local_guiche01"]

URL_PAINEL_FILA = f"/atendimento/recepcao/filas/{ids['fila_origem']}"

resp = c_recepcao.get(URL_PAINEL_FILA)
assert resp.status_code == 200
texto = resp.data.decode("utf-8")

# ===========================================================================
# A) Radio buttons não existem mais; entra o campo de busca + input oculto.
# ===========================================================================
assert 'type="radio" name="fila_destino_id"' not in texto
ok("A lista estática de radio buttons não existe mais no bloco Redirecionar")

assert 'id="busca-redirecionar"' in texto
assert '<input type="hidden" name="fila_destino_id" id="fila-destino-id"' in texto
ok("Campo de busca (#busca-redirecionar) e input oculto #fila-destino-id (name=fila_destino_id) presentes")

# Enter no campo de busca não pode submeter o form implicitamente (mesmo padrão
# já usado em #codigo/#nome - ver test_regressao_enter_submissao_implicita.py).
trecho_busca = texto[texto.index('id="busca-redirecionar"'):texto.index('id="busca-redirecionar"') + 400]
assert "onkeydown=\"if (event.key === 'Enter') event.preventDefault();\"" in trecho_busca
ok("#busca-redirecionar bloqueia submissão implícita via Enter, mesmo padrão dos outros campos")

# ===========================================================================
# B) Lista completa embutida como JSON (filtro 100% local, sem round-trip ao
#    servidor por tecla) - com o marcador `aberta` vindo de Fila.aberta_em().
# ===========================================================================
inicio = texto.index('id="dados-filas-destino">') + len('id="dados-filas-destino">')
fim = texto.index("</script>", inicio)
dados = json.loads(texto[inicio:fim])
por_id = {item["id"]: item for item in dados}

assert ids["fila_origem"] not in por_id
ok("A própria fila de origem não aparece como opção de destino")

assert por_id[ids["fila_aberta"]]["nome"] == "Dr. Rodrigo Brum"
assert por_id[ids["fila_aberta"]]["aberta"] is True
ok("Fila com horário de funcionamento sempre aberto -> aberta=true no JSON embutido")

assert por_id[ids["fila_fechada"]]["aberta"] is False
ok("Fila com horário de funcionamento sempre fechado -> aberta=false no JSON embutido")

assert ids["fila_outra_empresa"] not in por_id
ok("Isolamento multi-tenant: fila de outra empresa/unidade não aparece no JSON embutido")

# ===========================================================================
# C) Marcadores visuais e agrupamento (itens 2 e 3) presentes no JS do widget.
# ===========================================================================
assert "Atendendo agora" in texto and "Fora do horário" in texto
ok("Marcadores 'Atendendo agora' / 'Fora do horario' presentes no widget")

assert "divisor-dropdown-redirecionar" in texto
ok("Divisor visual entre o grupo de filas ativas e as demais está presente")

# ===========================================================================
# D) Alvos de toque grandes o bastante para totem/tablet (item 5).
# ===========================================================================
assert "min-height: 44px" in texto
ok("CSS do widget usa alvos de toque de pelo menos 44px (busca e itens do dropdown)")

# ===========================================================================
# C.2) Clicar/focar no campo (sem digitar nada) já abre a caixa de seleção com
#      TODAS as filas - bug reportado: antes só abria depois de 2+ caracteres
#      digitados, deixando o clique inicial sem nenhum retorno visual.
# ===========================================================================
assert "termoNormalizado.length < 2" not in texto
assert "termoNormalizado.length >= 2" not in texto
ok("O filtro não exige mais um mínimo de caracteres digitados pra abrir a caixa de seleção")

trecho_script = texto[texto.index("elBusca.addEventListener(\"input\""):]
assert 'elBusca.addEventListener("focus", () => renderizarDropdown(elBusca.value));' in trecho_script
assert 'elBusca.addEventListener("click", () => renderizarDropdown(elBusca.value));' in trecho_script
ok("Focar OU clicar no campo (mesmo já focado) dispara renderizarDropdown com o valor atual (vazio na primeira vez)")

# ===========================================================================
# E) Mesma correção replicada na tela por-Local (adendo 10.8) - chama a senha
#    primeiro (painel_local só popula filas_destino quando há senha chamada).
# ===========================================================================
resp = c_recepcao.get(f"/atendimento/recepcao/local/{ids['local_guiche01']}")
assert resp.status_code == 200
texto_local = resp.data.decode("utf-8")
assert 'type="radio" name="fila_destino_id"' not in texto_local
assert 'id="busca-redirecionar"' in texto_local
inicio = texto_local.index('id="dados-filas-destino">') + len('id="dados-filas-destino">')
fim = texto_local.index("</script>", inicio)
dados_local = json.loads(texto_local[inicio:fim])
assert {item["id"] for item in dados_local} == {ids["fila_aberta"], ids["fila_fechada"]}
ok("painel_local.html recebeu a mesma correção do widget de busca")

# ===========================================================================
# F) O comportamento de negócio do Realocar (mover a senha) continua idêntico:
#    o widget novo só precisa produzir o mesmo campo `fila_destino_id` de sempre.
#
# Verifica primeiro a rejeição de destino inválido (fila de outra empresa) -
# continua exatamente como antes, já que filas_destino() nunca mudou, só a UI
# que gera o campo - e só depois o caminho feliz, que MOVE a senha (e por isso
# precisa ser o último passo: `senha_atual_no_local` deixa de achá-la em
# fila_origem assim que ela é realocada).
# ===========================================================================
resp = c_recepcao.post(
    f"{URL_PAINEL_FILA}/realocar",
    data={"fila_destino_id": str(ids["fila_outra_empresa"])},
    follow_redirects=True,
)
assert resp.status_code == 200
assert "Selecione uma fila de destino válida" in resp.data.decode("utf-8")
ok("POST /realocar com fila de outra empresa continua sendo rejeitado (isolamento multi-tenant preservado)")

resp = c_recepcao.post(
    f"{URL_PAINEL_FILA}/realocar",
    data={"fila_destino_id": str(ids["fila_aberta"]), "nome": "Paciente Teste"},
    follow_redirects=True,
)
assert resp.status_code == 200
assert "realocada para" in resp.data.decode("utf-8")

with app.app_context():
    atualizada = db.session.get(Senha, ids["senha_chamada"])
    assert atualizada.fila_id == ids["fila_aberta"]
    assert atualizada.status == "aguardando"
    assert atualizada.nome_paciente == "Paciente Teste"
ok("POST /realocar com fila_destino_id (mesmo campo produzido pelo widget novo) move a senha normalmente")

print()
print(f"TODOS OS {len(passos)} TESTES DA BUSCA COM AUTOCOMPLETE NO BLOCO REDIRECIONAR PASSARAM")
