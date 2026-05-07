from flask_login import current_user
from sqlalchemy import text
from flask import request
from flask_socketio import emit, join_room, leave_room
from app.extensions import socketio, db




NAMESPACE_MENSAGENS = "/mensagens"


def _id_usuario_socket_logado() -> int | None:
    """Eu descubro o IDDimUsuarios do usuário logado para entrar na sala correta."""
    candidatos = [
        getattr(current_user, "IDDimUsuarios", None),
        getattr(current_user, "id", None),
        getattr(current_user, "Id", None),
        getattr(current_user, "ID", None),
    ]

    for valor in candidatos:
        try:
            if valor is not None and str(valor).strip() != "":
                return int(valor)
        except Exception:
            pass

    return None


def room_mensagens_usuario(id_usuario: int) -> str:
    """Eu gero o nome da sala exclusiva de mensagens do usuário."""
    return f"mensagens_usuario:{int(id_usuario)}"


def contar_mensagens_nao_lidas(id_usuario: int) -> int:
    """Eu conto as mensagens ativas e não lidas do usuário."""
    row = db.session.execute(
        text("""
            SELECT COUNT(1) AS Total
            FROM [Integracao].[Silver].[FatoMensagemUsuario] WITH (NOLOCK)
            WHERE IDDimUsuariosDestinatario = :id_usuario
              AND ISNULL(BitAtivo, 1) = 1
              AND ISNULL(BitLida, 0) = 0
        """),
        {"id_usuario": int(id_usuario)}
    ).mappings().first()

    return int(row["Total"] or 0) if row else 0


def emitir_resumo_mensagens_usuario(id_usuario: int, evento: str = "mensagens:resumo") -> None:
    """Eu envio para o navegador do usuário a quantidade atual de mensagens novas."""
    total = contar_mensagens_nao_lidas(int(id_usuario))

    socketio.emit(
        evento,
        {
            "ok": True,
            "id_usuario": int(id_usuario),
            "nao_lidas": total,
        },
        namespace=NAMESPACE_MENSAGENS,
        to=room_mensagens_usuario(int(id_usuario)),
    )


@socketio.on("connect", namespace=NAMESPACE_MENSAGENS)
def mensagens_conectar():
    """Eu conecto o usuário logado na sala privada de mensagens dele."""
    if not current_user.is_authenticated:
        return False

    id_usuario = _id_usuario_socket_logado()
    if not id_usuario:
        return False

    join_room(room_mensagens_usuario(id_usuario))

    emit(
        "mensagens:resumo",
        {
            "ok": True,
            "id_usuario": int(id_usuario),
            "nao_lidas": contar_mensagens_nao_lidas(id_usuario),
        },
        namespace=NAMESPACE_MENSAGENS,
    )


@socketio.on("mensagens:pedir_resumo", namespace=NAMESPACE_MENSAGENS)
def mensagens_pedir_resumo(dados=None):
    """Eu atualizo o contador quando o front pedir o resumo."""
    if not current_user.is_authenticated:
        return False

    id_usuario = _id_usuario_socket_logado()
    if not id_usuario:
        return False

    emit(
        "mensagens:resumo",
        {
            "ok": True,
            "id_usuario": int(id_usuario),
            "nao_lidas": contar_mensagens_nao_lidas(id_usuario),
        },
        namespace=NAMESPACE_MENSAGENS,
    )