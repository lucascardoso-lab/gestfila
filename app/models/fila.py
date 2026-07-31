from app.extensions import db
from .associations import fila_tipos_senha, tv_filas

DIAS_SEMANA = ("domingo", "segunda", "terca", "quarta", "quinta", "sexta", "sabado")

HORARIO_FUNCIONAMENTO_PADRAO = {
    dia: {"manha": True, "tarde": True} for dia in DIAS_SEMANA
}


class Fila(db.Model):
    """Fila de atendimento - seção 10.1 do BLUEPRINT: a partir da fusão de Serviço em
    Fila, esta tabela passou a carregar diretamente sigla/digitos_sequencial/
    zera_diariamente/prioridade_padrao (antes em `servicos`, tabela mantida como
    "morta" só para não descartar o histórico - ver migration). `servico_id`
    permanece nullable só por compatibilidade com linhas antigas; código novo nunca
    deve lê-lo ou escrevê-lo.
    """

    __tablename__ = "filas"

    id = db.Column(db.Integer, primary_key=True)
    empresa_id = db.Column(db.Integer, db.ForeignKey("empresas.id"), nullable=False)
    unidade_id = db.Column(db.Integer, db.ForeignKey("unidades.id"), nullable=False)
    servico_id = db.Column(db.Integer, db.ForeignKey("servicos.id"), nullable=True)  # legado - não usar
    nome = db.Column(db.String(150), nullable=False)

    sigla = db.Column(db.String(4), nullable=False)
    digitos_sequencial = db.Column(db.SmallInteger, default=3, nullable=False)
    zera_diariamente = db.Column(db.Boolean, default=True, nullable=False)
    prioridade_padrao = db.Column(db.SmallInteger, default=0, nullable=False)
    prioridade_rodizio = db.Column(db.SmallInteger, default=0, nullable=False)

    totem_id = db.Column(db.Integer, db.ForeignKey("totens.id"), nullable=True)  # NULL = todos os totens da unidade
    tv_id = db.Column(db.Integer, db.ForeignKey("tvs.id"), nullable=True)  # atalho de cadastro - popula tv_filas

    horario_funcionamento = db.Column(db.JSON, nullable=False, default=lambda: dict(HORARIO_FUNCIONAMENTO_PADRAO))
    tempo_espera_maximo_min = db.Column(db.Integer, nullable=True)
    tempo_atendimento_maximo_min = db.Column(db.Integer, nullable=True)
    alerta_email = db.Column(db.Boolean, default=False, nullable=False)

    # seção 10.4: quando o tipo de senha escolhido tem peso_prioridade == 0 (tratado
    # como "tipo padrão/normal"), o prefixo dele só entra no código se este campo
    # estiver marcado - evita um prefixo redundante tipo "LABN001" por padrão.
    exibir_prefixo_tipo_quando_normal = db.Column(db.Boolean, default=False, nullable=False)

    ativo = db.Column(db.Boolean, default=True, nullable=False)

    empresa = db.relationship("Empresa")
    unidade = db.relationship("Unidade")
    servico = db.relationship("Servico")  # legado - não usar em código novo
    totem = db.relationship("Totem")
    tvs = db.relationship("Tv", secondary=tv_filas, back_populates="filas")
    tipos_senha = db.relationship("TipoSenha", secondary=fila_tipos_senha, back_populates="filas")

    def __repr__(self):
        return f"<Fila {self.id} {self.nome}>"

    def aberta_em(self, momento=None):
        """Verifica o horário de funcionamento configurado (seção 10.1).

        `manhã` = 00:00–11:59:59 e `tarde` = 12:00–23:59:59 (o JSON só guarda
        booleano por período, sem horário exato - meio-dia é o corte mais simples e
        menos arbitrário dado o que a seção 10.1 especifica). "Horário de
        funcionamento" é um conceito de relógio de parede da clínica, não um
        instante UTC absoluto - por isso usa a hora no FUSO DA CLÍNICA
        (`app/utils/tempo.py`, config `FUSO_HORARIO`), resolvida no código e
        independente do fuso do SO do servidor (ver 10.36). Um `momento` explícito
        (aware ou naive) passado pelo chamador é respeitado como veio.
        """
        from app.utils.tempo import agora_na_clinica  # import local: evita ciclo model<->util no import

        momento = momento or agora_na_clinica()
        dia = DIAS_SEMANA[(momento.weekday() + 1) % 7]  # Python: segunda=0 -> nosso índice 1
        periodo = "manha" if momento.hour < 12 else "tarde"
        config_dia = (self.horario_funcionamento or {}).get(dia, {})
        return bool(config_dia.get(periodo, False))

    __table_args__ = (
        db.UniqueConstraint("unidade_id", "sigla", name="uq_fila_unidade_sigla"),
    )
