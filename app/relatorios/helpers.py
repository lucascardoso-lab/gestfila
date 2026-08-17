from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from flask import g, request

from app.admin.helpers import filas_da_empresa, unidades_da_empresa
from app.models import Unidade, Usuario
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

    filas_ids_validas = {
        f.id for f in filas_da_empresa(empresa) if f.unidade_id in unidades_ids_validas
    }
    fila_id = request.args.get("fila_id", type=int)
    if fila_id is not None and fila_id not in filas_ids_validas:
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
    )


def opcoes_filtro(empresa):
    unidades_ids_validas = _unidades_ids_visiveis(empresa)
    return {
        "unidades": [u for u in unidades_da_empresa(empresa) if u.id in unidades_ids_validas],
        "filas": [f for f in filas_da_empresa(empresa) if f.unidade_id in unidades_ids_validas],
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
    if g.is_super_admin:
        qs["empresa_id"] = empresa.id
    return qs


def mapas_nomes(empresa):
    """IDs -> nome legível, para enriquecer as linhas dos relatórios antes de exibir/exportar."""
    from app.models import Fila, Local, Unidade

    filas = {f.id: f.nome for f in Fila.query.filter_by(empresa_id=empresa.id).all()}
    unidades = {u.id: u.nome for u in Unidade.query.filter_by(empresa_id=empresa.id).all()}
    operadores = {u.id: u.nome for u in Usuario.query.filter_by(empresa_id=empresa.id).all()}
    # identificador_admin (não nome_exibicao) - telas gerenciais/relatórios usam
    # o identificador administrativo, combinado com o nome da fila pelo chamador
    # quando o formato "[fila] · [identificador_admin]" for aplicável (adendo
    # "Separação nome de exibição/identificador administrativo", BLUEPRINT.md).
    locais = {l.id: l.identificador_admin for l in Local.query.filter_by(empresa_id=empresa.id).all()}
    return filas, unidades, operadores, locais
