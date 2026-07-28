from app.extensions import db


class Totem(db.Model):
    """Totem físico (kiosk) - seção 10.1 do BLUEPRINT (fusão de Serviço em Fila).

    Uma unidade pode ter mais de um totem físico; uma fila pode ser restrita a um
    totem específico (fila.totem_id) ou ficar disponível em todos os totens da
    unidade (totem_id NULL). Desde a seção 10.6 (impressão física), o acesso
    público passou a ser por totem individual (/totem/<slug>/...), não só por
    unidade - necessário para saber qual impressora local (
    `nome_impressora_windows`) usar quando dois totens da mesma unidade têm
    impressoras diferentes. O slug é único globalmente (todas as empresas), então
    a URL não precisa mais carregar unidade_id separado - `unidade` já vem direto
    da relationship.
    """

    __tablename__ = "totens"

    id = db.Column(db.Integer, primary_key=True)
    empresa_id = db.Column(db.Integer, db.ForeignKey("empresas.id"), nullable=False)
    unidade_id = db.Column(db.Integer, db.ForeignKey("unidades.id"), nullable=False)
    nome = db.Column(db.String(150), nullable=False)
    nome_impressora_windows = db.Column(db.String(120), nullable=True)
    ativo = db.Column(db.Boolean, default=True, nullable=False)

    # slug legível pra URL pública (/totem/<slug>) - fixado na criação (ver
    # app/admin/totens.py::novo_totem), nunca recalculado ao renomear o totem depois.
    slug = db.Column(db.String(160), unique=True, nullable=False)

    empresa = db.relationship("Empresa")
    unidade = db.relationship("Unidade")

    def __repr__(self):
        return f"<Totem {self.id} {self.nome}>"
