from flask import flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user
from werkzeug.exceptions import BadGateway, Forbidden, HTTPException
from werkzeug.routing import BuildError

try:
    from flask_wtf.csrf import CSRFError
except Exception:
    CSRFError = None


def registrar_handlers(app):
    """Eu registro handlers centralizados para erros HTTP e exceções inesperadas."""

    def _e_resposta_json() -> bool:
        """Eu detecto se a rota espera JSON em vez de HTML."""
        melhor_accept = request.accept_mimetypes.best or ""
        eh_ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest"

        return (
            request.path.startswith("/api/")
            or request.is_json
            or eh_ajax
            or melhor_accept == "application/json"
        )

    def _url_inicio_padrao() -> str:
        """Eu resolvo uma URL segura para voltar ao início da aplicação."""
        for endpoint in (
            "Paineis.lista_paineis",
            "euromidia.index",
            "main.index",
            "index",
        ):
            try:
                return url_for(endpoint)
            except BuildError:
                continue

        return "/"

    def _descricao_usuario_logado() -> str:
        """Eu monto uma descrição curta do usuário atual para log."""
        try:
            if getattr(current_user, "is_authenticated", False):
                id_usuario = getattr(current_user, "id", None)
                nome_usuario = (
                    getattr(current_user, "nome", None)
                    or getattr(current_user, "username", None)
                    or getattr(current_user, "email", None)
                )
                return f"id={id_usuario} nome={nome_usuario}"
        except Exception:
            pass

        return "anonimo"

    def _montar_contexto_erro(codigo: int, detalhe: str | None = None) -> dict:
        """Eu converto o código do erro em um contexto amigável para tela e API."""
        mapa = {
            400: {
                "titulo": "Requisição inválida",
                "mensagem": "Os dados enviados para essa operação estão incompletos, inválidos ou expiraram.",
            },
            401: {
                "titulo": "Sessão não autorizada",
                "mensagem": "Sua sessão não está autorizada para essa operação. Faça login novamente e tente de novo.",
            },
            403: {
                "titulo": "Acesso negado",
                "mensagem": "Você não tem permissão para acessar esta funcionalidade.",
            },
            404: {
                "titulo": "Página não encontrada",
                "mensagem": "O endereço acessado não existe, foi alterado ou não está disponível.",
            },
            405: {
                "titulo": "Método não permitido",
                "mensagem": "A ação usada nesta rota não é permitida para este recurso.",
            },
            408: {
                "titulo": "Tempo esgotado",
                "mensagem": "A solicitação demorou mais do que o esperado. Tente novamente.",
            },
            409: {
                "titulo": "Conflito de operação",
                "mensagem": "Os dados foram alterados por outro processo ou a operação entrou em conflito com o estado atual.",
            },
            410: {
                "titulo": "Recurso indisponível",
                "mensagem": "Esse recurso não está mais disponível.",
            },
            413: {
                "titulo": "Arquivo muito grande",
                "mensagem": "O conteúdo enviado ultrapassa o limite permitido pelo sistema.",
            },
            415: {
                "titulo": "Formato não suportado",
                "mensagem": "O tipo de arquivo ou conteúdo enviado não é aceito nesta operação.",
            },
            422: {
                "titulo": "Dados não processáveis",
                "mensagem": "O sistema entendeu a requisição, mas não conseguiu processar os dados enviados.",
            },
            429: {
                "titulo": "Muitas tentativas",
                "mensagem": "Você realizou muitas requisições em pouco tempo. Aguarde alguns instantes e tente novamente.",
            },
            500: {
                "titulo": "Erro interno do servidor",
                "mensagem": "O sistema encontrou um problema inesperado ao processar sua solicitação.",
            },
            502: {
                "titulo": "Falha de comunicação",
                "mensagem": "O servidor recebeu uma resposta inválida de outro serviço interno.",
            },
            503: {
                "titulo": "Serviço temporariamente indisponível",
                "mensagem": "O sistema está temporariamente indisponível ou em manutenção.",
            },
            504: {
                "titulo": "Tempo limite excedido",
                "mensagem": "Um serviço interno demorou demais para responder.",
            },
        }

        contexto = mapa.get(
            codigo,
            {
                "titulo": f"Erro {codigo}",
                "mensagem": "Ocorreu um erro inesperado durante o processamento da solicitação.",
            },
        )

        return {
            "codigo": codigo,
            "titulo": contexto["titulo"],
            "mensagem": contexto["mensagem"],
            "detalhe": (detalhe or "").strip(),
        }

    def _responder_erro(codigo: int, detalhe: str | None = None):
        """Eu devolvo JSON para APIs e HTML amigável para navegação normal."""
        contexto = _montar_contexto_erro(codigo, detalhe)
        url_inicio = _url_inicio_padrao()
        url_voltar = request.referrer if request.referrer and request.referrer != request.url else None

        if _e_resposta_json():
            return (
                jsonify(
                    {
                        "ok": False,
                        "codigo": contexto["codigo"],
                        "erro": contexto["titulo"],
                        "mensagem": contexto["mensagem"],
                        "detalhe": contexto["detalhe"] or None,
                        "rota": request.path,
                    }
                ),
                codigo,
            )

        return (
            render_template(
                "euromidia/erros/erro_http.html",
                codigo_erro=contexto["codigo"],
                titulo_erro=contexto["titulo"],
                mensagem_erro=contexto["mensagem"],
                detalhe_erro=contexto["detalhe"],
                url_inicio=url_inicio,
                url_voltar=url_voltar,
                rota_atual=request.path,
            ),
            codigo,
        )

    @app.errorhandler(Forbidden)
    def _handle_403(erro):
        """Eu trato especificamente acesso negado."""
        if not _e_resposta_json() and request.method == "GET":
            destino = request.referrer
            if destino and destino != request.url:
                flash("Você não tem permissão para acessar essa tela.", "danger")
                return redirect(destino)

        detalhe = getattr(erro, "description", "") or ""
        return _responder_erro(403, detalhe)

    @app.errorhandler(BadGateway)
    def _handle_502(erro):
        """Eu trato especificamente falhas de comunicação entre serviços internos."""
        detalhe = getattr(erro, "description", "") or ""

        app.logger.error(
            "Erro HTTP 502 | rota=%s | metodo=%s | usuario=%s | detalhe=%s",
            request.path,
            request.method,
            _descricao_usuario_logado(),
            detalhe,
        )

        return _responder_erro(502, detalhe)

    if CSRFError is not None:
        @app.errorhandler(CSRFError)
        def _handle_csrf(erro):
            """Eu trato falhas de CSRF de forma amigável."""
            detalhe = "Sua sessão expirou ou o formulário perdeu a validade. Recarregue a página e tente novamente."

            app.logger.warning(
                "Falha de CSRF | rota=%s | metodo=%s | usuario=%s | detalhe=%s",
                request.path,
                request.method,
                _descricao_usuario_logado(),
                str(erro),
            )

            return _responder_erro(400, detalhe)

    @app.errorhandler(HTTPException)
    def _handle_http_exception(erro):
        """Eu trato todos os erros HTTP conhecidos do Flask/Werkzeug."""
        codigo = int(getattr(erro, "code", 500) or 500)
        detalhe = getattr(erro, "description", "") or ""

        if codigo >= 500:
            app.logger.error(
                "Erro HTTP %s | rota=%s | metodo=%s | usuario=%s | detalhe=%s",
                codigo,
                request.path,
                request.method,
                _descricao_usuario_logado(),
                detalhe,
            )

        return _responder_erro(codigo, detalhe)

    @app.errorhandler(Exception)
    def _handle_exception(erro):
        """Eu trato qualquer exceção não prevista e devolvo 500 amigável."""
        app.logger.exception(
            "Exceção não tratada | rota=%s | metodo=%s | usuario=%s",
            request.path,
            request.method,
            _descricao_usuario_logado(),
        )

        return _responder_erro(500)
