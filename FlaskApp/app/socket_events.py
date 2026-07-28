from flask_login import current_user
from sqlalchemy import text
from flask_socketio import emit, join_room, leave_room
from app.extensions import socketio, db


NAMESPACE_MENSAGENS = "/mensagens"


def _id_usuario_socket_logado() -> int | None:
    """Eu descubro o IDDimUsuarios do usuário logado para entrar na sala correta."""
    candidatos = [
        getattr(current_user, "IDDimUsuarios", None),
        getattr(current_user, "IDDimUsuario", None),
        getattr(current_user, "IDUsuario", None),
        getattr(current_user, "id_usuario", None),
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
    """Eu gero o nome da sala privada de mensagens do usuário."""
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
        {"id_usuario": int(id_usuario)},
    ).mappings().first()

    return int(row["Total"] or 0) if row else 0


def montar_payload_resumo_mensagens(id_usuario: int) -> dict:
    """Eu monto o payload padrão enviado ao navegador."""
    id_usuario_int = int(id_usuario)

    return {
        "ok": True,
        "id_usuario": id_usuario_int,
        "nao_lidas": contar_mensagens_nao_lidas(id_usuario_int),
    }


def emitir_resumo_mensagens_usuario(id_usuario: int, evento: str = "mensagens:resumo") -> dict:
    """Eu envio para a sala privada do usuário a quantidade atual de mensagens novas."""
    id_usuario_int = int(id_usuario)
    payload = montar_payload_resumo_mensagens(id_usuario_int)

    socketio.emit(
        evento,
        payload,
        namespace=NAMESPACE_MENSAGENS,
        to=room_mensagens_usuario(id_usuario_int),
    )

    return payload


def emitir_nova_mensagem_usuario(id_usuario: int) -> dict:
    """Eu padronizo o disparo usado quando uma mensagem nova é criada."""
    return emitir_resumo_mensagens_usuario(
        int(id_usuario),
        evento="mensagens:nova",
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
        montar_payload_resumo_mensagens(id_usuario),
        namespace=NAMESPACE_MENSAGENS,
    )


@socketio.on("disconnect", namespace=NAMESPACE_MENSAGENS)
def mensagens_desconectar():
    """Eu removo o usuário da sala privada quando o socket desconecta."""
    id_usuario = _id_usuario_socket_logado()
    if id_usuario:
        leave_room(room_mensagens_usuario(id_usuario))


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
        montar_payload_resumo_mensagens(id_usuario),
        namespace=NAMESPACE_MENSAGENS,
    )


# =========================================================
# PAINÉIS — atualização em tempo real da lista/ocupação
# =========================================================
import hashlib
import json
from datetime import datetime
from typing import Any

from app.extensions import cache


NAMESPACE_PAINEIS = "/paineis"


def _room_paineis_lista_usuario(id_usuario: int, chave_tela: str) -> str:
    """Eu gero uma sala isolada por usuário e por conjunto de filtros visível."""
    return f"paineis_lista_usuario:{int(id_usuario)}:{chave_tela}"


def _normalizar_texto_socket(valor: Any) -> str:
    return str(valor or "").strip()


def _normalizar_codface_socket(valor: Any) -> str:
    return _normalizar_texto_socket(valor).upper()


def _normalizar_tipo_socket(valor: Any) -> str:
    return _normalizar_texto_socket(valor).upper()


def _int_socket(valor: Any, padrao: int = 0) -> int:
    try:
        if valor is None:
            return padrao
        texto = str(valor).strip()
        if not texto:
            return padrao
        return int(float(texto.replace(",", ".")))
    except Exception:
        return padrao


def _data_iso_socket(valor: Any) -> str:
    try:
        texto = _normalizar_texto_socket(valor)
        if not texto:
            return ""
        if len(texto) >= 10:
            texto = texto[:10]
        datetime.strptime(texto, "%Y-%m-%d")
        return texto
    except Exception:
        return ""


