"""
Teste de regressão da seção 10.6 do BLUEPRINT (Impressão física da senha via
win32print/ESC-POS) - SUPERSEDIDA pelo adendo que troca pra impressão via
window.print() do navegador (ver tests/test_regressao_cupom_impressao_navegador.py
pra cobertura do mecanismo atual).

app/totem/impressao.py continua existindo no repositório (código legado, não
apagado - decisão registrada no BLUEPRINT), mas app/totem/routes.py::gerar_senha()
não chama mais nada dele. Este arquivo cobre duas coisas:

- unitário: as funções de app/totem/impressao.py continuam funcionando
  corretamente se algum dia forem chamadas de novo (montagem dos bytes ESC/POS,
  envio via win32print mockado, tolerância a falha sem propagar exceção);
- integração: confirma que o fluxo real do totem (POST /gerar) NÃO toca mais
  em win32print - a prova de que a Fase 3 (desativação) de fato tirou a
  chamada do caminho de geração de senha, não só documentou a intenção.

Execução: python -m tests.test_regressao_fase10_6 (a partir da raiz do projeto,
com o virtualenv ativado).
"""
import os

os.environ.setdefault("SECRET_KEY", "test")
os.environ.setdefault("FLASK_ENV", "testing")

from datetime import datetime
from unittest.mock import MagicMock, patch

from app import create_app
from app.extensions import db
from app.models import Empresa, Fila, Senha, Totem, TotemBotao, Unidade
from app.totem import impressao

app = create_app("testing")

passos = []


def ok(msg):
    passos.append(msg)
    print("OK:", msg)


with app.app_context():
    db.create_all()

    empresa = Empresa(nome="Clinica Impressao")
    db.session.add(empresa)
    db.session.commit()

    unidade = Unidade(empresa_id=empresa.id, nome="Unidade 1")
    db.session.add(unidade)
    db.session.commit()

    fila = Fila(empresa_id=empresa.id, unidade_id=unidade.id, nome="Laboratorio", sigla="LAB")
    db.session.add(fila)
    db.session.commit()

    totem_sem_impressora = Totem(
        empresa_id=empresa.id, unidade_id=unidade.id, nome="Totem sem impressora", slug="totem-sem-impressora",
    )
    totem_com_impressora = Totem(
        empresa_id=empresa.id, unidade_id=unidade.id, nome="Totem com impressora",
        nome_impressora_windows="Bematech MP-4200 TH", slug="totem-com-impressora",
    )
    totem_impressora_em_branco = Totem(
        empresa_id=empresa.id, unidade_id=unidade.id, nome="Totem impressora em branco",
        nome_impressora_windows="   ", slug="totem-impressora-em-branco",
    )
    db.session.add_all([totem_sem_impressora, totem_com_impressora, totem_impressora_em_branco])
    db.session.commit()

    botao_sem = TotemBotao(
        empresa_id=empresa.id, unidade_id=unidade.id, label="Laboratorio", fila_id=fila.id, sigla="LAB",
    )
    db.session.add(botao_sem)
    db.session.commit()

    ids = dict(
        unidade=unidade.id, fila=fila.id, botao=botao_sem.id,
        totem_sem=totem_sem_impressora.id, totem_com=totem_com_impressora.id,
        totem_branco=totem_impressora_em_branco.id,
    )


def db_get(modelo, id_):
    with app.app_context():
        return db.session.get(modelo, id_)


c_totem = app.test_client()

# ===========================================================================
# A) Montagem dos bytes ESC/POS do cupom.
# ===========================================================================
momento = datetime(2026, 6, 24, 14, 32)
dados = impressao.montar_cupom("Clinica Impressao", "LAB001", momento)
assert isinstance(dados, bytes) and len(dados) > 0
assert b"Clinica Impressao" in dados
assert b"LAB001" in dados
assert b"24/06/2026  14:32" in dados
ok("montar_cupom: bytes contêm nome da empresa, código da senha e data/hora formatadas")

dados_sem_destaque = impressao.montar_cupom("X", "Y", momento)
# o trecho do código deve estar entre os comandos de fonte dupla (GS!/ESC! de tamanho)
assert b"\x1d!" in dados_sem_destaque or b"\x1b!" in dados_sem_destaque
ok("montar_cupom: usa comando ESC/POS de fonte grande para o código da senha")

assert dados.endswith(b"\x1dV\x00") or b"\x1dV" in dados
ok("montar_cupom: inclui o comando de corte de papel (cut) ao final")

# ===========================================================================
# B) Envio via win32print (mockado) - fila de impressão nativa do Windows.
# ===========================================================================
with patch.object(impressao, "win32print") as mock_win32print:
    handle_falso = object()
    mock_win32print.OpenPrinter.return_value = handle_falso

    impressao.enviar_para_impressora_windows("Bematech MP-4200 TH", b"BYTES-DE-TESTE")

    mock_win32print.OpenPrinter.assert_called_once_with("Bematech MP-4200 TH")
    mock_win32print.StartDocPrinter.assert_called_once()
    assert mock_win32print.StartDocPrinter.call_args[0][0] is handle_falso
    mock_win32print.StartPagePrinter.assert_called_once_with(handle_falso)
    mock_win32print.WritePrinter.assert_called_once_with(handle_falso, b"BYTES-DE-TESTE")
    mock_win32print.EndPagePrinter.assert_called_once_with(handle_falso)
    mock_win32print.EndDocPrinter.assert_called_once_with(handle_falso)
    mock_win32print.ClosePrinter.assert_called_once_with(handle_falso)
