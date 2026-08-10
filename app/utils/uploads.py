"""Upload de logo por empresa (adendo 10.43) - único uso de upload de arquivo
do sistema hoje. Guarda só o nome do arquivo no banco (`Empresa.logo_filename`),
nunca o blob nem o nome original enviado pelo usuário - o arquivo físico vai
em `app/static/uploads/logos/`, nomeado deterministicamente como
`empresa_<id>.png`, sempre sobrescrevendo o anterior (sem depender do nome
enviado, sem acumular arquivos órfãos por empresa).

Validação por CONTEÚDO real, não extensão/Content-Type do upload (que o
cliente controla e pode mentir): usa Pillow pra de fato decodificar a imagem
e confirmar o formato. SVG é recusado de propósito - é XML, pode embutir
`<script>`/`onload` executável quando servido/aberto num navegador (risco de
XSS), diferente de PNG/JPG que são dados binários de pixel sem capacidade de
execução. Reencodar sempre para PNG (mesmo quando o original é JPG) também
funciona como sanitização: qualquer coisa fora dos pixels decodificados
(metadados, payload de polyglot anexado ao arquivo) é descartada no
processo, só sobra o que a Pillow decodificou como imagem de verdade.
`Image.open().verify()`/limite de pixels do Pillow (`Image.MAX_IMAGE_PIXELS`,
ligado por padrão) protegem contra decompression bomb (arquivo pequeno que
decodifica pra uma imagem gigante).
"""
import io
import os

from flask import current_app
from PIL import Image, UnidentifiedImageError

TAMANHO_MAXIMO_LOGO_BYTES = 2 * 1024 * 1024  # 2MB - limite documentado (10.43)
FORMATOS_LOGO_PERMITIDOS = {"PNG", "JPEG"}


def pasta_logos():
    return os.path.join(current_app.static_folder, "uploads", "logos")


def nome_arquivo_logo(empresa_id):
    return f"empresa_{empresa_id}.png"


def caminho_logo(empresa_id):
    return os.path.join(pasta_logos(), nome_arquivo_logo(empresa_id))


def salvar_logo_empresa(empresa_id, file_storage):
    """Valida (tamanho + conteúdo real via Pillow) e salva o logo da empresa,
    sempre como PNG determinístico, sobrescrevendo qualquer logo anterior.
    Levanta ValueError (mensagem já pronta pra exibir ao usuário) se a
    validação falhar - nada é escrito em disco nesse caso. Retorna o nome do
    arquivo salvo (pra gravar em Empresa.logo_filename)."""
    dados = file_storage.read()
    if not dados:
        raise ValueError("Arquivo vazio.")
    if len(dados) > TAMANHO_MAXIMO_LOGO_BYTES:
        raise ValueError(f"Arquivo muito grande (máximo {TAMANHO_MAXIMO_LOGO_BYTES // (1024 * 1024)}MB).")

    try:
        imagem = Image.open(io.BytesIO(dados))
        formato = imagem.format  # já resolvido no open(), pela leitura do cabeçalho real do arquivo
        imagem.verify()  # decodifica de fato pra confirmar que não é um arquivo corrompido/malformado
    except (UnidentifiedImageError, OSError, ValueError):
        raise ValueError("Arquivo não é uma imagem PNG ou JPG válida.") from None

    if formato not in FORMATOS_LOGO_PERMITIDOS:
        raise ValueError("Só são aceitos arquivos PNG ou JPG (SVG e outros formatos não são permitidos).")

    # verify() invalida o objeto Image pra qualquer uso posterior (Pillow) -
    # reabre do zero pra realmente decodificar e salvar.
    imagem_final = Image.open(io.BytesIO(dados))
    imagem_final = imagem_final.convert("RGBA")

    os.makedirs(pasta_logos(), exist_ok=True)
    nome_arquivo = nome_arquivo_logo(empresa_id)
    imagem_final.save(os.path.join(pasta_logos(), nome_arquivo), format="PNG")
    return nome_arquivo


def remover_logo_empresa(empresa_id):
    """Apaga o arquivo físico do logo, se existir - idempotente (não-erro se
    já não houver arquivo nenhum)."""
    caminho = caminho_logo(empresa_id)
    if os.path.exists(caminho):
        os.remove(caminho)
