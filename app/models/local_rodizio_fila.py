from app.extensions import db


class LocalRodizioFila(db.Model):
    """Rodízio automático de chamada entre múltiplas filas por Local (adendo 10.8,
    Fase 10.8-A - só o schema, sem nenhuma lógica de seleção ainda).

    É um "association object" (classe mapeada de verdade), não uma tabela
    associativa simples como `local_filas_destino` - carrega `ordem` e
    `chamadas_seguidas` por par Local×Fila, além das duas FKs. Mesma unidade
    obrigatória entre `local_id` e `fila_id` - validado na camada de aplicação
    (Fase 10.8-B), nunca no schema.

    Tabela nasce vazia: um Local sem nenhuma linha aqui não entra no fluxo de
    rodízio - continua no fluxo por fila_id existente, sem nenhuma mudança de
    comportamento (ver adendo 10.8, seção 5, BLUEPRINT.md).
    """

    __tablename__ = "local_rodizio_filas"

    local_id = db.Column(db.Integer, db.ForeignKey("locais.id"), primary_key=True)
    fila_id = db.Column(db.Integer, db.ForeignKey("filas.id"), primary_key=True)
    ordem = db.Column(db.SmallInteger, default=0, nullable=False)
    chamadas_seguidas = db.Column(db.SmallInteger, default=1, nullable=False)

    local = db.relationship("Local", back_populates="rodizio_ordenado")
    fila = db.relationship("Fila")

    def __repr__(self):
        return f"<LocalRodizioFila local={self.local_id} fila={self.fila_id}>"
