from datetime import datetime, timedelta

from flask import Blueprint, current_app, render_template, request, redirect, url_for, flash, session, abort
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
import requests





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


            if not getattr(current_user, "has_permission", None):
                abort(500, description="Usuário carregado não possui has_permission().")

            if not current_user.has_permission(codigo):

                flash("Você não tem permissão para acessar esta página.", "danger")
                abort(403)

            return view_func(*args, **kwargs)

        return wrapper
    return decorator






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
    )




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

            usuario.IDDimPerfilUsuario = id_perfil
            usuario.BitAtivo = bit_ativo
            usuario.UpdateAt = _agora()

            db.session.query(PermissoesUsuario).filter(
                PermissoesUsuario.IDDimUsuarios == usuario.IDDimUsuarios
            ).delete(synchronize_session=False)

            db.session.flush()

            agora = _agora()
            id_usuario_executor = None

            try:
                id_usuario_executor = int(current_user.get_id())
            except Exception:
                id_usuario_executor = None

            for permissao in permissoes:
                nome_campo = f"perm_{permissao.IDDimPermissoes}"
                tipo = _texto(request.form.get(nome_campo)).upper()

                if tipo not in {"CONCEDER", "REVOGAR"}:
                    continue

                nova_permissao_usuario = PermissoesUsuario(
                    IDDimUsuarios=usuario.IDDimUsuarios,
                    IDDimPermissoes=permissao.IDDimPermissoes,
                    TipoAtribuicao=tipo,
                    DataExpiracao=None,
                    DataCriacao=agora,
                    DataAtualizacao=agora,
                    CriadoPorIDDimUsuarios=id_usuario_executor,
                    Observacao="Alterado pela tela de permissões de usuários.",
                )

                db.session.add(nova_permissao_usuario)

            db.session.commit()
            db.session.expire_all()

            flash("Usuário atualizado com sucesso.", "success")

            return redirect(
                url_for(
                    "Autenticacao.usuarios_editar",
                    id_usuario=usuario.IDDimUsuarios,
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

    return render_template(
        "autenticacao/usuarios_editar.html",
        usuario=usuario,
        perfis=perfis,
        permissoes=permissoes,
        permissoes_extras_map=permissoes_extras_map,
        permissoes_perfil_ids=permissoes_perfil_ids,
        permissoes_perfil_codigos=permissoes_perfil_codigos,
    )