from datetime import datetime, timedelta, timezone

from flask import Blueprint, current_app, render_template, render_template_string, request, redirect, url_for, flash, session, abort
from flask_login import login_user, logout_user, login_required, current_user

from werkzeug.security import check_password_hash
from werkzeug.security import check_password_hash, generate_password_hash

from ..extensions import db,limiter
from ..models.autenticacao import DimUsuarios,DimPerfilUsuario,PermissoesUsuario,DimPermissoes,PermissoesPerfil
from functools import wraps

from urllib.parse import urlparse, urljoin
from flask_wtf import FlaskForm
from wtforms import StringField, PasswordField, SubmitField
from flask_wtf import RecaptchaField
from wtforms.validators import DataRequired
import os
import requests
from sqlalchemy import text





autenticacao_bp = Blueprint("Autenticacao", __name__)




class FormLogin(FlaskForm):
    email = StringField("Email", validators=[DataRequired()])
    senha = PasswordField("Senha", validators=[DataRequired()])
    recaptcha = RecaptchaField()
    entrar = SubmitField("Entrar")




def _texto(v: str) -> str:
    if v is None:
        return ""
    return str(v).strip()


def _agora():
    
    return datetime.now()


def _url_segura(url: str) -> bool:
   
    from urllib.parse import urlparse, urljoin
    

    if not url:
        return False

    host_url = urlparse(request.host_url)
    test_url = urlparse(urljoin(request.host_url, url))

    return (test_url.scheme in ("http", "https")) and (host_url.netloc == test_url.netloc)


def _validar_recaptcha_v2(token_resposta: str) -> tuple[bool, str]:
   
    if not token_resposta:
        return False, "Confirme o reCAPTCHA para continuar."

    
    recaptcha_secret = (
        current_app.config.get("RECAPTCHA_SECRET_KEY")
        or current_app.config.get("RECAPTCHA_PRIVATE_KEY")
        or "SEU_SECRET_AQUI_PARA_TESTE"
    )

    try:
        resp = requests.post(
            "https://www.google.com/recaptcha/api/siteverify",
            data={
                "secret": recaptcha_secret,
                "response": token_resposta,
                "remoteip": request.remote_addr, 
            },
            timeout=6,
        )
        data = resp.json()
    except Exception:
        return False, "Falha ao validar o reCAPTCHA. Tente novamente."

    if not data.get("success"):
        
        codigos = data.get("error-codes") or []
      
        if "invalid-input-secret" in codigos:
            return False, "reCAPTCHA: secret inválido (chave secreta errada)."
        if "invalid-input-response" in codigos:
            return False, "reCAPTCHA inválido. Marque novamente."
        return False, "Não foi possível validar o reCAPTCHA. Tente novamente."

    return True, ""


@autenticacao_bp.route("/login", methods=["GET", "POST"])
@limiter.limit("15 per minute", methods=["POST"])
def login():

    if request.method == "GET":
        if current_user.is_authenticated:
            return redirect(url_for("Paineis.lista_paineis"))

        site_key = current_app.config.get("RECAPTCHA_SITE_KEY") or current_app.config.get("RECAPTCHA_PUBLIC_KEY")

        if not site_key:
            raise RuntimeError("reCAPTCHA SITE KEY não configurada. Defina RECAPTCHA_SITE_KEY (ou RECAPTCHA_PUBLIC_KEY) no app.config.")

       
        current_app.config["RECAPTCHA_SITE_KEY"] = site_key

        return render_template(
            "autenticacao/login.html",
            recaptcha_erro=None
        )

 
    email = _texto(request.form.get("email")).lower()
    senha = _texto(request.form.get("senha"))
    lembrar = bool(request.form.get("lembrar"))

    recaptcha_token = _texto(request.form.get("g-recaptcha-response"))

    if not email or not senha:
        flash("Informe email e senha.", "warning")
        return redirect(url_for("Autenticacao.login"))

   
    ok_recaptcha, msg_recaptcha = _validar_recaptcha_v2(recaptcha_token)
    if not ok_recaptcha:
      
        flash("Falha no reCAPTCHA. Verifique e tente novamente.", "danger")

   
        if not current_app.config.get("RECAPTCHA_SITE_KEY"):
            current_app.config["RECAPTCHA_SITE_KEY"] = (
                current_app.config.get("RECAPTCHA_PUBLIC_KEY")
                or "SEU_SITE_KEY_AQUI_PARA_TESTE"
            )

        return render_template(
            "autenticacao/login.html",
            recaptcha_erro=msg_recaptcha,
        )

    
    usuario = (
        db.session.query(DimUsuarios)
        .filter(DimUsuarios.Email == email)
        .first()
    )

    if not usuario:
        flash("Credenciais inválidas.", "danger")
        return redirect(url_for("Autenticacao.login"))

    if not bool(usuario.BitAtivo):
        flash("Usuário inativo. Fale com o administrador.", "danger")
        return redirect(url_for("Autenticacao.login"))


    if not check_password_hash(usuario.HashSenha, senha):
        flash("Credenciais inválidas.", "danger")
        return redirect(url_for("Autenticacao.login"))

    
    session.permanent = lembrar
    login_user(usuario, remember=lembrar)

   
    try:
        usuario.UltimoLogin = _agora()
        usuario.UpdateAt = _agora()
        db.session.commit()
    except Exception:
        db.session.rollback()

   
    prox_url = request.args.get("next")
    if prox_url and _url_segura(prox_url):
        return redirect(prox_url)

    return redirect(url_for("Paineis.lista_paineis"))



@autenticacao_bp.route("/logout", methods=["POST", "GET"])
@login_required
def logout():

    logout_user()
    flash("Você saiu da sua conta.", "info")
    return redirect(url_for("Autenticacao.login"))






@autenticacao_bp.route("/me", methods=["GET"])
@login_required
def me():

    return {
        "id": current_user.get_id(),
        "nome": getattr(current_user, "NomeUsuario", None),
        "email": getattr(current_user, "Email", None),
        "ativo": bool(getattr(current_user, "BitAtivo", False)),
        "perfil_id": getattr(current_user, "IDDimPerfilUsuario", None),
    }




