from functools import wraps
from unicodedata import normalize

from flask import abort
from flask_login import current_user



ITENS_MENU_PAINEIS_USUARIO_LEGADO = {
    "disponibilidades",
    "empresas",
    "lista_ocupacao",
    "contratos",
}


MAPA_ITEM_MENU_PERMISSOES = {
    "atendimento": {"KANBAN_VER", "KANBAN_EDITAR"},
    "kanban": {"KANBAN_VER", "KANBAN_EDITAR"},
    "kanban_atendimento": {"KANBAN_VER", "KANBAN_EDITAR"},
    "historico_atendimento": {"KANBAN_VER", "KANBAN_EDITAR", "ADMIN_TUDO"},
    "historico_cards": {"KANBAN_VER", "KANBAN_EDITAR", "ADMIN_TUDO"},
    "historico_cards_lista": {"KANBAN_VER", "KANBAN_EDITAR", "ADMIN_TUDO"},
    "health_check_comercial": {"KANBAN_VER", "KANBAN_EDITAR", "ADMIN_TUDO"},

    "checkin_novo": {"CHECKIN_CRIAR", "CHECKIN_VER", "PAINEIS_LISTA_VER", "PAINEIS_VER", "ADMIN_TUDO"},
    "lista_checkins": {"CHECKIN_LISTA_VER", "CHECKIN_VER", "PAINEIS_LISTA_VER", "PAINEIS_VER", "ADMIN_TUDO"},
    "checkin_arquivo": {"CHECKIN_LISTA_VER", "CHECKIN_VER", "PAINEIS_LISTA_VER", "PAINEIS_VER", "ADMIN_TUDO"},
    "checkin_visualizar": {"CHECKIN_LISTA_VER", "CHECKIN_VER", "PAINEIS_LISTA_VER", "PAINEIS_VER", "ADMIN_TUDO"},

    "disponibilidades": {"PAINEIS_LISTA_VER", "PAINEIS_VER", "ADMIN_TUDO"},
    "paineis": {"PAINEIS_LISTA_VER", "PAINEIS_VER", "ADMIN_TUDO"},
    "grade_painel": {"PAINEIS_GRADE_VER", "PAINEIS_VER", "ADMIN_TUDO"},

    "empresas": {"CLIENTES_LISTA_VER", "ADMIN_TUDO"},
    "clientes": {"CLIENTES_LISTA_VER", "ADMIN_TUDO"},
    "clientes_detalhe": {"CLIENTES_DETALHE_VER", "CLIENTES_LISTA_VER", "ADMIN_TUDO"},

    "carteiras": {"CARTEIRA_PROPRIA_VER", "CARTEIRAS_VER", "ADMIN_TUDO"},
    "carteira_propria": {"CARTEIRA_PROPRIA_VER", "ADMIN_TUDO"},

    "lista_ocupacao": {"OCUPACAO_LISTA_VER", "ADMIN_TUDO"},
    "contratos": {"CONTRATOS_LISTA_VER", "CONTRATOS_VER", "ADMIN_TUDO"},

    "performance_paineis": {"ADMIN_TUDO"},
    "movimentacao_financeira": {"ADMIN_TUDO"},
    "inadimplentes": {"ADMIN_TUDO"},
    "auvo_produtos": {"ADMIN_TUDO"},
    "tickets_auvo": {"ADMIN_TUDO"},
    "criar_os_auvo": {"ADMIN_TUDO"},

    "admin": {"ADMIN_TUDO"},
    "permissao_desconto": {"ADMIN_TUDO"},
    "aprovacao_desconto": {"ADMIN_TUDO"},
    "aprovacao_contratos": {"ADMIN_TUDO"},
}


