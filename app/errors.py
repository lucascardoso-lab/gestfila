from flask import render_template


def register_error_handlers(app):
    @app.errorhandler(403)
    def proibido(erro):
        return render_template("errors/403.html"), 403

    @app.errorhandler(404)
    def nao_encontrado(erro):
        return render_template("errors/404.html"), 404

    @app.errorhandler(429)
    def limite_excedido(erro):
        return render_template("errors/429.html"), 429
