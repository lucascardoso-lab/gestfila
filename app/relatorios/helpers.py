from dataclasses import dataclass
from datetime import datetime
from typing import NamedTuple, Optional

from flask import g, request
from flask_login import current_user

from app.admin.helpers import filas_da_empresa, unidades_da_empresa
from app.extensions import db
from app.models import AreaFila, Fila, LogExportacaoRelatorio, Unidade, Usuario
from app.utils.rotulos import rotulos_areas, rotulos_filas
from app.utils.tempo import agora_na_clinica, janela_dia_clinica_em_utc


@dataclass
class Filtros:
    empresa_id: int
    unidades_permitidas: Optional[list]  # None = sem restrição (super admin)
    unidade_id: Optional[int]
    fila_id: Optional[int]
    operador_id: Optional[int]
    data_inicio: datetime
    data_fim_exclusivo: datetime
    data_inicio_str: str
    data_fim_str: str
    # Adendo 10.76 - com default (no fim) para não quebrar quem monta Filtros à
    # mão (dashboard e testes): filtro de Área e o "Agrupar por: Área".
    area_fila_id: Optional[int] = None
    agrupar_por_area: bool = False


def _unidades_ids_visiveis(empresa):
    """IDs de unidade que o usuário logado pode ver dentro da empresa resolvida.

    Usado tanto para validar o filtro de unidade quanto para restringir QUAIS
    filas aparecem como opção (uma fila pertence a exatamente 1 unidade) - sem
    isso, um gerencial restrito a uma unidade veria nomes de filas de unidades
    que não pode acessar só por estarem listados no <select>.
    """
    ids = {u.id for u in unidades_da_empresa(empresa)}
    if g.unidades_permitidas is not None:
        ids &= set(g.unidades_permitidas)
    return ids


def _operadores_visiveis(empresa):
    """Usuários (operadores) que o usuário logado pode ver dentro da empresa resolvida.

    Mesmo padrão de _unidades_ids_visiveis: g.unidades_permitidas é None para super
    admin (sem restrição) e já contém TODAS as unidades da empresa para o perfil
    'admin' (ver middleware/tenant.py), então nenhum dos dois é afetado por este
    filtro - só usuários comuns (recepção/coleta/gerencial) restritos a unidades
    específicas deixam de ver operadores vinculados só a outras unidades.
    """
    query = Usuario.query.filter_by(empresa_id=empresa.id)
    if g.unidades_permitidas is not None:
        query = query.filter(Usuario.unidades.any(Unidade.id.in_(g.unidades_permitidas)))
    return query.order_by(Usuario.nome).all()


def resolver_filtros(empresa):
    """Lê os filtros da querystring, validando cada um contra o que o usuário
    logado pode efetivamente ver (g.unidades_permitidas + empresa resolvida).

    Um valor fora do escopo permitido é tratado como 'sem filtro' (None) em vez de
    abortar - são selects numa tela de relatório, não uma URL de edição de recurso,
    então não há tentativa de adivinhar um ID por tentativa e erro que justifique
    um 404; o pior caso é o relatório sair sem aquele filtro aplicado.
    """
    agora_clinica = agora_na_clinica()
    hoje = agora_clinica.date()
    fuso = agora_clinica.tzinfo

    data_inicio_str = request.args.get("data_inicio") or hoje.isoformat()
    try:
        data_inicio_data = datetime.strptime(data_inicio_str, "%Y-%m-%d").date()
    except ValueError:
        data_inicio_data = hoje
        data_inicio_str = hoje.isoformat()

    data_fim_str = request.args.get("data_fim") or hoje.isoformat()
    try:
        data_fim_data = datetime.strptime(data_fim_str, "%Y-%m-%d").date()
    except ValueError:
        data_fim_data = hoje
        data_fim_str = hoje.isoformat()

    # data_inicio_data/data_fim_data são dias CIVIS da clínica (o usuário escolhe
    # no calendário pensando no dia local), mas Senha.criada_em é gravado em UTC
    # naive (seção 8) - um datetime.combine(...) direto, sem passar pela mesma
    # conversão fuso->UTC de janela_dia_clinica_em_utc() (10.39/10.44), reabriria
    # o mesmo bug de 3h já corrigido nela, só que neste call-site.
    data_inicio, _ = janela_dia_clinica_em_utc(datetime.combine(data_inicio_data, datetime.min.time(), tzinfo=fuso))
    _, data_fim_exclusivo = janela_dia_clinica_em_utc(datetime.combine(data_fim_data, datetime.min.time(), tzinfo=fuso))

    unidades_ids_validas = _unidades_ids_visiveis(empresa)

    unidade_id = request.args.get("unidade_id", type=int)
    if unidade_id is not None and unidade_id not in unidades_ids_validas:
        unidade_id = None

    # Área (adendo 10.76): da empresa e de uma unidade visível; área de OUTRA
    # unidade que a escolhida é descartada (mesmo padrão silencioso da fila).
    unidade_da_area_valida = {
        a.id: a.unidade_id for a in AreaFila.query.filter_by(empresa_id=empresa.id).all()
        if a.unidade_id in unidades_ids_validas
    }
    area_fila_id = request.args.get("area_fila_id", type=int)
    if area_fila_id is not None and area_fila_id not in unidade_da_area_valida:
        area_fila_id = None
    if area_fila_id is not None and unidade_id is not None and unidade_da_area_valida[area_fila_id] != unidade_id:
        area_fila_id = None

    filas_validas = {f.id: f for f in filas_da_empresa(empresa) if f.unidade_id in unidades_ids_validas}
    unidade_da_fila_valida = {fila_id: f.unidade_id for fila_id, f in filas_validas.items()}
    fila_id = request.args.get("fila_id", type=int)
    if fila_id is not None and fila_id not in unidade_da_fila_valida:
        fila_id = None
    # Fila de OUTRA unidade que a escolhida no filtro (adendo 10.69): antes, os
    # dois WHERE eram aplicados juntos e o relatório saía vazio sem nenhum
    # aviso. Descartada (vira "todas as filas da unidade"), mesmo padrão
    # silencioso dos demais filtros fora de escopo acima.
    if fila_id is not None and unidade_id is not None and unidade_da_fila_valida[fila_id] != unidade_id:
        fila_id = None
    # Fila fora da Área escolhida (10.76): descartada também - o resultado seria
    # vazio sem aviso, como no caso da unidade acima.
    if fila_id is not None and area_fila_id is not None and filas_validas[fila_id].area_fila_id != area_fila_id:
        fila_id = None

    operadores_ids_validos = {u.id for u in _operadores_visiveis(empresa)}
    operador_id = request.args.get("operador_id", type=int)
    if operador_id is not None and operador_id not in operadores_ids_validos:
        operador_id = None

    return Filtros(
        empresa_id=empresa.id,
        unidades_permitidas=(list(unidades_ids_validas) if g.unidades_permitidas is not None else None),
        unidade_id=unidade_id,
        fila_id=fila_id,
        operador_id=operador_id,
        data_inicio=data_inicio,
        data_fim_exclusivo=data_fim_exclusivo,
        data_inicio_str=data_inicio_str,
        data_fim_str=data_fim_str,
        area_fila_id=area_fila_id,
        agrupar_por_area=request.args.get("agrupar") == "area",
    )