ok("enviar_para_impressora_windows: abre/escreve/fecha o trabalho RAW na impressora correta, via win32print")

# StartDocPrinter deve pedir explicitamente o modo RAW (não driver gráfico) -
# é justamente isso que permite enviar os bytes ESC/POS sem reformatação.
with patch.object(impressao, "win32print") as mock_win32print:
    mock_win32print.OpenPrinter.return_value = object()
    impressao.enviar_para_impressora_windows("Qualquer Impressora", b"X")
    args_doc = mock_win32print.StartDocPrinter.call_args[0]
    info_documento = args_doc[2]  # (nome_doc, arquivo_saida, tipo_dados)
    assert info_documento[2] == "RAW"
ok("enviar_para_impressora_windows: trabalho de impressão é enviado em modo RAW")

# ===========================================================================
# C) imprimir_senha_se_configurado - totem sem impressora configurada.
# ===========================================================================
totem_sem = db_get(Totem, ids["totem_sem"])
with patch.object(impressao, "win32print") as mock_win32print:
    resultado = impressao.imprimir_senha_se_configurado(totem_sem, "Clinica Impressao", "LAB001")
    assert resultado is None
    mock_win32print.OpenPrinter.assert_not_called()
ok("imprimir_senha_se_configurado: totem sem nome_impressora_windows pula a impressão, sem tocar no win32print")

totem_branco = db_get(Totem, ids["totem_branco"])
with patch.object(impressao, "win32print") as mock_win32print:
    resultado = impressao.imprimir_senha_se_configurado(totem_branco, "Clinica Impressao", "LAB001")
    assert resultado is None
    mock_win32print.OpenPrinter.assert_not_called()
ok("imprimir_senha_se_configurado: nome_impressora_windows só com espaços também pula a impressão")

assert impressao.imprimir_senha_se_configurado(None, "Clinica Impressao", "LAB001") is None
ok("imprimir_senha_se_configurado: chamado sem totem (None) também pula, sem erro")

# ===========================================================================
# D) imprimir_senha_se_configurado - caminho feliz (mockado), em background.
# ===========================================================================
totem_com = db_get(Totem, ids["totem_com"])
with patch.object(impressao, "win32print") as mock_win32print:
    mock_win32print.OpenPrinter.return_value = object()
    thread = impressao.imprimir_senha_se_configurado(totem_com, "Clinica Impressao", "LAB002")
    assert thread is not None
    thread.join(timeout=5)
    assert not thread.is_alive()
    mock_win32print.OpenPrinter.assert_called_once_with("Bematech MP-4200 TH")
    args_write = mock_win32print.WritePrinter.call_args[0]
    assert b"LAB002" in args_write[1]
ok("imprimir_senha_se_configurado: com impressora configurada, envia os bytes corretos via win32print em background")

# ===========================================================================
# E) Falha de impressão (mockada) não propaga exceção - só loga o erro.
# ===========================================================================
with patch.object(impressao, "win32print") as mock_win32print, patch.object(impressao, "logger") as mock_logger:
    mock_win32print.OpenPrinter.side_effect = RuntimeError("impressora offline (simulado)")
    thread = impressao.imprimir_senha_se_configurado(totem_com, "Clinica Impressao", "LAB003")
    thread.join(timeout=5)
    assert not thread.is_alive()  # a thread terminou sem propagar a exceção
    mock_logger.exception.assert_called_once()
ok("Falha de impressão (mockada): não propaga exceção, e é registrada via logger.exception")

print()

# ===========================================================================
# F) Fase 3 (desativação): POST /gerar via totem COM nome_impressora_windows
#    configurado NÃO toca em win32print - prova de que a chamada foi
#    realmente removida de gerar_senha(), não só documentada. Antes desta
#    mudança este mesmo cenário (totem_com, impressora configurada) chamava
#    win32print.OpenPrinter automaticamente; agora não chama mais nada.
# ===========================================================================
with patch.object(impressao, "win32print") as mock_win32print:
    resp = c_totem.post(
        f"/totem/{ids['unidade']}/{ids['totem_com']}/gerar/{ids['botao']}",
        data={"qtd_acompanhantes": "0"},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert "LAB001" in resp.data.decode("utf-8")
    mock_win32print.OpenPrinter.assert_not_called()

with app.app_context():
    senha_gerada = Senha.query.filter_by(codigo="LAB001").first()
    assert senha_gerada is not None and senha_gerada.status == "aguardando"
ok("POST /gerar via totem COM impressora configurada não toca mais em win32print (Fase 3: chamada removida da rota)")

print()
print(f"TODOS OS {len(passos)} TESTES DA SEÇÃO 10.6 (LEGADO - IMPRESSÃO VIA WIN32PRINT) PASSARAM")
