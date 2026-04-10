from flask import request
from flask_socketio import emit, join_room, leave_room

from app.extensions import socketio


@socketio.on("connect", namespace="/kanban")
def ao_conectar() -> None:
    """Evento disparado quando o cliente conecta no namespace do kanban."""
    emit(
        "conexao_ok",
        {
            "mensagem": "Conectado com sucesso ao Socket.IO do kanban.",
            "sid": request.sid,
        },
    )


@socketio.on("disconnect", namespace="/kanban")
def ao_desconectar() -> None:
    """Evento disparado quando o cliente desconecta."""
   
    pass


@socketio.on("entrar_kanban", namespace="/kanban")
def entrar_kanban(dados: dict) -> None:
    """Coloca o cliente na sala do kanban informado."""
    id_kanban = dados.get("id_kanban")

    if not id_kanban:
        emit("erro_socket", {"mensagem": "id_kanban não informado."})
        return

    nome_sala = f"kanban:{id_kanban}"
    join_room(nome_sala)

    emit(
        "entrou_kanban",
        {
            "mensagem": "Cliente entrou na sala do kanban.",
            "sala": nome_sala,
            "id_kanban": id_kanban,
        },
    )


@socketio.on("sair_kanban", namespace="/kanban")
def sair_kanban(dados: dict) -> None:
    """Remove o cliente da sala do kanban informado."""
    id_kanban = dados.get("id_kanban")

    if not id_kanban:
        emit("erro_socket", {"mensagem": "id_kanban não informado."})
        return

    nome_sala = f"kanban:{id_kanban}"
    leave_room(nome_sala)

    emit(
        "saiu_kanban",
        {
            "mensagem": "Cliente saiu da sala do kanban.",
            "sala": nome_sala,
            "id_kanban": id_kanban,
        },
    )