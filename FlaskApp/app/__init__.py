import os
from datetime import timedelta
from flask import Flask, redirect, url_for
from sqlalchemy import event

from .extensions import db, login_manager, csrf, limiter, cache, socketio

from config import (
    SQLALCHEMY_DATABASE_URI,
    SECRET_KEY,
    RECAPTCHA_PUBLIC_KEY,
    RECAPTCHA_PRIVATE_KEY,
    RATELIMIT_STORAGE_URI,
    CACHE_REDIS_URL,
    MENSAGERIA_SOCKET_TOKEN,
    EXTENSOES_PERMITIDAS_CHECKING,
    TAMANHO_MAXIMO_UPLOAD_MB,
    LARGURA_MAXIMA_IMAGEM,
    ALTURA_MAXIMA_IMAGEM,
)

from .handlers import registrar_handlers


def create_app() -> Flask:
    app = Flask(__name__)
    registrar_handlers(app)

    app.config["SECRET_KEY"] = SECRET_KEY
    if not app.config["SECRET_KEY"]:
        raise RuntimeError("SECRET_KEY não definida no .env ou ambiente!")

    app.config["SQLALCHEMY_DATABASE_URI"] = SQLALCHEMY_DATABASE_URI
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

    app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
        "pool_pre_ping": True,
        "pool_recycle": 1800,
        "pool_size": 10,
        "max_overflow": 20,
        "pool_timeout": 30,
        "connect_args": {
            "timeout": 60,
        },
    }

    app.config["EXTENSOES_PERMITIDAS_CHECKING"] = EXTENSOES_PERMITIDAS_CHECKING
    app.config["TAMANHO_MAXIMO_UPLOAD_MB"] = TAMANHO_MAXIMO_UPLOAD_MB
    app.config["LARGURA_MAXIMA_IMAGEM"] = LARGURA_MAXIMA_IMAGEM
    app.config["ALTURA_MAXIMA_IMAGEM"] = ALTURA_MAXIMA_IMAGEM
    app.config["MAX_CONTENT_LENGTH"] = TAMANHO_MAXIMO_UPLOAD_MB * 1024 * 1024

    app.config["CHECKING_PASTA_TEMP"] = os.getenv(
        "CHECKING_PASTA_TEMP",
        "/home/guilherme_correa/PythonJobs/pipelines/FlaskApp/chekin/_temp",
    )

    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    app.config["SESSION_COOKIE_SECURE"] = False
    app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(hours=8)
    app.config["SESSION_REFRESH_EACH_REQUEST"] = True

    app.config["RATELIMIT_STORAGE_URI"] = RATELIMIT_STORAGE_URI

    app.config["CACHE_TYPE"] = "RedisCache"
    app.config["CACHE_REDIS_URL"] = CACHE_REDIS_URL
    app.config["CACHE_DEFAULT_TIMEOUT"] = 120
    app.config["CACHE_KEY_PREFIX"] = "flaskapp:"

    app.config["RECAPTCHA_PUBLIC_KEY"] = RECAPTCHA_PUBLIC_KEY
    app.config["RECAPTCHA_PRIVATE_KEY"] = RECAPTCHA_PRIVATE_KEY

    app.config["SOCKETIO_MESSAGE_QUEUE"] = os.getenv(
        "SOCKETIO_MESSAGE_QUEUE",
        CACHE_REDIS_URL,
    )

    app.config["SOCKETIO_CHANNEL"] = os.getenv(
        "SOCKETIO_CHANNEL",
        "flaskapp_socketio",
    )

    app.config["MENSAGERIA_SOCKET_TOKEN"] = os.getenv(
        "MENSAGERIA_SOCKET_TOKEN",
        MENSAGERIA_SOCKET_TOKEN,
    )

    db.init_app(app)
    login_manager.init_app(app)
    limiter.init_app(app)
    cache.init_app(app)
    csrf.init_app(app)

    socketio.init_app(
        app,
        async_mode="threading",
        cors_allowed_origins="*",
        message_queue=app.config["SOCKETIO_MESSAGE_QUEUE"],
        channel=app.config["SOCKETIO_CHANNEL"],
        manage_session=False,
        logger=False,
        engineio_logger=False,
    )

    login_manager.login_view = "Autenticacao.login"
    login_manager.session_protection = "strong"

    with app.app_context():
        engine = db.engine

        @event.listens_for(engine, "connect")
        def _set_sqlserver_session_settings(dbapi_connection, connection_record):
            cursor = dbapi_connection.cursor()
            cursor.execute(
                """
                SET ARITHABORT ON;
                SET ANSI_NULLS ON;
                SET QUOTED_IDENTIFIER ON;
                SET ANSI_WARNINGS ON;
                SET CONCAT_NULL_YIELDS_NULL ON;
                """
            )
            cursor.close()

    from .euromidia.controle_paineis_views import paineis_bp
    from .autenticacao.autenticacao_views import autenticacao_bp
    from .admin.admin_views import admin
    from .kanban.kanban_views import kanban_bp
    #from .admin.estoque_views import estoques_bp

    app.register_blueprint(paineis_bp, url_prefix="/paineis")
    app.register_blueprint(autenticacao_bp, url_prefix="/autenticacao")
    app.register_blueprint(admin, url_prefix="/admin")
    app.register_blueprint(kanban_bp, url_prefix="/kanban")
    # app.register_blueprint(estoques_bp, url_prefix="/estoques")

    from . import socket_events

    @app.route("/")
    def index():
        return redirect(url_for("Paineis.lista_paineis"))

    return app