def _normalizar_itens_socket(dados: dict | None) -> list[dict[str, Any]]:
    itens = []
    vistos = set()

    for item in ((dados or {}).get("itens") or []):
        codponto = _int_socket(item.get("codponto"))
        codface = _normalizar_codface_socket(item.get("codface"))
        tipo_prod = _normalizar_tipo_socket(item.get("tipo_prod"))

        if codponto <= 0 or not codface:
            continue

        chave = (codponto, codface, tipo_prod)
        if chave in vistos:
            continue
        vistos.add(chave)

        itens.append(
            {
                "codponto": codponto,
                "codface": codface,
                "tipo_prod": tipo_prod,
            }
        )

    return itens[:120]


def _hash_tela_paineis(dt_ini: str, dt_fim: str, itens: list[dict[str, Any]]) -> str:
    base = {
        "dt_ini": dt_ini,
        "dt_fim": dt_fim,
        "itens": itens,
    }
    bruto = json.dumps(base, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha1(bruto.encode("utf-8")).hexdigest()[:24]


def _cache_key_tela_paineis(dt_ini: str, dt_fim: str, itens: list[dict[str, Any]]) -> str:
    return f"paineis:ocupacao:lista:{_hash_tela_paineis(dt_ini, dt_fim, itens)}"


def _emitir_payload_cacheado_paineis(cache_key: str) -> bool:
    try:
        payload_cacheado = cache.get(cache_key)
    except Exception:
        payload_cacheado = None

    if not payload_cacheado:
        return False

    try:
        payload_cacheado = dict(payload_cacheado)
        payload_cacheado["origem"] = "redis_cache"
        emit("paineis:ocupacao:lote", payload_cacheado, namespace=NAMESPACE_PAINEIS)
        return True
    except Exception:
        return False


@socketio.on("connect", namespace=NAMESPACE_PAINEIS)
def paineis_conectar():
    """Eu aceito somente usuário autenticado no canal de painéis."""
    if not current_user.is_authenticated:
        return False

    id_usuario = _id_usuario_socket_logado()
    if not id_usuario:
        return False

    join_room(f"paineis_usuario:{int(id_usuario)}")
    emit(
        "paineis:conectado",
        {
            "ok": True,
            "id_usuario": int(id_usuario),
        },
        namespace=NAMESPACE_PAINEIS,
    )


@socketio.on("disconnect", namespace=NAMESPACE_PAINEIS)
def paineis_desconectar():
    """Eu retiro o usuário da sala base quando ele desconecta."""
    id_usuario = _id_usuario_socket_logado()
    if id_usuario:
        leave_room(f"paineis_usuario:{int(id_usuario)}")


@socketio.on("paineis:lista:inscrever", namespace=NAMESPACE_PAINEIS)
def paineis_lista_inscrever(dados=None):
    """Eu inscrevo a tela atual e disparo recálculo assíncrono da ocupação."""
    if not current_user.is_authenticated:
        return False

    id_usuario = _id_usuario_socket_logado()
    if not id_usuario:
        return False

    dados = dados or {}
    dt_ini = _data_iso_socket(dados.get("dt_ini"))
    dt_fim = _data_iso_socket(dados.get("dt_fim"))
    itens = _normalizar_itens_socket(dados)

    if not dt_ini or not dt_fim or not itens:
        emit(
            "paineis:ocupacao:erro",
            {
                "ok": False,
                "erro": "Não foi possível identificar período e faces visíveis para atualizar a ocupação.",
            },
            namespace=NAMESPACE_PAINEIS,
        )
        return False

    chave_tela = _hash_tela_paineis(dt_ini, dt_fim, itens)
    room = _room_paineis_lista_usuario(int(id_usuario), chave_tela)
    join_room(room)

    cache_key = _cache_key_tela_paineis(dt_ini, dt_fim, itens)
    lock_key = f"{cache_key}:lock"

    emit(
        "paineis:ocupacao:status",
        {
            "ok": True,
            "status": "inscrito",
            "room": room,
            "cache_key": cache_key,
        },
        namespace=NAMESPACE_PAINEIS,
    )

    if _emitir_payload_cacheado_paineis(cache_key):
        return True

    try:
        lock_criado = cache.add(lock_key, "1", timeout=12)
    except Exception:
        lock_criado = True

    if not lock_criado:
        emit(
            "paineis:ocupacao:status",
            {
                "ok": True,
                "status": "recalculo_em_andamento",
                "cache_key": cache_key,
            },
            namespace=NAMESPACE_PAINEIS,
        )
        return True

    try:
        from app.tasks.paineis_tempo_real_tasks import atualizar_ocupacao_lista_paineis_socket

        atualizar_ocupacao_lista_paineis_socket.apply_async(
            kwargs={
                "room": room,
                "dt_ini": dt_ini,
                "dt_fim": dt_fim,
                "itens": itens,
                "cache_key": cache_key,
            },
            queue="paineis_ocupacao",
        )

        emit(
            "paineis:ocupacao:status",
            {
                "ok": True,
                "status": "recalculo_agendado",
                "cache_key": cache_key,
            },
            namespace=NAMESPACE_PAINEIS,
        )
        return True

    except Exception as exc:
        try:
            cache.delete(lock_key)
        except Exception:
            pass

        emit(
            "paineis:ocupacao:erro",
            {
                "ok": False,
                "erro": str(exc),
                "cache_key": cache_key,
            },
            namespace=NAMESPACE_PAINEIS,
        )
        return False


@socketio.on("paineis:lista:pedir_atualizacao", namespace=NAMESPACE_PAINEIS)
def paineis_lista_pedir_atualizacao(dados=None):
    """Eu reaproveito a mesma inscrição quando o front pedir novo recálculo."""
    return paineis_lista_inscrever(dados or {})


# =========================================================
# VENCIMENTOS DE CAMPANHAS — ações refletidas em tempo real
# =========================================================
NAMESPACE_VENCIMENTOS_CAMPANHAS = "/vencimentos-campanhas"
ROOM_VENCIMENTOS_CAMPANHAS = "vencimentos_campanhas:usuarios_autenticados"


def emitir_atualizacao_vencimentos_campanhas(payload: dict | None = None) -> dict:
    """Distribui somente os identificadores e o novo estado após o commit da ação."""

    dados_origem = dict(payload or {})
    dados = {
        "ok": True,
        "acao": _normalizar_texto_socket(dados_origem.get("acao")).upper(),
        "fonte_linha": _normalizar_texto_socket(dados_origem.get("fonte_linha")).upper(),
        "id_reserva": _int_socket(dados_origem.get("id_reserva")) or None,
        "id_vencimento": _int_socket(dados_origem.get("id_vencimento")) or None,
        "id_card": _int_socket(dados_origem.get("id_card")) or None,
        "status": _normalizar_texto_socket(dados_origem.get("status")).upper(),
        "remover_linha": bool(dados_origem.get("remover_linha")),
        "mensagem": _normalizar_texto_socket(dados_origem.get("mensagem"))[:500],
        "atualizado_em": datetime.now().isoformat(timespec="seconds"),
    }

    socketio.emit(
        "vencimentos:atualizado",
        dados,
        namespace=NAMESPACE_VENCIMENTOS_CAMPANHAS,
        to=ROOM_VENCIMENTOS_CAMPANHAS,
    )
    return dados


@socketio.on("connect", namespace=NAMESPACE_VENCIMENTOS_CAMPANHAS)
def vencimentos_campanhas_conectar():
    """Aceita somente sessão autenticada e inscreve a tela no canal compartilhado."""

    if not current_user.is_authenticated:
        return False

    join_room(ROOM_VENCIMENTOS_CAMPANHAS)
    emit(
        "vencimentos:conectado",
        {
            "ok": True,
            "atualizado_em": datetime.now().isoformat(timespec="seconds"),
        },
        namespace=NAMESPACE_VENCIMENTOS_CAMPANHAS,
    )
    return True


@socketio.on("disconnect", namespace=NAMESPACE_VENCIMENTOS_CAMPANHAS)
def vencimentos_campanhas_desconectar():
    """Remove a conexão encerrada da sala da tela."""

    if current_user.is_authenticated:
        leave_room(ROOM_VENCIMENTOS_CAMPANHAS)
