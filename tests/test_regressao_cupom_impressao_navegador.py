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
    # cenário real reportado em produção (10.44) - mesma unidade_completa
    # (com endereço/telefone, o caso mais alto de conteúdo), fila com nome
    # do incidente real ("Recepção Banco de Sangue").
    fila_banco_sangue = Fila(
        empresa_id=empresa.id, unidade_id=unidade_completa.id,
        nome="Recepcao Banco de Sangue", sigla="RBS",
    )
    db.session.add_all([fila_completa, fila_sem_endereco, fila_banco_sangue])
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
    botao_banco_sangue = TotemBotao(
        empresa_id=empresa.id, unidade_id=unidade_completa.id, label="Banco de Sangue", fila_id=fila_banco_sangue.id, ordem=2,
    )
    db.session.add_all([botao_completo, botao_sem_endereco, botao_banco_sangue])
    db.session.commit()

    ids = dict(
        totem_completo=totem_completo.id, botao_completo=botao_completo.id,
        totem_sem_endereco=totem_sem_endereco.id, botao_sem_endereco=botao_sem_endereco.id,
        botao_banco_sangue=botao_banco_sangue.id,
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
# C.1) <style> do CUPOM (local, dentro do content block) vem ANTES do
#      <script> do cupom no documento (10.46) - bug real de produção:
#      impressão silenciosa (kiosk/always_print_silent) saía sem
#      formatação, só com diálogo normal saía certa. Causa: o <script>
#      chama window.print() de forma SÍNCRONA já no carregamento inicial
#      (guard de sessionStorage) - o parser HTML executa scripts assim que
#      os encontra, ANTES de processar o que vem depois deles no documento.
#      Com o <style> (regras de .cupom/@page/@media print) posicionado
#      DEPOIS do <script> (ordem antiga), print() podia disparar antes do
#      parser sequer ter chegado nessas regras - o CSSOM ainda não tinha
#      elas aplicadas. Com diálogo, a espera pelo clique humano escondia a
#      corrida; sem diálogo, não há essa folga. Não é bug de rede/webfont
#      (nunca foi) - é puramente ordem de parsing, por isso a correção é só
#      reordenar os dois blocos, sem nenhum setTimeout/await novo.
#      IMPORTANTE: usar rindex/índice do <script> LOCAL (o único que existe
#      no documento inteiro, então texto.index("<script>") já é ele), mas
#      texto.index("<style>") sozinho pegaria o <style> de _layout.html
#      (dentro do <head>, sempre antes de qualquer conteúdo do body) e
#      passaria mesmo com o <style>/<script> do CUPOM na ordem errada - foi
#      exatamente isso que aconteceu no sanity check desta correção (a
#      asserção continuou "passando" depois de reverter o fix de propósito).
#      Por isso aqui usamos o ÚLTIMO <style> do documento (rindex, sem
#      limite de posição): _layout.html só tem UM <style> (no <head>) e o
#      cupom só tem UM <style> - como o do layout vem sempre primeiro (é
#      do <head>, sempre antes do <body>), o ÚLTIMO <style> do documento
#      inteiro É necessariamente o do cupom, esteja ele antes OU depois do
#      <script> (que também é único no documento). Um rindex limitado a
#      "antes do <script>" (tentativa anterior) falha nesse exato cenário:
#      se o <style> do cupom estiver DEPOIS do <script> (bug revertido de
#      propósito nesta sanity check), a busca limitada só encontra de novo
#      o <style> do layout, e a asserção passa mesmo com o bug presente -
#      foi exatamente isso que escondeu a regressão no sanity check.
# ===========================================================================
indice_script_cupom = texto.index("<script>")
indice_style_cupom = texto.rindex("<style>")
assert indice_style_cupom < indice_script_cupom
ok("<style> do CUPOM (não o do _layout.html) vem antes do <script> (que chama window.print()) - CSSOM completo antes de qualquer script rodar")

# ===========================================================================
# D) CSS @media print isola só o cupom.
# ===========================================================================
assert "@media print" in texto
# "@media print {" (com a chave) - não a menção à regra dentro do comentário
# que vem antes explicando o motivo dela existir.
indice_regra_print = texto.index("@media print {")
# janela alargada na 10.40 (2000, era 1000) - o bloco @media print ganhou as
# regras de colapso de altura (html/body/.totem) ANTES da regra .cupom, então
# uma janela mais curta deixava de pegar "left: 0"/"right: 0" (que agora vêm
# mais adiante no texto).
bloco_print = texto[indice_regra_print:indice_regra_print + 2000]
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
ok("CSS do cupom usa peso bold uniforme (font-weight:700 em .cupom p), sem .cupom-linha normal")

# ===========================================================================
# D.3.1) Linhas tracejadas separadoras REMOVIDAS (pedido do cliente - layout
#        mais limpo, ref. cupom da Caixa Econômica sem separador): nem o
#        texto literal do traço, nem a classe/regra CSS `.cupom-separador`
#        (que nunca foi um `border`, sempre foi texto puro dentro de um
#        `<p>` - confirmado lendo o HTML real antes de mexer). Espaçamento
#        entre os blocos ajustado via `margin-top` em `.cupom-fila`/
#        `.cupom-datahora` no lugar do respiro visual que o separador dava,
#        sem reintroduzir nenhuma linha.
# ===========================================================================
assert "- - - - - - - - - - - - - - - -" not in texto  # texto literal do traço
assert "cupom-separador" not in texto  # classe inteira removida (HTML e CSS)
assert 'class="cupom-fila"' in texto and "margin-top: .6rem" in bloco_cupom_css
ok("Linhas tracejadas separadoras removidas (texto e CSS) - espaçamento ajustado via margin-top, sem reintroduzir nenhuma linha")

# ===========================================================================
# D.4) Página com ALTURA "auto" no @page (10.44 - DECISÃO REVISTA, supersede
#      a 10.40): a impressora real é um ROLO CONTÍNUO de 80mm de largura,
#      retrato, corte automático rente ao conteúdo (confirmado nesta sessão:
#      driver só oferece papel 80x297mm, referência visual da impressora
#      padrão da Caixa Econômica) - NÃO uma etiqueta de tamanho fixo 7x8cm
#      como a 10.40 assumiu por uma premissa física incorreta sobre o
#      hardware. `size: 80mm auto` volta a ser o comportamento CERTO (o corte
#      automático do rolo decide onde a etiqueta termina, não uma altura
#      fixa no CSS) - não é o mesmo bug que a 10.40 corrigiu (naquele caso o
#      hardware real era outro). Se esta impressora for trocada por uma de
#      etiqueta fixa no futuro, a mudança certa é voltar a fixar as DUAS
#      dimensões (como a 10.40 fazia), documentado no comentário do CSS.
# ===========================================================================
assert "@page { size: 80mm auto" in texto
assert "@page { size: 80mm 70mm" not in texto  # decisão da 10.40 superseded pela 10.44 - não é o hardware real
ok("CSS @page usa altura 'auto' (rolo contínuo, corte automático) - decisão revista na 10.44, não 'vai-e-volta' por capricho")

# ===========================================================================
# D.5) Colapso de altura do conteúdo escondido (10.40, MANTIDO na 10.44 -
#      continua necessário independente do tamanho de página): `visibility:
#      hidden` (usado pra isolar o cupom) NÃO tira os elementos do fluxo -
#      sem isso, o cabeçalho/título/dígitos/botões escondidos continuariam
#      ocupando altura normal, desperdiçando papel de rolo imprimindo espaço
#      vazio antes do corte automático. `.totem` precisa de `min-height: 0`
#      explícito - só `height: 0` não venceria o `min-height: 100vh` da
#      folha de estilo base (_layout.html).
# ===========================================================================
assert "html, body { height: 0; overflow: hidden; }" in bloco_print
assert ".totem { min-height: 0; height: 0;" in bloco_print
ok("CSS @media print colapsa a altura do conteúdo escondido (html/body/.totem) - mantido da 10.40, evita espaço vazio antes do corte")

# ===========================================================================
# D.6) Cenário REAL reportado em produção (10.44): fila "Recepção Banco de
#      Sangue", unidade com endereço E telefone (o conteúdo mais alto do
#      template - 6 linhas desde a remoção dos separadores: empresa, fila,
#      código, data/hora, endereço, telefone). Estrutural, não confirma "1 página"
#      literalmente (isso exige motor de impressão real - print-to-pdf,
#      rodado manualmente nesta sessão e documentado no adendo 10.44, mesmo
#      padrão das validações da 10.38/10.40, nunca comitadas como
#      teste automatizado - este projeto não depende de Chrome/PyMuPDF na
#      suíte). O que ESTE caso trava: o conteúdo completo do incidente real
#      chega certo no HTML, e as MESMAS regras de @page auto + colapso de
#      altura (D.4/D.5) se aplicam também a essa página - nada específico
#      desse cenário (nome da fila, combinação endereço+telefone) escapa das
#      regras gerais já testadas.
# ===========================================================================
resp = c_totem.post(
    f"/totem/totem-cupom-completo/gerar/{ids['botao_banco_sangue']}",
    data={"qtd_acompanhantes": "0"},
    follow_redirects=True,
)
assert resp.status_code == 200
texto_banco_sangue = resp.data.decode("utf-8")
assert "Clinica Cupom LTDA" in texto_banco_sangue
assert "Recepcao Banco de Sangue" in texto_banco_sangue
assert "RBS001" in texto_banco_sangue
assert "Av. Teste, 100 - Centro, Cidade-UF" in texto_banco_sangue
assert "(62) 3000-1111" in texto_banco_sangue
assert "@page { size: 80mm auto" in texto_banco_sangue
assert "html, body { height: 0; overflow: hidden; }" in texto_banco_sangue
ok("Cenário real (Recepção Banco de Sangue, endereço+telefone) gera o cupom certo com as mesmas regras de @page auto + colapso de altura")

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

# Valor padronizado em 3s (era 20s até a 10.28) - o pause/resume abaixo
# desacopla o timer da duração do diálogo, então não precisa mais de folga.
assert "var SEGUNDOS_RETORNO = 3;" in corpo_script
assert "var SEGUNDOS_RETORNO = 20;" not in corpo_script  # valor antigo não pode voltar
ok("SEGUNDOS_RETORNO padronizado em 3 (não mais 20)")

assert 'addEventListener("beforeprint", cancelarRetorno)' in corpo_script
assert 'addEventListener("afterprint", agendarRetorno)' in corpo_script
# cancelarRetorno precisa dar clearTimeout de verdade (não só existir de nome).
bloco_cancelar = corpo_script[corpo_script.index("function cancelarRetorno"):]
bloco_cancelar = bloco_cancelar[:bloco_cancelar.index("}") + 30]
assert "clearTimeout(timerRetorno)" in bloco_cancelar
ok("Timer de retorno é pausado em beforeprint (clearTimeout de verdade) e retomado em afterprint")

# Prova de que reduzir pra 3s é seguro mesmo com o diálogo ficando aberto por
# MAIS de 3s: sem runtime de navegador neste ambiente (mesma limitação já
# registrada no docstring deste arquivo), não dá pra literalmente abrir o
# diálogo, esperar >3s e cronometrar - a prova aqui é estrutural, mas trava a
# garantia matemática do comportamento: `agendarRetorno()` (chamado em TODA
# afterprint, inclusive depois de um diálogo demorado) primeiro cancela
# qualquer timer restante (`cancelarRetorno()`, sem depender de "quanto
# sobrou") e SÓ DEPOIS agenda um setTimeout novo com a duração CHEIA
# (`SEGUNDOS_RETORNO * 1000`, não um valor parcial/calculado a partir do
# tempo decorrido) - ou seja, os 3s sempre recomeçam do zero quando o
# diálogo fecha, nunca "já tinham estourado" durante a espera, não importa
# quanto tempo o diálogo ficou aberto.
bloco_agendar = corpo_script[corpo_script.index("function agendarRetorno"):]
bloco_agendar = bloco_agendar[:200]  # cobre o corpo inteiro da função (poucas linhas)
assert "}" in bloco_agendar  # sanity check - função tem fim dentro da janela
assert bloco_agendar.index("cancelarRetorno()") < bloco_agendar.index("setTimeout(")  # cancela ANTES de reagendar
assert "SEGUNDOS_RETORNO * 1000" in bloco_agendar  # duração CHEIA, não parcial/restante
ok("agendarRetorno() sempre cancela e reagenda a duração CHEIA - os 3s recomeçam do zero após o diálogo fechar, não importa quanto tempo ficou aberto")

print()
print(f"TODOS OS {len(passos)} TESTES DO CUPOM/IMPRESSÃO VIA NAVEGADOR PASSARAM")