ID_PERFIL_ADMIN_PADRAO = 1
ID_PERFIL_VENDEDOR_PADRAO = 3
ID_PERFIL_COORDENADOR_PADRAO = 5


PERMISSOES_OPERACIONAIS_COORDENADOR = {
    "KANBAN_VER",
    "KANBAN_EDITAR",
    "KANBAN_CUSTO_MARGEM_VER",
    "PAINEIS_VER",
    "PAINEIS_LISTA_VER",
    "PAINEIS_GRADE_VER",
    "OCUPACAO_LISTA_VER",
    "CLIENTES_LISTA_VER",
    "CLIENTES_DETALHE_VER",
    "CARTEIRAS_VER",
    "CARTEIRA_PROPRIA_VER",
    "PRECOS_EUROMIDIA_VER",
    "LISTA_PRECOS_EUROMIDIA_VER",
    "VENCIMENTOS_CAMPANHAS_VER",
}


PERMISSOES_BLOQUEADAS_COORDENADOR = {
    "USUARIOS_VER",
    "USUARIOS_EDITAR",
    "USUARIOS_CRIAR",
    "USUARIOS_EXCLUIR",
    "PERFIS_VER",
    "PERFIS_EDITAR",
    "PERMISSOES_VER",
    "PERMISSOES_EDITAR",
    "CHECKIN_VER",
    "CHECKIN_CRIAR",
    "CHECKIN_LISTA_VER",
    "APROVACAO_PRECO_VER",
    "APROVACAO_DESCONTO_VER",
    "APROVACAO_DESCONTO",
    "PERMISSAO_DESCONTO_VER",
    "PERMISSAO_DESCONTO_EDITAR",
    "AUVO_PRODUTOS_VER",
    "TICKETS_AUVO_VER",
    "CRIAR_OS_AUVO",
    "CONTRATOS_VER",
    "CONTRATOS_LISTA_VER",
    "MOVIMENTACAO_FINANCEIRA_VER",
    "MOVIMENTACAO_FINANCEIRA_LISTA_VER",
    "INADIMPLENTES_VER",
    "APROVACAO_CONTRATOS_VER",
    "APROVACAO_CONTRATOS",
    "FINANCEIRO_VER",
    "ATIVOS_VER",
    "PERFORMANCE_ATIVOS_VER",
}


ROTAS_ADMIN_OPERACIONAIS_COORDENADOR = (
    "/admin/vencimentos-campanhas",
    "/admin/precos/euromidia",
    "/precos/euromidia",
    "/lista-precos",
    "/listas-precos",
)


ROTAS_OPERACIONAIS_COORDENADOR = (
    "/kanban/atendimento",
    "/kanban/kanbans",
    "/kanban/historico-cards",
    "/kanban/historico-card",
    "/kanban/health-check-comercial",
    "/kanban/api/",
    "/kanban/",
)


ROTAS_BLOQUEADAS_COORDENADOR = (
    "/autenticacao/seguranca",
    "/paineis/seguranca",
    "/paineis/usuarios",
    "/paineis/permissoes",
    "/paineis/admin/usuarios",
    "/paineis/checkin/novo",
    "/paineis/checkin/lista",
    "/paineis/checkin/",
    "/paineis/contratos",
    "/admin/listadevedores",
    "/admin/inadimplentes",
    "/admin/movimentacao",
    "/admin/movimentacoes",
    "/admin/aprovacao_contratos",
    "/admin/aprovacao-contratos",
    "/admin/contratos",
    "/admin/financeiro",
    "/admin/ativos",
    "/admin/auvo/produtos",
    "/admin/tickets/auvo",
    "/admin/criar_os_auvo",
    "/admin/criar-os-auvo",
    "/admin/permissao_desconto",
    "/admin/permissao-desconto",
    "/admin/aprovacao_desconto",
    "/admin/aprovacao-desconto",
    "/kanban/aprovacao-preco",
)


def _normalizar_perfil_autenticacao(valor) -> str:
    texto = str(valor or "").strip().lower()
    if not texto:
        return ""
    try:
        import unicodedata
        texto = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode("ascii")
    except Exception:
        pass
    return texto.strip()


def _usuario_logado_eh_perfil_coordenador_autenticacao() -> bool:
    """Eu libero o perfil Coordenador pelo ID fixo 5, pelo nome carregado no usuário e por conferência no banco."""
    if not getattr(current_user, "is_authenticated", False):
        return False

    candidatos_id = [
        getattr(current_user, "IDDimPerfilUsuario", None),
        getattr(current_user, "id_dim_perfil_usuario", None),
        getattr(current_user, "IDPerfilUsuario", None),
        getattr(current_user, "id_perfil_usuario", None),
        getattr(current_user, "IDPerfil", None),
        getattr(current_user, "id_perfil", None),
    ]

    perfil = getattr(current_user, "perfil", None)
    if perfil is not None:
        candidatos_id.extend([
            getattr(perfil, "IDDimPerfilUsuario", None),
            getattr(perfil, "id_dim_perfil_usuario", None),
            getattr(perfil, "IDPerfilUsuario", None),
            getattr(perfil, "id_perfil_usuario", None),
            getattr(perfil, "IDPerfil", None),
            getattr(perfil, "id_perfil", None),
        ])

    id_perfil_encontrado = 0
    for valor in candidatos_id:
        try:
            id_perfil = int(valor or 0)
        except Exception:
            id_perfil = 0

        if id_perfil > 0:
            id_perfil_encontrado = id_perfil

        if id_perfil == ID_PERFIL_COORDENADOR_PADRAO:
            return True

    candidatos_nome = [
        getattr(current_user, "NomePerfil", None),
        getattr(current_user, "Perfil", None),
        getattr(current_user, "DescricaoPerfil", None),
        getattr(current_user, "perfil_nome", None),
        getattr(current_user, "nome_perfil", None),
        getattr(perfil, "NomePerfil", None) if perfil is not None else None,
        getattr(perfil, "Descricao", None) if perfil is not None else None,
        getattr(perfil, "Perfil", None) if perfil is not None else None,
    ]

    for valor in candidatos_nome:
        texto = _normalizar_perfil_autenticacao(valor)
        if texto == "coordenador" or "coordenador" in texto:
            return True

    if id_perfil_encontrado > 0:
        try:
            nome_perfil = (
                db.session.query(DimPerfilUsuario.NomePerfil)
                .filter(DimPerfilUsuario.IDDimPerfilUsuario == int(id_perfil_encontrado))
                .scalar()
            )
            texto = _normalizar_perfil_autenticacao(nome_perfil)
            if texto == "coordenador" or "coordenador" in texto:
                return True
        except Exception:
            db.session.rollback()

    return False


