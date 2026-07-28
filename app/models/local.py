from app.extensions import db
from .associations import local_filas_destino

TIPO_LOCAL = ("recepcao", "coleta", "outro")


class Local(db.Model):
    """Guichê/posto de atendimento (seção 10.3) - antes era texto livre digitado pelo
    operador na tela de Atendimento (campo `senhas.guiche_local`), o que gerava o mesmo
    posto físico identificado de formas diferentes por operadores diferentes."""

    __tablename__ = "locais"

    id = db.Column(db.Integer, primary_key=True)
    empresa_id = db.Column(db.Integer, db.ForeignKey("empresas.id"), nullable=False)
    unidade_id = db.Column(db.Integer, db.ForeignKey("unidades.id"), nullable=False)
    nome = db.Column(db.String(150), nullable=False)
    tipo = db.Column(db.Enum(*TIPO_LOCAL, name="tipo_local"), nullable=True)
    ativo = db.Column(db.Boolean, default=True, nullable=False)

    empresa = db.relationship("Empresa")
    unidade = db.relationship("Unidade")

    # restrição opcional de redirecionamento (bloco "Redirecionar" da tela de
    # Atendimento) - lista vazia = sem restrição, mantém o comportamento histórico de
    # mostrar todas as filas ativas da unidade (ver atendimento/helpers.py::filas_destino).
    # Sempre filas da MESMA unidade deste Local - validado em admin/locais.py, nunca no
    # schema (a tabela associativa não tem unidade_id própria).
    filas_destino_permitidas = db.relationship("Fila", secondary=local_filas_destino)

    # rodízio automático entre múltiplas filas (adendo 10.8, Fase 10.8-A - só o
    # schema; a lógica de seleção vem na Fase 10.8-C). Lista vazia = sem rodízio
    # configurado, continua no fluxo por fila_id existente (ver BLUEPRINT.md).
    # Ordenado por `ordem` - mesmo campo usado por selecionar_proxima_fila_e_senha().
    rodizio_ordenado = db.relationship(
        "LocalRodizioFila", back_populates="local",
        order_by="LocalRodizioFila.ordem", cascade="all, delete-orphan",
    )

    def __repr__(self):
        return f"<Local {self.id} {self.nome}>"
