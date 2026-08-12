from app.extensions import db


class TotemBotao(db.Model):
    __tablename__ = "totem_botoes"

    id = db.Column(db.Integer, primary_key=True)
    empresa_id = db.Column(db.Integer, db.ForeignKey("empresas.id"), nullable=False)
    unidade_id = db.Column(db.Integer, db.ForeignKey("unidades.id"), nullable=False)
    parent_id = db.Column(db.Integer, db.ForeignKey("totem_botoes.id"), nullable=True)
    servico_id = db.Column(db.Integer, db.ForeignKey("servicos.id"), nullable=True)  # legado - não usar
    fila_id = db.Column(db.Integer, db.ForeignKey("filas.id"), nullable=True)
    label = db.Column(db.String(120), nullable=False)

    # adendo 10.47: sigla usada na composição do código da senha (seção 2 do
    # BLUEPRINT) - antes vivia em `Fila.sigla` (deprecada, ver app/models/fila.py),
    # agora pertence ao botão que efetivamente gera a senha. Só obrigatória em
    # botões-FOLHA (fila_id preenchido); botões-menu nunca têm sigla, mesma
    # convenção já usada para `fila_id` desde a 10.1. Permite dois botões
    # diferentes apontarem pra mesma fila com siglas diferentes - o sequencial
    # numérico continua compartilhado (escopado por fila_id), só o prefixo muda.
    sigla = db.Column(db.String(4), nullable=True)

    ordem = db.Column(db.Integer, default=0, nullable=False)
    ativo = db.Column(db.Boolean, default=True, nullable=False)

    empresa = db.relationship("Empresa")
    unidade = db.relationship("Unidade")
    pai = db.relationship("TotemBotao", remote_side=[id], back_populates="filhos")
    filhos = db.relationship("TotemBotao", back_populates="pai")
    servico = db.relationship("Servico")  # legado - não usar em código novo
    fila = db.relationship("Fila")

    __table_args__ = (
        db.UniqueConstraint("unidade_id", "sigla", name="uq_totem_botao_unidade_sigla"),
    )

    def __repr__(self):
        return f"<TotemBotao {self.id} {self.label}>"