def _coordenador_acesso_bloqueado_no_request(codigo: str) -> bool:
    """_coordenador_acesso_bloqueado_no_request
    - Eu bloqueio rotas sensíveis para o perfil Coordenador antes de consultar permissões individuais.
    - Assim, mesmo que o Coordenador herde ADMIN_TUDO por engano, ele não acessa Segurança,
      Checkin, Financeiro, Solicitações Auvo, Performance Ativos ou Permissões de desconto/aprovação.
    """
    if not _usuario_logado_eh_perfil_coordenador_autenticacao():
        return False

    codigo = str(codigo or "").strip().upper()
    caminho = str(getattr(request, "path", "") or "").strip()

    if codigo in PERMISSOES_BLOQUEADAS_COORDENADOR:
        return True

    return any(caminho.startswith(prefixo) for prefixo in ROTAS_BLOQUEADAS_COORDENADOR)


def _coordenador_pode_usar_permissao_operacional(codigo: str) -> bool:
    """Eu libero o Coordenador para rotas operacionais sem abrir Segurança/Admin técnico inteiro."""
    codigo = str(codigo or "").strip().upper()
    if not codigo or not _usuario_logado_eh_perfil_coordenador_autenticacao():
        return False

    if _coordenador_acesso_bloqueado_no_request(codigo):
        return False

    caminho = str(getattr(request, "path", "") or "").strip()

    if codigo in PERMISSOES_OPERACIONAIS_COORDENADOR:
        return True

    if any(caminho.startswith(prefixo) for prefixo in ROTAS_OPERACIONAIS_COORDENADOR):
        return True

    if codigo == "ADMIN_TUDO":
        return any(caminho.startswith(prefixo) for prefixo in ROTAS_ADMIN_OPERACIONAIS_COORDENADOR)

    return False


def requer_permissao(codigo_permissao: str):
    codigo = (str(codigo_permissao or "").strip().upper())

    def decorator(view_func):
        @wraps(view_func)
        def wrapper(*args, **kwargs):

            if not current_user.is_authenticated:
                flash("Faça login para continuar.", "warning")
                return redirect(url_for("Autenticacao.login", next=request.url))


            if not bool(getattr(current_user, "BitAtivo", False)):
                flash("Usuário inativo. Fale com o administrador.", "danger")
                return redirect(url_for("Autenticacao.login"))


            if not codigo:

                abort(500, description="Permissão não configurada no endpoint.")


            if _coordenador_acesso_bloqueado_no_request(codigo):
                flash("Seu perfil Coordenador não possui acesso a esta página.", "danger")
                abort(403)


            if _coordenador_pode_usar_permissao_operacional(codigo):
                return view_func(*args, **kwargs)


            if not getattr(current_user, "has_permission", None):
                abort(500, description="Usuário carregado não possui has_permission().")

            if not current_user.has_permission(codigo):

                flash("Você não tem permissão para acessar esta página.", "danger")
                abort(403)

            return view_func(*args, **kwargs)

        return wrapper
    return decorator








ID_EMPRESA_PROPRIETARIA_EUROMIDIA = 3


def _converter_int_seguro(valor, padrao: int = 0) -> int:
    """Converte valores vindos do banco/sessão para int sem quebrar a request."""
    try:
        if valor is None:
            return padrao
        return int(valor)
    except Exception:
        return padrao


def _config_ambiente(nome: str, padrao: str = "") -> str:
    """Busca configuração primeiro no Flask config e depois no .env/os.environ."""
    valor = current_app.config.get(nome)
    if valor is None:
        valor = os.getenv(nome, padrao)
    return str(valor or padrao).strip()


def _normalizar_airflow_base_url(base_url: str) -> str:
    """Normaliza a URL base para evitar montar /api/v2/api/v2 por engano."""
    base = str(base_url or "").strip().rstrip("/")

    for sufixo in ("/api/v2", "/api/v1", "/api"):
        if base.lower().endswith(sufixo):
            base = base[: -len(sufixo)].rstrip("/")
            break

    return base


def _sanitizar_detalhe_erro_airflow(texto: str, usuario_api: str = "", senha_api: str = "") -> str:
    """Remove credenciais antes de mostrar o detalhe na tela/log."""
    saida = str(texto or "")

    for segredo in (senha_api, usuario_api):
        segredo = str(segredo or "").strip()
        if segredo:
            saida = saida.replace(segredo, "***")

    return saida


def _flag_habilitada(valor: str) -> bool:
    texto = str(valor or "").strip().lower()
    return texto in {"1", "true", "t", "sim", "s", "yes", "y", "on"}


def _usuario_logado_empresa_proprietaria() -> int:
    """Lê a empresa proprietária do usuário logado, preferindo o banco para evitar sessão desatualizada."""
    if not getattr(current_user, "is_authenticated", False):
        return 0

    id_usuario = _converter_int_seguro(getattr(current_user, "IDDimUsuarios", None))
    if id_usuario <= 0:
        id_usuario = _converter_int_seguro(current_user.get_id())

    if id_usuario > 0:
        try:
            valor_banco = (
                db.session.query(DimUsuarios.IDEmpresaProprietaria)
                .filter(DimUsuarios.IDDimUsuarios == id_usuario)
                .scalar()
            )
            return _converter_int_seguro(valor_banco)
        except Exception:
            db.session.rollback()

    return _converter_int_seguro(getattr(current_user, "IDEmpresaProprietaria", None))


def _usuario_logado_pode_executar_controle_contratos() -> bool:
    """Somente usuários ativos da empresa proprietária 3 podem ver e executar o botão."""
    if not getattr(current_user, "is_authenticated", False):
        return False

    if not bool(getattr(current_user, "BitAtivo", False)):
        return False

    return _usuario_logado_empresa_proprietaria() == ID_EMPRESA_PROPRIETARIA_EUROMIDIA


