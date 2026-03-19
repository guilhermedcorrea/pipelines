from flask import Flask, redirect, url_for
from datetime import timedelta
from sqlalchemy import event

from .extensions import db, login_manager, csrf, limiter, cache

from config import (
    SQLALCHEMY_DATABASE_URI,
    SECRET_KEY,
    RECAPTCHA_PUBLIC_KEY,
    RECAPTCHA_PRIVATE_KEY,
    RATELIMIT_STORAGE_URI,
    CACHE_REDIS_URL,
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
        "max_overflow": 2,
        "pool_timeout": 60,
        "connect_args": {
            "timeout": 60,
        },
    }

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

    db.init_app(app)
    login_manager.init_app(app)
    limiter.init_app(app)
    cache.init_app(app)
    csrf.init_app(app)

    login_manager.login_view = "Autenticacao.login"
    login_manager.session_protection = "strong"

    with app.app_context():
        engine = db.engine

        @event.listens_for(engine, "connect")
        def _set_sqlserver_session_settings(dbapi_connection, connection_record):
            cursor = dbapi_connection.cursor()
            cursor.execute("""
                SET ARITHABORT ON;
                SET ANSI_NULLS ON;
                SET QUOTED_IDENTIFIER ON;
                SET ANSI_WARNINGS ON;
                SET CONCAT_NULL_YIELDS_NULL ON;
            """)
            cursor.close()

    from .euromidia.controle_paineis_views import paineis_bp
    from .autenticacao.autenticacao_views import autenticacao_bp
    from .admin.admin_views import admin
    from .kanban.kanban_views import kanban_bp

    app.register_blueprint(paineis_bp, url_prefix="/paineis")
    app.register_blueprint(autenticacao_bp, url_prefix="/autenticacao")
    app.register_blueprint(admin, url_prefix="/admin")
    app.register_blueprint(kanban_bp, url_prefix="/kanban")

    @app.route("/")
    def index():
        return redirect(url_for("Paineis.lista_paineis"))

    return app