def opcoes_filtro(empresa):
    unidades_ids_validas = _unidades_ids_visiveis(empresa)
    filas = [f for f in filas_da_empresa(empresa) if f.unidade_id in unidades_ids_validas]
    return {
        "unidades": [u for u in unidades_da_empresa(empresa) if u.id in unidades_ids_validas],
        "filas": filas,
        # {fila_id: área_id} (strings; "" = sem área) para o JS do filtro de
        # Área (adendo 10.76) esconder as filas que não são da área escolhida.
        "area_da_fila": {str(f.id): str(f.area_fila_id) if f.area_fila_id else "" for f in filas},
        "rotulos_filas": rotulos_filas(empresa.id),
        # Áreas das unidades visíveis, inclusive INATIVAS (o template as marca
        # "(inativa)": continuam consultáveis nos relatórios - adendo 10.76).
        "areas": sorted(
            (a for a in AreaFila.query.filter_by(empresa_id=empresa.id).all() if a.unidade_id in unidades_ids_validas),
            key=lambda a: (a.unidade.nome, a.nome),
        ),
        "rotulos_areas": rotulos_areas(empresa.id),
        "operadores": _operadores_visiveis(empresa),
    }


def filtros_para_querystring(filtros, empresa):
    """Reconstrói os filtros atuais como dict para reusar em links (exportar CSV/Excel
    preservando o que está filtrado na tela, sem repetir esse parsing no template)."""
    qs = {"data_inicio": filtros.data_inicio_str, "data_fim": filtros.data_fim_str}
    if filtros.unidade_id:
        qs["unidade_id"] = filtros.unidade_id
    if filtros.fila_id:
        qs["fila_id"] = filtros.fila_id
    if filtros.operador_id:
        qs["operador_id"] = filtros.operador_id
    if filtros.area_fila_id:
        qs["area_fila_id"] = filtros.area_fila_id
    if filtros.agrupar_por_area:
        qs["agrupar"] = "area"
    if g.is_super_admin:
        qs["empresa_id"] = empresa.id
    return qs


