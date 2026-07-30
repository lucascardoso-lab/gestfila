"""Slug legível para URLs públicas de TV/Totem (não é mitigação de segurança - as
rotas /painel/tv/* e /totem/* já são intencionalmente públicas sem login, seção 6
do BLUEPRINT; um nome de TV/Totem é tão adivinhável quanto um ID pequeno por quem
conhece a nomenclatura da clínica. O único ganho aqui é uma URL mais legível/fácil
de digitar no equipamento físico, nunca controle de acesso)."""
import re
import unicodedata


def gerar_slug_base(nome):
    """lowercase, sem acento, espaços/pontuação viram hífen, hífens duplicados
    colapsados. Fallback 'item' se o nome não tiver nenhum caractere ASCII
    alfanumérico (ex.: nome só com emoji/CJK) - nunca retorna string vazia.

    Indicadores ordinais (º/ª, comuns em nomes tipo "1º Andar") são removidos antes
    da normalização - a decomposição NFKD padrão os transformaria numa letra 'o'/'a'
    literal ("1º Andar" -> "1o-andar"), o que não é o resultado esperado aqui.
    """
    texto = nome.replace("º", "").replace("ª", "")
    texto = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode("ascii")
    texto = re.sub(r"[^a-zA-Z0-9]+", "-", texto).strip("-").lower()
    return texto or "item"


def gerar_slug_unico(nome, ja_existe):
    """`ja_existe(candidato) -> bool` verifica unicidade (query no banco na app,
    checagem em memória durante uma migration). Só sufixa quando há colisão real -
    a primeira colisão vira '-2', não sufixa tudo por padrão."""
    base = gerar_slug_base(nome)
    if not ja_existe(base):
        return base
    n = 2
    while ja_existe(f"{base}-{n}"):
        n += 1
    return f"{base}-{n}"


def gerar_slug_audio_local(nome):
    """Slug para o nome do arquivo de áudio pré-gravado de um Local
    (`app/static/audio/local_<slug>.wav`) - fallback de TTS para Smart TVs cujo
    motor de voz do sistema não funciona (BLUEPRINT.md, adendo do áudio pré-gravado).

    Diferente de `gerar_slug_base` (que usa HÍFEN, para URL): aqui o separador é
    UNDERSCORE, para casar com a convenção de nome dos arquivos gravados
    (`local_guiche_01.wav`, `local_recepcao_01.wav`, etc.).

    FONTE ÚNICA DE VERDADE: este slug é calculado só aqui, no servidor, e enviado
    pronto no payload da chamada (`local_audio_slug`); o JS do painel de TV nunca
    recalcula slug nenhum - assim não há duas implementações para divergir (a
    escolha foi eliminar a divergência por construção, não só detectá-la por teste).

    Normalização: minúsculo, sem acento, ordinais º/ª removidos (mesmo motivo de
    gerar_slug_base), qualquer sequência de não-alfanuméricos vira um único '_',
    underscores das pontas removidos. Fallback 'local' se sobrar vazio - nunca
    retorna string vazia."""
    texto = nome.replace("º", "").replace("ª", "")
    texto = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode("ascii")
    texto = re.sub(r"[^a-zA-Z0-9]+", "_", texto).strip("_").lower()
    return texto or "local"
