"""
Teste de regressão do adendo que substitui a impressão silenciosa via
win32print/ESC-POS (seção 10.6) por impressão via diálogo nativo do navegador
(window.print()), com prévia visual do cupom - ver
tests/test_regressao_fase10_6.py pro mecanismo antigo (legado, não apagado,
só não é mais chamado).

Cobre:
- prévia do cupom na tela totem/senha_gerada.html traz nome da empresa,
  endereço/telefone da unidade (Fase 1, novos campos), fila e código da senha;
- unidade sem endereço/telefone cadastrado (nullable) não vaza "None" nem
  quebra a prévia - só omite as linhas;
- window.print() dispara AUTOMATICAMENTE ao carregar a tela (reduz fricção -
  adendo posterior ao original desta seção, que exigia clique manual), mas só
  UMA vez por senha - guardado por sessionStorage com chave por senha.id, pra
  um F5/reconexão na mesma aba não reabrir o diálogo pra senha já processada;
- botão "Imprimir comprovante de novo" continua existindo como fallback
  manual, sempre clicável, independente do disparo automático já ter
  acontecido;
- CSS @media print isola só o cupom (mesma regra que qualquer outra parte da
  tela some ao imprimir);
- geração de senha funciona igual, independente de Totem.nome_impressora_
  windows (campo legado) estar preenchido ou não - a nova abordagem não
  depende mais desse campo pra nada;
- retorno automático à tela inicial via setTimeout PRÓPRIO (não mais <meta
  refresh> - trocado nesta rodada porque meta refresh não tem gancho de JS
  nenhum, continuaria contando por baixo do diálogo de impressão), pausado
  em beforeprint e retomado em afterprint (eventos padrão do navegador,
  suportados desde o IE10).

Limitação de infraestrutura (mesmo motivo já registrado em outros testes de
JS do projeto - ex. test_regressao_som_configuravel.py): sem runtime de
navegador aqui, a cobertura do disparo automático/guard/pausa de timer é
estrutural (o código/contrato certo chega até o JS, na ordem certa), não uma
execução real do script.

Execução: python -m tests.test_regressao_cupom_impressao_navegador (a partir
da raiz do projeto, com o virtualenv ativado).
"""
import os

os.environ.setdefault("SECRET_KEY", "test")
os.environ.setdefault("FLASK_ENV", "testing")

from app import create_app
from app.extensions import db
from app.models import Empresa, Fila, Senha, Totem, TotemBotao, Unidade

app = create_app("testing")

passos = []


def ok(msg):
    passos.append(msg)
    print("OK:", msg)


with app.app_context():
    db.create_all()

    empresa = Empresa(nome="Clinica Cupom LTDA")
    db.session.add(empresa)
    db.session.commit()

    unidade_completa = Unidade(
        empresa_id=empresa.id, nome="Matriz",
        endereco="Av. Teste, 100 - Centro, Cidade-UF",
        telefone="(62) 3000-1111",
    )
    unidade_sem_endereco = Unidade(empresa_id=empresa.id, nome="Filial Sem Endereco")
    db.session.add_all([unidade_completa, unidade_sem_endereco])
    db.session.commit()

    fila_completa = Fila(empresa_id=empresa.id, unidade_id=unidade_completa.id, nome="Recepcao Geral", sigla="RG")
    fila_sem_endereco = Fila(empresa_id=empresa.id, unidade_id=unidade_sem_endereco.id, nome="Laboratorio", sigla="LAB")
    db.session.add_all([fila_completa, fila_sem_endereco])
    db.session.commit()

    # totem com nome_impressora_windows (legado) preenchido - prova que a nova
    # abordagem ignora esse campo (não usa mais pra decidir nada).
    totem_completo = Totem(
        empresa_id=empresa.id, unidade_id=unidade_completa.id, nome="Totem Matriz",
        nome_impressora_windows="Bematech MP-4200 TH", slug="totem-cupom-completo",
    )
    # totem SEM nome_impressora_windows - também deve funcionar igual.
    totem_sem_endereco = Totem(
        empresa_id=empresa.id, unidade_id=unidade_sem_endereco.id, nome="Totem Filial", slug="totem-cupom-sem-endereco",
    )
    db.session.add_all([totem_completo, totem_sem_endereco])
    db.session.commit()

    botao_completo = TotemBotao(
        empresa_id=empresa.id, unidade_id=unidade_completa.id, label="Recepcao", fila_id=fila_completa.id, ordem=1,
    )
    botao_sem_endereco = TotemBotao(
        empresa_id=empresa.id, unidade_id=unidade_sem_endereco.id, label="Laboratorio", fila_id=fila_sem_endereco.id, ordem=1,
    )
    db.session.add_all([botao_completo, botao_sem_endereco])
    db.session.commit()

    ids = dict(
        totem_completo=totem_completo.id, botao_completo=botao_completo.id,
        totem_sem_endereco=totem_sem_endereco.id, botao_sem_endereco=botao_sem_endereco.id,
    )

