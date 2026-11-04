"""Exibição do tipo de senha (adendo 10.84) nas telas do OPERADOR.

Uma função só alimenta os dois caminhos de cada tabela da tela de Atendimento:
o render inicial (Jinja, global registrado em `create_app`) e o JSON de
`/dados` (serializadores de `atendimento/chamada.py`). Assim o tipo não some
no primeiro refresh por WebSocket.

Nunca usada no painel de TV nem no totem: o payload `chamada` da TV
(`_payload_chamada`) não leva nome de tipo.
"""
import re

# Formato aceito no cadastro de tipo de senha (admin/tipos_senha.py importa daqui).
COR_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")


def cor_valida(cor):
    """Devolve a cor se for um hex #RRGGBB, senão None. Revalida na exibição
    (não só no cadastro) porque a cor vai num atributo `style`: um valor gravado
    fora do formulário (SQL manual, import) não pode virar CSS arbitrário."""
    if cor and COR_RE.match(cor):
        return cor
    return None


def tipo_senha_exibicao(senha):
    """{"nome", "cor"} do tipo escolhido na emissão, ou None se a senha não tem tipo.

    Tipo INATIVO continua aparecendo pelo nome: desativar só impede novas emissões,
    a senha já emitida segue sendo daquele tipo (excluir um tipo com senhas é
    bloqueado em admin/tipos_senha.py, então o vínculo nunca fica órfão).
    A cor é a do cadastro, sem regra própria: vai só no marcador ao lado do nome."""
    tipo = senha.tipo_senha
    if tipo is None:
        return None
    return {"nome": tipo.nome, "cor": cor_valida(tipo.cor)}