def _agora_utc_iso_airflow() -> str:
    """Retorna a data/hora UTC no formato aceito pela API pública v2 do Airflow."""
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _montar_payload_airflow_controle_contratos(dag_run_id: str, logical_date_utc: str) -> dict:
    """Monta o corpo oficial do POST /api/v2/dags/{dag_id}/dagRuns.

    Correção importante:
    - No Airflow 3.x, o schema da API v2 exige o campo logical_date no corpo.
    - Sem logical_date, o Airflow retorna HTTP 422: Field required.
    - O campo fica no mesmo nível de dag_run_id e conf.
    """
    return {
        "dag_run_id": dag_run_id,
        "logical_date": logical_date_utc,
        "conf": {
            "origem": "flask_perfil_usuario",
            "origem_disparo": "endpoint_flask",
            "acionado_por_id_usuario": _converter_int_seguro(getattr(current_user, "IDDimUsuarios", None) or current_user.get_id()),
            "acionado_por_nome": getattr(current_user, "NomeUsuario", None),
            "acionado_por_email": getattr(current_user, "Email", None),
            "id_empresa_proprietaria": _usuario_logado_empresa_proprietaria(),
            "caminho_host_controle_contratos": _config_ambiente("CAMINHO_HOST_CONTROLE_CONTRATOS", ""),
            "data_hora_acionamento": _agora().strftime("%Y-%m-%d %H:%M:%S"),
            "logical_date_utc": logical_date_utc,
        },
    }


def _detalhe_resposta_airflow(resposta: requests.Response, limite: int = 700) -> str:
    """Extrai uma mensagem útil da resposta do Airflow sem expor senha."""
    try:
        dados = resposta.json()
    except Exception:
        texto = resposta.text or ""
        return texto[:limite]

    if isinstance(dados, dict):
        partes = []
        for chave in ("detail", "title", "message", "error", "reason"):
            valor = dados.get(chave)
            if valor:
                partes.append(f"{chave}={valor}")

        if partes:
            return " | ".join(partes)[:limite]

        return str(dados)[:limite]

    return str(dados)[:limite]


def _obter_token_airflow_v3(base_url: str, usuario: str, senha: str, timeout_segundos: int) -> tuple[str | None, list[str], int | None]:
    """Obtém JWT para Airflow 3.x usando o formato oficial do endpoint /auth/token.

    Importante:
    - Airflow 3.x espera JSON no /auth/token.
    - Não envio form-url-encoded porque isso gera HTTP 422 no Airflow 3.x.
    - Retorno também o status HTTP para decidir se faz sentido tentar fallback legado v1.
    """
    endpoint = f"{base_url.rstrip('/')}/auth/token"

    try:
        resposta = requests.post(
            endpoint,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
            json={
                "username": usuario,
                "password": senha,
            },
            timeout=timeout_segundos,
        )
    except requests.Timeout:
        return None, [f"{endpoint} [json] -> timeout após {timeout_segundos}s"], None
    except requests.RequestException as erro:
        return None, [f"{endpoint} [json] -> erro de conexão: {erro}"], None

    if resposta.status_code in (200, 201):
        try:
            dados = resposta.json()
        except Exception:
            dados = {}

        token = dados.get("access_token")
        if token:
            return str(token), [], resposta.status_code

        return None, [f"{endpoint} [json] -> HTTP {resposta.status_code}: resposta sem access_token"], resposta.status_code

    return None, [f"{endpoint} [json] -> HTTP {resposta.status_code}: {_detalhe_resposta_airflow(resposta)}"], resposta.status_code


def _erro_airflow_hash_scrypt(erros: list[str]) -> bool:
    """Identifica o erro clássico de usuário do Airflow salvo com hash scrypt incompatível."""
    texto = " || ".join(erros or []).lower()
    return "unsupported hash type" in texto and "scrypt" in texto


def _post_airflow_dag_run_bearer(endpoint: str, token: str, timeout_segundos: int, payload: dict) -> requests.Response:
    return requests.post(
        endpoint,
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        },
        json=payload,
        timeout=timeout_segundos,
    )



@autenticacao_bp.route("/perfil", methods=["GET"])
@login_required
def perfil():
    """Tela HTML com informações do usuário logado + permissões + troca de senha."""
    u = current_user


    perfil = getattr(u, "perfil", None)

    permissoes_perfil = []
    if perfil and getattr(perfil, "permissoes", None):
        for p in (perfil.permissoes or []):
            if p and bool(getattr(p, "BitAtivo", True)):
                permissoes_perfil.append({
                    "codigo": (p.CodigoPermissao or "").strip(),
                    "descricao": (p.Descricao or "").strip(),
                    "ativo": bool(getattr(p, "BitAtivo", True)),
                })

    permissoes_perfil = sorted(permissoes_perfil, key=lambda x: x["codigo"])


    permissoes_extras = []
    agora = datetime.now()

    for row in (getattr(u, "permissoes_extras", None) or []):
        perm = getattr(row, "permissao", None)
        if not perm:
            continue
        if not bool(getattr(perm, "BitAtivo", True)):
            continue

        exp = getattr(row, "DataExpiracao", None)
        expirado = (exp is not None and exp <= agora)

        permissoes_extras.append({
            "codigo": (getattr(perm, "CodigoPermissao", "") or "").strip(),
            "descricao": (getattr(perm, "Descricao", "") or "").strip(),
            "tipo": (getattr(row, "TipoAtribuicao", "") or "").strip().upper(),
            "data_expiracao": exp,
            "expirado": bool(expirado),
            "observacao": (getattr(row, "Observacao", "") or "").strip(),
        })

    permissoes_extras = sorted(permissoes_extras, key=lambda x: (x["codigo"], x["tipo"]))

    return render_template(
        "autenticacao/perfil.html",
        usuario=u,
        perfil=perfil,
        permissoes_perfil=permissoes_perfil,
        permissoes_extras=permissoes_extras,
        pode_executar_controle_contratos=_usuario_logado_pode_executar_controle_contratos(),
    )




