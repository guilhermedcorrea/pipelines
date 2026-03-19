from functools import wraps
from unicodedata import normalize

from flask import abort
from flask_login import current_user




ITENS_MENU_PAINEIS_USUARIO = {
    "disponibilidades",
    "empresas",
    "lista_ocupacao",
    "contratos",
}


def _normalizar_texto_acl(valor) -> str:
    """_normalizar_texto_acl
    - Eu normalizo texto removendo acento, espaços extras e caixa.
    - Isso evita erro entre 'Usuário', 'usuario', 'USUÁRIO' etc.
    """
    texto = str(valor or "").strip().lower()
    texto = normalize("NFKD", texto).encode("ascii", "ignore").decode("ascii")
    return texto.strip()


def _perfil_usuario_logado() -> str:
    """_perfil_usuario_logado
    - Eu tento descobrir o perfil do usuário logado de forma tolerante.
    - Como eu não quero depender de um único nome de campo, eu testo alguns atributos comuns.
    """
    if not getattr(current_user, "is_authenticated", False):
        return ""

    candidatos = [
        getattr(current_user, "Perfil", None),
        getattr(current_user, "NomePerfil", None),
        getattr(current_user, "DescricaoPerfil", None),
        getattr(current_user, "TipoUsuario", None),
        getattr(current_user, "Grupo", None),
    ]

    perfil_rel = getattr(current_user, "perfil", None)
    if perfil_rel is not None:
        candidatos.extend([
            getattr(perfil_rel, "Perfil", None),
            getattr(perfil_rel, "NomePerfil", None),
            getattr(perfil_rel, "Descricao", None),
            getattr(perfil_rel, "DescricaoPerfil", None),
        ])

    for valor in candidatos:
        txt = _normalizar_texto_acl(valor)
        if txt:
            return txt

    return ""


def usuario_eh_perfil_restrito_menu_paineis() -> bool:
    """usuario_eh_perfil_restrito_menu_paineis
    - Eu digo se o usuário atual é do perfil 'Usuário'.
    - Admin total nunca entra nessa regra restrita.
    """
    if not getattr(current_user, "is_authenticated", False):
        return False

    try:
        if getattr(current_user, "has_permission", None):
            if current_user.has_permission("ADMIN_TUDO"):
                return False
    except Exception:
        pass

    perfil = _perfil_usuario_logado()
    return perfil == "usuario"


def pode_acessar_menu_paineis(item_menu: str) -> bool:
   
    if not getattr(current_user, "is_authenticated", False):
        return False

    chave = _normalizar_texto_acl(item_menu)

    try:
        if getattr(current_user, "has_permission", None):
            if current_user.has_permission("ADMIN_TUDO"):
                return True
    except Exception:
        pass

    if usuario_eh_perfil_restrito_menu_paineis():
        return chave in ITENS_MENU_PAINEIS_USUARIO

    return True


def requer_item_menu_paineis(item_menu: str):
    
    def decorator(view_func):
        @wraps(view_func)
        def wrapper(*args, **kwargs):
            if not getattr(current_user, "is_authenticated", False):
                return abort(401)

            if not pode_acessar_menu_paineis(item_menu):
                return abort(403)

            return view_func(*args, **kwargs)
        return wrapper
    return decorator



