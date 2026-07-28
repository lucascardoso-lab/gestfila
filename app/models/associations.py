from app.extensions import db

usuario_perfil = db.Table(
    "usuario_perfil",
    db.Column("usuario_id", db.Integer, db.ForeignKey("usuarios.id"), primary_key=True),
    db.Column("perfil_id", db.Integer, db.ForeignKey("perfis.id"), primary_key=True),
)

usuario_unidade = db.Table(
    "usuario_unidade",
    db.Column("usuario_id", db.Integer, db.ForeignKey("usuarios.id"), primary_key=True),
    db.Column("unidade_id", db.Integer, db.ForeignKey("unidades.id"), primary_key=True),
)

tv_filas = db.Table(
    "tv_filas",
    db.Column("tv_id", db.Integer, db.ForeignKey("tvs.id"), primary_key=True),
    db.Column("fila_id", db.Integer, db.ForeignKey("filas.id"), primary_key=True),
)

fila_tipos_senha = db.Table(
    "fila_tipos_senha",
    db.Column("fila_id", db.Integer, db.ForeignKey("filas.id"), primary_key=True),
    db.Column("tipo_senha_id", db.Integer, db.ForeignKey("tipos_senha.id"), primary_key=True),
)

# restrição opcional de redirecionamento por Local (bloco "Redirecionar" da tela de
# Atendimento) - sem unidade_id própria: Local e Fila já pertencem a uma unidade cada,
# a validação de que ambas batem é feita na camada de aplicação (admin/locais.py),
# nunca no schema.
local_filas_destino = db.Table(
    "local_filas_destino",
    db.Column("local_id", db.Integer, db.ForeignKey("locais.id"), primary_key=True),
    db.Column("fila_id", db.Integer, db.ForeignKey("filas.id"), primary_key=True),
)