@autenticacao_bp.route("/reserva-ocupacao", methods=["GET"])
@login_required
def reserva_ocupacao():
    """Redireciona a rota antiga para a nova tela de reserva em Painéis.

    A tela real não pertence ao blueprint de autenticação. Ela fica em:
    /paineis/reserva-ocupacao

    Mantive esta rota antiga apenas para não quebrar links salvos/favoritos.
    """
    if _usuario_logado_empresa_proprietaria() != ID_EMPRESA_PROPRIETARIA_EUROMIDIA:
        abort(403)

    return redirect(url_for("Paineis.reserva_ocupacao"))

@autenticacao_bp.route("/perfil/controle-contratos/executar", methods=["POST"])
@login_required
@limiter.limit("6 per minute")
def executar_pipeline_controle_contratos():
    """Aciona a DAG de Controle de Contratos no Airflow 3.x via API pública v2.

    Fluxo correto:
    1. Obtém JWT em POST /auth/token.
    2. Dispara a DAG em POST /api/v2/dags/{dag_id}/dagRuns.
    3. Envia logical_date, obrigatório no schema v2 do Airflow usado neste ambiente.

    Observação:
    - Não faço fallback para /api/v1 neste ambiente, porque o token JWT já comprova Airflow 3.x.
    - O fallback antigo gerava HTTP 405 e confundia o diagnóstico.
    """
    if not _usuario_logado_pode_executar_controle_contratos():
        flash("Você não tem permissão para executar esta ação.", "danger")
        abort(403)

    trigger_habilitado = _config_ambiente("AIRFLOW_TRIGGER_CONTROLE_CONTRATOS_HABILITADO", "1")
    if not _flag_habilitada(trigger_habilitado):
        flash("Execução manual desabilitada no ambiente.", "warning")
        return redirect(url_for("Autenticacao.perfil"))

    base_url_original = _config_ambiente("AIRFLOW_API_BASE_URL", "")
    base_url = _normalizar_airflow_base_url(base_url_original)
    usuario_api = _config_ambiente("AIRFLOW_API_USERNAME", "")
    senha_api = _config_ambiente("AIRFLOW_API_PASSWORD", "")
    dag_id = _config_ambiente("AIRFLOW_DAG_CONTROLE_CONTRATOS", "pipeline_controle_contratos_euromidia")

    try:
        timeout_segundos = int(_config_ambiente("AIRFLOW_API_TIMEOUT_SEGUNDOS", "15"))
    except Exception:
        timeout_segundos = 15

    if timeout_segundos < 5:
        timeout_segundos = 5

    if not base_url or not usuario_api or not senha_api or not dag_id:
        detalhe_config = (
            f"base_url_configurada={bool(base_url)} | "
            f"usuario_configurado={bool(usuario_api)} | "
            f"senha_configurada={bool(senha_api)} | "
            f"dag_id={dag_id or 'VAZIO'}"
        )
        current_app.logger.error("Configuração incompleta para acionar Airflow | %s", detalhe_config)
        flash(f"Configuração incompleta para executar. {detalhe_config}", "danger")
        return redirect(url_for("Autenticacao.perfil"))

    logical_date_utc = _agora_utc_iso_airflow()
    agora_utc_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    dag_run_id = f"manual__controle_contratos__{agora_utc_id}"
    payload = _montar_payload_airflow_controle_contratos(dag_run_id, logical_date_utc)

    erros: list[str] = []

    token, erros_token, status_token = _obter_token_airflow_v3(
        base_url=base_url,
        usuario=usuario_api,
        senha=senha_api,
        timeout_segundos=timeout_segundos,
    )
    erros.extend(erros_token)

    if not token:
        detalhe_log = " || ".join(erros) if erros else "sem detalhe retornado"
        detalhe_seguro = _sanitizar_detalhe_erro_airflow(
            detalhe_log,
            usuario_api=usuario_api,
            senha_api=senha_api,
        )

        current_app.logger.error(
            "Falha ao obter token do Airflow | dag_id=%s | usuario_flask=%s | base_url_original=%s | base_url_normalizada=%s | status_token=%s | erros=%s",
            dag_id,
            getattr(current_user, "Email", None),
            base_url_original,
            base_url,
            status_token,
            detalhe_seguro,
        )

        if _erro_airflow_hash_scrypt(erros_token):
            flash(
                "Falha ao executar Controle de Contratos: o usuário configurado na API do Airflow está com hash de senha incompatível (scrypt). "
                "Recrie ou resete a senha desse usuário dentro do container do Airflow e tente novamente. "
                f"Detalhe técnico: {detalhe_seguro[:1200]}",
                "danger",
            )
        else:
            flash(f"Falha ao executar Controle de Contratos: {detalhe_seguro[:1800]}", "danger")

        return redirect(url_for("Autenticacao.perfil"))

    endpoint_v2 = f"{base_url}/api/v2/dags/{dag_id}/dagRuns"

    try:
        resposta = _post_airflow_dag_run_bearer(
            endpoint=endpoint_v2,
            token=token,
            timeout_segundos=timeout_segundos,
            payload=payload,
        )

        if resposta.status_code in (200, 201):
            try:
                dados = resposta.json()
            except Exception:
                dados = {}

            dag_run_retorno = dados.get("dag_run_id") or dados.get("run_id") or dag_run_id
            estado = dados.get("state") or "queued"

            flash(f"Controle de Contratos executado. Run: {dag_run_retorno} | Estado: {estado}.", "success")
            return redirect(url_for("Autenticacao.perfil"))

        erros.append(f"{endpoint_v2} -> HTTP {resposta.status_code}: {_detalhe_resposta_airflow(resposta)}")

    except requests.Timeout:
        erros.append(f"{endpoint_v2} -> timeout após {timeout_segundos}s")
    except requests.RequestException as erro:
        erros.append(f"{endpoint_v2} -> erro de conexão: {erro}")

    detalhe_log = " || ".join(erros) if erros else "sem detalhe retornado"
    detalhe_seguro = _sanitizar_detalhe_erro_airflow(
        detalhe_log,
        usuario_api=usuario_api,
        senha_api=senha_api,
    )

    current_app.logger.error(
        "Falha ao acionar DAG Controle de Contratos via API v2 | dag_id=%s | usuario_flask=%s | base_url_original=%s | base_url_normalizada=%s | payload=%s | erros=%s",
        dag_id,
        getattr(current_user, "Email", None),
        base_url_original,
        base_url,
        payload,
        detalhe_seguro,
    )

    flash(f"Falha ao executar Controle de Contratos: {detalhe_seguro[:1800]}", "danger")
    return redirect(url_for("Autenticacao.perfil"))



