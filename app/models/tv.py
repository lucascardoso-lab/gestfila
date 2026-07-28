from app.extensions import db
from .associations import tv_filas


class Tv(db.Model):
    __tablename__ = "tvs"

    id = db.Column(db.Integer, primary_key=True)
    empresa_id = db.Column(db.Integer, db.ForeignKey("empresas.id"), nullable=False)
    unidade_id = db.Column(db.Integer, db.ForeignKey("unidades.id"), nullable=False)
    nome = db.Column(db.String(150), nullable=False)
    layout = db.Column(db.String(80), nullable=True)
    ativo = db.Column(db.Boolean, default=True, nullable=False)

    # slug legível pra URL pública (/painel/tv/<slug>) - fixado na criação (ver
    # app/admin/tvs.py::nova_tv), nunca recalculado ao renomear a TV depois (ver
    # app/utils/slug.py para a justificativa de não ser controle de acesso).
    slug = db.Column(db.String(160), unique=True, nullable=False)

    # áudio de chamada (seção 6) - por TV, não por fila/unidade, porque é uma
    # característica física do ambiente (sala barulhenta vs. silenciosa), não da fila.
    som_ativo = db.Column(db.Boolean, default=True, nullable=False)
    volume = db.Column(db.SmallInteger, default=100, nullable=False)  # 0-100

    empresa = db.relationship("Empresa")
    unidade = db.relationship("Unidade")
    filas = db.relationship("Fila", secondary=tv_filas, back_populates="tvs")

    def __repr__(self):
        return f"<Tv {self.id} {self.nome}>"
