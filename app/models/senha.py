from datetime import datetime

from app.extensions import db
from app.utils.tempo import janela_dia_clinica_em_utc

STATUS_SENHA = ("aguardando", "chamada", "em_atendimento", "finalizada", "cancelada")
# Status de uma senha ainda "aberta" (não finalizada nem cancelada) - o que a
# varredura de dias anteriores encerra (adendo 10.77).
STATUS_ABERTOS = ("aguardando", "chamada", "em_atendimento")
SETOR = ("recepcao", "coleta", "outro")


class Senha(db.Model):
    __tablename__ = "senhas"

    id = db.Column(db.Integer, primary_key=True)
    empresa_id = db.Column(db.Integer, db.ForeignKey("empresas.id"), nullable=False)
    unidade_id = db.Column(db.Integer, db.ForeignKey("unidades.id"), nullable=False)
    servico_id = db.Column(db.Integer, db.ForeignKey("servicos.id"), nullable=True)  # legado - não usar
    fila_id = db.Column(db.Integer, db.ForeignKey("filas.id"), nullable=False)

    codigo = db.Column(db.String(10), nullable=False)
    sequencial = db.Column(db.Integer, nullable=False)
    prioridade = db.Column(db.Boolean, default=False, nullable=False)
    status = db.Column(db.Enum(*STATUS_SENHA, name="status_senha"), default="aguardando", nullable=False)

    nome_paciente = db.Column(db.String(120), nullable=True)
    guiche_local = db.Column(db.String(60), nullable=True)  # legado - não usar (seção 10.3 - ver Local)
    local_id = db.Column(db.Integer, db.ForeignKey("locais.id"), nullable=True)
    observacoes = db.Column(db.Text, nullable=True)

    tipo_senha_id = db.Column(db.Integer, db.ForeignKey("tipos_senha.id"), nullable=True)
    qtd_acompanhantes = db.Column(db.SmallInteger, default=0, nullable=True)  # seção 10.5 - só informativo

    criada_em = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    chamada_em = db.Column(db.DateTime, nullable=True)
    chamada_nome_em = db.Column(db.DateTime, nullable=True)
    atendimento_iniciado_em = db.Column(db.DateTime, nullable=True)
    finalizada_em = db.Column(db.DateTime, nullable=True)

    operador_id = db.Column(db.Integer, db.ForeignKey("usuarios.id"), nullable=True)
    setor = db.Column(db.Enum(*SETOR, name="setor_senha"), nullable=True)

    servico = db.relationship("Servico")  # legado - não usar em código novo
    fila = db.relationship("Fila")
    local = db.relationship("Local")
    operador = db.relationship("Usuario")
    tipo_senha = db.relationship("TipoSenha")

    __table_args__ = (
        db.Index("ix_senhas_empresa_unidade_status", "empresa_id", "unidade_id", "status"),
        db.Index("ix_senhas_servico_criada_em", "servico_id", "criada_em"),
    )

    def __repr__(self):
        return f"<Senha {self.id} {self.codigo}>"


def criada_hoje_na_clinica():
    """Condição SQL "senha criada hoje no dia civil da clínica" (adendo 10.77) -
    aplicada a toda listagem/contagem/seleção de senhas 'aguardando' (telas de
    atendimento, Chamar, dashboards). Defesa em profundidade: a varredura do
    middleware (`encerrar_senhas_de_dias_anteriores`) já cancela as de dias
    anteriores, mas se ela falhar uma senha velha nunca é chamada antes da de
    hoje (o FIFO por `criada_em` a colocaria na frente) nem confundida com outra
    de mesmo código (o sequencial zera diariamente). Só o limite inferior da
    janela do 10.39 - não existe `criada_em` no futuro."""
    inicio_hoje, _fim = janela_dia_clinica_em_utc()
    return Senha.criada_em >= inicio_hoje


def senhas_abertas_de_dias_anteriores():
    """Query (não executada) das senhas ainda abertas (`STATUS_ABERTOS`)
    criadas ANTES do dia civil de hoje da clínica (adendo 10.77) - limite pela
    janela do 10.39, nunca por `.date()` em UTC. Base única da varredura do
    middleware, do comando `flask encerrar-pendentes` e do alerta do dashboard
    de Relatórios."""
    inicio_hoje, _fim = janela_dia_clinica_em_utc()
    return Senha.query.filter(Senha.status.in_(STATUS_ABERTOS), Senha.criada_em < inicio_hoje)