@autenticacao_bp.route("/perfil/senha", methods=["POST"])
@login_required
@limiter.limit("10 per hour")
def trocar_senha():
    """Troca senha do usuário logado (valida senha atual + confirma nova senha)."""
    senha_atual = _texto(request.form.get("senha_atual"))
    nova_senha = _texto(request.form.get("nova_senha"))
    confirmar = _texto(request.form.get("confirmar_senha"))

    if not senha_atual or not nova_senha or not confirmar:
        flash("Preencha todos os campos de senha.", "warning")
        return redirect(url_for("Autenticacao.perfil"))

    if nova_senha != confirmar:
        flash("A confirmação da nova senha não confere.", "danger")
        return redirect(url_for("Autenticacao.perfil"))

    if len(nova_senha) < 10:
        flash("A nova senha deve ter pelo menos 10 caracteres.", "danger")
        return redirect(url_for("Autenticacao.perfil"))

  
    if not check_password_hash(current_user.HashSenha or "", senha_atual):
        flash("Senha atual incorreta.", "danger")
        return redirect(url_for("Autenticacao.perfil"))

   
    try:
        u = db.session.query(DimUsuarios).filter(DimUsuarios.IDDimUsuarios == int(current_user.get_id())).first()
        if not u:
            flash("Usuário não encontrado para atualização.", "danger")
            return redirect(url_for("Autenticacao.perfil"))

        u.HashSenha = generate_password_hash(nova_senha)
        u.UpdateAt = _agora()
        db.session.commit()
        flash("Senha atualizada com sucesso.", "success")
    except Exception:
        db.session.rollback()
        flash("Falha ao atualizar senha. Tente novamente.", "danger")

    return redirect(url_for("Autenticacao.perfil"))












# ============================================================
# Permissões dos filtros avançados da lista de empresas
# ============================================================
PERMISSOES_FILTROS_CLIENTES = [
    {"codigo": "CLIENTES_FILTRO_CLASSE_VER", "rotulo": "Classe", "descricao": "Permite que vendedor visualize e use o filtro Classe na lista de empresas."},
    {"codigo": "CLIENTES_FILTRO_SETOR_VER", "rotulo": "Setor", "descricao": "Permite que vendedor visualize e use o filtro Setor na lista de empresas."},
    {"codigo": "CLIENTES_FILTRO_CLASSIFICACAO_MACRO_VER", "rotulo": "Classificação Score Macro", "descricao": "Permite que vendedor visualize e use o filtro Classificação Score Macro na lista de empresas."},
    {"codigo": "CLIENTES_FILTRO_SUBCLASSE_VER", "rotulo": "SubClasse", "descricao": "Permite que vendedor visualize e use o filtro SubClasse na lista de empresas."},
    {"codigo": "CLIENTES_FILTRO_CLASSE_VALOR_VER", "rotulo": "Classe Valor", "descricao": "Permite que vendedor visualize e use o filtro Classe Valor na lista de empresas."},
    {"codigo": "CLIENTES_FILTRO_ESCALA_OPERACIONAL_VER", "rotulo": "Escala Operacional", "descricao": "Permite que vendedor visualize e use o filtro Escala Operacional na lista de empresas."},
    {"codigo": "CLIENTES_FILTRO_CLASSE_ESTRUTURAL_VER", "rotulo": "Classe Estrutural", "descricao": "Permite que vendedor visualize e use o filtro Classe Estrutural na lista de empresas."},
    {"codigo": "CLIENTES_FILTRO_CLASSE_GEO_VER", "rotulo": "Classe Geo", "descricao": "Permite que vendedor visualize e use o filtro Classe Geo na lista de empresas."},
    {"codigo": "CLIENTES_FILTRO_CLASSE_FREQUENCIA_VER", "rotulo": "Classe Frequência", "descricao": "Permite que vendedor visualize e use o filtro Classe Frequência na lista de empresas."},
    {"codigo": "CLIENTES_FILTRO_CLASSE_RECENCIA_VER", "rotulo": "Classe Recência", "descricao": "Permite que vendedor visualize e use o filtro Classe Recência na lista de empresas."},
    {"codigo": "CLIENTES_FILTRO_PERFIL_PUBLICO_VER", "rotulo": "Perfil (Público Alvo)", "descricao": "Permite que vendedor visualize e use o filtro Perfil (Público Alvo) na lista de empresas."},
    {"codigo": "CLIENTES_FILTRO_USO_TERRITORIO_VER", "rotulo": "Uso do Território", "descricao": "Permite que vendedor visualize e use o filtro Uso do Território na lista de empresas."},
]


def _codigos_permissoes_filtros_clientes() -> set[str]:
    return {item["codigo"] for item in PERMISSOES_FILTROS_CLIENTES}


def _garantir_permissoes_filtros_clientes() -> None:
    """_garantir_permissoes_filtros_clientes
    - Eu crio/ativo as permissões dos filtros avançados, caso ainda não existam.
    - Assim a tela /autenticacao/seguranca/usuarios/<id> consegue liberar filtro por filtro.
    """
    codigos = sorted(_codigos_permissoes_filtros_clientes())
    agora = _agora()

    existentes = (
        db.session.query(DimPermissoes)
        .filter(DimPermissoes.CodigoPermissao.in_(codigos))
        .all()
    )
    por_codigo = {_texto(row.CodigoPermissao).upper(): row for row in existentes}

    alterou = False

    for item in PERMISSOES_FILTROS_CLIENTES:
        codigo = item["codigo"].strip().upper()
        descricao = item["descricao"].strip()
        row = por_codigo.get(codigo)

        if row is None:
            row = DimPermissoes()
            row.CodigoPermissao = codigo
            row.Descricao = descricao
            row.BitAtivo = True
            if hasattr(row, "DataCriacao"):
                row.DataCriacao = agora
            if hasattr(row, "DataAtualizacao"):
                row.DataAtualizacao = agora
            db.session.add(row)
            alterou = True
            continue

        mudou_linha = False
        if _texto(row.Descricao) != descricao:
            row.Descricao = descricao
            mudou_linha = True

        if not bool(getattr(row, "BitAtivo", False)):
            row.BitAtivo = True
            mudou_linha = True

        if mudou_linha and hasattr(row, "DataAtualizacao"):
            row.DataAtualizacao = agora

        alterou = alterou or mudou_linha

    if alterou:
        db.session.commit()