ITENS_MENU_ADMIN_BLOQUEADOS_PARA_VENDEDOR = {
    "admin",
    "permissao_desconto",
    "aprovacao_desconto",
    "aprovacao_contratos",
    "performance_paineis",
    "movimentacao_financeira",
    "inadimplentes",
    "auvo_produtos",
    "tickets_auvo",
    "criar_os_auvo",
    "checkin_novo",
    "lista_checkins",
    "checkin_arquivo",
    "checkin_visualizar",
    "health_check_comercial",
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
            getattr(perfil_rel, "NomePerfilUsuario", None),
        ])

    for valor in candidatos:
        txt = _normalizar_texto_acl(valor)
        if txt:
            return txt

    return ""


def _usuario_tem_permissao(codigo_permissao: str) -> bool:
    """_usuario_tem_permissao
    - Eu consulto o método has_permission do usuário logado.
    - Se o método não existir ou falhar, retorno False para não liberar indevidamente.
    """
    if not getattr(current_user, "is_authenticated", False):
        return False

    codigo = str(codigo_permissao or "").strip().upper()
    if not codigo:
        return False

    try:
        metodo = getattr(current_user, "has_permission", None)
        if not metodo:
            return False
        return bool(metodo(codigo))
    except Exception:
        return False


def _usuario_tem_alguma_permissao(codigos_permissao) -> bool:
    """_usuario_tem_alguma_permissao
    - Eu libero quando pelo menos uma permissão da lista existir.
    - Exemplo: KANBAN_EDITAR também deve permitir visualizar o menu Atendimento.
    """
    for codigo in codigos_permissao or []:
        if _usuario_tem_permissao(codigo):
            return True
    return False


def usuario_eh_perfil_vendedor() -> bool:
    """usuario_eh_perfil_vendedor
    - Eu identifico o perfil Vendedor pelo ID fixo informado: IDDimPerfilUsuario = 3.
    - Também mantenho uma conferência pelo nome, para evitar falha se algum carregamento vier sem ID.
    """
    if not getattr(current_user, "is_authenticated", False):
        return False

    try:
        if int(getattr(current_user, "IDDimPerfilUsuario", 0) or 0) == 3:
            return True
    except Exception:
        pass

    return _perfil_usuario_logado() == "vendedor"


def usuario_eh_perfil_restrito_menu_paineis() -> bool:
    """usuario_eh_perfil_restrito_menu_paineis
    - Eu digo se o usuário atual é de perfil restrito.
    - Admin total nunca entra nessa regra restrita.
    """
    if not getattr(current_user, "is_authenticated", False):
        return False

    if usuario_eh_perfil_vendedor():
        return True

    if _usuario_tem_permissao("ADMIN_TUDO"):
        return False

    perfil = _perfil_usuario_logado()

    return perfil in {
        "usuario",
        "vendedor",
    }


def pode_acessar_menu_paineis(item_menu: str) -> bool:
    """pode_acessar_menu_paineis
    - Eu controlo a visibilidade dos itens do menu Painéis.
    - Para 'atendimento', exijo KANBAN_VER ou KANBAN_EDITAR.
    - Para perfil restrito, não basta estar no perfil: precisa passar pela permissão do item.
    """
    if not getattr(current_user, "is_authenticated", False):
        return False

    chave = _normalizar_texto_acl(item_menu)

    if usuario_eh_perfil_vendedor() and chave in ITENS_MENU_ADMIN_BLOQUEADOS_PARA_VENDEDOR:
        return False

    if _usuario_tem_permissao("ADMIN_TUDO"):
        return True

    permissoes_exigidas = MAPA_ITEM_MENU_PERMISSOES.get(chave)
    if permissoes_exigidas:
        return _usuario_tem_alguma_permissao(permissoes_exigidas)

    if usuario_eh_perfil_restrito_menu_paineis():
        return chave in ITENS_MENU_PAINEIS_USUARIO_LEGADO

    return True


def requer_item_menu_paineis(item_menu: str):
    """requer_item_menu_paineis
    - Eu bloqueio endpoint quando o usuário não deveria acessar o item do menu.
    """
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