"""Tempo de espera no rodapé do painel de TV (adendo 10.88).

Número mostrado: MEDIANA da Espera inicial (emissão -> 1ª chamada) das senhas
CHAMADAS na última hora, nas filas da TV. É a mesma métrica da coluna "Espera
inicial" do Tempos por senha e do cartão do Dashboard (10.73/10.86) - só a
janela muda (última hora, não o período do filtro).

- Segmentos: os mesmos de `_segmentos_espera_por_senha` (10.66), sem
  pareamento novo; só os que abrem em 'gerada' (= a Espera inicial de
  `tempos_por_senha`, que é sempre o 1º segmento da senha). Não chama
  `tempos_por_senha` inteiro (5 a 7x mais caro, medido na FASE 1).
- Fila: a do SEGMENTO (snapshot da chamada = onde o paciente esperou), nunca a
  fila ATUAL da senha - uma senha emitida na Recepção e realocada para a Coleta
  conta na Recepção. Filas da TV = `tv.filas` (tv_filas), nunca `Fila.tv_id`.
- Janela: segmentos FECHADOS (chamada) em [max(agora - 60 min, início do dia
  da clínica), agora], só de senhas emitidas hoje (dia da clínica, 10.74) - a
  senha da noite anterior não entra.
- Menos de `MINIMO_ESPERAS_TV` esperas na janela -> None (número escondido).
- Cache em memória (`CACHE_SEGUNDOS`) por escopo empresa + unidades das filas +
  dia, recortado por TV: várias TVs da mesma unidade calculam uma vez. Vale
  por processo - com mais de 1 worker, cada um tem o seu (só recalcula mais
  vezes; o número é o mesmo).
"""
from datetime import timedelta
from statistics import median

from app.relatorios.helpers import Filtros
from app.relatorios.queries import _segmentos_espera_por_senha
from app.utils.tempo import agora_utc_naive, janela_dia_clinica_em_utc, segundos_arredondados

# Calibrável com os dados de produção (Q4 da FASE 1).
MINIMO_ESPERAS_TV = 5
JANELA_ESPERA_TV = timedelta(minutes=60)
CACHE_SEGUNDOS = 60

LIMITE_MENOS_DE = 5 * 60   # abaixo: "menos de 5 min"
LIMITE_MAIS_DE = 60 * 60   # acima: "mais de 1 hora"
PASSO_ARREDONDAMENTO = 5 * 60

# {(empresa_id, unidades, inicio_dia_utc): (calculado_em_utc, [(fila_id, chamada_utc, segundos)])}
_cache = {}


def limpar_cache():
    _cache.clear()


def texto_tempo_espera(segundos):
    """Texto arredondado para o paciente (sem mm:ss): "menos de 5 min", "~N min"
    (N = múltiplo de 5 mais próximo, meio para cima) ou "mais de 1 hora". Parte
    dos segundos inteiros de `segundos_arredondados` (mesmo critério "meio para
    cima" dos relatórios, 10.75). None -> None."""
    total = segundos_arredondados(segundos)
    if total is None:
        return None
    if total < LIMITE_MENOS_DE:
        return "menos de 5 min"
    if total > LIMITE_MAIS_DE:
        return "mais de 1 hora"
    minutos = (total + PASSO_ARREDONDAMENTO // 2) // PASSO_ARREDONDAMENTO * 5
    return f"~{minutos} min"


def _esperas_iniciais_do_escopo(empresa_id, unidade_ids, inicio_dia, fim_dia, agora):
    """Esperas iniciais (fila do segmento, instante da chamada, segundos) das
    senhas emitidas hoje no escopo - do cache quando calculado há menos de
    `CACHE_SEGUNDOS` (no relógio da clínica, `agora_utc_naive`)."""
    chave = (empresa_id, unidade_ids, inicio_dia)
    guardado = _cache.get(chave)
    if guardado is not None and timedelta(0) <= agora - guardado[0] < timedelta(seconds=CACHE_SEGUNDOS):
        return guardado[0], guardado[1]

    filtros = Filtros(
        empresa_id=empresa_id, unidades_permitidas=sorted(unidade_ids), unidade_id=None, fila_id=None,
        operador_id=None, data_inicio=inicio_dia, data_fim_exclusivo=fim_dia,
        data_inicio_str="", data_fim_str="",
    )
    esperas = [
        (fila_id, fechamento.timestamp, (fechamento.timestamp - abertura.timestamp).total_seconds())
        for abertura, fechamento, fila_id, _unidade_id in _segmentos_espera_por_senha(filtros)
        if abertura.evento == "gerada"
    ]
    # Entradas vencidas saem a cada novo cálculo (o cache não cresce com os dias).
    for k in [k for k, (quando, _) in _cache.items() if not timedelta(0) <= agora - quando < timedelta(seconds=CACHE_SEGUNDOS)]:
        del _cache[k]
    _cache[chave] = (agora, esperas)
    return agora, esperas


def esperas_na_janela(tv):
    """Segundos das esperas iniciais da janela atual nas filas da TV."""
    filas = [f for f in tv.filas if f.empresa_id == tv.empresa_id]
    if not filas:
        return []
    inicio_dia, fim_dia = janela_dia_clinica_em_utc()
    calculado_em, esperas = _esperas_iniciais_do_escopo(
        tv.empresa_id, frozenset(f.unidade_id for f in filas), inicio_dia, fim_dia, agora_utc_naive(),
    )
    fila_ids = {f.id for f in filas}
    inicio = max(calculado_em - JANELA_ESPERA_TV, inicio_dia)
    return [seg for fila_id, chamada, seg in esperas if fila_id in fila_ids and inicio <= chamada <= calculado_em]


def mediana_espera_tv(tv):
    """Mediana (segundos exatos) da janela, ou None abaixo do mínimo de amostra."""
    valores = esperas_na_janela(tv)
    if len(valores) < MINIMO_ESPERAS_TV:
        return None
    return median(valores)


def texto_espera_tv(tv):
    """Texto do rodapé da TV, ou None (opção desligada, sem filas ou sem amostra)."""
    if not tv.mostra_tempo_espera:
        return None
    return texto_tempo_espera(mediana_espera_tv(tv))