def _montar_permissoes_filtros_clientes(permissoes) -> list[dict]:
    por_codigo = {_texto(p.CodigoPermissao).upper(): p for p in (permissoes or [])}
    itens = []

    for item in PERMISSOES_FILTROS_CLIENTES:
        permissao = por_codigo.get(item["codigo"].upper())
        if permissao is None:
            continue
        itens.append({"permissao": permissao, "rotulo": item["rotulo"], "codigo": item["codigo"], "descricao": item["descricao"]})

    return itens


@autenticacao_bp.route("/seguranca/usuarios", methods=["GET"])
@login_required
@requer_permissao("USUARIOS_VER")
def usuarios_lista():
   
    q = _texto(request.args.get("q")).lower()

    consulta = db.session.query(DimUsuarios)

    if q:
        consulta = consulta.filter(
            db.or_(
                DimUsuarios.NomeUsuario.ilike(f"%{q}%"),
                DimUsuarios.Email.ilike(f"%{q}%"),
            )
        )

    usuarios = (
        consulta
        .order_by(DimUsuarios.NomeUsuario.asc())
        .all()
    )

    return render_template(
        "autenticacao/usuarios_lista.html",
        usuarios=usuarios,
        q=q,
    )




@autenticacao_bp.route("/seguranca/usuarios/<int:id_usuario>", methods=["GET", "POST"])
@login_required
@requer_permissao("USUARIOS_EDITAR")
def usuarios_editar(id_usuario):
    """usuarios_editar
    Eu edito o perfil do usuário e suas permissões individuais.

    Regra:
    - HERDAR: usa a permissão do perfil.
    - CONCEDER: adiciona uma permissão individual.
    - REVOGAR: remove uma permissão mesmo que o perfil tenha.

    Correção importante:
    - Eu não monto mais permissoes_extras_map usando usuario.permissoes_extras.
    - Eu busco direto da tabela PermissoesUsuario.
    - Assim, se está salvo no banco, aparece corretamente na tela.
    """

    usuario = (
        db.session.query(DimUsuarios)
        .filter(DimUsuarios.IDDimUsuarios == id_usuario)
        .first()
    )

    if not usuario:
        flash("Usuário não encontrado.", "danger")
        return redirect(url_for("Autenticacao.usuarios_lista"))

    perfis = (
        db.session.query(DimPerfilUsuario)
        .filter(DimPerfilUsuario.BitAtivo == True)
        .order_by(DimPerfilUsuario.NomePerfil.asc())
        .all()
    )

    try:
        _garantir_permissoes_filtros_clientes()
    except Exception:
        db.session.rollback()
        current_app.logger.exception("Erro ao garantir permissões de filtros avançados de clientes.")

    permissoes = (
        db.session.query(DimPermissoes)
        .filter(DimPermissoes.BitAtivo == True)
        .order_by(DimPermissoes.CodigoPermissao.asc())
        .all()
    )

    if request.method == "POST":
        try:
            id_perfil_raw = _texto(request.form.get("id_perfil"))

            if not id_perfil_raw:
                flash("Informe o perfil do usuário.", "warning")
                return redirect(
                    url_for(
                        "Autenticacao.usuarios_editar",
                        id_usuario=usuario.IDDimUsuarios,
                    )
                )

            id_perfil = int(id_perfil_raw)
            bit_ativo = request.form.get("bit_ativo") == "1"

            nova_senha_admin = _texto(request.form.get("nova_senha_admin"))
            confirmar_senha_admin = _texto(request.form.get("confirmar_senha_admin"))
            hash_senha_nova = None

            if nova_senha_admin or confirmar_senha_admin:
                if not nova_senha_admin or not confirmar_senha_admin:
                    flash("Para alterar a senha, preencha a nova senha e a confirmação.", "warning")
                    return redirect(
                        url_for(
                            "Autenticacao.usuarios_editar",
                            id_usuario=usuario.IDDimUsuarios,
                        )
                    )

                if nova_senha_admin != confirmar_senha_admin:
                    flash("A confirmação da nova senha não confere.", "danger")
                    return redirect(
                        url_for(
                            "Autenticacao.usuarios_editar",
                            id_usuario=usuario.IDDimUsuarios,
                        )
                    )

                if len(nova_senha_admin) < 10:
                    flash("A nova senha deve ter pelo menos 10 caracteres.", "danger")
                    return redirect(
                        url_for(
                            "Autenticacao.usuarios_editar",
                            id_usuario=usuario.IDDimUsuarios,
                        )
                    )

                hash_senha_nova = generate_password_hash(nova_senha_admin)

            perfil_existe = (
                db.session.query(DimPerfilUsuario.IDDimPerfilUsuario)
                .filter(DimPerfilUsuario.IDDimPerfilUsuario == id_perfil)
                .filter(DimPerfilUsuario.BitAtivo == True)
                .first()
            )

            if not perfil_existe:
                flash("Perfil informado não existe ou está inativo.", "danger")
                return redirect(
                    url_for(
                        "Autenticacao.usuarios_editar",
                        id_usuario=usuario.IDDimUsuarios,
                    )
                )

            id_usuario_int = int(usuario.IDDimUsuarios)
            agora = _agora()
            id_usuario_executor = None

            try:
                id_usuario_executor = int(current_user.get_id())
            except Exception:
                id_usuario_executor = None

            """Correção SQL Server/pyodbc:
            - Eu não atualizo DimUsuarios pelo objeto ORM aqui.
            - Em alguns ambientes SQL Server, o pyodbc retorna rowcount = -1 no UPDATE.
            - O SQLAlchemy interpreta isso como StaleDataError quando o objeto ORM é atualizado.
            - Por isso faço UPDATE/DELETE/INSERT via SQL explícito, evitando o flush automático do ORM.
            """
            parametros_update_usuario = {
                "id_perfil": int(id_perfil),
                "bit_ativo": 1 if bit_ativo else 0,
                "agora": agora,
                "id_usuario": id_usuario_int,
            }

            if hash_senha_nova:
                parametros_update_usuario["hash_senha"] = hash_senha_nova
                sql_update_usuario = """
                    UPDATE [Integracao].[Silver].[DimUsuarios]
                    SET
                        IDDimPerfilUsuario = :id_perfil,
                        BitAtivo = :bit_ativo,
                        HashSenha = :hash_senha,
                        UpdateAt = :agora
                    WHERE IDDimUsuarios = :id_usuario
                """
            else:
                sql_update_usuario = """
                    UPDATE [Integracao].[Silver].[DimUsuarios]
                    SET
                        IDDimPerfilUsuario = :id_perfil,
                        BitAtivo = :bit_ativo,
                        UpdateAt = :agora
                    WHERE IDDimUsuarios = :id_usuario
                """

            db.session.execute(
                text(sql_update_usuario),
                parametros_update_usuario,
            )

            db.session.execute(
                text("""
                    DELETE FROM [Integracao].[Silver].[PermissoesUsuario]
                    WHERE IDDimUsuarios = :id_usuario
                """),
                {"id_usuario": id_usuario_int},
            )

            linhas_permissoes = []

            for permissao in permissoes:
                nome_campo = f"perm_{permissao.IDDimPermissoes}"
                tipo = _texto(request.form.get(nome_campo)).upper()

                if tipo not in {"CONCEDER", "REVOGAR"}:
                    continue

                linhas_permissoes.append(
                    {
                        "id_usuario": id_usuario_int,
                        "id_permissao": int(permissao.IDDimPermissoes),
                        "tipo_atribuicao": tipo,
                        "data_criacao": agora,
                        "data_atualizacao": agora,
                        "id_usuario_executor": id_usuario_executor,
                        "observacao": "Alterado pela tela de permissões de usuários.",
                    }
                )

            if linhas_permissoes:
                db.session.execute(
                    text("""
                        INSERT INTO [Integracao].[Silver].[PermissoesUsuario] (
                            IDDimUsuarios,
                            IDDimPermissoes,
                            TipoAtribuicao,
                            DataExpiracao,
                            DataCriacao,
                            DataAtualizacao,
                            CriadoPorIDDimUsuarios,
                            Observacao
                        )
                        VALUES (
                            :id_usuario,
                            :id_permissao,
                            :tipo_atribuicao,
                            NULL,
                            :data_criacao,
                            :data_atualizacao,
                            :id_usuario_executor,
                            :observacao
                        )
                    """),
                    linhas_permissoes,
                )

            db.session.commit()
            db.session.expire_all()

            if hash_senha_nova:
                flash("Usuário, permissões e senha atualizados com sucesso.", "success")
            else:
                flash("Usuário atualizado com sucesso.", "success")

            return redirect(
                url_for(
                    "Autenticacao.usuarios_editar",
                    id_usuario=id_usuario_int,
                )
            )

        except Exception:
            db.session.rollback()
            current_app.logger.exception("Erro ao atualizar usuário/permissões.")
            flash("Erro ao atualizar usuário.", "danger")

            return redirect(
                url_for(
                    "Autenticacao.usuarios_editar",
                    id_usuario=usuario.IDDimUsuarios,
                )
            )

    usuario = (
        db.session.query(DimUsuarios)
        .filter(DimUsuarios.IDDimUsuarios == id_usuario)
        .first()
    )

    permissoes_usuario_linhas = (
        db.session.query(PermissoesUsuario, DimPermissoes)
        .join(
            DimPermissoes,
            PermissoesUsuario.IDDimPermissoes == DimPermissoes.IDDimPermissoes,
        )
        .filter(PermissoesUsuario.IDDimUsuarios == usuario.IDDimUsuarios)
        .filter(DimPermissoes.BitAtivo == True)
        .all()
    )

    permissoes_extras_map = {}

    for permissao_usuario, permissao in permissoes_usuario_linhas:
        tipo = _texto(permissao_usuario.TipoAtribuicao).upper()
        codigo = _texto(permissao.CodigoPermissao).upper()

        permissoes_extras_map[permissao_usuario.IDDimPermissoes] = tipo

        if codigo:
            permissoes_extras_map[codigo] = tipo

    permissoes_perfil_linhas = (
        db.session.query(PermissoesPerfil, DimPermissoes)
        .join(
            DimPermissoes,
            PermissoesPerfil.IDDimPermissoes == DimPermissoes.IDDimPermissoes,
        )
        .filter(PermissoesPerfil.IDDimPerfilUsuario == usuario.IDDimPerfilUsuario)
        .filter(DimPermissoes.BitAtivo == True)
        .all()
    )

    permissoes_perfil_ids = set()
    permissoes_perfil_codigos = set()

    for permissao_perfil, permissao in permissoes_perfil_linhas:
        permissoes_perfil_ids.add(permissao_perfil.IDDimPermissoes)

        codigo = _texto(permissao.CodigoPermissao).upper()

        if codigo:
            permissoes_perfil_codigos.add(codigo)

    permissoes_filtros_clientes = _montar_permissoes_filtros_clientes(permissoes)
    permissoes_filtros_clientes_ids = {
        int(item["permissao"].IDDimPermissoes)
        for item in permissoes_filtros_clientes
        if getattr(item["permissao"], "IDDimPermissoes", None) is not None
    }

    return render_template(
        "autenticacao/usuarios_editar.html",
        usuario=usuario,
        perfis=perfis,
        permissoes=permissoes,
        permissoes_filtros_clientes=permissoes_filtros_clientes,
        permissoes_filtros_clientes_ids=permissoes_filtros_clientes_ids,
        permissoes_extras_map=permissoes_extras_map,
        permissoes_perfil_ids=permissoes_perfil_ids,
        permissoes_perfil_codigos=permissoes_perfil_codigos,
    )