def filtros_legiveis(filtros, opcoes):
    """Filtros aplicados, por extenso (não os valores brutos/IDs) - alimenta o
    cabeçalho do PDF exportado (adendo 10.67), pra o arquivo ser
    autoexplicativo fora do sistema (e-mail, impresso, pasta compartilhada).
    Omite um filtro inteiramente quando não está aplicado ("todas as
    unidades", por exemplo, não vira uma linha "Unidade: todas") - só o
    período sempre aparece, porque `resolver_filtros` sempre resolve um
    (default "hoje", adendo 10.54)."""
    partes = []
    if filtros.unidade_id:
        nome = next((u.nome for u in opcoes["unidades"] if u.id == filtros.unidade_id), None)
        if nome:
            partes.append(f"Unidade: {nome}")
    if filtros.area_fila_id:
        rotulo = opcoes["rotulos_areas"].get(filtros.area_fila_id)
        if rotulo:
            partes.append(f"Área: {rotulo}")
    if filtros.fila_id:
        rotulo = opcoes["rotulos_filas"].get(filtros.fila_id)
        if rotulo:
            partes.append(f"Fila: {rotulo}")
    if filtros.operador_id:
        nome = next((o.nome for o in opcoes["operadores"] if o.id == filtros.operador_id), None)
        if nome:
            partes.append(f"Operador: {nome}")

    if filtros.agrupar_por_area:
        partes.append("Agrupado por: Área")

    data_inicio = datetime.strptime(filtros.data_inicio_str, "%Y-%m-%d").strftime("%d/%m/%Y")
    data_fim = datetime.strptime(filtros.data_fim_str, "%Y-%m-%d").strftime("%d/%m/%Y")
    if data_inicio == data_fim:
        partes.append(f"Período: {data_inicio}")
    else:
        partes.append(f"Período: {data_inicio} a {data_fim}")

    return partes


def registrar_exportacao(tipo_relatorio, formato, empresa, filtros):
    """Log de auditoria de exportação (adendo 10.67) - grava pros 3 formatos
    (CSV, Excel e PDF; nenhum tinha esse registro antes). Motivado pela mesma
    lente de LGPD já usada no restante do projeto (README, seção "Dado
    sensível"): registrar quem exportou o quê é parte de conseguir responder
    "quem teve acesso a este dado" se algum dia for preciso - não é redundante
    com LogAtendimento (que audita o ATENDIMENTO em si, não o ACESSO/
    exportação de dados por um usuário do admin)."""
    db.session.add(
        LogExportacaoRelatorio(
            usuario_id=current_user.id,
            empresa_id=empresa.id,
            tipo_relatorio=tipo_relatorio,
            formato=formato,
            unidade_id=filtros.unidade_id,
            fila_id=filtros.fila_id,
            operador_id=filtros.operador_id,
            area_fila_id=filtros.area_fila_id,
            data_inicio=filtros.data_inicio_str,
            data_fim=filtros.data_fim_str,
        )
    )
    db.session.commit()


class MapasNomes(NamedTuple):
    filas: dict  # fila_id -> rótulo "[SIGLA] Nome" (app/utils/rotulos.py) - telas/PDF
    filas_nome: dict  # fila_id -> Fila.nome puro - CSV/Excel, ao lado da coluna "unidade"
    filas_unidade: dict  # fila_id -> unidade_id
    unidades: dict
    operadores: dict
    locais: dict
    # Adendo 10.76: área_id -> rótulo "[SIGLA] Nome" (tela/PDF) e nome puro
    # (CSV/Excel); fila_id -> área ATUAL da fila (None = "Sem área").
    areas: dict
    areas_nome: dict
    areas_unidade: dict
    fila_area: dict


def mapas_nomes(empresa):
    """IDs -> nome legível, para enriquecer as linhas dos relatórios antes de exibir/exportar.

    Fila tem duas formas (adendo 10.69): `filas` (rótulo com prefixo de
    unidade, para tela/PDF/dashboards) e `filas_nome` (nome puro, para
    CSV/Excel - que trazem a unidade numa coluna própria, mais fácil de
    filtrar numa planilha do que um prefixo dentro da célula)."""
    from app.models import Local

    filas = Fila.query.filter_by(empresa_id=empresa.id).all()
    unidades = {u.id: u.nome for u in Unidade.query.filter_by(empresa_id=empresa.id).all()}
    operadores = {u.id: u.nome for u in Usuario.query.filter_by(empresa_id=empresa.id).all()}
    # identificador_admin (não nome_exibicao) - telas gerenciais/relatórios usam
    # o identificador administrativo, combinado com o nome da fila pelo chamador
    # quando o formato "[fila] · [identificador_admin]" for aplicável (adendo
    # "Separação nome de exibição/identificador administrativo", BLUEPRINT.md).
    locais = {l.id: l.identificador_admin for l in Local.query.filter_by(empresa_id=empresa.id).all()}
    areas = AreaFila.query.filter_by(empresa_id=empresa.id).all()
    return MapasNomes(
        filas=rotulos_filas(empresa.id),
        filas_nome={f.id: f.nome for f in filas},
        filas_unidade={f.id: f.unidade_id for f in filas},
        unidades=unidades,
        operadores=operadores,
        locais=locais,
        areas=rotulos_areas(empresa.id),
        areas_nome={a.id: a.nome for a in areas},
        areas_unidade={a.id: a.unidade_id for a in areas},
        fila_area={f.id: f.area_fila_id for f in filas},
    )


SEM_AREA = "Sem área"


def rotulo_area(mapas, area_id, nome_puro=False):
    """Nome da área para exibição (tela/PDF: rótulo "[SIGLA] Nome") ou
    exportação (CSV/Excel: nome puro, a unidade vem em coluna própria). Fila
    sem área -> "Sem área" (adendo 10.76: nunca some dos agrupamentos)."""
    if area_id is None:
        return SEM_AREA
    return (mapas.areas_nome if nome_puro else mapas.areas).get(area_id, SEM_AREA)
