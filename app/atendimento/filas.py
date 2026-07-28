from flask import flash, redirect, render_template, request, url_for

from app.extensions import db
from app.models import Senha
from app.utils.decorators import roles_required

from . import atendimento_bp
from .helpers import fila_acessivel_ou_404, filas_acessiveis

PERFIS_OPERACIONAIS = ("atendente", "executor", "atendente_executor", "admin")


@atendimento_bp.route("/filas")
@roles_required(*PERFIS_OPERACIONAIS)
def listar_filas():
    filas = filas_acessiveis()
    contagens = {
        fila.id: {
            "aguardando": Senha.query.filter_by(fila_id=fila.id, status="aguardando").count(),
            "prioridade": Senha.query.filter_by(
                fila_id=fila.id, status="aguardando", prioridade=True
            ).count(),
        }
        for fila in filas
    }
    return render_template("atendimento/listar_filas.html", filas=filas, contagens=contagens)


@atendimento_bp.route("/filas/<int:fila_id>/alocar", methods=["GET", "POST"])
@roles_required(*PERFIS_OPERACIONAIS)
def alocar_fila(fila_id):
    fila = fila_acessivel_ou_404(fila_id)

    if request.method == "POST":
        prioridade_rodizio = request.form.get("prioridade_rodizio", type=int)
        if prioridade_rodizio is None or not (0 <= prioridade_rodizio <= 20):
            flash("Quantidade de chamadas seguidas deve ser um número entre 0 e 20.", "error")
            return render_template("atendimento/alocar_fila.html", fila=fila)

        fila.prioridade_rodizio = prioridade_rodizio
        db.session.commit()
        flash(f"Rodízio da fila '{fila.nome}' atualizado.", "info")
        return redirect(url_for("atendimento.listar_filas"))

    return render_template("atendimento/alocar_fila.html", fila=fila)
