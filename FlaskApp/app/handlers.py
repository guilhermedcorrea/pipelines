
from flask import request, redirect, flash, url_for
from werkzeug.exceptions import Forbidden
from flask_login import current_user



def registrar_handlers(app):

    @app.errorhandler(Forbidden)
    def _handle_403(e):
      
        flash("Você não tem permissão para acessar essa tela.", "danger")

      
        destino = request.referrer

   
        if not destino:
            destino = url_for("Paineis.lista_paineis")

        return redirect(destino)