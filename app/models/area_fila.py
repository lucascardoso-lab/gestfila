from app.extensions import db


class AreaFila(db.Model):
    """Área (adendo 10.76): agrupamento cadastrável de filas de UMA unidade -
    um setor físico ("2º Andar", "Mezanino", "Laboratório") - usado SÓ no admin
    e nos relatórios. Totem, painel de TV, atendimento, rodízio, Iniciar Turno
    e Redirecionar não leem a área (teste estrutural em
    tests/test_regressao_areas_fila.py).

    Nome no código "AreaFila"/`area_fila_id` (não "setor": `setor` já é um
    conceito interno - Senha.setor, logs, URLs recepcao/coleta).

    Uma fila pertence a no máximo UMA área (`Fila.area_fila_id`, coluna - o
    schema impede N:N e a dupla contagem). A área é sempre a ATUAL da fila: os
    relatórios não têm snapshot de área por evento (limitação documentada no
    10.76/seção 11, igual à "fila atual")."""

    __tablename__ = "areas_fila"

    id = db.Column(db.Integer, primary_key=True)
    empresa_id = db.Column(db.Integer, db.ForeignKey("empresas.id", name="fk_areas_fila_empresa"), nullable=False)
    unidade_id = db.Column(db.Integer, db.ForeignKey("unidades.id", name="fk_areas_fila_unidade"), nullable=False)
    nome = db.Column(db.String(100), nullable=False)
    ativo = db.Column(db.Boolean, default=True, nullable=False)

    __table_args__ = (
        db.UniqueConstraint("unidade_id", "nome", name="uq_areas_fila_unidade_nome"),
    )

    empresa = db.relationship("Empresa")
    unidade = db.relationship("Unidade")
    filas = db.relationship("Fila", back_populates="area_fila", order_by="Fila.nome")

    def __repr__(self):
        return f"<AreaFila {self.id} {self.nome}>"