c_totem = app.test_client()

# ===========================================================================
# A) Cupom traz nome da empresa, endereço, telefone, fila e código da senha -
#    unidade com os dois campos novos (Fase 1) preenchidos.
# ===========================================================================
resp = c_totem.post(
    f"/totem/totem-cupom-completo/gerar/{ids['botao_completo']}",
    data={"qtd_acompanhantes": "0"},
    follow_redirects=True,
)
assert resp.status_code == 200
texto = resp.data.decode("utf-8")
assert "Clinica Cupom LTDA" in texto
assert "Av. Teste, 100 - Centro, Cidade-UF" in texto
assert "(62) 3000-1111" in texto
assert "Recepcao Geral" in texto
assert "RG001" in texto
ok("Cupom traz empresa, endereço, telefone, fila e código da senha (Fase 1 + Fase 2)")

with app.app_context():
    senha_completa = Senha.query.filter_by(codigo="RG001").first()
    assert senha_completa is not None and senha_completa.status == "aguardando"
ok("Geração de senha funciona normalmente mesmo com Totem.nome_impressora_windows (legado) preenchido")

# ===========================================================================
# B) Unidade sem endereço/telefone cadastrado - cupom não vaza "None", só
#    omite as linhas.
# ===========================================================================
resp = c_totem.post(
    f"/totem/totem-cupom-sem-endereco/gerar/{ids['botao_sem_endereco']}",
    data={"qtd_acompanhantes": "0"},
    follow_redirects=True,
)
assert resp.status_code == 200
texto = resp.data.decode("utf-8")
assert "Clinica Cupom LTDA" in texto
assert "Laboratorio" in texto
assert "LAB001" in texto
assert "None" not in texto
ok("Unidade sem endereço/telefone: cupom omite as linhas, sem vazar 'None'")

with app.app_context():
    senha_sem_endereco = Senha.query.filter_by(codigo="LAB001").first()
    assert senha_sem_endereco is not None and senha_sem_endereco.status == "aguardando"
ok("Geração de senha funciona normalmente mesmo sem nome_impressora_windows (legado) e sem endereço/telefone")

with app.app_context():
    id_senha_sem_endereco = Senha.query.filter_by(codigo="LAB001").first().id

corpo_script = texto[texto.index("<script>"):texto.index("</script>")]

# ===========================================================================
# C) window.print() dispara automaticamente ao carregar, guardado por
#    sessionStorage (chave por senha.id) pra nunca disparar 2x pra mesma
#    senha - e o botão "Imprimir comprovante de novo" continua funcional
#    como fallback manual, independente do disparo automático.
# ===========================================================================
assert 'id="botao-imprimir"' in texto
# window.print() só existe UMA vez no código-fonte, dentro da função
# compartilhada imprimirComprovante() - usada tanto pro disparo automático
# guardado quanto pro listener de clique do botão manual, nunca duplicada.
assert corpo_script.count("window.print()") == 1
assert "function imprimirComprovante()" in corpo_script
# addEventListener recebe a REFERÊNCIA da função (sem parênteses) - se
# chamasse imprimirComprovante() aqui, o clique não seria necessário, o
# print dispararia sozinho ao registrar o listener.
assert 'addEventListener("click", imprimirComprovante)' in corpo_script
# a chamada de fato (com parênteses) só aparece 1x fora da declaração da
# função - dentro do guard de sessionStorage (disparo automático).
chamadas_com_parenteses = corpo_script.count("imprimirComprovante()") - 1  # -1 = a própria declaração da função
assert chamadas_com_parenteses == 1
chave_esperada = f'"gestfila_totem_impresso_{id_senha_sem_endereco}"'
assert chave_esperada in corpo_script
ok("Chave de sessionStorage é montada com o id real da senha gerada nesta página")

# a chamada automática tem que estar DENTRO do guard (if !sessionStorage...),
# e o guard precisa marcar a flag ANTES de chamar imprimirComprovante() - pra
# um erro no meio do processo nunca deixar a flag "esquecida" como falsa.
bloco_guard = corpo_script[corpo_script.index("if (!sessionStorage.getItem"):]
assert "sessionStorage.setItem(CHAVE_JA_IMPRESSO" in bloco_guard
assert "imprimirComprovante()" in bloco_guard
indice_setitem = bloco_guard.index("sessionStorage.setItem(CHAVE_JA_IMPRESSO")
indice_chamada_auto = bloco_guard.index("imprimirComprovante()")
assert indice_setitem < indice_chamada_auto
ok("Disparo automático só acontece dentro do guard de sessionStorage, e a flag é marcada ANTES de imprimir")

# o botão manual chama a MESMA função (imprimirComprovante), mas o listener
# de clique em si não depende do guard - continua clicável quantas vezes
# o usuário quiser, mesmo depois do disparo automático já ter acontecido.
bloco_listener_manual = corpo_script[
    corpo_script.index('getElementById("botao-imprimir")'):corpo_script.index('getElementById("botao-imprimir")') + 150
]
assert "imprimirComprovante" in bloco_listener_manual
assert "sessionStorage" not in bloco_listener_manual
ok("Botão manual chama a mesma função de impressão, sem depender do guard - sempre disponível como fallback")

# ===========================================================================
# D) CSS @media print isola só o cupom.
# ===========================================================================
assert "@media print" in texto
# "@media print {" (com a chave) - não a menção à regra dentro do comentário
# que vem antes explicando o motivo dela existir.
indice_regra_print = texto.index("@media print {")
bloco_print = texto[indice_regra_print:indice_regra_print + 1000]
assert "visibility: hidden" in bloco_print
assert ".cupom" in bloco_print
ok("CSS @media print presente, isolando o cupom do resto da tela na impressão")

# ===========================================================================
# D.2) Centralização confiável na impressão (correção do cupom saindo à esquerda,
#      ocupando só parte do rolo): a regra @media print do .cupom estica de borda
#      a borda da PÁGINA (`left:0; right:0`, não `width:100%` num absoluto, que
#      alguns motores resolvem contra a largura da tela) e centraliza o conteúdo.
# ===========================================================================
assert "left: 0" in bloco_print and "right: 0" in bloco_print
assert "text-align: center" in bloco_print
assert "width: 100%" not in bloco_print  # a causa raiz do desalinhamento - não pode voltar
# `width: auto;` é ESSENCIAL: sem resetar o width:320px da regra base, num
# elemento absoluto o `width` vence e o `right:0` é IGNORADO (spec do CSS),
# deixando o cupom com 320px colado na esquerda. Confirmado por render de
# impressão real (print-to-pdf) que left:0/right:0 SEM width:auto não centraliza.
assert "width: auto;" in bloco_print
ok("CSS @media print centraliza o cupom na largura do papel (left:0/right:0 + width:auto + text-align:center, sem width:100%)")

# ===========================================================================
# D.3) Peso de fonte UNIFORME (correção do contraste desigual na térmica): todas
#      as linhas em bold (font-weight 700), hierarquia só por tamanho; nada de
#      peso normal ou cor cinza (que saem claros/pontilhados na impressão 1-bit).
# ===========================================================================
indice_cupom_css = texto.index(".cupom p {")
bloco_cupom_css = texto[indice_cupom_css:indice_regra_print]
assert "font-weight: 700" in bloco_cupom_css  # aplicado a .cupom p (todas as linhas)
# a classe antiga .cupom-linha (peso normal, usada em endereço/telefone/fila/
# data-hora) não existe mais - foi substituída por classes bold por tamanho.
assert "cupom-linha" not in texto
# separador não usa mais a variável de cinza (que imprimia fraco).
assert "cupom-separador { font-size: .9rem; color: var(--gf-cinza-medio)" not in texto
ok("CSS do cupom usa peso bold uniforme (font-weight:700 em .cupom p), sem .cupom-linha normal nem separador cinza")

# ===========================================================================
# E) Retorno automático à tela inicial via setTimeout PRÓPRIO (não mais meta
#    refresh - trocado porque não tinha gancho de JS nenhum), pausado em
#    beforeprint e retomado em afterprint.
# ===========================================================================
assert 'http-equiv="refresh"' not in texto  # meta refresh REMOVIDO de propósito nesta rodada
assert "setTimeout(function ()" in corpo_script
assert "SEGUNDOS_RETORNO * 1000" in corpo_script
assert 'window.location.href = URL_RETORNO' in corpo_script
assert "/totem/totem-cupom-sem-endereco" in texto  # URL_RETORNO aponta pro totem certo (Jinja já resolvido)
ok("Retorno automático via setTimeout próprio (sem <meta refresh>), apontando pro totem certo")

assert 'addEventListener("beforeprint", cancelarRetorno)' in corpo_script
assert 'addEventListener("afterprint", agendarRetorno)' in corpo_script
# cancelarRetorno precisa dar clearTimeout de verdade (não só existir de nome).
bloco_cancelar = corpo_script[corpo_script.index("function cancelarRetorno"):]
bloco_cancelar = bloco_cancelar[:bloco_cancelar.index("}") + 30]
assert "clearTimeout(timerRetorno)" in bloco_cancelar
ok("Timer de retorno é pausado em beforeprint (clearTimeout de verdade) e retomado em afterprint")

print()
print(f"TODOS OS {len(passos)} TESTES DO CUPOM/IMPRESSÃO VIA NAVEGADOR PASSARAM")
