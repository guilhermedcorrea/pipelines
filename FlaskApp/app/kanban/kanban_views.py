import json
import re
import unicodedata
from collections.abc import Mapping
from typing import Any
from decimal import Decimal, InvalidOperation
import requests
from flask import Blueprint, abort, current_app, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import text
from flask_socketio import disconnect, emit, join_room, leave_room
from ..extensions import cache, db, limiter, socketio



"""Kanban Euromidia Comercial"""


kanban_bp = Blueprint("kanban", __name__)




TABELA_SOLICITACAO_CONTRATO = "[Integracao].[Silver].[FatoSolicitacaoContratoEuromidia]"
TABELA_SOLICITACAO_CONTRATO_ITEM = "[Integracao].[Silver].[FatoSolicitacaoContratoItemEuromidia]"
TABELA_STATUS_CONTRATOS = "[Integracao].[Silver].[DimStatusContratos]"

ID_TAG_CONTRATO_EM_AVALIACAO = 14
NOME_TAG_CONTRATO_EM_AVALIACAO = "Contrato em Avaliação"
NOME_TAG_TIPO_CONTRATO_NOVO = "Novo Contrato"
NOME_TAG_TIPO_CONTRATO_ADITIVO = "Aditivo"







TABELA_EMPRESAS_PROPRIETARIAS = "[Integracao].[dbo].[EmpresaProprietaria]"
URL_API_MINHA_RECEITA = "https://minhareceita.org"

NOME_TAG_DESCONTO_APROVADO = "Desconto Aprovado"
TIPO_NOTA_APROVACAO_DESCONTO = "APROVACAO_DESCONTO"

NOME_TAG_APROVACAO_DESCONTO = "Aprovação Desconto"
PERCENTUAL_LIMITE_APROVACAO_DIRETORIA_SOBRE_CUSTO = Decimal("12")
TABELA_MOTIVO_ENCERRAMENTO_CARD = "[Kanban].[Silver].[DimKanbanMotivoEncerramento]"
TABELA_HISTORICO_ENCERRAMENTO_CARD = "[Kanban].[Silver].[FatoDimHistoricoEncerramentoCard]"
TABELA_CARD_TAG_HISTORICO= "[Kanban].[Silver].[FatoKanbanCardTagHistorico]"
TABELA_CARD_STATUS_HISTORICO = "[Kanban].[Silver].[FatoKanbanCardStatusHistorico]"
TABELA_CARD_OBSERVACOES = "[Kanban].[Silver].[FatoKanbanCardObservacoes]"
TABELA_CARD_NEGOCIACAO_PRECO = "[Kanban].[Silver].[FatoKanbanNegociacaoPreco]"
TABELA_STATUS_CARD = "[Kanban].[Silver].[DimKanbanStatusCard]"
TABELA_MOTIVO_INATIVACAO_CARD = "[Kanban].[Silver].[DimKanbanMotivoInativacaoCard]"
STATUS_CARD_FALLBACK_PADRAO = "ATIVO"
STATUS_CARD_FALLBACK_INATIVACAO = "CANCELADO"
TIPO_FASE_FALLBACK_PADRAO = "ATIVA"
TIPO_TAG_FALLBACK_PADRAO = "OPERACIONAL"
NAMESPACE_SOCKET_KANBAN = "/kanban"

TIMEOUT_CACHE_CURTO = 20
TIMEOUT_CACHE_MEDIO = 60
TIMEOUT_CACHE_LONGO = 300
LIMITE_CARDS_POR_FASE = 100

TABELA_KANBAN = "[Kanban].[Silver].[DimKanban]"
TABELA_KANBAN_FASE = "[Kanban].[Silver].[DimKanbanFase]"
TABELA_CARD = "[Kanban].[Silver].[FatoKanbanCard]"
TABELA_CARD_MOVIMENTO = "[Kanban].[Silver].[FatoKanbanCardMovimento]"
TABELA_CARD_NOTA = "[Kanban].[Silver].[FatoKanbanCardNota]"
TABELA_CARD_LOG = "[Kanban].[Silver].[FatoKanbanCardLog]"
TABELA_EMPRESAS = "[Integracao].[Silver].[DimEmpresas]"
TABELA_CNAES = "[Integracao].[Silver].[DimCnaes]"


def _id_empresa_usuario() -> int:
    return int(getattr(current_user, "IDEmpresaProprietaria", 0) or 0)



def _id_usuario() -> int:
    return int(getattr(current_user, "IDDimUsuarios", 0) or 0)



def _assert_login() -> int:
    if not getattr(current_user, "is_authenticated", False):
        abort(401)
    uid = _id_usuario()
    if not uid:
        abort(401)
    return uid



def _id_empresa_usuario_or_403() -> int:
    _assert_login()
    id_emp = _id_empresa_usuario()
    if not id_emp:
        abort(403, "Usuário sem IDEmpresaProprietaria definida")
    return id_emp





def _resolver_id_empresa_proprietaria_movimento(id_kanban: int, id_empresa_padrao: int) -> int:
    """
    Regra de negócio:
    - se o kanban for o principal IDDimKanban = 1, o movimento deve ser gravado com IDEmpresaProprietaria = 3
    - caso contrário, uso a empresa proprietária padrão do card/usuário
    """
    if int(id_kanban or 0) == 1:
        return 3

    return int(id_empresa_padrao or 0)






def _sql_filtro_cards_nao_concluidos_no_quadro(alias_card: str = "c") -> str:
    """
    Remove do quadro cards cuja fase atual seja final/concluída.

    Regra:
    - NomeFase = 'Concluido'
    - ou TipoFase = 'SUCESSO'
    """
    return f"""
      AND NOT EXISTS (
            SELECT 1
            FROM {TABELA_KANBAN_FASE} f_final
            WHERE f_final.IDDimKanbanFase = {alias_card}.IDDimKanbanFaseAtual
              AND f_final.IDDimKanban = {alias_card}.IDDimKanban
              AND f_final.Ativo = 1
              AND (
                    LOWER(LTRIM(RTRIM(ISNULL(f_final.NomeFase, '')))) = 'concluido'
                 OR UPPER(LTRIM(RTRIM(ISNULL(f_final.TipoFase, '')))) = 'SUCESSO'
              )
        )
    """.rstrip()





def _obter_id_dim_usuario_logado() -> int | None:
    candidatos = [
        getattr(current_user, "IDDimUsuarios", None),
        getattr(current_user, "id", None),
    ]

    for valor in candidatos:
        try:
            valor_int = int(valor)
            if valor_int > 0:
                return valor_int
        except Exception:
            pass

    if hasattr(current_user, "get_id"):
        try:
            valor_int = int(current_user.get_id())
            if valor_int > 0:
                return valor_int
        except Exception:
            pass

    return None




def _obter_id_empresa_proprietaria_usuario_logado() -> int | None:
    candidatos = [
        getattr(current_user, "IDEmpresaProprietaria", None),
        getattr(current_user, "id_empresa_proprietaria", None),
    ]

    for valor in candidatos:
        try:
            valor_int = int(valor)
            if valor_int > 0:
                return valor_int
        except Exception:
            pass

    return None











def _resolver_id_status_card_movimento(
    nome_fase_para: str | None = None,
    *,
    card_inativado: bool = False,
) -> int | None:
    """
    Regra fixa para gravar IDDimKanbanStatusCard no histórico.

    Regras:
    - fases em andamento:
        A Fazer (Back Office)
        Proposta Enviada
        Refazer
        Aprovado Cliente
        Aguardando Liberação (Gerencia)
        Documentos Enviados
      => 1

    - fase Concluido
      => 3

    - card inativado/excluído
      => 2
    """
    if card_inativado:
        return 2

    nome_normalizado = _normalizar_texto_comparacao(nome_fase_para)

    fases_status_1 = {
        "a fazer (back office)",
        "proposta enviada",
        "refazer",
        "aprovado cliente",
        "aguardando liberacao (gerencia)",
        "documentos enviados",
    }

    if nome_normalizado in fases_status_1:
        return 1

    if nome_normalizado == "concluido":
        return 3

    return None


def _registrar_status_historico_card(
    *,
    id_card: int,
    id_fase: int | None,
    id_status_card: int | None,
    id_usuario: int | None,
    id_empresa_proprietaria: int | None,
) -> None:
    """
    Registra o snapshot lógico de status do card na fase informada.

    Observação importante:
    esta tabela não possui data/hora no esquema atual, então ela funciona
    como trilha estrutural complementar. A linha do tempo cronológica continua
    vindo principalmente de FatoKanbanCardMovimento, FatoKanbanCardObservacoes
    e FatoKanbanCardLog.
    """
    if id_status_card in (None, "", 0):
        return

    _inserir_registro_dinamico(
        TABELA_CARD_STATUS_HISTORICO,
        {
            "IDEmpresaProprietaria": int(id_empresa_proprietaria) if id_empresa_proprietaria not in (None, "", 0) else None,
            "IDDimKanbanStatusCard": int(id_status_card),
            "IDFatoKanbanCard": int(id_card),
            "IDDimKanbanFase": int(id_fase) if id_fase not in (None, "", 0) else None,
            "IDDimUsuarios": int(id_usuario) if id_usuario not in (None, "", 0) else None,
        },
    )


def _registrar_tag_historico_card(
    *,
    id_fato_kanban_card_tag: int | None,
    id_card: int,
    id_fase: int | None,
    id_usuario: int | None,
    id_empresa_proprietaria: int | None,
) -> None:
    """
    Registra em qual fase a alteração de tag ocorreu.

    A tabela histórica de tag também não carrega timestamp no esquema atual.
    Por isso eu gravo aqui a referência estrutural da fase/usuário e deixo a
    cronologia completa por conta da própria FatoKanbanCardTag (AplicadoEm /
    RemovidoEm) e da FatoKanbanCardLog.
    """
    if id_fato_kanban_card_tag in (None, "", 0):
        return

    _inserir_registro_dinamico(
        TABELA_CARD_TAG_HISTORICO,
        {
            "IDEmpresaProprietaria": int(id_empresa_proprietaria) if id_empresa_proprietaria not in (None, "", 0) else None,
            "IDFatoKanbanCardTag": int(id_fato_kanban_card_tag),
            "IDFatoKanbanCard": int(id_card),
            "IDDimKanbanFase": int(id_fase) if id_fase not in (None, "", 0) else None,
            "IDDimUsuarios": int(id_usuario) if id_usuario not in (None, "", 0) else None,
        },
    )






def _rows_para_dicts(rows: Any) -> list[dict[str, Any]]:
    return [dict(r) if isinstance(r, Mapping) else r for r in (rows or [])]



def _row_para_dict(row: Any) -> dict[str, Any] | None:
    return dict(row) if isinstance(row, Mapping) else row



def _log_debug_usuario() -> None:
    is_auth = getattr(current_user, "is_authenticated", None)
    ia = getattr(current_user, "is_active", None)
    try:
        is_active_val = ia() if callable(ia) else ia
    except Exception:
        is_active_val = "erro"

    current_app.logger.warning(
        "KANBAN DEBUG: is_authenticated=%s is_active=%s IDDimUsuarios=%s get_id=%s email=%s",
        is_auth,
        is_active_val,
        getattr(current_user, "IDDimUsuarios", None),
        (current_user.get_id() if hasattr(current_user, "get_id") else None),
        getattr(current_user, "Email", None),
    )



def _request_pede_dado_fresco() -> bool:
    """
    Permite ao front pedir leitura sem usar cache do backend.

    Exemplos aceitos:
    - ?fresh=1
    - ?fresh=true
    - ?no_cache=1
    """
    valor = (
        request.args.get("fresh")
        or request.args.get("no_cache")
        or ""
    ).strip().lower()

    return valor in {"1", "true", "t", "yes", "y", "sim"}




from datetime import datetime, date

def _para_data_sql_ou_none(valor: Any):
    if valor is None:
        return None

    if isinstance(valor, (datetime, date)):
        return valor

    if isinstance(valor, str):
        texto = valor.strip()
        if not texto:
            return None

        formatos = (
            "%Y-%m-%d",
            "%d/%m/%Y",
            "%Y-%m-%d %H:%M:%S",
            "%d/%m/%Y %H:%M:%S",
        )

        for formato in formatos:
            try:
                return datetime.strptime(texto, formato)
            except ValueError:
                pass

    return None













def _rowversion_para_hex(valor: Any) -> str | None:
    print(f"[KANBAN][_rowversion_para_hex] INICIO tipo={type(valor).__name__} valor={valor!r}")

    if valor is None:
        print("[KANBAN][_rowversion_para_hex] valor is None -> retorno None")
        current_app.logger.warning("KANBAN: _rowversion_para_hex recebeu None.")
        return None

    if isinstance(valor, memoryview):
        print("[KANBAN][_rowversion_para_hex] valor é memoryview -> convertendo para bytes")
        current_app.logger.info("KANBAN: _rowversion_para_hex recebeu memoryview.")
        valor = valor.tobytes()

    if isinstance(valor, (bytes, bytearray)):
        resultado = bytes(valor).hex().upper()
        print(
            f"[KANBAN][_rowversion_para_hex] valor binário convertido com sucesso -> "
            f"hex={resultado} tamanho_bytes={len(bytes(valor))}"
        )
        current_app.logger.info(
            "KANBAN: _rowversion_para_hex converteu bytes/bytearray com sucesso. hex=%s",
            resultado,
        )
        return resultado

    if isinstance(valor, str):
        texto_original = valor
        texto = valor.strip().upper()

        print(
            f"[KANBAN][_rowversion_para_hex] valor é str -> original={texto_original!r} normalizado={texto!r}"
        )
        current_app.logger.info(
            "KANBAN: _rowversion_para_hex recebeu string. original=%r normalizado=%r",
            texto_original,
            texto,
        )

        if not texto:
            print("[KANBAN][_rowversion_para_hex] string vazia -> retorno None")
            current_app.logger.warning("KANBAN: _rowversion_para_hex recebeu string vazia.")
            return None

        if texto.startswith("0X"):
            texto = texto[2:]
            print(f"[KANBAN][_rowversion_para_hex] removido prefixo 0X -> {texto!r}")

        texto = re.sub(r"[^0-9A-F]", "", texto)
        print(f"[KANBAN][_rowversion_para_hex] texto após regex hex -> {texto!r}")

        if not texto:
            print("[KANBAN][_rowversion_para_hex] texto sem caracteres hex válidos -> retorno None")
            current_app.logger.warning(
                "KANBAN: _rowversion_para_hex recebeu string sem conteúdo hexadecimal válido. original=%r",
                texto_original,
            )
            return None

        if len(texto) % 2 != 0:
            print(
                f"[KANBAN][_rowversion_para_hex] quantidade ímpar de caracteres hex ({len(texto)}) -> retorno None"
            )
            current_app.logger.warning(
                "KANBAN: _rowversion_para_hex recebeu string hex com tamanho ímpar. texto=%r tamanho=%s",
                texto,
                len(texto),
            )
            return None

        print(f"[KANBAN][_rowversion_para_hex] string convertida com sucesso -> hex={texto}")
        current_app.logger.info(
            "KANBAN: _rowversion_para_hex converteu string com sucesso. hex=%s",
            texto,
        )
        return texto

    try:
        print(
            f"[KANBAN][_rowversion_para_hex] tentando conversão defensiva com bytes(valor) para tipo={type(valor).__name__}"
        )
        valor_bytes = bytes(valor)
        if valor_bytes:
            resultado = valor_bytes.hex().upper()
            print(
                f"[KANBAN][_rowversion_para_hex] conversão defensiva funcionou -> "
                f"hex={resultado} tamanho_bytes={len(valor_bytes)}"
            )
            current_app.logger.info(
                "KANBAN: _rowversion_para_hex converteu via bytes(valor). tipo=%s hex=%s",
                type(valor).__name__,
                resultado,
            )
            return resultado

        print("[KANBAN][_rowversion_para_hex] bytes(valor) retornou vazio -> retorno None")
        current_app.logger.warning(
            "KANBAN: _rowversion_para_hex bytes(valor) retornou vazio. tipo=%s valor=%r",
            type(valor).__name__,
            valor,
        )
        return None
    except Exception as exc:
        print(
            f"[KANBAN][_rowversion_para_hex] ERRO na conversão defensiva tipo={type(valor).__name__} erro={exc}"
        )
        current_app.logger.warning(
            "KANBAN: _rowversion_para_hex falhou na conversão defensiva. tipo=%s valor=%r erro=%s",
            type(valor).__name__,
            valor,
            exc,
        )

    print(
        f"[KANBAN][_rowversion_para_hex] tipo não suportado -> tipo={type(valor).__name__} valor={valor!r}"
    )
    current_app.logger.warning(
        "KANBAN: _rowversion_para_hex recebeu tipo não suportado. tipo=%s valor=%r",
        type(valor).__name__,
        valor,
    )
    return None




def _rowversion_hex_para_bytes(valor: Any) -> bytes | None:
    texto = str(valor or "").strip().upper()
    if not texto:
        return None

    if texto.startswith("0X"):
        texto = texto[2:]

    try:
        return bytes.fromhex(texto)
    except ValueError:
        return None

















def _obter_tag_em_atendimento(id_kanban: int) -> dict[str, Any] | None:
    return _obter_tag_por_nome(id_kanban, "Em Atendimento", somente_ativa=True)


def _card_possui_tag_ativa(id_card: int, id_tag: int) -> bool:
    sql = text("""
        SELECT TOP (1) 1
        FROM [Kanban].[Silver].[FatoKanbanCardTag]
        WHERE IDFatoKanbanCard = :id_card
          AND IDDimKanbanTag = :id_tag
          AND RemovidoEm IS NULL;
    """)

    existe = db.session.execute(
        sql,
        {
            "id_card": int(id_card),
            "id_tag": int(id_tag),
        },
    ).scalar()

    return bool(existe)


def _aplicar_tag_no_card(
    *,
    id_card: int,
    id_tag: int,
    id_usuario: int,
    id_empresa_proprietaria: int | None,
) -> bool:
    if not id_card or not id_tag:
        return False

    if _card_possui_tag_ativa(id_card, id_tag):
        return False

    sql_insert = text("""
        INSERT INTO [Kanban].[Silver].[FatoKanbanCardTag]
            (IDFatoKanbanCard, IDDimKanbanTag, AplicadoEm, AplicadoPor, IDEmpresaProprietaria)
        VALUES
            (:id_card, :id_tag, GETDATE(), :id_usuario, :id_empresa);
    """)

    db.session.execute(
        sql_insert,
        {
            "id_card": int(id_card),
            "id_tag": int(id_tag),
            "id_usuario": int(id_usuario),
            "id_empresa": int(id_empresa_proprietaria or 0) or None,
        },
    )
    return True


def _remover_tag_do_card(*, id_card: int, id_tag: int, id_usuario: int) -> bool:
    if not id_card or not id_tag:
        return False

    sql = text("""
        UPDATE [Kanban].[Silver].[FatoKanbanCardTag]
        SET RemovidoEm = GETDATE(),
            RemovidoPor = :id_usuario
        WHERE IDFatoKanbanCard = :id_card
          AND IDDimKanbanTag = :id_tag
          AND RemovidoEm IS NULL;
    """)

    resultado = db.session.execute(
        sql,
        {
            "id_card": int(id_card),
            "id_tag": int(id_tag),
            "id_usuario": int(id_usuario),
        },
    )

    return bool(getattr(resultado, "rowcount", 0) or 0)


def _garantir_tag_em_atendimento_no_card(
    *,
    id_card: int,
    id_kanban: int,
    id_usuario: int,
    id_empresa_proprietaria: int | None,
    falhar_se_nao_existir: bool = False,
) -> bool:
    tag = _obter_tag_em_atendimento(id_kanban)

    if not tag:
        if falhar_se_nao_existir:
            raise RuntimeError(
                "A tag obrigatória 'Em Atendimento' não está cadastrada/ativa para este kanban."
            )
        return False

    return _aplicar_tag_no_card(
        id_card=int(id_card),
        id_tag=int(tag.get("IDDimKanbanTag") or 0),
        id_usuario=int(id_usuario),
        id_empresa_proprietaria=id_empresa_proprietaria,
    )


def _remover_tag_em_atendimento_do_card(*, id_card: int, id_kanban: int, id_usuario: int) -> bool:
    tag = _obter_tag_em_atendimento(id_kanban)
    if not tag:
        return False

    return _remover_tag_do_card(
        id_card=int(id_card),
        id_tag=int(tag.get("IDDimKanbanTag") or 0),
        id_usuario=int(id_usuario),
    )











def _salvar_vinculos_painel_face_card(
    id_card: int,
    vinculos_preparados: list[dict] | None = None,
    id_usuario: int | None = None,
    id_empresa_proprietaria: int | None = None,
    itens_painel_face: list[dict] | None = None,
) -> None:
    """
    Salva o estado operacional de painel/face do card sem duplicar linhas ativas idênticas.

    Lógica desta versão:
    1) a identidade operacional do vínculo é a combinação painel + face
    2) a linha ativa só é encerrada quando o estado comercial/operacional realmente mudou
    3) reenviar o mesmo payload não cria nova linha
    4) remover um painel/face do payload encerra logicamente a linha ativa correspondente
    5) diferenças cosméticas de formato (None, "", vírgula, casas decimais) são normalizadas antes da comparação
    """

    if isinstance(vinculos_preparados, list):
        itens_entrada = vinculos_preparados
    elif isinstance(itens_painel_face, list):
        itens_entrada = itens_painel_face
    else:
        itens_entrada = []

    def obter_primeiro(item: dict, chaves: tuple[str, ...]) -> object:
        for chave in chaves:
            if chave in item:
                valor = item.get(chave)
                if valor is not None and str(valor).strip() != "":
                    return valor
        return None

    def normalizar_texto(valor: object) -> str | None:
        if valor is None:
            return None
        texto = " ".join(str(valor).strip().split())
        return texto if texto else None

    def normalizar_inteiro(valor: object) -> int | None:
        if valor is None or valor == "":
            return None
        if isinstance(valor, bool):
            return int(valor)
        try:
            return int(str(valor).strip())
        except (TypeError, ValueError):
            return None

    def normalizar_decimal(valor: object, casas: str = "0.0001") -> Decimal | None:
        if valor is None or valor == "":
            return None

        if isinstance(valor, Decimal):
            dec = valor
        else:
            if isinstance(valor, (int, float)):
                texto = str(valor)
            else:
                texto = str(valor).strip()
                if not texto:
                    return None
                if "," in texto:
                    texto = texto.replace(".", "").replace(",", ".")
            try:
                dec = Decimal(str(texto))
            except (InvalidOperation, TypeError, ValueError):
                return None

        try:
            return dec.quantize(Decimal(casas))
        except Exception:
            return dec

    def normalizar_data(valor: object) -> date | None:
        return _normalizar_data_reserva_kanban(valor)

    def linha_tem_conteudo_minimo(linha: dict) -> bool:
        return bool(linha.get("IDDimPaineisEuromidia") and linha.get("IDDimFacesPaineis"))

    def normalizar_linha(item: dict, ordem_padrao: int) -> dict:
        linha = {
            "Ordem": normalizar_inteiro(
                obter_primeiro(item, ("Ordem", "ordem", "Indice", "indice", "Sequencia", "sequencia"))
            ),
            "IDDimPaineisEuromidia": normalizar_inteiro(
                obter_primeiro(
                    item,
                    (
                        "IDDimPaineisEuromidia",
                        "id_dim_paineis_euromidia",
                        "IDPainel",
                        "id_painel",
                        "IDPainelMidia",
                        "IDDimPainel",
                    ),
                )
            ),
            "IDDimFacesPaineis": normalizar_inteiro(
                obter_primeiro(
                    item,
                    (
                        "IDDimFacesPaineis",
                        "id_dim_faces_paineis",
                        "IDFace",
                        "id_face",
                        "IDPainelFace",
                        "IDDimFace",
                        "id_dim_face",
                    ),
                )
            ),
            "CodPonto": normalizar_texto(
                obter_primeiro(item, ("CodPonto", "cod_ponto", "CodigoPonto", "codigo_ponto"))
            ),
            "CodFace": normalizar_texto(
                obter_primeiro(item, ("CodFace", "cod_face", "CodigoFace", "codigo_face"))
            ),
            "TipoPainel": normalizar_texto(
                obter_primeiro(item, ("TipoPainel", "tipo_painel", "TipoMidia", "tipo_midia"))
            ),
            "AnoCusto": normalizar_inteiro(obter_primeiro(item, ("AnoCusto", "ano_custo"))),
            "CustoTabela": normalizar_decimal(
                obter_primeiro(item, ("CustoTabela", "custo_tabela", "Custo", "custo"))
            ),
            "IDDimTabelaPrecosEuromidia": normalizar_inteiro(
                obter_primeiro(
                    item,
                    (
                        "IDDimTabelaPrecosEuromidia",
                        "id_dim_tabela_precos_euromidia",
                        "IDTabelaPreco",
                        "id_tabela_preco",
                        "id_preco",
                    ),
                )
            ),
            "PeriodoExibicao": normalizar_texto(
                obter_primeiro(item, ("PeriodoExibicao", "periodo_exibicao", "Periodo", "periodo"))
            ),
            "ExibicoesDia": normalizar_inteiro(obter_primeiro(item, ("ExibicoesDia", "exibicoes_dia"))),
            "ValorTabela": normalizar_decimal(obter_primeiro(item, ("ValorTabela", "valor_tabela"))),
            "Tabela": normalizar_texto(obter_primeiro(item, ("Tabela", "tabela", "NomeTabela", "nome_tabela"))),
            "PoliticaTrocas": normalizar_texto(obter_primeiro(item, ("PoliticaTrocas", "politica_trocas"))),
            "ValorTroca": normalizar_decimal(obter_primeiro(item, ("ValorTroca", "valor_troca"))),
            "NovoValor": normalizar_decimal(
                obter_primeiro(item, ("NovoValor", "novo_valor", "ValorNegociado", "valor_negociado"))
            ),
            "PercentualDesconto": normalizar_decimal(
                obter_primeiro(item, ("PercentualDesconto", "percentual_desconto", "DescontoPercentual"))
            ),
            "ValorVendaFinal": normalizar_decimal(
                obter_primeiro(item, ("ValorVendaFinal", "valor_venda_final", "ValorVenda", "valor_venda"))
            ),
            "MargemValor": normalizar_decimal(obter_primeiro(item, ("MargemValor", "margem_valor"))),
            "MargemPercentual": normalizar_decimal(
                obter_primeiro(item, ("MargemPercentual", "margem_percentual"))
            ),
            "DataInicio": normalizar_data(
                obter_primeiro(
                    item,
                    (
                        "DataInicio",
                        "data_inicio",
                        "PeriodoInicio",
                        "periodo_inicio",
                        "DataInicioReserva",
                        "data_inicio_reserva",
                    ),
                )
            ),
            "DataFim": normalizar_data(
                obter_primeiro(
                    item,
                    (
                        "DataFim",
                        "data_fim",
                        "PeriodoTermino",
                        "periodo_termino",
                        "DataFimReserva",
                        "data_fim_reserva",
                    ),
                )
            ),
        }

        if linha["Ordem"] is None:
            linha["Ordem"] = ordem_padrao

        if (linha["DataInicio"] is None) ^ (linha["DataFim"] is None):
            raise ValueError("Preencha Data de e Data até para o mesmo painel/face.")

        if (
            linha["DataInicio"] is not None
            and linha["DataFim"] is not None
            and linha["DataFim"] < linha["DataInicio"]
        ):
            raise ValueError("A Data até não pode ser menor que a Data de.")

        return linha

    def chave_principal(linha: dict) -> tuple[int | None, int | None]:
        return (
            normalizar_inteiro(linha.get("IDDimPaineisEuromidia")),
            normalizar_inteiro(linha.get("IDDimFacesPaineis")),
        )

    def assinatura_estado(linha: dict) -> tuple:
        return (
            normalizar_texto(linha.get("CodPonto")),
            normalizar_texto(linha.get("CodFace")),
            normalizar_texto(linha.get("TipoPainel")),
            normalizar_inteiro(linha.get("AnoCusto")),
            normalizar_decimal(linha.get("CustoTabela")),
            normalizar_inteiro(linha.get("IDDimTabelaPrecosEuromidia")),
            normalizar_texto(linha.get("PeriodoExibicao")),
            normalizar_inteiro(linha.get("ExibicoesDia")),
            normalizar_decimal(linha.get("ValorTabela")),
            normalizar_texto(linha.get("Tabela")),
            normalizar_texto(linha.get("PoliticaTrocas")),
            normalizar_decimal(linha.get("ValorTroca")),
            normalizar_decimal(linha.get("NovoValor")),
            normalizar_decimal(linha.get("PercentualDesconto")),
            normalizar_decimal(linha.get("ValorVendaFinal")),
            normalizar_decimal(linha.get("MargemValor")),
            normalizar_decimal(linha.get("MargemPercentual")),
            normalizar_data(linha.get("DataInicio")),
            normalizar_data(linha.get("DataFim")),
        )

    sql_buscar_ativos = text(
        """
        SELECT
            IDFatoKanbanCardPainelFace,
            IDFatoKanbanCard,
            Ordem,
            IDDimPaineisEuromidia,
            IDDimFacesPaineis,
            CodPonto,
            CodFace,
            TipoPainel,
            AnoCusto,
            CustoTabela,
            IDDimTabelaPrecosEuromidia,
            PeriodoExibicao,
            ExibicoesDia,
            ValorTabela,
            Tabela,
            PoliticaTrocas,
            ValorTroca,
            NovoValor,
            PercentualDesconto,
            ValorVendaFinal,
            MargemValor,
            MargemPercentual,
            DataInicio,
            DataFim,
            Ativo
        FROM [Kanban].[Silver].[FatoKanbanCardPainelFace]
        WHERE IDFatoKanbanCard = :id_card
          AND ISNULL(Ativo, 1) = 1
        """
    )

    linhas_ativas = [
        dict(linha._mapping)
        for linha in db.session.execute(sql_buscar_ativos, {"id_card": int(id_card)})
    ]

    ativos_por_chave: dict[tuple[int | None, int | None], dict] = {}
    for linha in linhas_ativas:
        linha_normalizada = {
            "IDFatoKanbanCardPainelFace": normalizar_inteiro(linha.get("IDFatoKanbanCardPainelFace")),
            "Ordem": normalizar_inteiro(linha.get("Ordem")),
            "IDDimPaineisEuromidia": normalizar_inteiro(linha.get("IDDimPaineisEuromidia")),
            "IDDimFacesPaineis": normalizar_inteiro(linha.get("IDDimFacesPaineis")),
            "CodPonto": normalizar_texto(linha.get("CodPonto")),
            "CodFace": normalizar_texto(linha.get("CodFace")),
            "TipoPainel": normalizar_texto(linha.get("TipoPainel")),
            "AnoCusto": normalizar_inteiro(linha.get("AnoCusto")),
            "CustoTabela": normalizar_decimal(linha.get("CustoTabela")),
            "IDDimTabelaPrecosEuromidia": normalizar_inteiro(linha.get("IDDimTabelaPrecosEuromidia")),
            "PeriodoExibicao": normalizar_texto(linha.get("PeriodoExibicao")),
            "ExibicoesDia": normalizar_inteiro(linha.get("ExibicoesDia")),
            "ValorTabela": normalizar_decimal(linha.get("ValorTabela")),
            "Tabela": normalizar_texto(linha.get("Tabela")),
            "PoliticaTrocas": normalizar_texto(linha.get("PoliticaTrocas")),
            "ValorTroca": normalizar_decimal(linha.get("ValorTroca")),
            "NovoValor": normalizar_decimal(linha.get("NovoValor")),
            "PercentualDesconto": normalizar_decimal(linha.get("PercentualDesconto")),
            "ValorVendaFinal": normalizar_decimal(linha.get("ValorVendaFinal")),
            "MargemValor": normalizar_decimal(linha.get("MargemValor")),
            "MargemPercentual": normalizar_decimal(linha.get("MargemPercentual")),
            "DataInicio": normalizar_data(linha.get("DataInicio")),
            "DataFim": normalizar_data(linha.get("DataFim")),
        }

        chave = chave_principal(linha_normalizada)
        if chave[0] and chave[1] and chave not in ativos_por_chave:
            ativos_por_chave[chave] = linha_normalizada

    novos_por_chave: dict[tuple[int | None, int | None], dict] = {}
    for indice, item in enumerate(itens_entrada, start=1):
        if not isinstance(item, dict):
            continue
        linha = normalizar_linha(item, ordem_padrao=indice)
        if not linha_tem_conteudo_minimo(linha):
            continue
        chave = chave_principal(linha)
        if chave[0] and chave[1]:
            novos_por_chave[chave] = linha

    sql_encerrar = text(
        """
        UPDATE [Kanban].[Silver].[FatoKanbanCardPainelFace]
           SET Ativo = 0,
               DataAtualizacao = GETDATE(),
               RemovidoEm = GETDATE(),
               RemovidoPor = :id_usuario
         WHERE IDFatoKanbanCardPainelFace = :id_fato_kanban_card_painel_face
           AND ISNULL(Ativo, 1) = 1
        """
    )

    sql_inserir = text(
        """
        INSERT INTO [Kanban].[Silver].[FatoKanbanCardPainelFace]
        (
            IDFatoKanbanCard,
            Ordem,
            IDDimPaineisEuromidia,
            IDDimFacesPaineis,
            CodPonto,
            CodFace,
            TipoPainel,
            AnoCusto,
            CustoTabela,
            IDDimTabelaPrecosEuromidia,
            PeriodoExibicao,
            ExibicoesDia,
            ValorTabela,
            Tabela,
            PoliticaTrocas,
            ValorTroca,
            NovoValor,
            PercentualDesconto,
            ValorVendaFinal,
            MargemValor,
            MargemPercentual,
            Ativo,
            CriadoEm,
            DataAtualizacao,
            RemovidoEm,
            RemovidoPor,
            IDUsuario,
            IDEmpresaProprietaria,
            DataInicio,
            DataFim
        )
        VALUES
        (
            :id_card,
            :ordem,
            :id_painel,
            :id_face,
            :cod_ponto,
            :cod_face,
            :tipo_painel,
            :ano_custo,
            :custo_tabela,
            :id_tabela_preco,
            :periodo_exibicao,
            :exibicoes_dia,
            :valor_tabela,
            :tabela,
            :politica_trocas,
            :valor_troca,
            :novo_valor,
            :percentual_desconto,
            :valor_venda_final,
            :margem_valor,
            :margem_percentual,
            1,
            GETDATE(),
            GETDATE(),
            NULL,
            NULL,
            :id_usuario,
            :id_empresa_proprietaria,
            :data_inicio,
            :data_fim
        )
        """
    )

    chaves_ativas = set(ativos_por_chave.keys())
    chaves_novas = set(novos_por_chave.keys())

    for chave_removida in sorted(
        chaves_ativas - chaves_novas,
        key=lambda item: (
            item[0] is None,
            item[0] or 0,
            item[1] is None,
            item[1] or 0,
        ),
    ):
        linha_ativa = ativos_por_chave[chave_removida]
        id_linha = linha_ativa.get("IDFatoKanbanCardPainelFace")
        if id_linha:
            db.session.execute(
                sql_encerrar,
                {
                    "id_usuario": id_usuario,
                    "id_fato_kanban_card_painel_face": int(id_linha),
                },
            )

    for chave, linha_nova in novos_por_chave.items():
        linha_ativa = ativos_por_chave.get(chave)

        if linha_ativa:
            assinatura_ativa = assinatura_estado(linha_ativa)
            assinatura_nova = assinatura_estado(linha_nova)

            if assinatura_ativa == assinatura_nova:
                continue

            id_linha = linha_ativa.get("IDFatoKanbanCardPainelFace")
            if id_linha:
                db.session.execute(
                    sql_encerrar,
                    {
                        "id_usuario": id_usuario,
                        "id_fato_kanban_card_painel_face": int(id_linha),
                    },
                )

        db.session.execute(
            sql_inserir,
            {
                "id_card": int(id_card),
                "ordem": linha_nova.get("Ordem"),
                "id_painel": linha_nova.get("IDDimPaineisEuromidia"),
                "id_face": linha_nova.get("IDDimFacesPaineis"),
                "cod_ponto": linha_nova.get("CodPonto"),
                "cod_face": linha_nova.get("CodFace"),
                "tipo_painel": linha_nova.get("TipoPainel"),
                "ano_custo": linha_nova.get("AnoCusto"),
                "custo_tabela": linha_nova.get("CustoTabela"),
                "id_tabela_preco": linha_nova.get("IDDimTabelaPrecosEuromidia"),
                "periodo_exibicao": linha_nova.get("PeriodoExibicao"),
                "exibicoes_dia": linha_nova.get("ExibicoesDia"),
                "valor_tabela": linha_nova.get("ValorTabela"),
                "tabela": linha_nova.get("Tabela"),
                "politica_trocas": linha_nova.get("PoliticaTrocas"),
                "valor_troca": linha_nova.get("ValorTroca"),
                "novo_valor": linha_nova.get("NovoValor"),
                "percentual_desconto": linha_nova.get("PercentualDesconto"),
                "valor_venda_final": linha_nova.get("ValorVendaFinal"),
                "margem_valor": linha_nova.get("MargemValor"),
                "margem_percentual": linha_nova.get("MargemPercentual"),
                "id_usuario": id_usuario,
                "id_empresa_proprietaria": id_empresa_proprietaria,
                "data_inicio": linha_nova.get("DataInicio"),
                "data_fim": linha_nova.get("DataFim"),
            },
        )










def _quebrar_nome_tabela_schema_objeto(nome_tabela: str) -> tuple[str | None, str]:
    partes = [
        str(parte or "").strip().strip("[]").strip()
        for parte in str(nome_tabela or "").strip().split(".")
    ]
    partes = [parte for parte in partes if parte]

    if not partes:
        return None, ""

    if len(partes) == 1:
        return "dbo", partes[0]

    if len(partes) == 2:
        return partes[0], partes[1]

    return partes[-2], partes[-1]


def _obter_tipo_coluna_sql(nome_tabela: str, nome_coluna: str) -> str | None:
    schema_nome, tabela_nome = _quebrar_nome_tabela_schema_objeto(nome_tabela)
    coluna_nome = str(nome_coluna or "").strip().strip("[]").strip()

    if not tabela_nome or not coluna_nome:
        return None

    sql = text("""
        SELECT TOP (1)
            tp.name
        FROM sys.tables t
        INNER JOIN sys.schemas s
            ON s.schema_id = t.schema_id
        INNER JOIN sys.columns c
            ON c.object_id = t.object_id
        INNER JOIN sys.types tp
            ON tp.user_type_id = c.user_type_id
        WHERE s.name = :schema_nome
          AND t.name = :tabela_nome
          AND c.name = :coluna_nome;
    """)

    tipo = db.session.execute(
        sql,
        {
            "schema_nome": schema_nome or "dbo",
            "tabela_nome": tabela_nome,
            "coluna_nome": coluna_nome,
        },
    ).scalar()

    if not tipo:
        return None

    return str(tipo).strip().lower()


def _normalizar_hex_sql(valor: Any) -> str | None:
    if valor is None:
        return None

    texto = str(valor).strip().upper()
    if not texto:
        return None

    if texto.startswith("0X"):
        texto = texto[2:]

    texto = re.sub(r"[^0-9A-F]", "", texto)
    if not texto:
        return None

    if len(texto) % 2 != 0:
        return None

    return texto


def _garantir_versao_concorrencia_card(id_card: int, id_kanban: int) -> str | None:
  
    if not _card_tem_versao_concorrencia():
        return None

    tipo_coluna = _obter_tipo_coluna_sql(TABELA_CARD, "VersaoConcorrencia")
    print(
        f"[KANBAN][_garantir_versao_concorrencia_card] INICIO id_card={id_card} "
        f"id_kanban={id_kanban} tipo_coluna={tipo_coluna!r}"
    )

    if tipo_coluna not in {"rowversion", "timestamp"}:
        try:
            sql_seed = text(f"""
                UPDATE {TABELA_CARD}
                SET VersaoConcorrencia = CONVERT(varbinary(8), CRYPT_GEN_RANDOM(8))
                OUTPUT INSERTED.VersaoConcorrencia
                WHERE IDFatoKanbanCard = :id_card
                  AND IDDimKanban = :id_kanban
                  AND Ativo = 1
                  AND VersaoConcorrencia IS NULL;
            """)

            valor_seed = db.session.execute(
                sql_seed,
                {
                    "id_card": int(id_card),
                    "id_kanban": int(id_kanban),
                },
            ).scalar()

            if valor_seed is not None:
                db.session.commit()
                _bump_card(int(id_card))
                _bump_kanban(int(id_kanban))

                versao_seed_hex = _rowversion_para_hex(valor_seed)
                print(
                    "[KANBAN][_garantir_versao_concorrencia_card] "
                    f"seed executado com sucesso -> versao_seed_hex={versao_seed_hex!r}"
                )
                if versao_seed_hex:
                    return versao_seed_hex

        except Exception:
            db.session.rollback()
            current_app.logger.exception(
                "KANBAN: falha ao semear VersaoConcorrencia para card legado. id_card=%s id_kanban=%s",
                id_card,
                id_kanban,
            )

    sql_ler = text(f"""
        SELECT TOP (1)
            VersaoConcorrencia,
            CONVERT(varchar(34), VersaoConcorrencia, 1) AS VersaoConcorrenciaHexSql
        FROM {TABELA_CARD}
        WHERE IDFatoKanbanCard = :id_card
          AND IDDimKanban = :id_kanban
          AND Ativo = 1;
    """)

    row = db.session.execute(
        sql_ler,
        {
            "id_card": int(id_card),
            "id_kanban": int(id_kanban),
        },
    ).mappings().first()

    if not row:
        return None

    versao_hex = _rowversion_para_hex(row.get("VersaoConcorrencia"))
    if versao_hex:
        return versao_hex

    return _normalizar_hex_sql(row.get("VersaoConcorrenciaHexSql"))











def _sala_kanban(id_kanban: int) -> str:
    return f"kanban:{int(id_kanban)}"



def _obter_celery_app() -> Any | None:
    return current_app.extensions.get("celery")



def _enfileirar_evento_assincrono(nome_evento: str, payload: dict[str, Any]) -> None:
    """
    Gancho opcional para Celery.
    Não transformo operação síncrona do usuário em tarefa assíncrona.
    Uso Celery apenas para pós-processamento: auditoria, integração, indexação etc.
    """
    celery_app = _obter_celery_app()
    nome_task = current_app.config.get("KANBAN_EVENT_TASK_NAME")

    if not celery_app or not nome_task:
        return

    try:
        celery_app.send_task(nome_task, kwargs={"nome_evento": nome_evento, "payload": payload})
    except Exception:
        current_app.logger.exception(
            "Falha ao enviar evento do kanban para Celery. evento=%s payload=%s",
            nome_evento,
            payload,
        )


def _emitir_evento_kanban(id_kanban: int, nome_evento: str, payload: dict[str, Any]) -> None:
    payload_final = _serializar_para_socket({**payload, "id_kanban": int(id_kanban)})
    print(payload_final)

    try:
        socketio.emit(
            nome_evento,
            payload_final,
            namespace=NAMESPACE_SOCKET_KANBAN,
            to=_sala_kanban(id_kanban),
        )
    except Exception:
        current_app.logger.exception(
            "Falha ao emitir evento Socket.IO. evento=%s payload=%s",
            nome_evento,
            payload_final,
        )

    _enfileirar_evento_assincrono(nome_evento=nome_evento, payload=payload_final)



def _cache_get_int(chave: str, padrao: int = 1) -> int:
    valor = cache.get(chave)
    try:
        return int(valor)
    except Exception:
        return padrao



def _cache_inc(chave: str) -> int:
    valor_atual = _cache_get_int(chave, 1)
    novo = valor_atual + 1
    cache.set(chave, novo, timeout=TIMEOUT_CACHE_LONGO * 100)
    return novo



def _versao_empresa(id_emp: int) -> int:
    return _cache_get_int(f"kanban:versao:empresa:{int(id_emp)}", 1)



def _versao_kanban(id_kanban: int) -> int:
    return _cache_get_int(f"kanban:versao:kanban:{int(id_kanban)}", 1)



def _versao_card(id_card: int) -> int:
    return _cache_get_int(f"kanban:versao:card:{int(id_card)}", 1)



def _bump_empresa(id_emp: int) -> None:
    _cache_inc(f"kanban:versao:empresa:{int(id_emp)}")



def _bump_kanban(id_kanban: int) -> None:
    _cache_inc(f"kanban:versao:kanban:{int(id_kanban)}")



def _bump_card(id_card: int) -> None:
    _cache_inc(f"kanban:versao:card:{int(id_card)}")



def _invalidar_kanban(id_emp: int, id_kanban: int, id_card: int | None = None) -> None:
    _bump_empresa(id_emp)
    _bump_kanban(id_kanban)
    if id_card:
        _bump_card(id_card)





from datetime import date, datetime
from decimal import Decimal
from collections.abc import Mapping

def _serializar_para_socket(valor: Any) -> Any:
    if valor is None:
        return None

    if isinstance(valor, (str, int, float, bool)):
        return valor

    if isinstance(valor, Decimal):
        return float(valor)

    if isinstance(valor, (datetime, date)):
        return valor.isoformat()

    if isinstance(valor, memoryview):
        valor = valor.tobytes()

    if isinstance(valor, (bytes, bytearray)):
        return bytes(valor).hex().upper()

    if isinstance(valor, Mapping):
        return {str(chave): _serializar_para_socket(v) for chave, v in valor.items()}

    if isinstance(valor, (list, tuple, set)):
        return [_serializar_para_socket(v) for v in valor]

    return str(valor)








def _chave_cache_json(prefixo: str, *partes: Any) -> str:
    partes_str = [prefixo]
    for parte in partes:
        if isinstance(parte, (dict, list, tuple)):
            partes_str.append(json.dumps(parte, sort_keys=True, ensure_ascii=False, default=str))
        else:
            partes_str.append(str(parte))
    return "|".join(partes_str)



def _cache_json_get(chave: str) -> dict[str, Any] | list[Any] | None:
    valor = cache.get(chave)
    return valor if isinstance(valor, (dict, list)) else None



def _cache_json_set(chave: str, valor: dict[str, Any] | list[Any], timeout: int) -> None:
    cache.set(chave, valor, timeout=timeout)



def _objeto_existe(nome_objeto: str) -> bool:
    chave = _chave_cache_json("kanban:schema:objeto", nome_objeto)
    em_cache = cache.get(chave)
    if em_cache is not None:
        return bool(em_cache)

    sql = text("SELECT CASE WHEN OBJECT_ID(:nome_objeto) IS NULL THEN 0 ELSE 1 END;")
    existe = bool(db.session.execute(sql, {"nome_objeto": nome_objeto}).scalar() or 0)
    cache.set(chave, existe, timeout=TIMEOUT_CACHE_LONGO * 100)
    return existe



def _quebrar_nome_banco_schema_tabela(nome_tabela: str) -> tuple[str | None, str | None, str]:
    partes = [
        str(parte or "").strip().strip("[]").strip()
        for parte in str(nome_tabela or "").strip().split(".")
    ]
    partes = [parte for parte in partes if parte]

    if not partes:
        return None, None, ""

    if len(partes) == 1:
        return None, "dbo", partes[0]

    if len(partes) == 2:
        return None, partes[0], partes[1]

    return partes[-3], partes[-2], partes[-1]





def _coluna_existe(nome_tabela: str, nome_coluna: str, ignorar_cache: bool = False) -> bool:
    ignorar_cache = bool(
        ignorar_cache or str(nome_coluna).strip().lower() == "versaoconcorrencia"
    )

    banco_nome, schema_nome, tabela_nome = _quebrar_nome_banco_schema_tabela(nome_tabela)
    coluna_nome = str(nome_coluna or "").strip().strip("[]").strip()

    print(
        f"[KANBAN][_coluna_existe] INICIO nome_tabela={nome_tabela!r} "
        f"nome_coluna={nome_coluna!r} banco={banco_nome!r} schema={schema_nome!r} tabela={tabela_nome!r}"
    )

    current_app.logger.info(
        "KANBAN: _coluna_existe iniciado. nome_tabela=%r nome_coluna=%r banco=%r schema=%r tabela=%r",
        nome_tabela,
        nome_coluna,
        banco_nome,
        schema_nome,
        tabela_nome,
    )

    if not tabela_nome or not coluna_nome:
        print(
            f"[KANBAN][_coluna_existe] nome inválido. "
            f"tabela={tabela_nome!r} coluna={coluna_nome!r}"
        )
        current_app.logger.warning(
            "KANBAN: _coluna_existe recebeu nome inválido. tabela=%r coluna=%r",
            tabela_nome,
            coluna_nome,
        )
        return False

    chave = _chave_cache_json(
        "kanban:schema:coluna",
        banco_nome or "",
        schema_nome or "dbo",
        tabela_nome,
        coluna_nome,
    )

    if not ignorar_cache:
        em_cache = cache.get(chave)
        if em_cache is not None:
            print(
                f"[KANBAN][_coluna_existe] CACHE HIT banco={banco_nome!r} "
                f"schema={schema_nome!r} tabela={tabela_nome!r} coluna={coluna_nome!r} valor={bool(em_cache)}"
            )
            current_app.logger.info(
                "KANBAN: _coluna_existe cache hit. banco=%r schema=%r tabela=%r coluna=%r valor=%s",
                banco_nome,
                schema_nome,
                tabela_nome,
                coluna_nome,
                bool(em_cache),
            )
            return bool(em_cache)

    if banco_nome:
        sql = text(f"""
            SELECT TOP (1)
                1
            FROM [{banco_nome}].sys.tables t
            INNER JOIN [{banco_nome}].sys.schemas s
                ON s.schema_id = t.schema_id
            INNER JOIN [{banco_nome}].sys.columns c
                ON c.object_id = t.object_id
            WHERE s.name = :schema_nome
              AND t.name = :tabela_nome
              AND c.name = :coluna_nome;
        """)
    else:
        sql = text("""
            SELECT TOP (1)
                1
            FROM sys.tables t
            INNER JOIN sys.schemas s
                ON s.schema_id = t.schema_id
            INNER JOIN sys.columns c
                ON c.object_id = t.object_id
            WHERE s.name = :schema_nome
              AND t.name = :tabela_nome
              AND c.name = :coluna_nome;
        """)

    resultado = db.session.execute(
        sql,
        {
            "schema_nome": schema_nome or "dbo",
            "tabela_nome": tabela_nome,
            "coluna_nome": coluna_nome,
        },
    ).scalar()

    existe = bool(resultado)

    print(
        f"[KANBAN][_coluna_existe] RESULTADO banco={banco_nome!r} schema={schema_nome!r} "
        f"tabela={tabela_nome!r} coluna={coluna_nome!r} existe={existe}"
    )

    current_app.logger.info(
        "KANBAN: _coluna_existe resultado. banco=%r schema=%r tabela=%r coluna=%r existe=%s",
        banco_nome,
        schema_nome,
        tabela_nome,
        coluna_nome,
        existe,
    )

    if not ignorar_cache:
        cache.set(chave, existe, timeout=TIMEOUT_CACHE_LONGO)
        print(
            f"[KANBAN][_coluna_existe] CACHE SET banco={banco_nome!r} schema={schema_nome!r} "
            f"tabela={tabela_nome!r} coluna={coluna_nome!r} valor={existe}"
        )
        current_app.logger.info(
            "KANBAN: _coluna_existe cache set. banco=%r schema=%r tabela=%r coluna=%r valor=%s timeout=%s",
            banco_nome,
            schema_nome,
            tabela_nome,
            coluna_nome,
            existe,
            TIMEOUT_CACHE_LONGO,
        )

    return existe





    def _quebrar_nome_tabela(nome: str) -> tuple[str | None, str]:
        bruto = str(nome or "").strip()
        if not bruto:
            return None, ""

        partes = [_limpar_identificador(p) for p in bruto.split(".") if _limpar_identificador(p)]

        if not partes:
            return None, ""

        if len(partes) == 1:
            return "dbo", partes[0]

        if len(partes) == 2:
            return partes[0], partes[1]

        return partes[-2], partes[-1]

    """
    Eu continuo ignorando cache automaticamente para VersaoConcorrencia,
    porque ela já era um caso especial no seu código.
    Além disso, agora também posso forçar ignorar cache externamente.
    """
    ignorar_cache = bool(
        ignorar_cache or str(nome_coluna).strip().lower() == "versaoconcorrencia"
    )

    schema_nome, tabela_nome = _quebrar_nome_tabela(nome_tabela)
    coluna_nome = _limpar_identificador(nome_coluna)

    chave = _chave_cache_json(
        "kanban:schema:coluna",
        schema_nome or "dbo",
        tabela_nome,
        coluna_nome,
    )

    if not ignorar_cache:
        em_cache = cache.get(chave)
        if em_cache is not None:
            print(
                f"[KANBAN][_coluna_existe] CACHE HIT schema={schema_nome!r} tabela={tabela_nome!r} "
                f"coluna={coluna_nome!r} valor={bool(em_cache)}"
            )
            current_app.logger.info(
                "KANBAN: _coluna_existe cache hit. schema=%r tabela=%r coluna=%r valor=%s",
                schema_nome,
                tabela_nome,
                coluna_nome,
                bool(em_cache),
            )
            return bool(em_cache)

    print(
        f"[KANBAN][_coluna_existe] CONSULTANDO BANCO nome_tabela_original={nome_tabela!r} "
        f"schema_resolvido={schema_nome!r} tabela_resolvida={tabela_nome!r} "
        f"coluna={coluna_nome!r} ignorar_cache={ignorar_cache}"
    )

    current_app.logger.info(
        "KANBAN: _coluna_existe consultando banco. nome_tabela_original=%r schema_resolvido=%r "
        "tabela_resolvida=%r coluna=%r ignorar_cache=%s",
        nome_tabela,
        schema_nome,
        tabela_nome,
        coluna_nome,
        ignorar_cache,
    )

    if not tabela_nome or not coluna_nome:
        print(
            f"[KANBAN][_coluna_existe] nome inválido -> schema={schema_nome!r} tabela={tabela_nome!r} coluna={coluna_nome!r}"
        )
        current_app.logger.warning(
            "KANBAN: _coluna_existe recebeu nome inválido. nome_tabela=%r nome_coluna=%r",
            nome_tabela,
            nome_coluna,
        )
        return False

    sql = text("""
        SELECT TOP (1)
            1
        FROM sys.tables t
        INNER JOIN sys.schemas s
            ON s.schema_id = t.schema_id
        INNER JOIN sys.columns c
            ON c.object_id = t.object_id
        WHERE s.name = :schema_nome
          AND t.name = :tabela_nome
          AND c.name = :coluna_nome;
    """)

    resultado = db.session.execute(
        sql,
        {
            "schema_nome": schema_nome or "dbo",
            "tabela_nome": tabela_nome,
            "coluna_nome": coluna_nome,
        },
    ).scalar()

    existe = bool(resultado)

    print(
        f"[KANBAN][_coluna_existe] RESULTADO schema={schema_nome!r} tabela={tabela_nome!r} "
        f"coluna={coluna_nome!r} existe={existe}"
    )

    current_app.logger.info(
        "KANBAN: _coluna_existe resultado. schema=%r tabela=%r coluna=%r existe=%s",
        schema_nome,
        tabela_nome,
        coluna_nome,
        existe,
    )

    if not ignorar_cache:
        cache.set(chave, existe, timeout=TIMEOUT_CACHE_LONGO)
        print(
            f"[KANBAN][_coluna_existe] CACHE SET schema={schema_nome!r} tabela={tabela_nome!r} "
            f"coluna={coluna_nome!r} valor={existe}"
        )
        current_app.logger.info(
            "KANBAN: _coluna_existe cache set. schema=%r tabela=%r coluna=%r valor=%s timeout=%s",
            schema_nome,
            tabela_nome,
            coluna_nome,
            existe,
            TIMEOUT_CACHE_LONGO,
        )

    return existe


def _nome_coluna_empresa_relacionada_card() -> str | None:
    """
    Retorna a coluna da FatoKanbanCard usada para vincular a empresa ao card.

    Regra atual do kanban:
    - a coluna correta é IDEmpresa
    - mantenho fallback apenas por compatibilidade com estruturas antigas
    """
    for nome_coluna in ("IDEmpresa", "IDCliente", "IDEmpresaRelacionada"):
        if _coluna_existe(TABELA_CARD, nome_coluna, ignorar_cache=True):
            return nome_coluna

    return None


def _nome_coluna_usuario_relacionado_card() -> str | None:
    for nome_coluna in ("IDVendedorUsuario", "IDDimUsuarios"):
        if _coluna_existe(TABELA_CARD, nome_coluna):
            return nome_coluna
    return None



def _sql_select_empresa_relacionada_card(alias_card: str = "c") -> str:
    nome_coluna = _nome_coluna_empresa_relacionada_card()
    if nome_coluna:
        return f"{alias_card}.{nome_coluna} AS IDEmpresaRelacionadaCard"
    return "CAST(NULL AS int) AS IDEmpresaRelacionadaCard"




def _sql_select_versao_concorrencia_card(alias_card: str = "c") -> str:
    print(
        f"[KANBAN][_sql_select_versao_concorrencia_card] INICIO alias_card={alias_card!r} TABELA_CARD={TABELA_CARD!r}"
    )
    current_app.logger.info(
        "KANBAN: _sql_select_versao_concorrencia_card iniciado. alias_card=%r TABELA_CARD=%r",
        alias_card,
        TABELA_CARD,
    )

    tem_versao = _card_tem_versao_concorrencia()

    if tem_versao:
        sql_select = f"{alias_card}.VersaoConcorrencia AS VersaoConcorrencia"
        print(
            f"[KANBAN][_sql_select_versao_concorrencia_card] usando coluna real -> {sql_select}"
        )
        current_app.logger.info(
            "KANBAN: _sql_select_versao_concorrencia_card usando coluna real. sql_select=%r",
            sql_select,
        )
        return sql_select

    sql_fallback = "CAST(NULL AS varbinary(8)) AS VersaoConcorrencia"
    print(
        f"[KANBAN][_sql_select_versao_concorrencia_card] coluna não encontrada -> usando fallback {sql_fallback}"
    )
    current_app.logger.warning(
        "KANBAN: _sql_select_versao_concorrencia_card não encontrou coluna VersaoConcorrencia em %r. Usando fallback.",
        TABELA_CARD,
    )
    return sql_fallback




def _sql_select_usuario_relacionado_card(alias_card: str = "c") -> str:
    nome_coluna = _nome_coluna_usuario_relacionado_card()
    if nome_coluna:
        return f"{alias_card}.{nome_coluna} AS IDUsuarioRelacionadoCard"
    return "CAST(NULL AS int) AS IDUsuarioRelacionadoCard"

def _card_tem_versao_concorrencia() -> bool:
    print(
        f"[KANBAN][_card_tem_versao_concorrencia] verificando coluna VersaoConcorrencia em TABELA_CARD={TABELA_CARD!r}"
    )
    current_app.logger.info(
        "KANBAN: _card_tem_versao_concorrencia verificando coluna. TABELA_CARD=%r",
        TABELA_CARD,
    )

    existe = _coluna_existe(TABELA_CARD, "VersaoConcorrencia")

    print(
        f"[KANBAN][_card_tem_versao_concorrencia] resultado existe={existe} para TABELA_CARD={TABELA_CARD!r}"
    )
    current_app.logger.info(
        "KANBAN: _card_tem_versao_concorrencia resultado. TABELA_CARD=%r existe=%s",
        TABELA_CARD,
        existe,
    )

    return existe


    def _sql_select_versao_concorrencia_card(alias_card: str = "c") -> str:

        print(
            f"[KANBAN][_sql_select_versao_concorrencia_card] INICIO alias_card={alias_card!r} TABELA_CARD={TABELA_CARD!r}"
        )
        current_app.logger.info(
            "KANBAN: _sql_select_versao_concorrencia_card iniciado. alias_card=%r TABELA_CARD=%r",
            alias_card,
            TABELA_CARD,
        )

        tem_versao = _card_tem_versao_concorrencia()

        if tem_versao:
            sql_select = f"{alias_card}.VersaoConcorrencia AS VersaoConcorrencia"
            print(
                f"[KANBAN][_sql_select_versao_concorrencia_card] usando coluna real -> {sql_select}"
            )
            current_app.logger.info(
                "KANBAN: _sql_select_versao_concorrencia_card usando coluna real. sql_select=%r",
                sql_select,
            )
            return sql_select

        sql_fallback = "CAST(NULL AS varbinary(8)) AS VersaoConcorrencia"
        print(
            f"[KANBAN][_sql_select_versao_concorrencia_card] coluna não encontrada -> usando fallback {sql_fallback}"
        )
        current_app.logger.warning(
            "KANBAN: _sql_select_versao_concorrencia_card não encontrou coluna VersaoConcorrencia em %r. "
            "Usando fallback.",
            TABELA_CARD,
        )
        return sql_fallback



def _sql_join_empresa_relacionada_card(alias_card: str = "c", alias_empresa: str = "e", alias_cnae: str = "cn") -> str:
    nome_coluna = _nome_coluna_empresa_relacionada_card()
    if nome_coluna:
        return f"""
        LEFT JOIN {TABELA_EMPRESAS} {alias_empresa}
          ON {alias_empresa}.IDEmpresa = {alias_card}.{nome_coluna}
        LEFT JOIN {TABELA_CNAES} {alias_cnae}
          ON {alias_cnae}.cnaepadrao = {alias_empresa}.CNAE
        """.strip()

    return f"""
    LEFT JOIN {TABELA_EMPRESAS} {alias_empresa}
      ON 1 = 0
    LEFT JOIN {TABELA_CNAES} {alias_cnae}
      ON 1 = 0
    """.strip()






def _obter_id_empresa_relacionada_card(card: Mapping[str, Any] | dict[str, Any] | None) -> int | None:
    if not card:
        return None

    for chave in ("IDEmpresaRelacionadaCard", "IDEmpresa", "IDCliente", "IDEmpresaRelacionada"):
        valor = card.get(chave)
        if valor not in (None, ""):
            try:
                return int(valor)
            except Exception:
                return None
    return None


def _obter_id_usuario_relacionado_card(card: Mapping[str, Any] | dict[str, Any] | None) -> int | None:
    if not card:
        return None

    for chave in ("IDUsuarioRelacionadoCard", "IDVendedorUsuario", "IDDimUsuarios"):
        valor = card.get(chave)
        if valor not in (None, ""):
            try:
                return int(valor)
            except Exception:
                return None
    return None



def _json_para_log(valor: Any) -> str | None:
    if valor is None:
        return None
    try:
        return json.dumps(valor, ensure_ascii=False, sort_keys=True, default=str)
    except Exception:
        return str(valor)



def _cor_hex_valida(cor_hex: str | None) -> bool:
    if not cor_hex:
        return False
    return bool(re.fullmatch(r"#[0-9A-Fa-f]{6}", cor_hex.strip()))



def _normalizar_texto_comparacao(valor: Any) -> str:
    texto = str(valor or "").strip().lower()
    if not texto:
        return ""
    texto = unicodedata.normalize("NFKD", texto)
    texto = "".join(ch for ch in texto if not unicodedata.combining(ch))
    texto = re.sub(r"\s+", " ", texto)
    return texto



def _normalizar_codigo_dominio(valor: Any) -> str:
    return str(valor or "").strip().upper()


def _status_card_tabela_existe() -> bool:
    return _objeto_existe(TABELA_STATUS_CARD)


def _obter_status_card_configurados(*, incluir_inativos: bool = False) -> list[dict[str, Any]]:
    chave = _chave_cache_json("kanban:dominio:status_card", incluir_inativos)
    em_cache = _cache_json_get(chave)
    if em_cache is not None:
        return em_cache

    resultado: list[dict[str, Any]] = []

    if _status_card_tabela_existe():
        where_ativo = "" if incluir_inativos else "WHERE ISNULL(sc.Ativo, 1) = 1"
        sql = text(f"""
            SELECT
                sc.IDDimKanbanStatusCard,
                sc.CodigoStatus,
                sc.NomeExibicao,
                ISNULL(sc.Ativo, 1) AS Ativo,
                sc.CriadoEm,
                sc.AtualizadoEm
            FROM {TABELA_STATUS_CARD} sc
            {where_ativo}
            ORDER BY sc.IDDimKanbanStatusCard ASC;
        """)
        rows = db.session.execute(sql).mappings().all()
        for row in rows:
            codigo = _normalizar_codigo_dominio(row.get("CodigoStatus"))
            if not codigo:
                continue
            resultado.append(
                {
                    "IDDimKanbanStatusCard": row.get("IDDimKanbanStatusCard"),
                    "CodigoStatus": codigo,
                    "NomeExibicao": str(row.get("NomeExibicao") or codigo.title()).strip(),
                    "Ativo": int(row.get("Ativo") or 0),
                    "BitPadrao": 1 if codigo == STATUS_CARD_FALLBACK_PADRAO else 0,
                    "BitStatusFinal": 1 if codigo in {"CONCLUIDO", STATUS_CARD_FALLBACK_INATIVACAO} else 0,
                    "BitStatusInativacao": 1 if codigo == STATUS_CARD_FALLBACK_INATIVACAO else 0,
                    "BitExibirNoKanban": 1,
                }
            )
    else:
        sql = text(f"""
            SELECT DISTINCT c.StatusCard AS CodigoStatus
            FROM {TABELA_CARD} c
            WHERE NULLIF(LTRIM(RTRIM(ISNULL(c.StatusCard, ''))), '') IS NOT NULL
            ORDER BY c.StatusCard ASC;
        """)
        rows = db.session.execute(sql).mappings().all()
        for row in rows:
            codigo = _normalizar_codigo_dominio(row.get("CodigoStatus"))
            if not codigo:
                continue
            resultado.append(
                {
                    "IDDimKanbanStatusCard": None,
                    "CodigoStatus": codigo,
                    "NomeExibicao": codigo.title(),
                    "Ativo": 1,
                    "BitPadrao": 1 if codigo == STATUS_CARD_FALLBACK_PADRAO else 0,
                    "BitStatusFinal": 1 if codigo in {"CONCLUIDO", STATUS_CARD_FALLBACK_INATIVACAO} else 0,
                    "BitStatusInativacao": 1 if codigo == STATUS_CARD_FALLBACK_INATIVACAO else 0,
                    "BitExibirNoKanban": 1,
                }
            )

    _cache_json_set(chave, resultado, TIMEOUT_CACHE_MEDIO)
    return resultado


def _obter_status_card_validos() -> set[str]:
    return {
        _normalizar_codigo_dominio(item.get("CodigoStatus"))
        for item in _obter_status_card_configurados()
        if _normalizar_codigo_dominio(item.get("CodigoStatus"))
    }


def _obter_status_card_por_codigo(codigo_status: Any) -> dict[str, Any] | None:
    codigo_normalizado = _normalizar_codigo_dominio(codigo_status)
    if not codigo_normalizado:
        return None
    for item in _obter_status_card_configurados(incluir_inativos=True):
        if _normalizar_codigo_dominio(item.get("CodigoStatus")) == codigo_normalizado:
            return item
    return None


def _obter_id_status_card_por_codigo(codigo_status: Any) -> int | None:
    item = _obter_status_card_por_codigo(codigo_status)
    if not item:
        return None
    valor = item.get("IDDimKanbanStatusCard")
    try:
        return int(valor) if valor is not None else None
    except Exception:
        return None


def _obter_status_card_padrao() -> str:
    if STATUS_CARD_FALLBACK_PADRAO in _obter_status_card_validos():
        return STATUS_CARD_FALLBACK_PADRAO
    for item in _obter_status_card_configurados():
        if int(item.get("BitPadrao") or 0) == 1:
            codigo = _normalizar_codigo_dominio(item.get("CodigoStatus"))
            if codigo:
                return codigo
    for item in _obter_status_card_configurados():
        codigo = _normalizar_codigo_dominio(item.get("CodigoStatus"))
        if codigo:
            return codigo
    return STATUS_CARD_FALLBACK_PADRAO


def _obter_status_card_inativacao() -> str:
    if STATUS_CARD_FALLBACK_INATIVACAO in _obter_status_card_validos():
        return STATUS_CARD_FALLBACK_INATIVACAO
    for item in _obter_status_card_configurados():
        if int(item.get("BitStatusInativacao") or 0) == 1:
            codigo = _normalizar_codigo_dominio(item.get("CodigoStatus"))
            if codigo:
                return codigo
    return STATUS_CARD_FALLBACK_INATIVACAO






"""Eu busco os itens painel/face vinculados ao card para compor o histórico."""
def _buscar_itens_historico_card(id_card: int, id_empresa_proprietaria: int) -> list[dict]:
    sql = """
    SELECT
        i.IDFatoKanbanCardPainelFace AS id_item,
        i.IDFatoKanbanCard AS id_card,
        i.Ordem AS ordem,
        i.IDDimPaineisEuromidia AS id_painel,
        i.IDDimFacesPaineis AS id_face_painel,
        i.CodPonto AS cod_ponto,
        i.CodFace AS cod_face,
        i.TipoPainel AS tipo_painel,
        i.AnoCusto AS ano_custo,
        i.CustoTabela AS custo_tabela,
        i.IDDimTabelaPrecosEuromidia AS id_tabela_preco,
        i.PeriodoExibicao AS periodo_exibicao,
        i.ExibicoesDia AS exibicoes_dia,
        i.ValorTabela AS valor_tabela,
        i.Tabela AS tabela,
        i.PoliticaTrocas AS politica_trocas,
        i.ValorTroca AS valor_troca,
        i.NovoValor AS novo_valor,
        i.PercentualDesconto AS percentual_desconto,
        i.ValorVendaFinal AS valor_venda_final,
        i.MargemValor AS margem_valor,
        i.MargemPercentual AS margem_percentual,
        i.Ativo AS ativo,
        i.CriadoEm AS criado_em,
        i.DataAtualizacao AS atualizado_em,
        i.RemovidoEm AS removido_em,
        i.RemovidoPor AS removido_por,
        i.IDUsuario AS id_usuario,
        i.IDEmpresaProprietaria AS id_empresa_proprietaria_evento
    FROM [Kanban].[Silver].[FatoKanbanCardPainelFace] i
    INNER JOIN [Kanban].[Silver].[FatoKanbanCard] card_aut
        ON card_aut.IDFatoKanbanCard = i.IDFatoKanbanCard
       AND card_aut.IDEmpresaProprietaria = :id_empresa_proprietaria
    WHERE i.IDFatoKanbanCard = :id_card
    ORDER BY
        ISNULL(i.Ordem, 999999),
        i.CriadoEm DESC,
        i.IDFatoKanbanCardPainelFace DESC
    """
    return _executar_sql_mapeado(
        sql,
        {
            "id_card": id_card,
            "id_empresa_proprietaria": id_empresa_proprietaria,
        },
    )















def _status_card_eh_final(status_card: Any) -> bool:
    item = _obter_status_card_por_codigo(status_card)
    if item is not None:
        return int(item.get("BitStatusFinal") or 0) == 1
    codigo = _normalizar_codigo_dominio(status_card)
    return codigo in {"CONCLUIDO", STATUS_CARD_FALLBACK_INATIVACAO}


def _sql_filtro_status_card_visiveis(alias_card: str = "c") -> str:
    if _status_card_tabela_existe():
        return f"""
          AND EXISTS (
                SELECT 1
                FROM {TABELA_STATUS_CARD} sc
                WHERE ISNULL(sc.Ativo, 1) = 1
                  AND UPPER(LTRIM(RTRIM(ISNULL(sc.CodigoStatus, '')))) = UPPER(LTRIM(RTRIM(ISNULL({alias_card}.StatusCard, ''))))
          )
        """.rstrip()
    return f"AND NULLIF(LTRIM(RTRIM(ISNULL({alias_card}.StatusCard, ''))), '') IS NOT NULL"


def _obter_tipos_fase_configurados(id_kanban: int | None = None, id_emp: int | None = None) -> list[str]:
    if id_kanban is None and id_emp is None:
        return []

    chave = _chave_cache_json("kanban:dominio:tipos_fase", id_kanban, id_emp)
    em_cache = _cache_json_get(chave)
    if em_cache is not None:
        return em_cache

    if id_kanban is not None:
        sql = text(f"""
            SELECT DISTINCT f.TipoFase
            FROM {TABELA_KANBAN_FASE} f
            WHERE f.IDDimKanban = :id_kanban
              AND f.Ativo = 1
              AND NULLIF(LTRIM(RTRIM(ISNULL(f.TipoFase, ''))), '') IS NOT NULL
            ORDER BY f.TipoFase ASC;
        """)
        params = {"id_kanban": int(id_kanban)}
    else:
        sql = text(f"""
            SELECT DISTINCT f.TipoFase
            FROM {TABELA_KANBAN_FASE} f
            JOIN {TABELA_KANBAN} k
              ON k.IDDimKanban = f.IDDimKanban
            WHERE k.IDEmpresaProprietaria = :id_emp
              AND k.Ativo = 1
              AND f.Ativo = 1
              AND NULLIF(LTRIM(RTRIM(ISNULL(f.TipoFase, ''))), '') IS NOT NULL
            ORDER BY f.TipoFase ASC;
        """)
        params = {"id_emp": int(id_emp)}

    rows = db.session.execute(sql, params).mappings().all()
    resultado = [
        _normalizar_codigo_dominio(row.get("TipoFase"))
        for row in rows
        if _normalizar_codigo_dominio(row.get("TipoFase"))
    ]
    _cache_json_set(chave, resultado, TIMEOUT_CACHE_MEDIO)
    return resultado


def _obter_tipos_tag_configurados(id_kanban: int | None = None, id_emp: int | None = None) -> list[str]:
    tabela_tag = "[Kanban].[Silver].[DimKanbanTag]"
    if not _objeto_existe(tabela_tag):
        return []
    if id_kanban is None and id_emp is None:
        return []

    chave = _chave_cache_json("kanban:dominio:tipos_tag", id_kanban, id_emp)
    em_cache = _cache_json_get(chave)
    if em_cache is not None:
        return em_cache

    if id_kanban is not None:
        sql = text(f"""
            SELECT DISTINCT t.TipoTag
            FROM {tabela_tag} t
            WHERE t.IDDimKanban = :id_kanban
              AND t.Ativo = 1
              AND NULLIF(LTRIM(RTRIM(ISNULL(t.TipoTag, ''))), '') IS NOT NULL
            ORDER BY t.TipoTag ASC;
        """)
        params = {"id_kanban": int(id_kanban)}
    else:
        sql = text(f"""
            SELECT DISTINCT t.TipoTag
            FROM {tabela_tag} t
            JOIN {TABELA_KANBAN} k
              ON k.IDDimKanban = t.IDDimKanban
            WHERE k.IDEmpresaProprietaria = :id_emp
              AND k.Ativo = 1
              AND t.Ativo = 1
              AND NULLIF(LTRIM(RTRIM(ISNULL(t.TipoTag, ''))), '') IS NOT NULL
            ORDER BY t.TipoTag ASC;
        """)
        params = {"id_emp": int(id_emp)}

    rows = db.session.execute(sql, params).mappings().all()
    resultado = [
        _normalizar_codigo_dominio(row.get("TipoTag"))
        for row in rows
        if _normalizar_codigo_dominio(row.get("TipoTag"))
    ]
    _cache_json_set(chave, resultado, TIMEOUT_CACHE_MEDIO)
    return resultado


def _obter_tipo_fase_padrao(id_kanban: int, id_emp: int) -> str:
    tipos = _obter_tipos_fase_configurados(id_kanban=id_kanban, id_emp=id_emp)
    return tipos[0] if tipos else TIPO_FASE_FALLBACK_PADRAO


def _obter_tipo_tag_padrao(id_kanban: int, id_emp: int) -> str:
    tipos = _obter_tipos_tag_configurados(id_kanban=id_kanban, id_emp=id_emp)
    return tipos[0] if tipos else TIPO_TAG_FALLBACK_PADRAO


def _obter_motivos_inativacao_configurados() -> list[dict[str, Any]]:
    chave = _chave_cache_json("kanban:dominio:motivos_inativacao")
    em_cache = _cache_json_get(chave)
    if em_cache is not None:
        return em_cache

    resultado: list[dict[str, Any]] = []

    if _objeto_existe(TABELA_MOTIVO_INATIVACAO_CARD):
        sql = text(f"""
            SELECT
                m.IDDimKanbanMotivoInativacaoCard,
                m.CodigoMotivo,
                m.NomeExibicao,
                ISNULL(m.BitAtivo, 1) AS BitAtivo
            FROM {TABELA_MOTIVO_INATIVACAO_CARD} m
            WHERE ISNULL(m.BitAtivo, 1) = 1
            ORDER BY m.IDDimKanbanMotivoInativacaoCard ASC;
        """)
        rows = db.session.execute(sql).mappings().all()
        for row in rows:
            codigo = _normalizar_codigo_dominio(row.get("CodigoMotivo"))
            nome = str(row.get("NomeExibicao") or "").strip()
            if not codigo or not nome:
                continue
            resultado.append(
                {
                    "ID": row.get("IDDimKanbanMotivoInativacaoCard"),
                    "Codigo": codigo,
                    "Descricao": nome,
                }
            )

    _cache_json_set(chave, resultado, TIMEOUT_CACHE_MEDIO)
    return resultado


def _normalizar_motivo_inativacao_card(valor: Any) -> dict[str, Any] | None:
    texto_original = str(valor or "").strip()
    if not texto_original:
        return None

    texto_comparacao = _normalizar_texto_comparacao(texto_original)
    for item in _obter_motivos_inativacao_configurados():
        if texto_original.isdigit() and item.get("ID") is not None and int(item.get("ID")) == int(texto_original):
            return item
        codigo = _normalizar_texto_comparacao(item.get("Codigo"))
        descricao = _normalizar_texto_comparacao(item.get("Descricao"))
        if texto_comparacao in {codigo, descricao}:
            return item
    return None


def _fase_define_status_concluido(nome_fase: Any, tipo_fase: Any) -> bool:
    return _normalizar_texto_comparacao(nome_fase) == "concluido" or _normalizar_codigo_dominio(tipo_fase) == "SUCESSO"


def _obter_status_card_para_fase(id_fase: int) -> str:
    try:
        fase = _obter_fase_autorizada(id_fase, incluir_inativa=True)
    except Exception:
        return _obter_status_card_padrao()

    if _fase_define_status_concluido(fase.get("NomeFase"), fase.get("TipoFase")):
        if "CONCLUIDO" in _obter_status_card_validos():
            return "CONCLUIDO"
    return _obter_status_card_padrao()



def _inserir_registro_dinamico(nome_tabela: str, valores: dict[str, Any], colunas_getdate: tuple[str, ...] = ()) -> None:
    if not _objeto_existe(nome_tabela):
        return

    colunas_params: list[str] = []
    params: dict[str, Any] = {}
    colunas_literais: list[tuple[str, str]] = []

    for coluna, valor in valores.items():
        if valor is None or not _coluna_existe(nome_tabela, coluna):
            continue
        colunas_params.append(coluna)
        params[coluna] = valor

    for coluna in colunas_getdate:
        if _coluna_existe(nome_tabela, coluna) and coluna not in colunas_params:
            colunas_literais.append((coluna, "GETDATE()"))

    if not colunas_params and not colunas_literais:
        return

    colunas_insert = [f"[{col}]" for col in colunas_params] + [f"[{col}]" for col, _ in colunas_literais]
    valores_insert = [f":{col}" for col in colunas_params] + [expr for _, expr in colunas_literais]

    sql = text(
        f"INSERT INTO {nome_tabela} ({', '.join(colunas_insert)}) VALUES ({', '.join(valores_insert)});"
    )
    db.session.execute(sql, params)








def _inserir_registro_dinamico_output_id(
    nome_tabela: str,
    coluna_id_output: str,
    valores: dict[str, Any],
    colunas_getdate: tuple[str, ...] = (),
) -> int | None:
    """
    Insere dinamicamente e devolve o ID gerado pelo OUTPUT INSERTED.
    """
    if not _objeto_existe(nome_tabela):
        raise RuntimeError(f"A tabela {nome_tabela} não existe.")

    if not _coluna_existe(nome_tabela, coluna_id_output):
        raise RuntimeError(
            f"A coluna de retorno {coluna_id_output} não existe em {nome_tabela}."
        )

    colunas_parametros: list[str] = []
    parametros: dict[str, Any] = {}
    colunas_literais: list[tuple[str, str]] = []

    for coluna, valor in valores.items():
        if valor is None:
            continue
        if not _coluna_existe(nome_tabela, coluna):
            continue

        colunas_parametros.append(coluna)
        parametros[coluna] = valor

    for coluna in colunas_getdate:
        if _coluna_existe(nome_tabela, coluna) and coluna not in colunas_parametros:
            colunas_literais.append((coluna, "GETDATE()"))

    if not colunas_parametros and not colunas_literais:
        raise RuntimeError(
            f"Nenhuma coluna válida foi encontrada para inserir em {nome_tabela}."
        )

    colunas_insert = [f"[{coluna}]" for coluna in colunas_parametros]
    colunas_insert += [f"[{coluna}]" for coluna, _ in colunas_literais]

    valores_insert = [f":{coluna}" for coluna in colunas_parametros]
    valores_insert += [literal for _, literal in colunas_literais]

    sql = text(
        f"""
        INSERT INTO {nome_tabela}
        (
            {", ".join(colunas_insert)}
        )
        OUTPUT INSERTED.[{coluna_id_output}]
        VALUES
        (
            {", ".join(valores_insert)}
        );
        """
    )

    valor_id = db.session.execute(sql, parametros).scalar()
    return int(valor_id) if valor_id not in (None, "") else None


def _obter_solicitacao_contrato_ativa_por_card(id_card: int) -> dict[str, Any] | None:
    if not _objeto_existe(TABELA_SOLICITACAO_CONTRATO):
        return None

    filtro_ativo = ""
    if _coluna_existe(TABELA_SOLICITACAO_CONTRATO, "BitAtivo"):
        filtro_ativo = "AND BitAtivo = 1"

    sql = text(
        f"""
        SELECT TOP (1)
            IDFatoSolicitacaoContratoEuromidia,
            IDFatoKanbanCard,
            TipoSolicitacao,
            IDDimStatusContratos
        FROM {TABELA_SOLICITACAO_CONTRATO}
        WHERE IDFatoKanbanCard = :id_card
          {filtro_ativo}
        ORDER BY
            DataCriacao DESC,
            IDFatoSolicitacaoContratoEuromidia DESC;
        """
    )

    row = db.session.execute(sql, {"id_card": int(id_card)}).mappings().first()
    return dict(row) if row else None


def _resolver_tipo_solicitacao_por_tags_ativas(tags_ativas: list[dict[str, Any]]) -> str:
    nomes_tags = {
        _normalizar_texto_comparacao(item.get("NomeTag"))
        for item in (tags_ativas or [])
        if isinstance(item, dict)
    }

    tem_novo_contrato = _normalizar_texto_comparacao(NOME_TAG_TIPO_CONTRATO_NOVO) in nomes_tags
    tem_aditivo = _normalizar_texto_comparacao(NOME_TAG_TIPO_CONTRATO_ADITIVO) in nomes_tags

    if tem_novo_contrato and tem_aditivo:
        raise ValueError(
            "O card está com as tags 'Novo Contrato' e 'Aditivo' ao mesmo tempo. Corrija isso antes de enviar para avaliação."
        )

    if tem_novo_contrato:
        return "NOVO_CONTRATO"

    if tem_aditivo:
        return "ADITIVO"

    raise ValueError(
        "Para enviar o contrato para avaliação, o card precisa ter a tag 'Novo Contrato' ou 'Aditivo'."
    )


def _obter_id_status_contrato_em_avaliacao() -> int:
    """
    Resolve dinamicamente o ID do status 'Em Avaliação' na dimensão de status.
    Ajuste o nome da tabela se no seu banco ela estiver em outro schema.
    """
    if not _objeto_existe(TABELA_STATUS_CONTRATOS):
        raise RuntimeError(
            f"A tabela de status {TABELA_STATUS_CONTRATOS} não existe. Ajuste a constante TABELA_STATUS_CONTRATOS."
        )

    coluna_id = "IDDimStatusContratos"
    if not _coluna_existe(TABELA_STATUS_CONTRATOS, coluna_id):
        raise RuntimeError(
            f"A coluna {coluna_id} não existe em {TABELA_STATUS_CONTRATOS}."
        )

    colunas_codigo = [
        coluna
        for coluna in ("CodigoStatus", "Codigo", "Sigla", "CodigoSituacao")
        if _coluna_existe(TABELA_STATUS_CONTRATOS, coluna)
    ]

    colunas_nome = [
        coluna
        for coluna in ("NomeStatus", "Nome", "Descricao", "DescricaoStatus")
        if _coluna_existe(TABELA_STATUS_CONTRATOS, coluna)
    ]

    if not colunas_codigo and not colunas_nome:
        raise RuntimeError(
            f"Não encontrei colunas de código/nome em {TABELA_STATUS_CONTRATOS} para localizar o status 'Em Avaliação'."
        )

    filtros_status: list[str] = []

    for coluna in colunas_codigo:
        filtros_status.append(
            f"UPPER(LTRIM(RTRIM(ISNULL({coluna}, '')))) IN ('EM_AVALIACAO', 'EM AVALIACAO', 'AVALIACAO')"
        )

    for coluna in colunas_nome:
        filtros_status.append(
            f"UPPER(LTRIM(RTRIM(ISNULL({coluna}, '')))) IN ('EM AVALIAÇÃO', 'EM AVALIACAO', 'CONTRATO EM AVALIAÇÃO', 'CONTRATO EM AVALIACAO')"
        )

    filtro_ativo = ""
    if _coluna_existe(TABELA_STATUS_CONTRATOS, "Ativo"):
        filtro_ativo = "AND Ativo = 1"
    elif _coluna_existe(TABELA_STATUS_CONTRATOS, "BitAtivo"):
        filtro_ativo = "AND BitAtivo = 1"

    sql = text(
        f"""
        SELECT TOP (1)
            {coluna_id} AS IDDimStatusContratos
        FROM {TABELA_STATUS_CONTRATOS}
        WHERE (
            {" OR ".join(filtros_status)}
        )
        {filtro_ativo}
        ORDER BY {coluna_id} ASC;
        """
    )

    valor = db.session.execute(sql).scalar()

    if valor in (None, ""):
        raise RuntimeError(
            "Não encontrei o status 'Em Avaliação' na DimStatusContratos. Cadastre esse status antes de usar a tag."
        )

    return int(valor)


def _obter_valor_mensal_item_solicitacao(item: dict[str, Any]) -> Decimal | None:
    for campo in ("ValorVendaFinal", "NovoValor", "ValorTabela"):
        valor = _valor_decimal(item.get(campo))
        if valor is not None:
            return valor
    return None


def _montar_resumo_paineis_solicitacao(
    painel_faces: list[dict[str, Any]],
) -> dict[str, Any]:
    codigos_ponto: set[str] = set()
    codigos_face: set[str] = set()
    valor_total_mensal = Decimal("0")

    for item in painel_faces or []:
        cod_ponto = str(item.get("CodPonto") or "").strip()
        cod_face = str(item.get("CodFace") or "").strip().upper()

        if cod_ponto:
            codigos_ponto.add(cod_ponto)

        if cod_face:
            codigos_face.add(cod_face)

        valor_item = _obter_valor_mensal_item_solicitacao(item)
        if valor_item is not None:
            valor_total_mensal += valor_item

    return {
        "quantidade_pontos": len(codigos_ponto),
        "quantidade_faces": len(codigos_face),
        "valor_total_mensal": valor_total_mensal,
    }


def _criar_solicitacao_contrato_em_avaliacao_para_card(
    *,
    id_card: int,
    id_usuario: int,
    id_empresa_proprietaria: int,
) -> dict[str, Any]:
    """
    Cria cabeçalho e itens da solicitação quando a tag 'Contrato em Avaliação'
    entra no card.

    Regra:
    - não duplica solicitação ativa do mesmo card
    - exige tipo de contrato definido por tag
    - exige ao menos um painel/face vinculado
    """
    solicitacao_existente = _obter_solicitacao_contrato_ativa_por_card(int(id_card))
    if solicitacao_existente:
        return {
            "criada": False,
            "id_solicitacao": int(solicitacao_existente.get("IDFatoSolicitacaoContratoEuromidia") or 0) or None,
            "motivo": "Solicitação ativa já existente para este card.",
        }

    detalhe = _obter_card_detalhe_payload(int(id_card))
    card = detalhe.get("card") if isinstance(detalhe.get("card"), dict) else {}
    tags_ativas = detalhe.get("tags") if isinstance(detalhe.get("tags"), list) else []
    painel_faces = (
        detalhe.get("painel_faces")
        if isinstance(detalhe.get("painel_faces"), list)
        else _listar_paineis_vinculados_card(int(id_card))
    )

    if not painel_faces:
        raise ValueError(
            "Para enviar contrato para avaliação, o card precisa ter pelo menos um painel/face vinculado."
        )

    tipo_solicitacao = _resolver_tipo_solicitacao_por_tags_ativas(tags_ativas)
    id_status_contrato = _obter_id_status_contrato_em_avaliacao()
    resumo = _montar_resumo_paineis_solicitacao(painel_faces)

    id_empresa_relacionada = _obter_id_empresa_relacionada_card(card)

    razao_social = (
        card.get("EmpresaRazaoSocial")
        or card.get("RazaoSocial")
        or None
    )
    cnpj = (
        card.get("EmpresaCNPJ")
        or card.get("CNPJ")
        or None
    )

    nome_vendedor = (
        card.get("NomeUsuarioResponsavel")
        or card.get("NomeUsuario")
        or card.get("Vendedor")
        or None
    )

    observacao_base = "Solicitação criada automaticamente ao aplicar a tag 'Contrato em Avaliação' no card do Kanban."
    descricao_card = str(card.get("Descricao") or "").strip()
    observacao = (
        f"{observacao_base} Descrição do card: {descricao_card}"
        if descricao_card
        else observacao_base
    )

    id_solicitacao = _inserir_registro_dinamico_output_id(
        TABELA_SOLICITACAO_CONTRATO,
        "IDFatoSolicitacaoContratoEuromidia",
        {
            "IDFatoKanbanCard": int(id_card),
            "IDDimStatusContratos": int(id_status_contrato),
            "IDDimUsuariosCriacao": int(id_usuario),
            "IDDimUsuariosEnvioAvaliacao": int(id_usuario),
            "IDEmpresa": int(id_empresa_relacionada) if id_empresa_relacionada else None,
            "IDEmpresaProprietaria": int(id_empresa_proprietaria) if id_empresa_proprietaria else None,
            "TipoSolicitacao": tipo_solicitacao,
            "CNPJ": str(cnpj).strip() if cnpj else None,
            "RazaoSocial": str(razao_social).strip() if razao_social else None,
            "Vendedor": str(nome_vendedor).strip() if nome_vendedor else None,
            "QuantidadePontos": int(resumo["quantidade_pontos"] or 0),
            "QuantidadeFaces": int(resumo["quantidade_faces"] or 0),
            "TotalFaturamentoBrutoMensal": resumo["valor_total_mensal"],
            "Observacao": observacao[:1000],
        },
        colunas_getdate=("DataEnvioAvaliacao",),
    )

    if not id_solicitacao:
        raise RuntimeError("Não foi possível gerar o cabeçalho da solicitação de contrato.")

    total_itens_criados = 0

    for item in painel_faces:
        if not isinstance(item, dict):
            continue

        valor_mensal = _obter_valor_mensal_item_solicitacao(item)

        _inserir_registro_dinamico(
            TABELA_SOLICITACAO_CONTRATO_ITEM,
            {
                "IDFatoSolicitacaoContratoEuromidia": int(id_solicitacao),
                "IDFatoKanbanCard": int(id_card),
                "IDDimUsuariosCriacao": int(id_usuario),
                "IDPainelEuromidia": int(item.get("IDDimPaineisEuromidia") or 0) or None,
                "IDDimFacesPaineis": int(item.get("IDDimFacesPaineis") or 0) or None,
                "IDEmpresaProprietaria": int(id_empresa_proprietaria) if id_empresa_proprietaria else None,
                "CNPJ": str(cnpj).strip() if cnpj else None,
                "RazaoSocial": str(razao_social).strip() if razao_social else None,
                "Vendedor": str(nome_vendedor).strip() if nome_vendedor else None,
                "CodPonto": str(item.get("CodPonto") or "").strip() or None,
                "CodFace": str(item.get("CodFace") or "").strip() or None,
                "Tipo": str(item.get("TipoPainel") or "").strip() or None,
                "DataInicioPrevisto": _para_data_sql_ou_none(item.get("DataInicio") or item.get("DataInicioReserva")),
                "DataTerminoPrevisto": _para_data_sql_ou_none(item.get("DataFim") or item.get("DataFimReserva")),
                "FaturamentoBrutoMensal": valor_mensal,
                "Faturamento": valor_mensal,
                "OBS": "Item gerado automaticamente a partir do vínculo painel/face do card.",
                "Status": "EM_AVALIACAO",
            }
        )

        total_itens_criados += 1

    return {
        "criada": True,
        "id_solicitacao": int(id_solicitacao),
        "total_itens": int(total_itens_criados),
        "tipo_solicitacao": tipo_solicitacao,
    }

















def _obter_snapshot_card_log(id_card: int, *, incluir_inativo: bool = True) -> dict[str, Any] | None:
    filtro_ativo = "" if incluir_inativo else "AND c.Ativo = 1"
    sql = text(f"""
        SELECT
            c.IDFatoKanbanCard,
            c.IDDimKanban,
            c.IDDimKanbanFaseAtual,
            c.Titulo,
            c.Descricao,
            c.StatusCard,
            c.Ativo,
            c.CriadoEm,
            c.AtualizadoEm,
            c.IDEmpresaProprietaria,
            {_sql_select_empresa_relacionada_card('c')},
            {_sql_select_usuario_relacionado_card('c')}
        FROM {TABELA_CARD} c
        WHERE c.IDFatoKanbanCard = :id_card
          {filtro_ativo};
    """)
    row = db.session.execute(sql, {"id_card": int(id_card)}).mappings().first()
    return dict(row) if row else None



def _registrar_log_card(
    *,
    id_card: int,
    id_kanban: int | None,
    id_empresa_proprietaria: int | None,
    id_usuario_acao: int | None,
    tipo_evento: str,
    subtipo_evento: str | None = None,
    id_fase_de: int | None = None,
    id_fase_para: int | None = None,
    motivo: str | None = None,
    observacao: str | None = None,
    tabela_origem: str | None = None,
    id_registro_origem: int | None = None,
    payload_antes: dict[str, Any] | None = None,
    payload_depois: dict[str, Any] | None = None,
) -> None:
    if not _objeto_existe(TABELA_CARD_LOG):
        return

    id_empresa_relacionada = _obter_id_empresa_relacionada_card(payload_depois) or _obter_id_empresa_relacionada_card(payload_antes)
    id_usuario_relacionado = _obter_id_usuario_relacionado_card(payload_depois) or _obter_id_usuario_relacionado_card(payload_antes)

    valores = {
        "IDFatoKanbanCard": int(id_card),
        "IDDimKanban": int(id_kanban) if id_kanban else None,
        "IDEmpresaProprietaria": int(id_empresa_proprietaria) if id_empresa_proprietaria else None,
        "IDEmpresaRelacionada": int(id_empresa_relacionada) if id_empresa_relacionada else None,
        "IDUsuarioAcao": int(id_usuario_acao) if id_usuario_acao else None,
        "IDUsuarioRelacionado": int(id_usuario_relacionado) if id_usuario_relacionado else None,
        "TipoEvento": (tipo_evento or "").strip()[:80] or None,
        "SubtipoEvento": (subtipo_evento or "").strip()[:120] or None,
        "IDFaseDe": int(id_fase_de) if id_fase_de else None,
        "IDFasePara": int(id_fase_para) if id_fase_para else None,
        "Motivo": (motivo or "").strip()[:300] or None,
        "Observacao": (observacao or "").strip()[:2000] or None,
        "TabelaOrigem": (tabela_origem or "").strip()[:150] or None,
        "IDRegistroOrigem": int(id_registro_origem) if id_registro_origem else None,
        "PayloadAntes": _json_para_log(payload_antes),
        "PayloadDepois": _json_para_log(payload_depois),
        "TextoLivre": (observacao or "").strip()[:2000] or None,
    }

    _inserir_registro_dinamico(
        TABELA_CARD_LOG,
        valores,
        colunas_getdate=("OcorridoEm", "DataHoraEvento", "CriadoEm", "DataInserido"),
    )



def _contar_cards_ativos_fase(id_fase: int) -> int:
    sql = text(f"""
        SELECT COUNT(1)
        FROM {TABELA_CARD}
        WHERE IDDimKanbanFaseAtual = :id_fase
          AND Ativo = 1;
    """)
    return int(db.session.execute(sql, {"id_fase": int(id_fase)}).scalar() or 0)



def _contar_cards_ativos_kanban(id_kanban: int) -> int:
    sql = text(f"""
        SELECT COUNT(1)
        FROM {TABELA_CARD}
        WHERE IDDimKanban = :id_kanban
          AND Ativo = 1;
    """)
    return int(db.session.execute(sql, {"id_kanban": int(id_kanban)}).scalar() or 0)



def _obter_kanban_autorizado(id_kanban: int, *, incluir_inativo: bool = False) -> dict[str, Any]:
    id_emp = _id_empresa_usuario_or_403()
    filtro_ativo = "" if incluir_inativo else "AND k.Ativo = 1"
    sql = text(f"""
        SELECT
            k.IDDimKanban,
            k.NomeKanban,
            k.Descricao,
            k.Ativo,
            k.CriadoEm,
            k.BitPrincipal,
            k.IDEmpresaProprietaria
        FROM {TABELA_KANBAN} k
        WHERE k.IDDimKanban = :id_kanban
          AND k.IDEmpresaProprietaria = :id_emp
          {filtro_ativo};
    """)
    row = db.session.execute(sql, {"id_kanban": id_kanban, "id_emp": id_emp}).mappings().first()
    if not row:
        abort(403, "Você não tem permissão para acessar este kanban")
    return dict(row)



def _obter_cfg_kanban(id_kanban: int) -> dict[str, Any]:
    kanban = _obter_kanban_autorizado(id_kanban)
    id_emp_prop = int(kanban.get("IDEmpresaProprietaria") or 0)
    bit_principal = int(kanban.get("BitPrincipal") or 0)
    return {
        "IDDimKanban": int(kanban.get("IDDimKanban") or id_kanban),
        "IDEmpresaProprietaria": kanban.get("IDEmpresaProprietaria"),
        "BitPrincipal": kanban.get("BitPrincipal"),
        "MostrarPainelFaceNoCard": bool((id_emp_prop == 3) and (bit_principal == 1)),
    }



def _obter_fase_autorizada(id_fase: int, *, incluir_inativa: bool = False) -> dict[str, Any]:
    id_emp = _id_empresa_usuario_or_403()
    filtro_ativo = "" if incluir_inativa else "AND f.Ativo = 1"
    cor_select = "f.CorHex," if _coluna_existe(TABELA_KANBAN_FASE, "CorHex") else "CAST(NULL AS varchar(7)) AS CorHex,"
    sql = text(f"""
        SELECT
            f.IDDimKanbanFase,
            f.IDDimKanban,
            f.NomeFase,
            f.OrdemFase,
            f.TipoFase,
            {cor_select}
            f.Ativo,
            k.IDEmpresaProprietaria,
            k.BitPrincipal
        FROM {TABELA_KANBAN_FASE} f
        JOIN {TABELA_KANBAN} k
          ON k.IDDimKanban = f.IDDimKanban
        WHERE f.IDDimKanbanFase = :id_fase
          AND k.IDEmpresaProprietaria = :id_emp
          AND k.Ativo = 1
          {filtro_ativo};
    """)
    row = db.session.execute(sql, {"id_fase": id_fase, "id_emp": id_emp}).mappings().first()
    if not row:
        abort(403, "Você não tem permissão para acessar esta fase")
    return dict(row)



def _validar_fase_do_kanban(id_kanban: int, id_fase: int) -> bool:
    try:
        fase = _obter_fase_autorizada(id_fase)
    except Exception:
        return False
    return int(fase.get("IDDimKanban") or 0) == int(id_kanban or 0)







def _obter_motivos_encerramento_card(*, incluir_inativos: bool = False) -> list[dict[str, Any]]:
    """
    Lê os motivos de encerramento válidos do kanban.

    Regras:
    - usa DimKanbanMotivoEncerramento
    - prioriza a empresa do usuário
    - aceita também registros globais / empresa 3, para não quebrar ambiente compartilhado
    - devolve um Codigo derivado do NomeMotivo para manter compatibilidade com o front antigo
      (ex.: "Preço" -> "PRECO", "Apenas Informações" -> "APENAS_INFORMACOES")
    """
    if not _objeto_existe(TABELA_MOTIVO_ENCERRAMENTO_CARD):
        return []

    id_emp = _id_empresa_usuario_or_403()

    chave = _chave_cache_json(
        "kanban:motivos:encerramento",
        id_emp,
        1 if incluir_inativos else 0,
    )
    em_cache = _cache_json_get(chave)
    if isinstance(em_cache, list):
        return em_cache

    filtro_ativo = "" if incluir_inativos else "AND m.Ativo = 1"

    sql = text(f"""
        SELECT
            m.IDDimKanbanMotivoEncerramento,
            m.NomeMotivo,
            m.TipoMotivo,
            m.Ativo,
            m.IDEmpresaProprietaria,
            m.IDDimUsuarios
        FROM {TABELA_MOTIVO_ENCERRAMENTO_CARD} m
        WHERE
            (m.IDEmpresaProprietaria = :id_emp OR m.IDEmpresaProprietaria = 3 OR m.IDEmpresaProprietaria IS NULL)
            AND UPPER(LTRIM(RTRIM(ISNULL(m.TipoMotivo, 'ENCERRAMENTO')))) = 'ENCERRAMENTO'
            {filtro_ativo}
            AND NULLIF(LTRIM(RTRIM(ISNULL(m.NomeMotivo, ''))), '') IS NOT NULL
        ORDER BY
            CASE
                WHEN m.IDEmpresaProprietaria = :id_emp THEN 0
                WHEN m.IDEmpresaProprietaria = 3 THEN 1
                ELSE 2
            END,
            m.NomeMotivo ASC,
            m.IDDimKanbanMotivoEncerramento ASC;
    """)

    rows = db.session.execute(sql, {"id_emp": int(id_emp)}).mappings().all()

    mapa_unico: dict[str, dict[str, Any]] = {}

    for row in rows:
        item = dict(row)
        nome_motivo = str(item.get("NomeMotivo") or "").strip()
        codigo_motivo = _normalizar_codigo_dominio(nome_motivo)

        if not codigo_motivo:
            continue

        item["Codigo"] = codigo_motivo
        item["Descricao"] = nome_motivo

        """
        Se vier motivo repetido entre empresa local e base compartilhada,
        fico com o primeiro porque a ordenação já priorizou a empresa do usuário.
        """
        if codigo_motivo not in mapa_unico:
            mapa_unico[codigo_motivo] = item

    resultado = list(mapa_unico.values())
    _cache_json_set(chave, resultado, TIMEOUT_CACHE_MEDIO)
    return resultado









def _normalizar_motivo_encerramento_card(motivo_informado: Any) -> dict[str, Any] | None:
    """
    Normaliza o motivo enviado pelo front.

    Aceita:
    - ID numérico da DimKanbanMotivoEncerramento
    - Nome do motivo
    - Código derivado do nome
    - aliases antigos do HTML atual (PRECO, DESISTENCIA, APENAS_INFORMACOES, OUTRO_MOTIVO)
    """
    motivos = _obter_motivos_encerramento_card(incluir_inativos=True)
    if not motivos:
        return None

    texto = str(motivo_informado or "").strip()
    if not texto:
        return None

    id_informado: int | None = None
    try:
        id_informado = int(texto)
    except Exception:
        id_informado = None

    codigo_informado = _normalizar_codigo_dominio(texto)

    aliases = {
        "OUTRO_MOTIVO": "OUTROS",
        "OUTROS": "OUTROS",
        "PRECO": "PRECO",
        "DESISTENCIA": "DESISTENCIA",
        "APENAS_INFORMACOES": "APENAS_INFORMACOES",
        "CONCORRENTE": "CONCORRENTE",
    }

    codigos_aceitos = set()
    if codigo_informado:
        codigos_aceitos.add(codigo_informado)
        if codigo_informado in aliases:
            codigos_aceitos.add(aliases[codigo_informado])

    nome_comparacao = _normalizar_texto_comparacao(texto)

    for item in motivos:
        item_dict = dict(item)

        try:
            id_item = int(item_dict.get("IDDimKanbanMotivoEncerramento") or 0)
        except Exception:
            id_item = 0

        nome_item = str(item_dict.get("NomeMotivo") or "").strip()
        codigo_item = _normalizar_codigo_dominio(nome_item)

        if id_informado and id_item == id_informado:
            item_dict["Codigo"] = codigo_item
            item_dict["Descricao"] = nome_item
            return item_dict

        if codigo_item and codigo_item in codigos_aceitos:
            item_dict["Codigo"] = codigo_item
            item_dict["Descricao"] = nome_item
            return item_dict

        if nome_comparacao and _normalizar_texto_comparacao(nome_item) == nome_comparacao:
            item_dict["Codigo"] = codigo_item
            item_dict["Descricao"] = nome_item
            return item_dict

    return None









def _registrar_historico_encerramento_card(
    *,
    id_card: int,
    id_motivo_encerramento: int,
    nome_motivo: str,
    id_fase: int | None,
    id_usuario: int,
    observacoes: str | None,
) -> dict[str, Any]:
    """
    Sempre registra o encerramento nesta tabela:
    Silver.FatoDimHistoricoEncerramentoCard
    """
    if not _objeto_existe(TABELA_HISTORICO_ENCERRAMENTO_CARD):
        raise RuntimeError(
            "A tabela Silver.FatoDimHistoricoEncerramentoCard não existe ou não está acessível."
        )

    sql = text(f"""
        INSERT INTO {TABELA_HISTORICO_ENCERRAMENTO_CARD}
        (
            NomeMotivo,
            IDDimKanbanMotivoEncerramento,
            IDDimKanbanFase,
            IDFatoKanbanCard,
            IDDimUsuarios,
            Observacoes,
            DataAtualizacao
        )
        OUTPUT
            INSERTED.IDFatoDimHistoricoEncerramentoCard,
            INSERTED.NomeMotivo,
            INSERTED.IDDimKanbanMotivoEncerramento,
            INSERTED.IDDimKanbanFase,
            INSERTED.IDFatoKanbanCard,
            INSERTED.IDDimUsuarios,
            INSERTED.Observacoes,
            INSERTED.DataAtualizacao
        VALUES
        (
            :nome_motivo,
            :id_motivo_encerramento,
            :id_fase,
            :id_card,
            :id_usuario,
            :observacoes,
            GETDATE()
        );
    """)

    row = db.session.execute(
        sql,
        {
            "nome_motivo": str(nome_motivo or "").strip()[:100],
            "id_motivo_encerramento": int(id_motivo_encerramento),
            "id_fase": int(id_fase) if id_fase else None,
            "id_card": int(id_card),
            "id_usuario": int(id_usuario),
            "observacoes": (str(observacoes or "").strip()[:1000] or None),
        },
    ).mappings().first()

    if not row:
        raise RuntimeError("Não foi possível gravar o histórico de encerramento do card.")

    return dict(row)








def _obter_card_autorizado(id_card: int, *, incluir_inativo: bool = False) -> dict[str, Any]:
    id_emp = _id_empresa_usuario_or_403()
    filtro_ativo = "" if incluir_inativo else "AND c.Ativo = 1"
    sql = text(f"""
        SELECT
            c.IDFatoKanbanCard,
            c.IDDimKanban,
            c.IDDimKanbanFaseAtual,
            c.Titulo,
            c.Descricao,
            c.StatusCard,
            c.CriadoEm,
            c.AtualizadoEm,
            c.IDDimKanbanOrigem,
            c.IDDimKanbanMotivoEncerramento,
            c.MotivoEncerramentoObs,
            c.IDEmpresaProprietaria,
            {_sql_select_empresa_relacionada_card('c')},
            {_sql_select_usuario_relacionado_card('c')},
            k.BitPrincipal,
            k.IDEmpresaProprietaria AS IDEmpresaDoKanban
        FROM {TABELA_CARD} c
        JOIN {TABELA_KANBAN} k
          ON k.IDDimKanban = c.IDDimKanban
        WHERE c.IDFatoKanbanCard = :id_card
          AND k.IDEmpresaProprietaria = :id_emp
          AND k.Ativo = 1
          {filtro_ativo};
    """)
    row = db.session.execute(sql, {"id_card": id_card, "id_emp": id_emp}).mappings().first()
    if not row:
        abort(403, "Você não tem permissão para acessar este card")
    return dict(row)



def _fase_deve_aparecer_no_template(id_fase: int | None) -> bool:
  
    return int(id_fase or 0) != 9




def _obter_fases_kanban(id_kanban: int) -> list[dict[str, Any]]:
    cor_select = "CorHex," if _coluna_existe(TABELA_KANBAN_FASE, "CorHex") else "CAST(NULL AS varchar(7)) AS CorHex,"
    sql_fases = text(f"""
        SELECT
            IDDimKanbanFase,
            NomeFase,
            OrdemFase,
            TipoFase,
            {cor_select}
            Ativo
        FROM {TABELA_KANBAN_FASE}
        WHERE IDDimKanban = :id_kanban
          AND Ativo = 1
          AND IDDimKanbanFase <> 9
        ORDER BY OrdemFase ASC;
    """)
    fases = db.session.execute(sql_fases, {"id_kanban": id_kanban}).mappings().all()
    return _rows_para_dicts(fases)


def _obter_tags_kanban(id_kanban: int) -> list[dict[str, Any]]:
    sql_tags = text("""
        SELECT
            IDDimKanbanTag,
            NomeTag,
            TipoTag,
            CorHex,
            Icone,
            AfetaCorCard,
            AplicacaoUnica
        FROM [Kanban].[Silver].[DimKanbanTag]
        WHERE IDDimKanban = :id_kanban
          AND Ativo = 1
        ORDER BY NomeTag ASC;
    """)
    tags = db.session.execute(sql_tags, {"id_kanban": id_kanban}).mappings().all()
    return _rows_para_dicts(tags)



def _obter_card_tags_kanban(id_kanban: int) -> list[dict[str, Any]]:
    sql_card_tags = text("""
        SELECT
            ct.IDFatoKanbanCard,
            t.IDDimKanbanTag,
            t.NomeTag,
            t.CorHex,
            t.Icone
        FROM [Kanban].[Silver].[FatoKanbanCardTag] ct
        JOIN [Kanban].[Silver].[DimKanbanTag] t
          ON t.IDDimKanbanTag = ct.IDDimKanbanTag
        WHERE t.IDDimKanban = :id_kanban
          AND t.Ativo = 1
          AND ct.RemovidoEm IS NULL;
    """)
    card_tags = db.session.execute(sql_card_tags, {"id_kanban": id_kanban}).mappings().all()
    return _rows_para_dicts(card_tags)





def _obter_cards_kanban(id_kanban: int) -> list[dict[str, Any]]:
    sql_cards = text(f"""
        SELECT
            c.IDFatoKanbanCard,
            c.IDDimKanban,
            c.IDDimKanbanFaseAtual,
            c.Titulo,
            c.StatusCard,
            c.CriadoEm,
            c.AtualizadoEm,
            c.IDEmpresaProprietaria,
            {_sql_select_empresa_relacionada_card('c')},
            {_sql_select_usuario_relacionado_card('c')},
            e.RazaoSocial AS EmpresaRazaoSocial,
            e.CNPJ AS EmpresaCNPJ,
            e.CNAE AS EmpresaCNAE,
            cn.Classe AS EmpresaClasse,
            cn.Setor AS EmpresaSetor
        FROM {TABELA_CARD} c
        {_sql_join_empresa_relacionada_card('c', 'e', 'cn')}
        WHERE c.IDDimKanban = :id_kanban
          AND c.Ativo = 1
          {_sql_filtro_status_card_visiveis('c')}
          {_sql_filtro_cards_nao_concluidos_no_quadro('c')}
        ORDER BY
            CASE WHEN c.AtualizadoEm IS NULL THEN c.CriadoEm ELSE c.AtualizadoEm END DESC,
            c.IDFatoKanbanCard DESC;
    """)
    rows = db.session.execute(sql_cards, {"id_kanban": id_kanban}).mappings().all()
    return _rows_para_dicts(rows)




def _valor_decimal(valor: Any) -> Decimal | None:
    if valor is None:
        return None
    if isinstance(valor, Decimal):
        return valor
    texto = str(valor).strip().replace("R$", "").replace(" ", "")
    if not texto:
        return None
    if "," in texto and "." in texto:
        texto = texto.replace(".", "").replace(",", ".")
    else:
        texto = texto.replace(",", ".")
    try:
        return Decimal(texto)
    except (InvalidOperation, ValueError):
        return None


def _decimal_para_float(valor: Any) -> float | None:
    dec = _valor_decimal(valor)
    return float(dec) if dec is not None else None


def _normalizar_texto(valor: Any) -> str:
    return str(valor or "").strip()




@socketio.on("connect", namespace=NAMESPACE_SOCKET_KANBAN)
def socket_connect_kanban(auth: Any | None = None):
    if not getattr(current_user, "is_authenticated", False):
        current_app.logger.warning("Socket kanban recusado: usuário não autenticado. sid=%s", getattr(request, "sid", None))
        return False

    emit(
        "socket_ack",
        {
            "ok": True,
            "evento": "connect",
            "id_usuario": _id_usuario(),
            "id_empresa": _id_empresa_usuario(),
        },
    )


@socketio.on("disconnect", namespace=NAMESPACE_SOCKET_KANBAN)
def socket_disconnect_kanban():
    if getattr(current_user, "is_authenticated", False):
        current_app.logger.info(
            "Socket kanban desconectado. sid=%s usuario=%s empresa=%s",
            getattr(request, "sid", None),
            _id_usuario(),
            _id_empresa_usuario(),
        )


@socketio.on("entrar_kanban", namespace=NAMESPACE_SOCKET_KANBAN)
def socket_entrar_kanban(payload: Any | None = None):
    if not getattr(current_user, "is_authenticated", False):
        emit("socket_erro", {"ok": False, "msg": "Usuário não autenticado."})
        disconnect()
        return

    dados = payload if isinstance(payload, dict) else {}

    try:
        id_kanban = int(dados.get("id_kanban") or 0)
    except Exception:
        id_kanban = 0

    if not id_kanban:
        emit("socket_erro", {"ok": False, "msg": "id_kanban inválido."})
        return

    try:
        _obter_kanban_autorizado(id_kanban)
    except Exception:
        emit(
            "socket_erro",
            {
                "ok": False,
                "id_kanban": id_kanban,
                "msg": "Você não tem permissão para entrar neste kanban.",
            },
        )
        return

    sala = _sala_kanban(id_kanban)
    join_room(sala)
    emit(
        "socket_ack",
        {
            "ok": True,
            "evento": "entrar_kanban",
            "id_kanban": id_kanban,
            "sala": sala,
            "sid": getattr(request, "sid", None),
        },
    )


@socketio.on("sair_kanban", namespace=NAMESPACE_SOCKET_KANBAN)
def socket_sair_kanban(payload: Any | None = None):
    if not getattr(current_user, "is_authenticated", False):
        return

    dados = payload if isinstance(payload, dict) else {}

    try:
        id_kanban = int(dados.get("id_kanban") or 0)
    except Exception:
        id_kanban = 0

    if not id_kanban:
        return

    sala = _sala_kanban(id_kanban)
    leave_room(sala)
    emit(
        "socket_ack",
        {
            "ok": True,
            "evento": "sair_kanban",
            "id_kanban": id_kanban,
            "sala": sala,
            "sid": getattr(request, "sid", None),
        },
    )


def _obter_painel_por_id(id_painel: int) -> dict[str, Any] | None:
    sql = text("""
        SELECT TOP 1
            p.IDDimPaineisEuromidia,
            p.CodPonto,
            p.Tipo,
            p.Logradouro,
            p.Cidade,
            p.UF,
            p.Bairro,
            p.Numero,
            p.CEP,
            p.QuantidadeFaces,
            p.BitAtivo
        FROM [Integracao].[Silver].[DimPaineisEuromidia] p
        WHERE TRY_CONVERT(int, p.IDDimPaineisEuromidia) = TRY_CONVERT(int, :id_painel)
        ORDER BY p.DataAtualizacao DESC, p.IDDimPaineisEuromidia DESC;
    """)
    row = db.session.execute(sql, {"id_painel": int(id_painel)}).mappings().first()
    return dict(row) if row else None


def _resolver_face_do_painel(id_painel: int, cod_face: str) -> dict[str, Any] | None:
    painel = _obter_painel_por_id(id_painel)
    if not painel:
        return None

    cod_face = _normalizar_texto(cod_face)
    if not cod_face:
        return None

    sql = text("""
        SELECT TOP 1
            f.IDDimFacesPaineis,
            f.CodPonto,
            f.Face,
            f.CodFace,
            f.Tipo
        FROM [Integracao].[Silver].[DimFacesPaineis] f
        WHERE TRY_CONVERT(int, f.IDDimPaineisEuromidia) = TRY_CONVERT(int, :id_painel)
          AND LTRIM(RTRIM(ISNULL(f.CodFace, ''))) = :cod_face
        ORDER BY f.IDDimFacesPaineis DESC;
    """)
    row = db.session.execute(sql, {"id_painel": int(id_painel), "cod_face": cod_face}).mappings().first()
    if row:
        return dict(row)

    sql_fallback = text("""
        SELECT TOP 1
            f.IDDimFacesPaineis,
            f.CodPonto,
            f.Face,
            f.CodFace,
            f.Tipo
        FROM [Integracao].[Silver].[DimFacesPaineis] f
        WHERE TRY_CONVERT(int, f.CodPonto) = TRY_CONVERT(int, :cod_ponto)
          AND UPPER(LTRIM(RTRIM(ISNULL(f.Tipo, '')))) = UPPER(LTRIM(RTRIM(:tipo_painel)))
          AND LTRIM(RTRIM(ISNULL(f.CodFace, ''))) = :cod_face
        ORDER BY f.IDDimFacesPaineis DESC;
    """)
    row = db.session.execute(
        sql_fallback,
        {
            "cod_ponto": painel.get("CodPonto"),
            "tipo_painel": _normalizar_texto(painel.get("Tipo")),
            "cod_face": cod_face,
        },
    ).mappings().first()
    return dict(row) if row else None


def _obter_custo_por_codponto(cod_ponto: int) -> dict[str, Any] | None:
    sql = text("""
        SELECT TOP 1
            c.IDDimCustoPainel,
            c.Ano,
            c.CodPonto,
            c.Origem,
            c.Valor,
            c.DataCarga
        FROM [Integracao].[Silver].[DimCustoPainel] c
        WHERE TRY_CONVERT(int, c.CodPonto) = TRY_CONVERT(int, :cod_ponto)
        ORDER BY c.Ano DESC, c.DataCarga DESC, c.IDDimCustoPainel DESC;
    """)
    row = db.session.execute(sql, {"cod_ponto": int(cod_ponto)}).mappings().first()
    return dict(row) if row else None


def _obter_precos_painel_face(id_painel: int, id_dim_face: int | None, tipo_painel: str) -> list[dict[str, Any]]:
    sql = text("""
        SELECT
            tp.IDDimTabelaPrecosEuromidia,
            tp.IDDimPaineisEuromidia,
            tp.IDDimFacesPaineis,
            tp.Tipo,
            tp.PeriodoExibicao,
            tp.ExibicoesDia,
            tp.Valor,
            tp.PoliticaTrocas,
            tp.Tabela,
            tp.DataPublicacao,
            tp.DataValidade,
            tp.DataAtualizacao,
            tp.BitAtivo,
            tp.AlteradoPor,
            tp.ValorTroca
        FROM [Integracao].[Silver].[FatoTabelaPrecosEuromidia] tp
        WHERE TRY_CONVERT(int, tp.IDDimPaineisEuromidia) = TRY_CONVERT(int, :id_painel)
          AND UPPER(LTRIM(RTRIM(ISNULL(tp.Tipo, '')))) = UPPER(LTRIM(RTRIM(:tipo_painel)))
          AND (
                :id_dim_face IS NULL
                OR tp.IDDimFacesPaineis IS NULL
                OR TRY_CONVERT(int, tp.IDDimFacesPaineis) = TRY_CONVERT(int, :id_dim_face)
              )
        ORDER BY
            CASE WHEN ISNULL(tp.BitAtivo, 0) = 1 THEN 0 ELSE 1 END,
            ISNULL(tp.DataValidade, '9999-12-31') DESC,
            ISNULL(tp.DataPublicacao, '9999-12-31') DESC,
            tp.IDDimTabelaPrecosEuromidia DESC;
    """)
    rows = db.session.execute(
        sql,
        {
            "id_painel": int(id_painel),
            "id_dim_face": int(id_dim_face) if id_dim_face else None,
            "tipo_painel": _normalizar_texto(tipo_painel),
        },
    ).mappings().all()
    return _rows_para_dicts(rows)


def _obter_preco_por_id(id_preco: int, id_painel: int, id_dim_face: int | None, tipo_painel: str) -> dict[str, Any] | None:
    sql = text("""
        SELECT TOP 1
            tp.IDDimTabelaPrecosEuromidia,
            tp.IDDimPaineisEuromidia,
            tp.IDDimFacesPaineis,
            tp.Tipo,
            tp.PeriodoExibicao,
            tp.ExibicoesDia,
            tp.Valor,
            tp.PoliticaTrocas,
            tp.Tabela,
            tp.DataPublicacao,
            tp.DataValidade,
            tp.DataAtualizacao,
            tp.BitAtivo,
            tp.AlteradoPor,
            tp.ValorTroca
        FROM [Integracao].[Silver].[FatoTabelaPrecosEuromidia] tp
        WHERE tp.IDDimTabelaPrecosEuromidia = :id_preco
          AND TRY_CONVERT(int, tp.IDDimPaineisEuromidia) = TRY_CONVERT(int, :id_painel)
          AND UPPER(LTRIM(RTRIM(ISNULL(tp.Tipo, '')))) = UPPER(LTRIM(RTRIM(:tipo_painel)))
          AND (
                :id_dim_face IS NULL
                OR tp.IDDimFacesPaineis IS NULL
                OR TRY_CONVERT(int, tp.IDDimFacesPaineis) = TRY_CONVERT(int, :id_dim_face)
              )
        ORDER BY tp.IDDimTabelaPrecosEuromidia DESC;
    """)
    row = db.session.execute(
        sql,
        {
            "id_preco": int(id_preco),
            "id_painel": int(id_painel),
            "id_dim_face": int(id_dim_face) if id_dim_face else None,
            "tipo_painel": _normalizar_texto(tipo_painel),
        },
    ).mappings().first()
    return dict(row) if row else None


def _calcular_margens_comerciais(custo: Any, valor_tabela: Any, novo_valor: Any, percentual_desconto: Any) -> dict[str, Any]:
    custo_dec = _valor_decimal(custo) or Decimal('0')
    valor_tabela_dec = _valor_decimal(valor_tabela)
    novo_valor_dec = _valor_decimal(novo_valor)
    percentual_dec = _valor_decimal(percentual_desconto)

    valor_final: Decimal | None = None
    percentual_aplicado: Decimal | None = None

    if novo_valor_dec is not None:
        valor_final = novo_valor_dec
    elif percentual_dec is not None and valor_tabela_dec is not None:
        percentual_aplicado = percentual_dec
        valor_final = valor_tabela_dec * (Decimal('1') - (percentual_dec / Decimal('100')))
    else:
        valor_final = valor_tabela_dec

    margem_valor: Decimal | None = None
    margem_percentual: Decimal | None = None
    if valor_final is not None:
        margem_valor = valor_final - custo_dec
        if valor_final != 0:
            margem_percentual = (margem_valor / valor_final) * Decimal('100')

    return {
        "Custo": float(custo_dec) if custo is not None else None,
        "ValorTabela": float(valor_tabela_dec) if valor_tabela_dec is not None else None,
        "NovoValor": float(novo_valor_dec) if novo_valor_dec is not None else None,
        "PercentualDesconto": float(percentual_aplicado if percentual_aplicado is not None else percentual_dec) if (percentual_aplicado is not None or percentual_dec is not None) else None,
        "ValorVendaFinal": float(valor_final) if valor_final is not None else None,
        "MargemValor": float(margem_valor) if margem_valor is not None else None,
        "MargemPercentual": float(margem_percentual) if margem_percentual is not None else None,
    }








def _normalizar_data_reserva_kanban(valor: Any) -> date | None:
    if valor is None:
        return None

    if isinstance(valor, date) and not isinstance(valor, datetime):
        return valor

    if isinstance(valor, datetime):
        return valor.date()

    texto = str(valor).strip()
    if not texto:
        return None

    for formato in ("%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(texto, formato).date()
        except Exception:
            pass

    return None


def _obter_vendedor_logado_reserva_kanban(id_empresa_proprietaria: int) -> dict[str, Any] | None:
    sql = text("""
        SELECT TOP 1
            v.IDVendedor,
            v.NomeVendedor
        FROM [Integracao].[dbo].[Vendedores] v
        INNER JOIN [Integracao].[Silver].[DimUsuarios] u
            ON u.IDDimUsuarios = v.IDDimUsuarios
           AND COALESCE(u.IDEmpresaProprietaria, 0) = COALESCE(v.IDEmpresaProprietaria, 0)
        WHERE u.IDDimUsuarios = :id_usuario
          AND COALESCE(u.BitAtivo, 1) = 1
          AND COALESCE(v.BitAtivo, 1) = 1
          AND COALESCE(v.IDEmpresaProprietaria, 0) = :id_empresa
        ORDER BY v.IDVendedor
    """)

    row = db.session.execute(
        sql,
        {
            "id_usuario": int(_id_usuario()),
            "id_empresa": int(id_empresa_proprietaria),
        },
    ).mappings().first()

    return dict(row) if row else None


def _obter_razao_social_empresa_reserva_kanban(id_empresa_relacionada: Any) -> str:
    try:
        id_empresa_int = int(id_empresa_relacionada or 0)
    except Exception:
        id_empresa_int = 0

    if not id_empresa_int:
        return ""

    sql = text("""
        SELECT TOP 1
            RazaoSocial
        FROM [Integracao].[Silver].[DimEmpresas]
        WHERE IDEmpresa = :id_empresa
    """)
    valor = db.session.execute(sql, {"id_empresa": int(id_empresa_int)}).scalar()
    return str(valor or "").strip()


def _obter_capacidade_face_reserva_kanban(cod_face: str) -> dict[str, Any]:
    sql = text("""
        SELECT TOP 1
            IDPainelEuromidia = TRY_CONVERT(int, p.IDDimPaineisEuromidia),
            CodPonto = TRY_CONVERT(int, p.CodPonto),
            TipoPainel = UPPER(LTRIM(RTRIM(COALESCE(p.Tipo, '')))),
            QuantidadeFaces = TRY_CONVERT(int, NULLIF(p.QuantidadeFaces, 0)),
            BitAtivo = COALESCE(p.BitAtivo, 1)
        FROM [Integracao].[Silver].[DimFacesPaineis] f
        INNER JOIN [Integracao].[Silver].[DimPaineisEuromidia] p
            ON TRY_CONVERT(int, p.CodPonto) = TRY_CONVERT(int, f.CodPonto)
        WHERE UPPER(LTRIM(RTRIM(COALESCE(f.CodFace, '')))) = UPPER(LTRIM(RTRIM(:cod_face)))
        ORDER BY
            COALESCE(p.BitAtivo, 1) DESC,
            TRY_CONVERT(int, p.IDDimPaineisEuromidia) DESC
    """)

    row = db.session.execute(sql, {"cod_face": str(cod_face or "").strip()}).mappings().first()
    if not row:
        raise ValueError(f"Não foi possível localizar o painel da face {cod_face}.")

    item = dict(row)
    tipo_painel = str(item.get("TipoPainel") or "").strip().upper()
    eh_digital = 1 if "DIGITAL" in tipo_painel else 0
    capacidade_slots = int(item.get("QuantidadeFaces") or 0) if eh_digital else 1
    if eh_digital and capacidade_slots <= 0:
        capacidade_slots = 16

    item["EhDigital"] = eh_digital
    item["CapacidadeSlots"] = capacidade_slots
    item["BitAtivo"] = int(item.get("BitAtivo") or 0)

    return item


def _reserva_kanban_ja_existe(
    *,
    cod_face: str,
    data_inicio: date,
    data_fim: date,
    marcador_observacao: str,
) -> bool:
    sql = text("""
        SELECT TOP 1 1
        FROM [Integracao].[Silver].[FatoOcupacaoPaineisEuromidia]
        WHERE UPPER(LTRIM(RTRIM(COALESCE(CodFace, '')))) = UPPER(LTRIM(RTRIM(:cod_face)))
          AND CanceladoEm IS NULL
          AND Status IN ('ATIVO', 'RESERVADO')
          AND TRY_CONVERT(date, DataInicio) = :data_inicio
          AND TRY_CONVERT(date, DataFim) = :data_fim
          AND COALESCE(Observacao, '') LIKE :marcador_prefixo
    """)

    existe = db.session.execute(
        sql,
        {
            "cod_face": str(cod_face or "").strip(),
            "data_inicio": data_inicio,
            "data_fim": data_fim,
            "marcador_prefixo": f"{marcador_observacao}%",
        },
    ).scalar()

    return bool(existe)


def _validar_conflito_reserva_kanban(
    *,
    cod_face: str,
    data_inicio: date,
    data_fim: date,
    eh_digital: int,
    capacidade_slots: int,
) -> bool:
    if int(eh_digital or 0) != 1:
        sql = text("""
            SELECT TOP 1 1
            FROM [Integracao].[Silver].[FatoOcupacaoPaineisEuromidia]
            WHERE UPPER(LTRIM(RTRIM(COALESCE(CodFace, '')))) = UPPER(LTRIM(RTRIM(:cod_face)))
              AND CanceladoEm IS NULL
              AND Status IN ('ATIVO', 'RESERVADO')
              AND NOT (
                    TRY_CONVERT(date, :data_fim) < TRY_CONVERT(date, DataInicio)
                 OR TRY_CONVERT(date, :data_inicio) > TRY_CONVERT(date, DataFim)
              )
        """)
        existe = db.session.execute(
            sql,
            {
                "cod_face": cod_face,
                "data_inicio": data_inicio,
                "data_fim": data_fim,
            },
        ).scalar()
        return bool(existe)

    sql = text("""
        ;WITH Dias AS (
            SELECT c.[Data]
            FROM [Integracao].[Silver].[DimCalendario] c
            WHERE c.[Data] >= :data_inicio
              AND c.[Data] <= :data_fim
        ),
        Uso AS (
            SELECT
                d.[Data] AS Dia,
                SlotsOcupados = COALESCE(SUM(COALESCE(NULLIF(fo.SpanQtd, 0), 1)), 0)
            FROM Dias d
            LEFT JOIN [Integracao].[Silver].[FatoOcupacaoPaineisEuromidia] fo
                ON d.[Data] >= CAST(fo.DataInicio AS date)
               AND d.[Data] <= CAST(fo.DataFim AS date)
               AND UPPER(LTRIM(RTRIM(COALESCE(fo.CodFace, '')))) = UPPER(LTRIM(RTRIM(:cod_face)))
               AND fo.CanceladoEm IS NULL
               AND fo.Status IN ('ATIVO', 'RESERVADO')
            GROUP BY d.[Data]
        )
        SELECT TOP 1 1
        FROM Uso
        WHERE SlotsOcupados >= :capacidade_slots
        ORDER BY Dia
    """)

    existe = db.session.execute(
        sql,
        {
            "cod_face": cod_face,
            "data_inicio": data_inicio,
            "data_fim": data_fim,
            "capacidade_slots": int(capacidade_slots or 0),
        },
    ).scalar()

    return bool(existe)


def _obter_proxima_prioridade_reserva_kanban(
    *,
    cod_face: str,
    data_inicio: date,
    data_fim: date,
    cota: int | None,
    spanqtd: int | None,
) -> int:
    sql = text("""
        SELECT
            COALESCE(MAX(COALESCE(ReservaOrdemPrioridade, 0)), 0) + 1
        FROM [Integracao].[Silver].[FatoOcupacaoPaineisEuromidia] WITH (UPDLOCK, HOLDLOCK)
        WHERE UPPER(LTRIM(RTRIM(COALESCE(CodFace, '')))) = UPPER(LTRIM(RTRIM(:cod_face)))
          AND CanceladoEm IS NULL
          AND Status IN ('ATIVO', 'RESERVADO')
          AND TRY_CONVERT(date, DataInicio) = :data_inicio
          AND TRY_CONVERT(date, DataFim) = :data_fim
          AND ((:cota IS NULL AND Cota IS NULL) OR (Cota = :cota))
          AND ((:spanqtd IS NULL AND SpanQtd IS NULL) OR (COALESCE(SpanQtd, 0) = COALESCE(:spanqtd, 0)))
    """)

    valor = db.session.execute(
        sql,
        {
            "cod_face": cod_face,
            "data_inicio": data_inicio,
            "data_fim": data_fim,
            "cota": cota,
            "spanqtd": spanqtd,
        },
    ).scalar()

    try:
        return int(valor or 1)
    except Exception:
        return 1


def _criar_reservas_painel_faces_kanban(
    *,
    id_card: int,
    titulo_card: str,
    id_empresa_relacionada: int | None,
    painel_faces_payload: list[Any] | None,
    vinculos_preparados: list[dict[str, Any]] | None,
    id_usuario: int,
    id_empresa_proprietaria: int,
) -> int:
    if not isinstance(painel_faces_payload, list) or not painel_faces_payload:
        return 0

    mapa_vinculos: dict[tuple[int | None, str], dict[str, Any]] = {}
    for vinculo in (vinculos_preparados or []):
        chave = (
            int(vinculo.get("id_painel") or 0) or None,
            str(vinculo.get("cod_face") or "").strip().upper(),
        )
        if chave[1]:
            mapa_vinculos[chave] = vinculo

    vendedor_logado = _obter_vendedor_logado_reserva_kanban(int(id_empresa_proprietaria))
    id_vendedor = int(vendedor_logado.get("IDVendedor") or 0) if vendedor_logado else None
    nome_vendedor = str(vendedor_logado.get("NomeVendedor") or "").strip() if vendedor_logado else ""

    razao_social = _obter_razao_social_empresa_reserva_kanban(id_empresa_relacionada)
    marca_exibida_padrao = razao_social or str(titulo_card or "").strip() or f"Card {int(id_card)}"

    reservas_criadas = 0

    for item in painel_faces_payload:
        if not isinstance(item, dict):
            continue

        data_inicio = _normalizar_data_reserva_kanban(item.get("data_inicio"))
        data_fim = _normalizar_data_reserva_kanban(item.get("data_fim"))

        if data_inicio is None and data_fim is None:
            continue

        if data_inicio is None or data_fim is None:
            raise ValueError("Preencha Data de e Data até para reservar o painel/face.")

        if data_fim < data_inicio:
            raise ValueError("A Data até não pode ser menor que a Data de.")

        id_painel = int(item.get("id_painel") or 0) or None
        cod_face = str(item.get("cod_face") or "").strip().upper()

        if not cod_face:
            raise ValueError("CodFace obrigatório para criar a reserva.")

        vinculo = mapa_vinculos.get((id_painel, cod_face))
        if not vinculo:
            raise ValueError(f"Não foi possível preparar o vínculo comercial da face {cod_face} para reservar.")

        capacidade = _obter_capacidade_face_reserva_kanban(cod_face)
        if int(capacidade.get("BitAtivo") or 0) != 1:
            raise ValueError(f"O painel da face {cod_face} está inativo.")

        cod_ponto = int(vinculo.get("cod_ponto") or capacidade.get("CodPonto") or 0)
        id_painel_final = int(vinculo.get("id_painel") or capacidade.get("IDPainelEuromidia") or 0) or None
        eh_digital = int(capacidade.get("EhDigital") or 0)
        capacidade_slots = int(capacidade.get("CapacidadeSlots") or 0)

        if not cod_ponto:
            raise ValueError(f"Não foi possível resolver o CodPonto da face {cod_face}.")

        marcador_observacao = f"[KANBAN_CARD={int(id_card)}][COD_FACE={cod_face}]"

        if _reserva_kanban_ja_existe(
            cod_face=cod_face,
            data_inicio=data_inicio,
            data_fim=data_fim,
            marcador_observacao=marcador_observacao,
        ):
            continue

        sem_capacidade = _validar_conflito_reserva_kanban(
            cod_face=cod_face,
            data_inicio=data_inicio,
            data_fim=data_fim,
            eh_digital=eh_digital,
            capacidade_slots=capacidade_slots,
        )

        spanqtd_novo = 1 if eh_digital == 1 else None

        cota_valor = vinculo.get("exibicoes_dia")
        try:
            cota_int = int(cota_valor) if cota_valor not in (None, "", 0) else None
        except Exception:
            cota_int = None

        reserva_ordem_prioridade = (
            _obter_proxima_prioridade_reserva_kanban(
                cod_face=cod_face,
                data_inicio=data_inicio,
                data_fim=data_fim,
                cota=cota_int,
                spanqtd=spanqtd_novo,
            )
            if sem_capacidade
            else 1
        )

        dias_int = ((data_fim - data_inicio).days + 1)

        observacao_insert = f"{marcador_observacao} Reserva criada pelo salvamento do card no Kanban."

        sql_insert = text("""
            INSERT INTO [Integracao].[Silver].[FatoOcupacaoPaineisEuromidia] (
                DataAtualizacao,
                Referencia,
                CodPonto,
                CodFace,
                IDPainelEuromidia,
                Origem,
                Status,
                DataInicio,
                DataFim,
                SpanQtd,
                Cota,
                MarcaExibida,
                Vendedor,
                IDVendedor,
                IDCliente,
                IDFatoControleContratos,
                NumeroContrato,
                NumeroPrevia,
                Observacao,
                Dias,
                ExpiraEm,
                CriadoEm,
                CriadoPorIDUsuario,
                ReservaOrdemPrioridade
            )
            VALUES (
                SYSDATETIME(),
                CONVERT(varchar(64),
                    HASHBYTES(
                        'SHA2_256',
                        CONCAT(
                            'KANBAN|',
                            COALESCE(CONVERT(varchar(30), :cod_ponto), ''), '|',
                            UPPER(LTRIM(RTRIM(COALESCE(:cod_face, '')))), '|',
                            COALESCE(CONVERT(varchar(10), :data_inicio, 23), ''), '|',
                            COALESCE(CONVERT(varchar(10), :data_fim, 23), ''), '|',
                            COALESCE(CONVERT(varchar(30), :spanqtd), ''), '|',
                            COALESCE(CONVERT(varchar(30), :cota), ''), '|',
                            COALESCE(CONVERT(varchar(30), :id_cliente), ''), '|',
                            COALESCE(CONVERT(varchar(30), :id_vendedor), ''), '|',
                            COALESCE(CONVERT(varchar(30), :id_card), '')
                        )
                    ),
                    2
                ),
                :cod_ponto,
                :cod_face,
                :id_painel,
                'KANBAN',
                'RESERVADO',
                :data_inicio,
                :data_fim,
                :spanqtd,
                :cota,
                :marca_exibida,
                :vendedor_nome,
                :id_vendedor,
                :id_cliente,
                NULL,
                NULL,
                NULL,
                :observacao,
                :dias,
                DATEADD(day, :dias, SYSDATETIME()),
                SYSDATETIME(),
                :criado_por,
                :reserva_ordem_prioridade
            )
        """)

        db.session.execute(
            sql_insert,
            {
                "id_card": int(id_card),
                "cod_ponto": int(cod_ponto),
                "cod_face": cod_face,
                "id_painel": id_painel_final,
                "data_inicio": data_inicio,
                "data_fim": data_fim,
                "spanqtd": spanqtd_novo,
                "cota": cota_int,
                "marca_exibida": marca_exibida_padrao[:200],
                "vendedor_nome": nome_vendedor[:200] if nome_vendedor else None,
                "id_vendedor": id_vendedor,
                "id_cliente": int(id_empresa_relacionada) if id_empresa_relacionada not in (None, "", 0) else None,
                "observacao": observacao_insert[:1000],
                "dias": int(dias_int),
                "criado_por": int(id_usuario),
                "reserva_ordem_prioridade": int(reserva_ordem_prioridade),
            },
        )

        reservas_criadas += 1

    return reservas_criadas








def _preparar_vinculos_painel_faces(painel_faces_payload: list[Any], id_empresa_proprietaria: int) -> list[dict[str, Any]]:
    vinculos_preparados: list[dict[str, Any]] = []

    for ordem_rel, item in enumerate((painel_faces_payload or []), start=1):
        if not isinstance(item, dict):
            raise ValueError("Cada item de painel_faces deve ser um objeto")

        id_painel_item = int(item.get("id_painel") or 0)
        cod_face_item = _normalizar_texto(item.get("cod_face"))

        if not id_painel_item:
            raise ValueError("Painel é obrigatório em cada vinculação")
        if not cod_face_item:
            raise ValueError("Face é obrigatória em cada vinculação")

        data_inicio_item = _normalizar_data_reserva_kanban(
            item.get("data_inicio")
            or item.get("DataInicio")
            or item.get("data_inicio_reserva")
            or item.get("DataInicioReserva")
        )
        data_fim_item = _normalizar_data_reserva_kanban(
            item.get("data_fim")
            or item.get("DataFim")
            or item.get("data_fim_reserva")
            or item.get("DataFimReserva")
        )

        if (data_inicio_item is None) ^ (data_fim_item is None):
            raise ValueError("Preencha Data de e Data até para o mesmo painel/face.")

        if data_inicio_item is not None and data_fim_item is not None and data_fim_item < data_inicio_item:
            raise ValueError("A Data até não pode ser menor que a Data de.")

        painel_item = _obter_painel_por_id(id_painel_item)
        if not painel_item:
            raise ValueError(f"Painel {id_painel_item} não encontrado")

        face_item = _resolver_face_do_painel(id_painel_item, cod_face_item)
        if not face_item:
            raise ValueError(f"A face {cod_face_item} não pertence ao painel selecionado")

        custo_item = _obter_custo_por_codponto(int(painel_item.get("CodPonto") or 0))

        id_preco_item = item.get("id_preco")
        if id_preco_item in ("", None):
            id_preco_item = None
        else:
            try:
                id_preco_item = int(id_preco_item)
            except Exception as exc:
                raise ValueError("Preço selecionado inválido") from exc

        preco_item = None
        if id_preco_item:
            preco_item = _obter_preco_por_id(
                id_preco=id_preco_item,
                id_painel=id_painel_item,
                id_dim_face=int(face_item.get("IDDimFacesPaineis") or 0)
                if face_item.get("IDDimFacesPaineis") is not None
                else None,
                tipo_painel=_normalizar_texto(painel_item.get("Tipo")),
            )
            if not preco_item:
                raise ValueError(
                    f"O preço selecionado não é válido para o painel/face informado ({cod_face_item})"
                )

        novo_valor_item = _valor_decimal(item.get("novo_valor"))
        percentual_item = _valor_decimal(item.get("percentual_desconto"))
        if novo_valor_item is not None and percentual_item is not None:
            percentual_item = None

        metricas = _calcular_margens_comerciais(
            custo_item.get("Valor") if custo_item else None,
            preco_item.get("Valor") if preco_item else None,
            novo_valor_item,
            percentual_item,
        )

        vinculos_preparados.append(
            {
                "ordem": ordem_rel,
                "id_painel": int(id_painel_item),
                "id_dim_face": int(face_item.get("IDDimFacesPaineis") or 0)
                if face_item.get("IDDimFacesPaineis") is not None
                else None,
                "cod_ponto": int(painel_item.get("CodPonto") or 0)
                if painel_item.get("CodPonto") is not None
                else None,
                "cod_face": cod_face_item,
                "tipo_painel": _normalizar_texto(painel_item.get("Tipo")) or None,
                "ano_custo": int(custo_item.get("Ano") or 0)
                if custo_item and custo_item.get("Ano") is not None
                else None,
                "custo_tabela": metricas.get("Custo"),
                "id_preco": int(preco_item.get("IDDimTabelaPrecosEuromidia") or 0) if preco_item else None,
                "periodo_exibicao": preco_item.get("PeriodoExibicao") if preco_item else None,
                "exibicoes_dia": int(preco_item.get("ExibicoesDia") or 0)
                if preco_item and preco_item.get("ExibicoesDia") is not None
                else None,
                "valor_tabela": metricas.get("ValorTabela"),
                "tabela": preco_item.get("Tabela") if preco_item else None,
                "politica_trocas": preco_item.get("PoliticaTrocas") if preco_item else None,
                "valor_troca": _decimal_para_float(preco_item.get("ValorTroca")) if preco_item else None,
                "novo_valor": metricas.get("NovoValor"),
                "percentual_desconto": metricas.get("PercentualDesconto"),
                "valor_venda_final": metricas.get("ValorVendaFinal"),
                "margem_valor": metricas.get("MargemValor"),
                "margem_percentual": metricas.get("MargemPercentual"),
                "data_inicio": data_inicio_item,
                "data_fim": data_fim_item,
                "id_usuario": _id_usuario(),
                "id_empresa": int(id_empresa_proprietaria),
            }
        )

    return vinculos_preparados







def _buscar_ultima_negociacao_preco_card(
    *,
    id_card: int,
    id_painel: int,
    id_face: int,
) -> dict[str, Any] | None:
    """
    Busca a última linha histórica da negociação para o mesmo card/painel/face.
    """
    sql = text("""
        SELECT TOP 1
            IDFatoKanbanNegociacaoPreco,
            IDDimTabelaPrecosEuromidia,
            CustoAtual,
            PrecoAtual,
            MargemAtual,
            CustoAtualRateado,
            PrecoAtualRateado,
            MargemAtualRateado,
            CustoProposto,
            PrecoProposto,
            MargemProposta,
            CustoPropostoRateado,
            PrecoPropostoRateado,
            DescontoProposto,
            PeriodoInicio,
            PeriodoTermino,
            ObservacoesProposta
        FROM [Kanban].[Silver].[FatoKanbanNegociacaoPreco]
        WHERE IDFatoKanbanCard = :id_card
          AND IDDimPaineisEuromidia = :id_painel
          AND IDDimFacesPaineis = :id_face
        ORDER BY IDFatoKanbanNegociacaoPreco DESC;
    """)

    row = db.session.execute(
        sql,
        {
            "id_card": int(id_card),
            "id_painel": int(id_painel),
            "id_face": int(id_face),
        },
    ).mappings().first()

    return dict(row) if row else None






def _normalizar_int_negociacao(valor: Any) -> int | None:
    if valor in (None, ""):
        return None
    try:
        return int(valor)
    except Exception:
        return None


def _normalizar_decimal_negociacao(valor: Any, casas: str = "0.0001") -> Decimal | None:
    dec = _valor_decimal(valor)
    if dec is None:
        return None

    try:
        return dec.quantize(Decimal(casas))
    except Exception:
        return dec


def _calcular_margem_percentual_negociacao(
    custo: Any,
    preco: Any,
) -> Decimal | None:
    custo_dec = _valor_decimal(custo)
    preco_dec = _valor_decimal(preco)

    if custo_dec is None:
        return None

    if preco_dec in (None, Decimal("0")):
        return None

    return ((preco_dec - custo_dec) / preco_dec) * Decimal("100")


def _montar_assinatura_negociacao_preco(
    *,
    id_tabela_preco: Any,
    custo_atual: Any,
    preco_atual: Any,
    margem_atual: Any,
    custo_proposto: Any,
    preco_proposto: Any,
    margem_proposta: Any,
    desconto_proposto: Any,
) -> tuple:
    """
    Cria uma assinatura estável da negociação.
    Se a assinatura atual for igual à última assinatura histórica,
    não existe motivo para criar nova linha no histórico.
    """
    return (
        _normalizar_int_negociacao(id_tabela_preco),
        _normalizar_decimal_negociacao(custo_atual),
        _normalizar_decimal_negociacao(preco_atual),
        _normalizar_decimal_negociacao(margem_atual),
        _normalizar_decimal_negociacao(custo_proposto),
        _normalizar_decimal_negociacao(preco_proposto),
        _normalizar_decimal_negociacao(margem_proposta),
        _normalizar_decimal_negociacao(desconto_proposto),
    )





def _listar_estado_atual_negociacao_card(id_card: int) -> list[dict[str, Any]]:
    """
    Lê a foto atual canônica do card a partir da tabela operacional.
    É essa tabela que deve ser a fonte da verdade para decidir se houve mudança.
    """
    sql = text("""
        SELECT
            pf.IDFatoKanbanCardPainelFace,
            pf.Ordem,
            pf.IDDimPaineisEuromidia,
            pf.IDDimFacesPaineis,
            pf.IDDimTabelaPrecosEuromidia,
            pf.CustoTabela,
            pf.ValorTabela,
            pf.NovoValor,
            pf.PercentualDesconto,
            pf.ValorVendaFinal,
            pf.MargemValor,
            pf.MargemPercentual,
            pf.DataInicio,
            pf.DataFim
        FROM [Kanban].[Silver].[FatoKanbanCardPainelFace] pf
        WHERE pf.IDFatoKanbanCard = :id_card
          AND ISNULL(pf.Ativo, 1) = 1
        ORDER BY
            ISNULL(pf.Ordem, 0),
            pf.IDFatoKanbanCardPainelFace;
    """)

    rows = db.session.execute(sql, {"id_card": int(id_card)}).mappings().all()
    return [dict(row) for row in rows]





def _registrar_negociacao_preco_card(
    *,
    id_card: int,
    id_kanban: int,
    id_fase_atual: int | None,
    status_card: str | None,
    id_empresa_relacionada: int | None,
    vinculos_preparados: list[dict[str, Any]],
    observacoes_proposta: str | None = None,
) -> None:
    """
    Grava histórico de negociação de preço sem duplicar linha quando nada mudou.

    Fundamento desta versão:
    - a fonte da verdade é a tabela operacional FatoKanbanCardPainelFace já salva nesta transação
    - o histórico FatoKanbanNegociacaoPreco só recebe novo registro quando a assinatura comercial mudou
    - o período também é persistido e participa da assinatura
    """

    if not vinculos_preparados:
        return

    def _tem_valor_informado(valor: Any) -> bool:
        if valor is None:
            return False
        if isinstance(valor, str) and not valor.strip():
            return False
        return True

    def _para_int_ou_none(valor: Any) -> int | None:
        if valor in (None, ""):
            return None
        try:
            return int(valor)
        except (TypeError, ValueError):
            return None

    def _para_decimal_ou_none(valor: Any) -> Decimal | None:
        if valor in (None, ""):
            return None
        return _valor_decimal(valor)

    def _para_data_ou_none(valor: Any) -> date | None:
        return _normalizar_data_reserva_kanban(valor)

    def _calcular_margem_percentual(custo: Any, preco: Any) -> Decimal | None:
        custo_dec = _valor_decimal(custo)
        preco_dec = _valor_decimal(preco)

        if custo_dec is None:
            return None
        if preco_dec in (None, Decimal("0")):
            return None

        return ((preco_dec - custo_dec) / preco_dec) * Decimal("100")

    def _montar_insert_dinamico(valores: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        colunas: list[str] = []
        marcadores: list[str] = []
        parametros: dict[str, Any] = {}

        def adicionar(nome_coluna: str, nome_parametro: str, valor: Any, usar_getdate: bool = False) -> None:
            if not _coluna_existe(TABELA_CARD_NEGOCIACAO_PRECO, nome_coluna):
                return

            colunas.append(nome_coluna)

            if usar_getdate:
                marcadores.append("GETDATE()")
            else:
                marcadores.append(f":{nome_parametro}")
                parametros[nome_parametro] = valor

        adicionar("IDDimUsuarios", "id_usuario", valores.get("id_usuario"))
        adicionar("IDEmpresaProprietaria", "id_empresa_proprietaria", valores.get("id_empresa_proprietaria"))
        adicionar("IDDimTabelaPrecosEuromidia", "id_tabela_preco", valores.get("id_tabela_preco"))
        adicionar("IDEmpresa", "id_empresa_relacionada", valores.get("id_empresa_relacionada"))
        adicionar("IDFatoKanbanCard", "id_card", valores.get("id_card"))
        adicionar("IDDimKanbanFase", "id_fase_atual", valores.get("id_fase_atual"))
        adicionar("IDDimKanbanStatusCard", "id_status_card", valores.get("id_status_card"))
        adicionar("IDFatoControleContratosEuromidia", "id_controle_contrato", None)
        adicionar("BitAditivoContrato", "bit_aditivo", 0)
        adicionar("ObservacoesProposta", "observacoes_proposta", valores.get("observacoes_proposta"))
        adicionar("IDDimPaineisEuromidia", "id_painel", valores.get("id_painel"))
        adicionar("IDDimFacesPaineis", "id_face", valores.get("id_face"))
        adicionar("DataPrecoProposto", "data_preco_proposto", None, usar_getdate=True)

        adicionar("CustoAtual", "custo_atual", valores.get("custo_atual"))
        adicionar("PrecoAtual", "preco_atual", valores.get("preco_atual"))
        adicionar("MargemAtual", "margem_atual", valores.get("margem_atual"))

        adicionar("CustoAtualRateado", "custo_atual_rateado", valores.get("custo_atual_rateado"))
        adicionar("PrecoAtualRateado", "preco_atual_rateado", valores.get("preco_atual_rateado"))
        adicionar("MargemAtualRateado", "margem_atual_rateado", valores.get("margem_atual_rateado"))

        adicionar("CustoProposto", "custo_proposto", valores.get("custo_proposto"))
        adicionar("PrecoProposto", "preco_proposto", valores.get("preco_proposto"))
        adicionar("MargemProposta", "margem_proposta", valores.get("margem_proposta"))

        adicionar("CustoPropostoRateado", "custo_proposto_rateado", valores.get("custo_proposto_rateado"))
        adicionar("PrecoPropostoRateado", "preco_proposto_rateado", valores.get("preco_proposto_rateado"))

        adicionar("DescontoProposto", "desconto_proposto", valores.get("desconto_proposto"))

        adicionar("PeriodoInicio", "periodo_inicio", valores.get("periodo_inicio"))
        adicionar("PeriodoTermino", "periodo_termino", valores.get("periodo_termino"))

        adicionar("IDDimUsuariosAprovacaoPreco", "id_usuario_aprovacao", None)
        adicionar("DataAprovacaoPreco", "data_aprovacao", None)
        adicionar("PrecoAprovado", "preco_aprovado", None)
        adicionar("DescontoAprovado", "desconto_aprovado", None)
        adicionar("ObservacoesAprovacao", "observacoes_aprovacao", None)
        adicionar("BitAutorizacaoDiretoria", "bit_autorizacao_diretoria", valores.get("bit_autorizacao_diretoria", 0))

        if not colunas:
            raise ValueError("Nenhuma coluna válida encontrada em FatoKanbanNegociacaoPreco para gravar a negociação.")

        sql = f"""
            INSERT INTO {TABELA_CARD_NEGOCIACAO_PRECO}
            (
                {", ".join(colunas)}
            )
            VALUES
            (
                {", ".join(marcadores)}
            );
        """
        return sql, parametros

    id_status_card = _obter_id_status_card_por_codigo(status_card)
    id_usuario_atual = _id_usuario()
    id_empresa_proprietaria_negociacao = _resolver_id_empresa_proprietaria_movimento(
        id_kanban=id_kanban,
        id_empresa_padrao=_id_empresa_usuario_or_403(),
    )

    estados_atuais = _listar_estado_atual_negociacao_card(int(id_card))
    if not estados_atuais:
        return

    chaves_processadas: set[tuple[int, int]] = set()

    for estado in estados_atuais:
        id_painel = _para_int_ou_none(estado.get("IDDimPaineisEuromidia"))
        id_face = _para_int_ou_none(estado.get("IDDimFacesPaineis"))
        if not id_painel or not id_face:
            continue

        chave_painel_face = (int(id_painel), int(id_face))
        if chave_painel_face in chaves_processadas:
            continue
        chaves_processadas.add(chave_painel_face)

        id_tabela_preco = _para_int_ou_none(estado.get("IDDimTabelaPrecosEuromidia"))
        custo_atual = _para_decimal_ou_none(estado.get("CustoTabela"))
        preco_atual = _para_decimal_ou_none(estado.get("ValorTabela"))
        margem_atual = _calcular_margem_percentual(custo_atual, preco_atual)

        novo_valor = _para_decimal_ou_none(estado.get("NovoValor"))
        percentual_desconto = _para_decimal_ou_none(estado.get("PercentualDesconto"))
        valor_venda_final = _para_decimal_ou_none(estado.get("ValorVendaFinal"))
        periodo_inicio_atual = _para_data_ou_none(estado.get("DataInicio"))
        periodo_termino_atual = _para_data_ou_none(estado.get("DataFim"))

        preco_proposto = novo_valor
        if preco_proposto is None:
            preco_proposto = valor_venda_final
        if preco_proposto is None:
            preco_proposto = preco_atual

        custo_proposto = custo_atual
        margem_proposta = _calcular_margem_percentual(custo_proposto, preco_proposto)
        precisa_aprovacao_diretoria = _estado_precisa_aprovacao_diretoria(estado)

        tem_operacao_comercial_ou_periodo = any(
            _tem_valor_informado(valor)
            for valor in (
                id_tabela_preco,
                novo_valor,
                percentual_desconto,
                valor_venda_final,
                preco_atual,
                periodo_inicio_atual,
                periodo_termino_atual,
            )
        )
        if not tem_operacao_comercial_ou_periodo:
            continue

        ultima_negociacao = _buscar_ultima_negociacao_preco_card(
            id_card=int(id_card),
            id_painel=int(id_painel),
            id_face=int(id_face),
        )

        if not _negociacao_preco_foi_alterada(
            ultima_negociacao,
            id_tabela_preco=id_tabela_preco,
            custo_atual=custo_atual,
            preco_atual=preco_atual,
            margem_atual_percentual=margem_atual,
            custo_proposto=custo_proposto,
            preco_proposto=preco_proposto,
            margem_proposta_percentual=margem_proposta,
            desconto_proposto=percentual_desconto,
            periodo_inicio=periodo_inicio_atual,
            periodo_termino=periodo_termino_atual,
        ):
            continue

        valores_insert = {
            "id_usuario": id_usuario_atual,
            "id_empresa_proprietaria": id_empresa_proprietaria_negociacao,
            "id_tabela_preco": id_tabela_preco,
            "id_empresa_relacionada": _para_int_ou_none(id_empresa_relacionada),
            "id_card": int(id_card),
            "id_fase_atual": _para_int_ou_none(id_fase_atual),
            "id_status_card": _para_int_ou_none(id_status_card),
            "observacoes_proposta": observacoes_proposta,
            "id_painel": int(id_painel),
            "id_face": int(id_face),
            "custo_atual": custo_atual,
            "preco_atual": preco_atual,
            "margem_atual": margem_atual,
            "custo_atual_rateado": custo_atual,
            "preco_atual_rateado": preco_atual,
            "margem_atual_rateado": margem_atual,
            "custo_proposto": custo_proposto,
            "preco_proposto": preco_proposto,
            "margem_proposta": margem_proposta,
            "custo_proposto_rateado": custo_proposto,
            "preco_proposto_rateado": preco_proposto,
            "desconto_proposto": percentual_desconto,
            "periodo_inicio": periodo_inicio_atual,
            "periodo_termino": periodo_termino_atual,
            "bit_autorizacao_diretoria": 1 if precisa_aprovacao_diretoria else 0,
        }

        sql_insert, parametros_insert = _montar_insert_dinamico(valores_insert)
        db.session.execute(text(sql_insert), parametros_insert)

    



def _listar_paineis_vinculados_card(id_card: int) -> list[dict[str, Any]]:
    sql = text("""
        SELECT
            r.IDFatoKanbanCardPainelFace,
            r.Ordem,
            r.IDDimPaineisEuromidia,
            r.IDDimFacesPaineis,
            r.CodPonto,
            r.CodFace,
            r.TipoPainel,
            r.AnoCusto,
            r.CustoTabela,
            r.IDDimTabelaPrecosEuromidia,
            r.PeriodoExibicao,
            r.ExibicoesDia,
            r.ValorTabela,
            r.Tabela,
            r.PoliticaTrocas,
            r.ValorTroca,
            r.NovoValor,
            r.PercentualDesconto,
            r.ValorVendaFinal,
            r.MargemValor,
            r.MargemPercentual,
            CONVERT(varchar(10), r.DataInicio, 23) AS DataInicio,
            CONVERT(varchar(10), r.DataFim, 23) AS DataFim,
            p.Logradouro,
            p.Cidade,
            p.UF,
            p.Bairro,
            p.Numero,
            p.QuantidadeFaces,
            rv.DataInicioReserva,
            rv.DataFimReserva
        FROM [Kanban].[Silver].[FatoKanbanCardPainelFace] r
        LEFT JOIN [Integracao].[Silver].[DimPaineisEuromidia] p
          ON p.IDDimPaineisEuromidia = r.IDDimPaineisEuromidia
        OUTER APPLY (
            SELECT TOP 1
                CONVERT(varchar(10), fo.DataInicio, 23) AS DataInicioReserva,
                CONVERT(varchar(10), fo.DataFim, 23) AS DataFimReserva
            FROM [Integracao].[Silver].[FatoOcupacaoPaineisEuromidia] fo
            WHERE UPPER(LTRIM(RTRIM(COALESCE(fo.CodFace, '')))) = UPPER(LTRIM(RTRIM(COALESCE(r.CodFace, ''))))
              AND fo.CanceladoEm IS NULL
              AND fo.Status IN ('ATIVO', 'RESERVADO')
              AND COALESCE(fo.Observacao, '') LIKE (
                    '[KANBAN_CARD=' + CONVERT(varchar(20), :id_card) + '][COD_FACE=' + UPPER(LTRIM(RTRIM(COALESCE(r.CodFace, '')))) + ']%'
                  )
            ORDER BY fo.CriadoEm DESC, fo.DataAtualizacao DESC
        ) rv
        WHERE r.IDFatoKanbanCard = :id_card
          AND r.Ativo = 1
        ORDER BY r.Ordem ASC, r.IDFatoKanbanCardPainelFace ASC;
    """)
    rows = db.session.execute(sql, {"id_card": int(id_card)}).mappings().all()
    return _rows_para_dicts(rows)





def _url_fallback_imagem_painel_orcamento() -> str:
    return url_for("static", filename="imagens/painel-publicitario.png")






def _normalizar_url_imagem_painel_orcamento(url_imagem: Any) -> str | None:
    texto = _normalizar_texto(url_imagem)
    if not texto:
        return None

    texto = str(texto).strip().replace("\\", "/")
    texto = re.sub(r"/+", "/", texto)

    if texto.startswith(("http://", "https://")):
        return texto

    caminho_static_url = getattr(current_app, "static_url_path", "/static") or "/static"
    caminho_static_url = "/" + caminho_static_url.strip("/")

    if texto.startswith(caminho_static_url + "/") or texto == caminho_static_url:
        return texto

    marcadores_static = (
        "/FlaskApp/app/static/",
        "/app/static/",
        "/app/app/static/",
        "/static/",
        "static/",
    )

    texto_lower = texto.lower()
    for marcador in marcadores_static:
        marcador_lower = marcador.lower()
        pos = texto_lower.find(marcador_lower)
        if pos >= 0:
            relativo = texto[pos + len(marcador):].lstrip("/")
            if relativo:
                return url_for("static", filename=relativo)

    padroes_relativos = (
        "orcamentopaineiseuromidia/",
        "imagens/",
        "painéis/",
        "paineis/",
    )

    texto_relativo = texto.lstrip("/")
    texto_relativo_lower = texto_relativo.lower()
    if any(texto_relativo_lower.startswith(prefixo) for prefixo in padroes_relativos):
        return url_for("static", filename=texto_relativo)

    if texto.startswith("/"):
        return texto

    return "/" + texto_relativo







def _montar_nome_painel_orcamento(item: dict[str, Any]) -> str:
    tipo_painel = _normalizar_texto(item.get("TipoPainel"))
    cod_ponto = _normalizar_texto(item.get("CodPonto"))
    cod_face = _normalizar_texto(item.get("CodFace"))

    partes: list[str] = []

    if tipo_painel:
        partes.append(tipo_painel)

    if cod_ponto:
        partes.append(f"Ponto {cod_ponto}")

    if cod_face:
        partes.append(f"Face {cod_face}")

    return " • ".join(partes) if partes else "Painel publicitário"


def _montar_endereco_painel_orcamento(item: dict[str, Any]) -> str:
    logradouro = _normalizar_texto(item.get("Logradouro"))
    numero = _normalizar_texto(item.get("Numero"))
    bairro = _normalizar_texto(item.get("Bairro"))
    cidade = _normalizar_texto(item.get("Cidade"))
    uf = _normalizar_texto(item.get("UF"))

    cidade_uf = "/".join([parte for parte in [cidade, uf] if parte])
    linha_1 = ", ".join([parte for parte in [logradouro, numero] if parte])
    linha_2 = " - ".join([parte for parte in [bairro, cidade_uf] if parte])

    endereco = " • ".join([parte for parte in [linha_1, linha_2] if parte])
    return endereco or "Endereço não informado"





def _url_cabecalho_orcamento() -> str:
    return url_for(
        "static",
        filename="OrcamentoPaineisEuromidia/cabecalho_orcamento.JPG",
    )


def _obter_imagens_painel_orcamento(
    *,
    id_face_painel: Any = None,
    cod_face: Any = None,
    cod_ponto: Any = None,
) -> list[dict[str, Any]]:
    id_face = int(id_face_painel or 0) if str(id_face_painel or "").strip() else None
    cod_face_norm = _normalizar_texto(cod_face).upper()
    cod_ponto_norm = _normalizar_texto(cod_ponto)

    sql = text("""
        WITH imagens_base AS (
            SELECT
                i.IDDimImagemPainel,
                i.IDDimFacesPaineis,
                i.UrlImagem,
                i.NumeroImagem,
                i.DataAtualizacao,
                i.BitAtivo,
                i.CodFace,
                i.CodPonto,
                i.BitImagemOrcamento,
                CASE
                    WHEN :id_face_painel IS NOT NULL
                         AND TRY_CONVERT(int, i.IDDimFacesPaineis) = TRY_CONVERT(int, :id_face_painel)
                    THEN 0
                    WHEN :cod_face <> ''
                         AND UPPER(LTRIM(RTRIM(COALESCE(i.CodFace, '')))) = :cod_face
                    THEN 1
                    WHEN :cod_ponto <> ''
                         AND LTRIM(RTRIM(COALESCE(i.CodPonto, ''))) = :cod_ponto
                    THEN 2
                    ELSE 9
                END AS OrdemCorrespondencia
            FROM [Integracao].[Silver].[DimImagemPainel] i
            WHERE ISNULL(i.BitAtivo, 1) = 1
              AND TRY_CONVERT(int, ISNULL(i.BitImagemOrcamento, 0)) = 1
              AND (
                    (:id_face_painel IS NOT NULL AND TRY_CONVERT(int, i.IDDimFacesPaineis) = TRY_CONVERT(int, :id_face_painel))
                 OR (:cod_face <> '' AND UPPER(LTRIM(RTRIM(COALESCE(i.CodFace, '')))) = :cod_face)
                 OR (:cod_ponto <> '' AND LTRIM(RTRIM(COALESCE(i.CodPonto, ''))) = :cod_ponto)
              )
        )
        SELECT
            IDDimImagemPainel,
            IDDimFacesPaineis,
            UrlImagem,
            NumeroImagem,
            DataAtualizacao,
            BitAtivo,
            CodFace,
            CodPonto,
            BitImagemOrcamento,
            OrdemCorrespondencia
        FROM imagens_base
        ORDER BY
            OrdemCorrespondencia ASC,
            CASE
                WHEN TRY_CONVERT(int, NumeroImagem) IS NULL THEN 999999
                ELSE TRY_CONVERT(int, NumeroImagem)
            END ASC,
            DataAtualizacao DESC,
            IDDimImagemPainel DESC
    """)

    rows = db.session.execute(
        sql,
        {
            "id_face_painel": id_face,
            "cod_face": cod_face_norm,
            "cod_ponto": cod_ponto_norm,
        },
    ).mappings().all()

    imagens: list[dict[str, Any]] = []
    urls_vistas: set[str] = set()

    for row in rows:
        registro = dict(row)
        url_imagem = _normalizar_url_imagem_painel_orcamento(registro.get("UrlImagem"))

        if not url_imagem:
            continue

        if url_imagem in urls_vistas:
            continue

        urls_vistas.add(url_imagem)

        imagens.append(
            {
                "id_imagem_painel": int(registro.get("IDDimImagemPainel") or 0) or None,
                "url": url_imagem,
                "numero_imagem": int(registro.get("NumeroImagem") or 0) or len(imagens) + 1,
                "cod_face": _normalizar_texto(registro.get("CodFace")),
                "cod_ponto": _normalizar_texto(registro.get("CodPonto")),
                "bit_imagem_orcamento": int(registro.get("BitImagemOrcamento") or 0),
                "ordem_correspondencia": int(registro.get("OrdemCorrespondencia") or 9),
                "fallback": False,
            }
        )

    if imagens:
        return imagens

    return [
        {
            "id_imagem_painel": None,
            "url": _url_fallback_imagem_painel_orcamento(),
            "numero_imagem": 1,
            "cod_face": cod_face_norm,
            "cod_ponto": cod_ponto_norm,
            "bit_imagem_orcamento": None,
            "ordem_correspondencia": 99,
            "fallback": True,
        }
    ]



def _montar_orcamento_card_payload(id_card: int) -> dict[str, Any]:
    detalhe = _obter_card_detalhe_payload(int(id_card))
    card = detalhe.get("card") if isinstance(detalhe.get("card"), dict) else {}
    painel_faces = detalhe.get("painel_faces") if isinstance(detalhe.get("painel_faces"), list) else []

    empresa = {
        "id_empresa": int(card.get("IDEmpresaRelacionadaCard") or 0) or None,
        "razao_social": _normalizar_texto(card.get("EmpresaRazaoSocial")),
        "cnpj": _normalizar_texto(card.get("EmpresaCNPJ")),
        "cnae": _normalizar_texto(card.get("EmpresaCNAE")),
        "setor": _normalizar_texto(card.get("EmpresaSetor")),
        "classe": _normalizar_texto(card.get("EmpresaClasse")),
    }

    itens_orcamento: list[dict[str, Any]] = []
    valor_total = Decimal("0")

    for indice, item in enumerate(painel_faces, start=1):
        if not isinstance(item, dict):
            continue

        valor_final = _valor_decimal(item.get("ValorVendaFinal"))
        preco_venda_atual = _valor_decimal(item.get("ValorTabela"))
        valor_exibido = valor_final if valor_final is not None else preco_venda_atual
        origem_preco = "Valor final" if valor_final is not None else "Preço de venda atual"

        if valor_exibido is not None:
            valor_total += valor_exibido

        imagens = _obter_imagens_painel_orcamento(
            id_face_painel=item.get("IDDimFacesPaineis"),
            cod_face=item.get("CodFace"),
            cod_ponto=item.get("CodPonto"),
        )

        itens_orcamento.append(
            {
                "indice": indice,
                "id_item": int(item.get("IDFatoKanbanCardPainelFace") or 0) or None,
                "id_painel": int(item.get("IDDimPaineisEuromidia") or 0) or None,
                "id_face_painel": int(item.get("IDDimFacesPaineis") or 0) or None,
                "nome_painel": _montar_nome_painel_orcamento(item),
                "tipo_painel": _normalizar_texto(item.get("TipoPainel")),
                "cod_ponto": _normalizar_texto(item.get("CodPonto")),
                "cod_face": _normalizar_texto(item.get("CodFace")),
                "endereco": _montar_endereco_painel_orcamento(item),
                "logradouro": _normalizar_texto(item.get("Logradouro")),
                "numero": _normalizar_texto(item.get("Numero")),
                "bairro": _normalizar_texto(item.get("Bairro")),
                "cidade": _normalizar_texto(item.get("Cidade")),
                "uf": _normalizar_texto(item.get("UF")),
                "periodo_exibicao": _normalizar_texto(item.get("PeriodoExibicao")),
                "exibicoes_dia": int(item.get("ExibicoesDia") or 0) or None,
                "tabela": _normalizar_texto(item.get("Tabela")),
                "politica_trocas": _normalizar_texto(item.get("PoliticaTrocas")),
                "valor_troca": _decimal_para_float(item.get("ValorTroca")),
                "preco_venda_atual": _decimal_para_float(preco_venda_atual),
                "valor_final": _decimal_para_float(valor_final),
                "valor_exibido": _decimal_para_float(valor_exibido),
                "origem_preco": origem_preco,
                "margem_percentual": _decimal_para_float(item.get("MargemPercentual")),
                "imagens": imagens,
                "quantidade_imagens": len(imagens),
                "tem_imagem_real": any(not bool(img.get("fallback")) for img in imagens),
            }
        )

    return {
        "ok": True,
        "id_card": int(id_card),
        "titulo_card": _normalizar_texto(card.get("Titulo")) or f"Card {int(id_card)}",
        "descricao_card": _normalizar_texto(card.get("Descricao")),
        "empresa": empresa,
        "cabecalho": {
            "url": _url_cabecalho_orcamento(),
            "alt": "Cabeçalho do orçamento Euromídia",
        },
        "itens": itens_orcamento,
        "resumo": {
            "quantidade_paineis": len(itens_orcamento),
            "valor_total": float(valor_total),
            "data_geracao": datetime.now().strftime("%d/%m/%Y %H:%M"),
        },
    }








def _obter_tags_do_card(id_card: int) -> list[dict[str, Any]]:
    sql_tags = text("""
        SELECT
            ct.IDFatoKanbanCard,
            ct.IDDimKanbanTag,
            t.NomeTag,
            t.CorHex,
            t.Icone
        FROM [Kanban].[Silver].[FatoKanbanCardTag] ct
        JOIN [Kanban].[Silver].[DimKanbanTag] t
          ON t.IDDimKanbanTag = ct.IDDimKanbanTag
        WHERE ct.IDFatoKanbanCard = :id_card
          AND ct.RemovidoEm IS NULL
          AND t.Ativo = 1
        ORDER BY t.NomeTag ASC;
    """)
    rows = db.session.execute(sql_tags, {"id_card": int(id_card)}).mappings().all()
    return _rows_para_dicts(rows)



def _obter_notas_do_card(id_card: int) -> list[dict[str, Any]]:
    if not _objeto_existe(TABELA_CARD_OBSERVACOES):
        return []

    sql_notas = text(f"""
        SELECT
            o.IDFatoKanbanCardObservacoes AS IDFatoKanbanCardNota,
            o.IDFatoKanbanCardObservacoes AS IDFatoKanbanCardObservacoes,
            CASE
                WHEN TRY_CONVERT(int, o.IDDimKanbanStatusCard) = 2 THEN 'INATIVACAO'
                ELSE 'OBS'
            END AS TipoNota,
            o.Observacao AS Texto,
            o.CriadoEm,
            o.IDDimUsuarios AS CriadoPor,
            CAST(NULL AS int) AS IDEmpresa,
            o.IDEmpresaProprietaria,
            o.IDDimKanbanStatusCard,
            o.IDDimKanbanFase
        FROM {TABELA_CARD_OBSERVACOES} o
        WHERE o.IDFatoKanbanCard = :id_card
        ORDER BY o.CriadoEm DESC, o.IDFatoKanbanCardObservacoes DESC;
    """)
    rows = db.session.execute(sql_notas, {"id_card": int(id_card)}).mappings().all()
    return _rows_para_dicts(rows)



def _obter_painel_faces_do_card(id_card: int) -> list[dict[str, Any]]:
    return _listar_paineis_vinculados_card(int(id_card))




def _obter_card_detalhe_payload(id_card: int) -> dict[str, Any]:
    print(f"[KANBAN][_obter_card_detalhe_payload] INICIO id_card={id_card}")
    current_app.logger.info("KANBAN: _obter_card_detalhe_payload iniciado. id_card=%s", id_card)

    card_escopo = _obter_card_autorizado(id_card)
    print(f"[KANBAN][_obter_card_detalhe_payload] card_escopo={card_escopo!r}")

    id_kanban = int(card_escopo.get("IDDimKanban") or 0)
    print(f"[KANBAN][_obter_card_detalhe_payload] id_kanban={id_kanban}")

    sql = text(f"""
        SELECT
            c.IDFatoKanbanCard,
            c.IDDimKanban,
            c.IDDimKanbanFaseAtual,
            c.Titulo,
            c.Descricao,
            c.StatusCard,
            c.CriadoEm,
            c.AtualizadoEm,
            c.VersaoConcorrencia AS VersaoConcorrencia,
            CONVERT(varchar(34), c.VersaoConcorrencia, 1) AS VersaoConcorrenciaHexSql,
            c.IDDimKanbanOrigem,
            c.IDDimKanbanMotivoEncerramento,
            c.MotivoEncerramentoObs,
            c.IDEmpresaProprietaria,
            {_sql_select_empresa_relacionada_card('c')},
            {_sql_select_usuario_relacionado_card('c')},
            {_sql_select_nome_usuario_relacionado_card('usuario')},
            e.RazaoSocial AS EmpresaRazaoSocial,
            e.CNPJ AS EmpresaCNPJ,
            e.CNAE AS EmpresaCNAE,
            cn.Classe AS EmpresaClasse,
            cn.Setor AS EmpresaSetor,
            rp.QuantidadePaineisVinculados,
            rp.QuantidadePaineisUnicos,
            rp.ValorTotalPaineis
        FROM {TABELA_CARD} c
        {_sql_join_empresa_relacionada_card('c', 'e', 'cn')}
        {_sql_join_usuario_relacionado_card('c', 'usuario')}
        {_sql_join_resumo_paineis_card('c', 'rp')}
        WHERE c.IDFatoKanbanCard = :id_card
          AND c.Ativo = 1;
    """)

    card = db.session.execute(sql, {"id_card": int(id_card)}).mappings().first()

    if not card:
        abort(404, "Card não encontrado")

    card_dict = dict(card)
    card_dict["QuantidadePaineisVinculados"] = int(card_dict.get("QuantidadePaineisVinculados") or 0)
    card_dict["QuantidadePaineisUnicos"] = int(card_dict.get("QuantidadePaineisUnicos") or 0)
    card_dict["ValorTotalPaineis"] = _decimal_para_float(card_dict.get("ValorTotalPaineis"))

    valor_versao_bruta = card_dict.pop("VersaoConcorrencia", None)
    valor_versao_hex_sql = card_dict.pop("VersaoConcorrenciaHexSql", None)

    print(
        "[KANBAN][_obter_card_detalhe_payload] valor_versao_bruta=",
        repr(valor_versao_bruta),
        " tipo=",
        type(valor_versao_bruta).__name__ if valor_versao_bruta is not None else None
    )
    print(
        "[KANBAN][_obter_card_detalhe_payload] valor_versao_hex_sql=",
        repr(valor_versao_hex_sql)
    )

    current_app.logger.info(
        "KANBAN: detalhe card id=%s | versao_bruta=%r | tipo=%s | versao_hex_sql=%r",
        id_card,
        valor_versao_bruta,
        type(valor_versao_bruta).__name__ if valor_versao_bruta is not None else None,
        valor_versao_hex_sql,
    )

    versao_hex_convertida = _rowversion_para_hex(valor_versao_bruta)
    if not versao_hex_convertida:
        versao_hex_convertida = _rowversion_para_hex(valor_versao_hex_sql)

    card_dict["VersaoConcorrenciaHex"] = versao_hex_convertida
    card_dict["VersaoConcorrencia"] = versao_hex_convertida

    sql_tags = text("""
        SELECT
            ct.IDFatoKanbanCard,
            ct.IDDimKanbanTag,
            t.NomeTag,
            t.CorHex,
            t.Icone
        FROM [Kanban].[Silver].[FatoKanbanCardTag] ct
        JOIN [Kanban].[Silver].[DimKanbanTag] t
          ON t.IDDimKanbanTag = ct.IDDimKanbanTag
        WHERE ct.IDFatoKanbanCard = :id_card
          AND ct.RemovidoEm IS NULL
          AND t.Ativo = 1
        ORDER BY t.NomeTag ASC;
    """)
    tags = db.session.execute(sql_tags, {"id_card": int(id_card)}).mappings().all()

    if _objeto_existe(TABELA_CARD_OBSERVACOES):
        sql_notas = text(f"""
            SELECT
                o.IDFatoKanbanCardObservacoes AS IDFatoKanbanCardNota,
                o.IDFatoKanbanCardObservacoes AS IDFatoKanbanCardObservacoes,
                CASE
                    WHEN TRY_CONVERT(int, o.IDDimKanbanStatusCard) = 2 THEN 'INATIVACAO'
                    ELSE 'OBS'
                END AS TipoNota,
                o.Observacao AS Texto,
                o.CriadoEm,
                o.IDDimUsuarios AS CriadoPor,
                CAST(NULL AS int) AS IDEmpresa,
                o.IDEmpresaProprietaria,
                o.IDDimKanbanStatusCard,
                o.IDDimKanbanFase
            FROM {TABELA_CARD_OBSERVACOES} o
            WHERE o.IDFatoKanbanCard = :id_card
            ORDER BY o.CriadoEm DESC, o.IDFatoKanbanCardObservacoes DESC;
        """)
        notas = db.session.execute(sql_notas, {"id_card": int(id_card)}).mappings().all()
    else:
        notas = []

    paineis_vinculados = _listar_paineis_vinculados_card(id_card)

    retorno = {
        "ok": True,
        "card": card_dict,
        "kanban_cfg": _obter_cfg_kanban(id_kanban),
        "tags": _rows_para_dicts(tags),
        "notas": _rows_para_dicts(notas),
        "paineis_vinculados": paineis_vinculados,
        "painel_faces": paineis_vinculados,
        "painelFaces": paineis_vinculados,
    }

    print(
        f"[KANBAN][_obter_card_detalhe_payload] FIM id_card={id_card} "
        f"versao={retorno['card'].get('VersaoConcorrenciaHex')!r}"
    )

    return retorno



@kanban_bp.route("/atendimento", methods=["GET"])
@login_required
def atendimento_redirect():
    _assert_login()
    id_emp = _id_empresa_usuario_or_403()

    sql_principal = text("""
        SELECT TOP 1 IDDimKanban
        FROM [Kanban].[Silver].[DimKanban]
        WHERE Ativo = 1
          AND IDEmpresaProprietaria = :id_emp
          AND BitPrincipal = 1
        ORDER BY CriadoEm DESC;
    """)
    id_kanban = db.session.execute(sql_principal, {"id_emp": id_emp}).scalar()

    if not id_kanban:
        sql_fallback = text("""
            SELECT TOP 1 IDDimKanban
            FROM [Kanban].[Silver].[DimKanban]
            WHERE Ativo = 1
              AND IDEmpresaProprietaria = :id_emp
            ORDER BY
              CASE WHEN BitPrincipal = 1 THEN 0 ELSE 1 END,
              CriadoEm DESC;
        """)
        id_kanban = db.session.execute(sql_fallback, {"id_emp": id_emp}).scalar()

    if not id_kanban:
        abort(404, "Nenhum kanban ativo encontrado para essa empresa")

    return redirect(url_for("kanban.kanban_view", id_kanban=int(id_kanban)))


@kanban_bp.route("/kanbans", methods=["GET"])
@login_required

def kanbans_lista():
    _log_debug_usuario()
    _assert_login()
    id_emp = _id_empresa_usuario_or_403()

    sql = text("""
        SELECT
            IDDimKanban,
            NomeKanban,
            Descricao,
            Ativo,
            CriadoEm
        FROM [Kanban].[Silver].[DimKanban]
        WHERE Ativo = 1
          AND IDEmpresaProprietaria = :id_emp
        ORDER BY CriadoEm DESC;
    """)
    kanbans = db.session.execute(sql, {"id_emp": id_emp}).mappings().all()
    return render_template("kanban/kanbans_lista.html", kanbans=kanbans)


@kanban_bp.route("/<int:id_kanban>", methods=["GET"])
@login_required

def kanban_view(id_kanban: int):
    _assert_login()
    try:
        kanban = _obter_kanban_autorizado(id_kanban)
        return render_template("kanban/kanban_view.html", kanban=kanban)
    except Exception as exc:
        current_app.logger.exception("Erro no kanban_view id_kanban=%s: %s", id_kanban, exc)
        return render_template("erros/500.html"), 500


@kanban_bp.route("/<int:id_kanban>/tags", methods=["GET"])
@login_required

def kanban_tags_view(id_kanban: int):
    _assert_login()
    kanban = _obter_kanban_autorizado(id_kanban)

    sql_tags = text("""
        SELECT
            IDDimKanbanTag, NomeTag, TipoTag, CorHex, Icone, AfetaCorCard,
            PodeVendedorAplicar, PodeAdminAplicar, AplicacaoUnica, Ativo
        FROM [Kanban].[Silver].[DimKanbanTag]
        WHERE IDDimKanban = :id_kanban
        ORDER BY Ativo DESC, NomeTag ASC;
    """)
    tags = db.session.execute(sql_tags, {"id_kanban": id_kanban}).mappings().all()
    return render_template("kanban/tags.html", kanban=kanban, tags=tags)




@kanban_bp.route("/api/kanbans", methods=["GET"])
@login_required
@limiter.limit("120/minute")
def api_kanbans_listar():
    _assert_login()
    id_emp = _id_empresa_usuario_or_403()

    chave = _chave_cache_json(
        "kanban:api:kanbans",
        id_emp,
        _versao_empresa(id_emp),
    )
    em_cache = _cache_json_get(chave)
    if em_cache is not None:
        return jsonify(em_cache)

    sql = text("""
        SELECT IDDimKanban, NomeKanban, Descricao, Ativo, CriadoEm
        FROM [Kanban].[Silver].[DimKanban]
        WHERE Ativo = 1
          AND IDEmpresaProprietaria = :id_emp
        ORDER BY CriadoEm DESC;
    """)
    rows = db.session.execute(sql, {"id_emp": id_emp}).mappings().all()
    payload = {"ok": True, "kanbans": _rows_para_dicts(rows)}
    _cache_json_set(chave, payload, TIMEOUT_CACHE_MEDIO)
    return jsonify(payload)









def _obter_resumo_comercial_kanban(id_kanban: int) -> dict[str, Any]:
    """
    Retorna o resumo comercial do quadro inteiro.

    Regras:
    - considera apenas cards ativos e com status visível no kanban
    - considera apenas vínculos painel/face ativos (foto atual do card)
    - contagem de tags usa DISTINCT por card para não duplicar quando houver mais de uma linha
    - margem percentual total é calculada de forma ponderada:
      (venda_total - custo_total) / venda_total * 100
      e não pela média simples das margens linha a linha
    """
    sql = text(f"""
        ;WITH CardsBase AS (
            SELECT c.IDFatoKanbanCard
            FROM {TABELA_CARD} c
            WHERE c.IDDimKanban = :id_kanban
              AND c.Ativo = 1
              {_sql_filtro_status_card_visiveis('c')}
        ),
        TagsAtivas AS (
            SELECT DISTINCT
                ct.IDFatoKanbanCard,
                UPPER(LTRIM(RTRIM(ISNULL(t.NomeTag, '')))) AS NomeTagNormalizado
            FROM [Kanban].[Silver].[FatoKanbanCardTag] ct
            INNER JOIN [Kanban].[Silver].[DimKanbanTag] t
                ON t.IDDimKanbanTag = ct.IDDimKanbanTag
            INNER JOIN CardsBase cb
                ON cb.IDFatoKanbanCard = ct.IDFatoKanbanCard
            WHERE ct.RemovidoEm IS NULL
              AND ISNULL(t.Ativo, 1) = 1
        ),
        PainelFaceAtivo AS (
            SELECT
                pf.IDFatoKanbanCard,
                CAST(ISNULL(pf.CustoTabela, 0) AS DECIMAL(18, 4)) AS ValorCustoLinha,
                CAST(
                    ISNULL(
                        pf.ValorVendaFinal,
                        ISNULL(pf.NovoValor, ISNULL(pf.ValorTabela, 0))
                    )
                    AS DECIMAL(18, 4)
                ) AS ValorVendaLinha
            FROM [Kanban].[Silver].[FatoKanbanCardPainelFace] pf
            INNER JOIN CardsBase cb
                ON cb.IDFatoKanbanCard = pf.IDFatoKanbanCard
            WHERE ISNULL(pf.Ativo, 1) = 1
        ),
        TotaisFinanceiros AS (
            SELECT
                CAST(ISNULL(SUM(ValorCustoLinha), 0) AS DECIMAL(18, 4)) AS ValorCustoTotal,
                CAST(ISNULL(SUM(ValorVendaLinha), 0) AS DECIMAL(18, 4)) AS ValorVendaTotal
            FROM PainelFaceAtivo
        )
        SELECT
            ISNULL((
                SELECT COUNT(DISTINCT ta.IDFatoKanbanCard)
                FROM TagsAtivas ta
                WHERE ta.NomeTagNormalizado IN ('EM ATENDIMENTO', 'ATENDIMENTO')
            ), 0) AS QuantidadeAtendimentosAtivos,
            ISNULL((
                SELECT COUNT(DISTINCT ta.IDFatoKanbanCard)
                FROM TagsAtivas ta
                WHERE ta.NomeTagNormalizado IN (
                    'APROVACAO DESCONTO',
                    'APROVAÇÃO DESCONTO',
                    'APROVACAO PRECO',
                    'APROVAÇÃO PREÇO'
                )
            ), 0) AS QuantidadeAprovacaoPreco,
            tf.ValorCustoTotal,
            tf.ValorVendaTotal,
            CAST(
                CASE
                    WHEN ISNULL(tf.ValorVendaTotal, 0) = 0 THEN 0
                    ELSE ((tf.ValorVendaTotal - tf.ValorCustoTotal) / NULLIF(tf.ValorVendaTotal, 0)) * 100
                END
                AS DECIMAL(18, 4)
            ) AS MargemPercentualTotal
        FROM TotaisFinanceiros tf;
    """)

    row = db.session.execute(sql, {"id_kanban": int(id_kanban)}).mappings().first() or {}

    return {
        "QuantidadeAtendimentosAtivos": int(row.get("QuantidadeAtendimentosAtivos") or 0),
        "QuantidadeAprovacaoPreco": int(row.get("QuantidadeAprovacaoPreco") or 0),
        "ValorCustoTotal": float(row.get("ValorCustoTotal") or 0),
        "ValorVendaTotal": float(row.get("ValorVendaTotal") or 0),
        "MargemPercentualTotal": float(row.get("MargemPercentualTotal") or 0),
    }







@kanban_bp.route("/api/kanbans/<int:id_kanban>/resumo-comercial", methods=["GET"])
@login_required
@limiter.limit("120/minute")
def api_kanban_resumo_comercial(id_kanban: int):
    _assert_login()
    _obter_kanban_autorizado(id_kanban)
    id_emp = _id_empresa_usuario_or_403()

    usar_cache = not _request_pede_dado_fresco()

    chave = _chave_cache_json(
        "kanban:api:resumo-comercial",
        id_emp,
        id_kanban,
        _versao_empresa(id_emp),
        _versao_kanban(id_kanban),
    )

    if usar_cache:
        em_cache = _cache_json_get(chave)
        if em_cache is not None:
            return jsonify(em_cache)

    payload = {
        "ok": True,
        "resumo_comercial": _obter_resumo_comercial_kanban(id_kanban),
    }

    if usar_cache:
        _cache_json_set(chave, payload, 5)

    return jsonify(payload)




def _obter_paineis_catalogo() -> list[dict[str, Any]]:
    chave = _chave_cache_json("kanban:catalogo:paineis")
    em_cache = _cache_json_get(chave)
    if em_cache is not None:
        return em_cache

    sql_paineis = text("""
        SELECT
            p.IDDimPaineisEuromidia,
            p.CodPonto,
            p.Tipo,
            p.Logradouro,
            p.Cidade,
            p.UF,
            p.Bairro,
            p.Numero,
            p.CEP,
            p.QuantidadeFaces,
            p.BitAtivo
        FROM [Integracao].[Silver].[DimPaineisEuromidia] p
        WHERE p.BitAtivo = 1
          AND p.CodPonto IS NOT NULL
          AND LTRIM(RTRIM(p.CodPonto)) <> ''
        ORDER BY p.UF ASC, p.Cidade ASC, p.Tipo ASC, p.CodPonto ASC;
    """)
    paineis = _rows_para_dicts(db.session.execute(sql_paineis).mappings().all())
    _cache_json_set(chave, paineis, TIMEOUT_CACHE_LONGO)
    return paineis





@kanban_bp.route("/api/kanbans/<int:id_kanban>/dados", methods=["GET"])
@login_required
@limiter.limit("120/minute")
def api_kanban_dados(id_kanban: int):
    _assert_login()
    kanban_cfg = _obter_cfg_kanban(id_kanban)
    id_emp = _id_empresa_usuario_or_403()

    try:
        limite_inicial_por_fase = int(request.args.get("limit_inicial") or 8)
    except Exception:
        return jsonify({"ok": False, "msg": "limit_inicial inválido"}), 400

    limite_inicial_por_fase = max(1, min(limite_inicial_por_fase, LIMITE_CARDS_POR_FASE))
    usar_cache = not _request_pede_dado_fresco()

    chave = _chave_cache_json(
        "kanban:api:dados",
        id_emp,
        id_kanban,
        limite_inicial_por_fase,
        _versao_empresa(id_emp),
        _versao_kanban(id_kanban),
    )

    if usar_cache:
        em_cache = _cache_json_get(chave)
        if em_cache is not None:
            return jsonify(em_cache)

    fases_base = _obter_fases_kanban(id_kanban)
    tags_catalogo = _obter_tags_kanban(id_kanban)
    vendedores_catalogo = _obter_vendedores_kanban(id_kanban)
    paineis_catalogo = _obter_paineis_catalogo() if kanban_cfg["MostrarPainelFaceNoCard"] else []

    sql_totais = text(f"""
        SELECT
            c.IDDimKanbanFaseAtual AS IDDimKanbanFase,
            COUNT(1) AS QuantidadeCardsTotal
        FROM {TABELA_CARD} c
        WHERE c.IDDimKanban = :id_kanban
          AND c.Ativo = 1
          {_sql_filtro_status_card_visiveis('c')}
          AND NOT EXISTS (
                SELECT 1
                FROM {TABELA_KANBAN_FASE} f_final
                WHERE f_final.IDDimKanbanFase = c.IDDimKanbanFaseAtual
                  AND f_final.IDDimKanban = c.IDDimKanban
                  AND ISNULL(f_final.Ativo, 1) = 1
                  AND (
                        LOWER(LTRIM(RTRIM(ISNULL(f_final.NomeFase, '')))) = 'concluido'
                     OR UPPER(LTRIM(RTRIM(ISNULL(f_final.TipoFase, '')))) = 'SUCESSO'
                  )
            )
        GROUP BY c.IDDimKanbanFaseAtual;
    """)
    rows_totais = db.session.execute(sql_totais, {"id_kanban": id_kanban}).mappings().all()
    mapa_totais_por_fase = {
        int(r["IDDimKanbanFase"]): int(r["QuantidadeCardsTotal"] or 0)
        for r in rows_totais
        if r.get("IDDimKanbanFase") is not None
    }

    sql_cards_iniciais = text(f"""
        ;WITH CardsOrdenados AS (
            SELECT
                c.IDFatoKanbanCard,
                c.IDDimKanban,
                c.IDDimKanbanFaseAtual,
                c.Titulo,
                c.StatusCard,
                c.CriadoEm,
                c.AtualizadoEm,
                {_sql_select_versao_concorrencia_card('c')},
                c.IDEmpresaProprietaria,
                {_sql_select_empresa_relacionada_card('c')},
                {_sql_select_usuario_relacionado_card('c')},
                {_sql_select_nome_usuario_relacionado_card('usuario')},
                e.RazaoSocial AS EmpresaRazaoSocial,
                e.CNPJ AS EmpresaCNPJ,
                e.CNAE AS EmpresaCNAE,
                cn.Classe AS EmpresaClasse,
                cn.Setor AS EmpresaSetor,
                rp.QuantidadePaineisVinculados,
                rp.QuantidadePaineisUnicos,
                rp.ValorTotalPaineis,
                ROW_NUMBER() OVER (
                    PARTITION BY c.IDDimKanbanFaseAtual
                    ORDER BY
                        CASE WHEN c.AtualizadoEm IS NULL THEN c.CriadoEm ELSE c.AtualizadoEm END DESC,
                        c.IDFatoKanbanCard DESC
                ) AS RowNumFase
            FROM {TABELA_CARD} c
            {_sql_join_empresa_relacionada_card('c', 'e', 'cn')}
            {_sql_join_usuario_relacionado_card('c', 'usuario')}
            {_sql_join_resumo_paineis_card('c', 'rp')}
            WHERE c.IDDimKanban = :id_kanban
              AND c.Ativo = 1
              {_sql_filtro_status_card_visiveis('c')}
              AND NOT EXISTS (
                    SELECT 1
                    FROM {TABELA_KANBAN_FASE} f_final
                    WHERE f_final.IDDimKanbanFase = c.IDDimKanbanFaseAtual
                      AND f_final.IDDimKanban = c.IDDimKanban
                      AND ISNULL(f_final.Ativo, 1) = 1
                      AND (
                            LOWER(LTRIM(RTRIM(ISNULL(f_final.NomeFase, '')))) = 'concluido'
                         OR UPPER(LTRIM(RTRIM(ISNULL(f_final.TipoFase, '')))) = 'SUCESSO'
                      )
                )
        )
        SELECT
            IDFatoKanbanCard,
            IDDimKanban,
            IDDimKanbanFaseAtual,
            Titulo,
            StatusCard,
            CriadoEm,
            AtualizadoEm,
            VersaoConcorrencia,
            IDEmpresaProprietaria,
            IDEmpresaRelacionadaCard,
            IDUsuarioRelacionadoCard,
            NomeUsuarioResponsavel,
            EmpresaRazaoSocial,
            EmpresaCNPJ,
            EmpresaCNAE,
            EmpresaClasse,
            EmpresaSetor,
            QuantidadePaineisVinculados,
            QuantidadePaineisUnicos,
            ValorTotalPaineis,
            RowNumFase
        FROM CardsOrdenados
        WHERE RowNumFase <= :limite_inicial_por_fase
        ORDER BY IDDimKanbanFaseAtual ASC, RowNumFase ASC, IDFatoKanbanCard DESC;
    """)

    rows_cards_iniciais = db.session.execute(
        sql_cards_iniciais,
        {
            "id_kanban": id_kanban,
            "limite_inicial_por_fase": limite_inicial_por_fase,
        },
    ).mappings().all()

    cards_iniciais: list[dict[str, Any]] = []
    mapa_carregados_por_fase: dict[int, int] = {}
    ids_cards_iniciais: list[int] = []

    for row in rows_cards_iniciais:
        card_dict = dict(row)

        card_dict["VersaoConcorrenciaHex"] = _rowversion_para_hex(
            card_dict.pop("VersaoConcorrencia", None)
        )
        card_dict["QuantidadePaineisVinculados"] = int(card_dict.get("QuantidadePaineisVinculados") or 0)
        card_dict["QuantidadePaineisUnicos"] = int(card_dict.get("QuantidadePaineisUnicos") or 0)
        card_dict["ValorTotalPaineis"] = _decimal_para_float(card_dict.get("ValorTotalPaineis"))

        cards_iniciais.append(card_dict)

        id_card = int(card_dict.get("IDFatoKanbanCard") or 0)
        if id_card:
            ids_cards_iniciais.append(id_card)

        id_fase_card = int(card_dict.get("IDDimKanbanFaseAtual") or 0)
        if id_fase_card:
            mapa_carregados_por_fase[id_fase_card] = int(mapa_carregados_por_fase.get(id_fase_card, 0)) + 1

    card_tags_iniciais: list[dict[str, Any]] = []

    if ids_cards_iniciais:
        placeholders = ", ".join(f":id_card_{idx}" for idx, _ in enumerate(ids_cards_iniciais))
        params_card_tags = {
            f"id_card_{idx}": int(id_card)
            for idx, id_card in enumerate(ids_cards_iniciais)
        }

        sql_card_tags = text(f"""
            SELECT
                ct.IDFatoKanbanCard,
                ct.IDDimKanbanTag,
                t.NomeTag,
                t.CorHex,
                t.Icone
            FROM [Kanban].[Silver].[FatoKanbanCardTag] ct
            JOIN [Kanban].[Silver].[DimKanbanTag] t
              ON t.IDDimKanbanTag = ct.IDDimKanbanTag
            WHERE ct.RemovidoEm IS NULL
              AND t.Ativo = 1
              AND ct.IDFatoKanbanCard IN ({placeholders})
            ORDER BY ct.IDFatoKanbanCard ASC, t.NomeTag ASC;
        """)
        card_tags_iniciais = _rows_para_dicts(
            db.session.execute(sql_card_tags, params_card_tags).mappings().all()
        )

    fases_payload: list[dict[str, Any]] = []

    for fase in fases_base:
        fase_dict = dict(fase)
        id_fase = int(fase_dict.get("IDDimKanbanFase") or 0)

        quantidade_total = int(mapa_totais_por_fase.get(id_fase, 0))
        quantidade_carregada = int(mapa_carregados_por_fase.get(id_fase, 0))

        fase_dict["QuantidadeCardsTotal"] = quantidade_total
        fase_dict["QuantidadeCardsCarregadosInicialmente"] = quantidade_carregada
        fase_dict["QuantidadeCardsRestantes"] = max(0, quantidade_total - quantidade_carregada)
        fase_dict["CargaInicialCompleta"] = quantidade_carregada >= quantidade_total

        fases_payload.append(fase_dict)

    payload = {
        "ok": True,
        "kanban_cfg": dict(kanban_cfg),
        "fases": fases_payload,
        "cards": cards_iniciais,
        "tags": tags_catalogo,
        "vendedores": vendedores_catalogo,
        "card_tags": card_tags_iniciais,
        "paineis": paineis_catalogo,
        "resumo_comercial": _obter_resumo_comercial_kanban(id_kanban),
        "limit_inicial_por_fase": limite_inicial_por_fase,
        "carga_parcial": True,
    }

    if usar_cache:
        _cache_json_set(chave, payload, TIMEOUT_CACHE_CURTO)

    return jsonify(payload)



@kanban_bp.route("/api/kanbans/<int:id_kanban>/fases", methods=["GET"])
@login_required
@limiter.limit("120/minute")
def api_fases_listar(id_kanban: int):
    _assert_login()
    _obter_kanban_autorizado(id_kanban)
    id_emp = _id_empresa_usuario_or_403()

    chave = _chave_cache_json(
        "kanban:api:fases",
        id_emp,
        id_kanban,
        _versao_kanban(id_kanban),
    )
    em_cache = _cache_json_get(chave)
    if em_cache is not None:
        return jsonify(em_cache)

    payload = {"ok": True, "fases": _obter_fases_kanban(id_kanban)}
    _cache_json_set(chave, payload, TIMEOUT_CACHE_CURTO)
    return jsonify(payload)





@kanban_bp.route("/api/kanbans/<int:id_kanban>/cards", methods=["GET"])
@login_required
@limiter.limit("180/minute")
def api_cards_listar_por_fase(id_kanban: int):
    _assert_login()
    _obter_kanban_autorizado(id_kanban)
    id_emp = _id_empresa_usuario_or_403()

    try:
        id_fase = int(request.args.get("id_fase") or 0)
    except Exception:
        return jsonify({"ok": False, "msg": "id_fase inválido"}), 400

    try:
        offset = max(int(request.args.get("offset") or 0), 0)
    except Exception:
        return jsonify({"ok": False, "msg": "offset inválido"}), 400

    try:
        limit = int(request.args.get("limit") or 30)
    except Exception:
        return jsonify({"ok": False, "msg": "limit inválido"}), 400

    limit = max(1, min(limit, LIMITE_CARDS_POR_FASE))

    if not id_fase or not _validar_fase_do_kanban(id_kanban, id_fase):
        return jsonify({"ok": False, "msg": "Fase inválida para este kanban"}), 400

    usar_cache = not _request_pede_dado_fresco()

    def _json_resposta(payload: dict, no_cache_http: bool = False):
        resposta = jsonify(payload)

        if no_cache_http:
            resposta.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
            resposta.headers["Pragma"] = "no-cache"
            resposta.headers["Expires"] = "0"

        return resposta

    chave = _chave_cache_json(
        "kanban:api:cards:fase",
        id_emp,
        id_kanban,
        id_fase,
        offset,
        limit,
        _versao_kanban(id_kanban),
    )

    if usar_cache:
        em_cache = _cache_json_get(chave)
        if em_cache is not None:
            return _json_resposta(em_cache)

    sql_total = text(f"""
        SELECT COUNT(1)
        FROM {TABELA_CARD} c
        WHERE c.IDDimKanban = :id_kanban
          AND c.IDDimKanbanFaseAtual = :id_fase
          AND c.Ativo = 1
          {_sql_filtro_status_card_visiveis('c')}
          AND NOT EXISTS (
                SELECT 1
                FROM {TABELA_KANBAN_FASE} f_final
                WHERE f_final.IDDimKanbanFase = c.IDDimKanbanFaseAtual
                  AND f_final.IDDimKanban = c.IDDimKanban
                  AND ISNULL(f_final.Ativo, 1) = 1
                  AND (
                        LOWER(LTRIM(RTRIM(ISNULL(f_final.NomeFase, '')))) = 'concluido'
                     OR UPPER(LTRIM(RTRIM(ISNULL(f_final.TipoFase, '')))) = 'SUCESSO'
                  )
            );
    """)

    total = int(
        db.session.execute(
            sql_total,
            {
                "id_kanban": id_kanban,
                "id_fase": id_fase,
            },
        ).scalar() or 0
    )

    sql_cards = text(f"""
        SELECT
            c.IDFatoKanbanCard,
            c.IDDimKanban,
            c.IDDimKanbanFaseAtual,
            c.Titulo,
            c.StatusCard,
            c.CriadoEm,
            c.AtualizadoEm,
            {_sql_select_versao_concorrencia_card('c')},
            c.IDEmpresaProprietaria,
            {_sql_select_empresa_relacionada_card('c')},
            {_sql_select_usuario_relacionado_card('c')},
            {_sql_select_nome_usuario_relacionado_card('usuario')},
            e.RazaoSocial AS EmpresaRazaoSocial,
            e.CNPJ AS EmpresaCNPJ,
            e.CNAE AS EmpresaCNAE,
            cn.Classe AS EmpresaClasse,
            cn.Setor AS EmpresaSetor,
            rp.QuantidadePaineisVinculados,
            rp.QuantidadePaineisUnicos,
            rp.ValorTotalPaineis
        FROM {TABELA_CARD} c
        {_sql_join_empresa_relacionada_card('c', 'e', 'cn')}
        {_sql_join_usuario_relacionado_card('c', 'usuario')}
        {_sql_join_resumo_paineis_card('c', 'rp')}
        WHERE c.IDDimKanban = :id_kanban
          AND c.IDDimKanbanFaseAtual = :id_fase
          AND c.Ativo = 1
          {_sql_filtro_status_card_visiveis('c')}
          AND NOT EXISTS (
                SELECT 1
                FROM {TABELA_KANBAN_FASE} f_final
                WHERE f_final.IDDimKanbanFase = c.IDDimKanbanFaseAtual
                  AND f_final.IDDimKanban = c.IDDimKanban
                  AND ISNULL(f_final.Ativo, 1) = 1
                  AND (
                        LOWER(LTRIM(RTRIM(ISNULL(f_final.NomeFase, '')))) = 'concluido'
                     OR UPPER(LTRIM(RTRIM(ISNULL(f_final.TipoFase, '')))) = 'SUCESSO'
                  )
            )
        ORDER BY
            CASE
                WHEN c.AtualizadoEm IS NULL THEN c.CriadoEm
                ELSE c.AtualizadoEm
            END DESC,
            c.IDFatoKanbanCard DESC
        OFFSET :offset ROWS FETCH NEXT :limit ROWS ONLY;
    """)

    rows_cards = db.session.execute(
        sql_cards,
        {
            "id_kanban": id_kanban,
            "id_fase": id_fase,
            "offset": offset,
            "limit": limit,
        },
    ).mappings().all()

    cards = []
    for row in rows_cards:
        card = dict(row)
        card["VersaoConcorrenciaHex"] = _rowversion_para_hex(
            card.pop("VersaoConcorrencia", None)
        )
        card["QuantidadePaineisVinculados"] = int(card.get("QuantidadePaineisVinculados") or 0)
        card["QuantidadePaineisUnicos"] = int(card.get("QuantidadePaineisUnicos") or 0)
        card["ValorTotalPaineis"] = _decimal_para_float(card.get("ValorTotalPaineis"))
        cards.append(card)

    payload = {
        "ok": True,
        "id_kanban": id_kanban,
        "id_fase": id_fase,
        "offset": offset,
        "limit": limit,
        "total": total,
        "cards": cards,
    }

    if usar_cache:
        _cache_json_set(chave, payload, TIMEOUT_CACHE_CURTO)

    return _json_resposta(payload, no_cache_http=not usar_cache)





@kanban_bp.route("/api/cards/<int:id_card>", methods=["GET"])
@login_required
@limiter.limit("180/minute")
def api_card_detalhe(id_card: int):
    _assert_login()

    id_emp = _id_empresa_usuario_or_403()
    card_escopo = _obter_card_autorizado(id_card)
    id_kanban = int(card_escopo.get("IDDimKanban") or 0)

    usar_cache = not _request_pede_dado_fresco()

    chave = _chave_cache_json(
        "kanban:api:card:detalhe",
        id_emp,
        id_kanban,
        id_card,
        _versao_kanban(id_kanban),
        _versao_card(id_card),
    )

    if usar_cache:
        em_cache = _cache_json_get(chave)
        if em_cache is not None:
            return jsonify(em_cache)

    payload = _obter_card_detalhe_payload(id_card)

    if not isinstance(payload, dict):
        payload = {"ok": True}

    card_payload = payload.get("card")
    if not isinstance(card_payload, dict):
        card_payload = {}

    versao_hex = (
        card_payload.get("VersaoConcorrenciaHex")
        or card_payload.get("versao_concorrencia")
        or card_payload.get("versaoConcorrencia")
        or card_payload.get("VersaoConcorrencia")
    )

    versao_hex = _normalizar_hex_sql(versao_hex)

    card_payload["VersaoConcorrenciaHex"] = versao_hex
    card_payload["VersaoConcorrencia"] = versao_hex
    card_payload["versaoConcorrencia"] = versao_hex
    card_payload["versao_concorrencia"] = versao_hex

    if card_payload.get("IDFatoKanbanCard") is None:
        card_payload["IDFatoKanbanCard"] = int(id_card)

    payload["card"] = card_payload

    if "painel_faces" not in payload:
        payload["painel_faces"] = payload.get("paineis_vinculados", []) or []

    if "painelFaces" not in payload:
        payload["painelFaces"] = payload.get("painel_faces", []) or []

    if usar_cache:
        _cache_json_set(chave, payload, TIMEOUT_CACHE_CURTO)

    return jsonify(payload)




@kanban_bp.route("/api/cards/<int:id_card>/orcamento", methods=["GET"])
@login_required
@limiter.limit("60/minute")
def api_card_orcamento(id_card: int):
    _assert_login()

    id_emp = _id_empresa_usuario_or_403()
    card_escopo = _obter_card_autorizado(id_card)
    id_kanban = int(card_escopo.get("IDDimKanban") or 0)

    usar_cache = not _request_pede_dado_fresco()

    chave = _chave_cache_json(
        "kanban:api:card:orcamento",
        id_emp,
        id_kanban,
        id_card,
        _versao_kanban(id_kanban),
        _versao_card(id_card),
    )

    if usar_cache:
        em_cache = _cache_json_get(chave)
        if em_cache is not None:
            return jsonify(em_cache)

    payload = _montar_orcamento_card_payload(id_card)

    if usar_cache:
        _cache_json_set(chave, payload, TIMEOUT_CACHE_CURTO)

    return jsonify(payload)








@kanban_bp.route("/api/empresas", methods=["GET"])
@login_required
@limiter.limit("60/minute")
def api_empresas_lista():
    _assert_login()

    chave = _chave_cache_json("kanban:api:empresas:lista")
    em_cache = _cache_json_get(chave)
    if em_cache is not None:
        return jsonify(em_cache)

    sql = text("""
        SELECT TOP 500
            e.IDEmpresa,
            e.RazaoSocial,
            e.CNPJ,
            e.CNAE,
            cn.Classe,
            cn.Setor
        FROM [Integracao].[Silver].[DimEmpresas] e
        LEFT JOIN [Integracao].[Silver].[DimCnaes] cn
          ON cn.cnaepadrao = e.CNAE
        WHERE e.RazaoSocial IS NOT NULL
          AND LTRIM(RTRIM(e.RazaoSocial)) <> ''
        ORDER BY e.RazaoSocial ASC;
    """)
    rows = db.session.execute(sql).mappings().all()
    payload = {"ok": True, "empresas": _rows_para_dicts(rows)}
    _cache_json_set(chave, payload, TIMEOUT_CACHE_LONGO)
    return jsonify(payload)


@kanban_bp.route("/api/empresas/buscar", methods=["GET"])
@login_required
@limiter.limit("120/minute")
def api_empresas_buscar():
    _assert_login()

    q = (request.args.get("q") or "").strip()
    if len(q) < 2:
        return jsonify({"ok": True, "empresas": []})

    q_digits = "".join([c for c in q if c.isdigit()])
    chave = _chave_cache_json("kanban:api:empresas:buscar", q.lower(), q_digits)
    em_cache = _cache_json_get(chave)
    if em_cache is not None:
        return jsonify(em_cache)

    sql = text("""
        SELECT TOP 25
            e.IDEmpresa,
            e.RazaoSocial,
            e.CNPJ,
            e.CNAE,
            c.Classe,
            c.Setor
        FROM [Integracao].[Silver].[DimEmpresas] e
        LEFT JOIN [Integracao].[Silver].[DimCnaes] c
          ON c.cnaepadrao = e.CNAE
        WHERE (e.RazaoSocial LIKE :q_like)
           OR (e.CNPJ LIKE :q_like)
           OR (:q_digits <> '' AND REPLACE(REPLACE(REPLACE(e.CNPJ,'.',''),'/',''),'-','') LIKE :q_digits_like)
        ORDER BY
            CASE WHEN e.RazaoSocial LIKE :q_like_inicio THEN 0 ELSE 1 END,
            e.RazaoSocial ASC;
    """)
    empresas = db.session.execute(
        sql,
        {
            "q_like": f"%{q}%",
            "q_like_inicio": f"{q}%",
            "q_digits": q_digits,
            "q_digits_like": f"%{q_digits}%",
        },
    ).mappings().all()

    payload = {"ok": True, "empresas": _rows_para_dicts(empresas)}
    _cache_json_set(chave, payload, TIMEOUT_CACHE_MEDIO)
    return jsonify(payload)


@kanban_bp.route("/api/paineis", methods=["GET"])
@login_required
@limiter.limit("60/minute")
def api_paineis_lista():
    _assert_login()
    return jsonify({"ok": True, "paineis": _obter_paineis_catalogo()})


@kanban_bp.route("/api/paineis/<int:cod_ponto>/faces", methods=["GET"])
@login_required
@limiter.limit("120/minute")
def api_faces_por_painel(cod_ponto: int):
    _assert_login()
    cod_ponto = int(cod_ponto or 0)
    if cod_ponto <= 0:
        return jsonify({"ok": False, "msg": "CodPonto inválido"}), 400

    chave = _chave_cache_json("kanban:api:faces", cod_ponto)
    em_cache = _cache_json_get(chave)
    if em_cache is not None:
        return jsonify(em_cache)

    sql = text("""
        SELECT
            f.CodFace,
            f.Face
        FROM [Integracao].[Silver].[DimFacesPaineis] f
        WHERE f.CodPonto = :cod_ponto
          AND f.CodFace IS NOT NULL
          AND LTRIM(RTRIM(f.CodFace)) <> ''
        GROUP BY f.CodFace, f.Face
        ORDER BY
            CASE WHEN f.Face IS NULL OR LTRIM(RTRIM(f.Face)) = '' THEN 1 ELSE 0 END,
            f.Face ASC,
            f.CodFace ASC;
    """)

    try:
        rows = db.session.execute(sql, {"cod_ponto": cod_ponto}).mappings().all()
    except Exception as exc:
        return jsonify({"ok": False, "msg": "Erro ao consultar faces do painel", "erro": str(exc)}), 500

    faces: list[dict[str, Any]] = []
    for row in rows:
        codface = str(row.get("CodFace") or "").strip()
        face = str(row.get("Face") or "").strip()
        if not codface:
            continue
        label = f"Face {face} • CodFace {codface}" if face else f"CodFace {codface}"
        faces.append({"CodFace": codface, "Face": face or None, "Label": label})

    payload = {"ok": True, "cod_ponto": cod_ponto, "total": len(faces), "faces": faces}
    _cache_json_set(chave, payload, TIMEOUT_CACHE_LONGO)
    return jsonify(payload)


@kanban_bp.route("/api/paineis/id/<int:id_painel>/faces", methods=["GET"])
@login_required
@limiter.limit("120/minute")
def api_faces_por_id_painel(id_painel: int):
    _assert_login()
    painel = _obter_painel_por_id(id_painel)
    if not painel:
        return jsonify({"ok": False, "msg": "Painel não encontrado"}), 404

    chave = _chave_cache_json("kanban:api:faces:id_painel", id_painel)
    em_cache = _cache_json_get(chave)
    if em_cache is not None:
        return jsonify(em_cache)

    sql = text("""
        SELECT
            f.IDDimFacesPaineis,
            f.CodFace,
            f.Face,
            f.Tipo
        FROM [Integracao].[Silver].[DimFacesPaineis] f
        WHERE f.CodFace IS NOT NULL
          AND LTRIM(RTRIM(f.CodFace)) <> ''
          AND (
                TRY_CONVERT(int, f.IDDimPaineisEuromidia) = TRY_CONVERT(int, :id_painel)
                OR (
                    TRY_CONVERT(int, f.CodPonto) = TRY_CONVERT(int, :cod_ponto)
                    AND UPPER(LTRIM(RTRIM(ISNULL(f.Tipo, '')))) = UPPER(LTRIM(RTRIM(:tipo_painel)))
                )
              )
        GROUP BY f.IDDimFacesPaineis, f.CodFace, f.Face, f.Tipo
        ORDER BY
            CASE WHEN f.Face IS NULL OR LTRIM(RTRIM(f.Face)) = '' THEN 1 ELSE 0 END,
            f.Face ASC,
            f.CodFace ASC;
    """)
    rows = db.session.execute(
        sql,
        {
            "id_painel": int(id_painel),
            "cod_ponto": painel.get("CodPonto"),
            "tipo_painel": _normalizar_texto(painel.get("Tipo")),
        },
    ).mappings().all()

    faces = []
    for row in rows:
        codface = _normalizar_texto(row.get("CodFace"))
        face = _normalizar_texto(row.get("Face"))
        if not codface:
            continue
        label = f"Face {face} • CodFace {codface}" if face else f"CodFace {codface}"
        faces.append(
            {
                "IDDimFacesPaineis": int(row.get("IDDimFacesPaineis") or 0) if row.get("IDDimFacesPaineis") is not None else None,
                "CodFace": codface,
                "Face": face or None,
                "Tipo": _normalizar_texto(row.get("Tipo")) or None,
                "Label": label,
            }
        )

    payload = {
        "ok": True,
        "id_painel": int(id_painel),
        "cod_ponto": painel.get("CodPonto"),
        "tipo_painel": painel.get("Tipo"),
        "total": len(faces),
        "faces": faces,
    }
    _cache_json_set(chave, payload, TIMEOUT_CACHE_LONGO)
    return jsonify(payload)


@kanban_bp.route("/api/paineis/id/<int:id_painel>/faces/<string:cod_face>/comercial", methods=["GET"])
@login_required
@limiter.limit("120/minute")
def api_comercial_painel_face(id_painel: int, cod_face: str):
    _assert_login()
    painel = _obter_painel_por_id(id_painel)
    if not painel:
        return jsonify({"ok": False, "msg": "Painel não encontrado"}), 404

    face = _resolver_face_do_painel(id_painel, cod_face)
    if not face:
        return jsonify({"ok": False, "msg": "Face não encontrada para o painel selecionado"}), 404

    custo = _obter_custo_por_codponto(int(painel.get("CodPonto") or 0))
    precos = _obter_precos_painel_face(
        id_painel=int(id_painel),
        id_dim_face=int(face.get("IDDimFacesPaineis") or 0) if face.get("IDDimFacesPaineis") is not None else None,
        tipo_painel=_normalizar_texto(painel.get("Tipo")),
    )

    precos_payload = []
    for row in precos:
        linha = {
            "IDDimTabelaPrecosEuromidia": int(row.get("IDDimTabelaPrecosEuromidia") or 0),
            "PeriodoExibicao": row.get("PeriodoExibicao"),
            "ExibicoesDia": row.get("ExibicoesDia"),
            "Valor": _decimal_para_float(row.get("Valor")),
            "Tabela": row.get("Tabela"),
            "PoliticaTrocas": row.get("PoliticaTrocas"),
            "ValorTroca": _decimal_para_float(row.get("ValorTroca")),
            "BitAtivo": int(row.get("BitAtivo") or 0),
            "DataPublicacao": row.get("DataPublicacao"),
            "DataValidade": row.get("DataValidade"),
        }
        precos_payload.append(linha)

    payload = {
        "ok": True,
        "painel": painel,
        "face": face,
        "custo": {
            "IDDimCustoPainel": int(custo.get("IDDimCustoPainel") or 0) if custo else None,
            "Ano": int(custo.get("Ano") or 0) if custo and custo.get("Ano") is not None else None,
            "CodPonto": int(custo.get("CodPonto") or 0) if custo and custo.get("CodPonto") is not None else None,
            "Origem": custo.get("Origem") if custo else None,
            "Valor": _decimal_para_float(custo.get("Valor")) if custo else None,
            "DataCarga": custo.get("DataCarga") if custo else None,
            "observacao": "O custo disponível hoje está na DimCustoPainel por CodPonto. Logo, ele é custo do painel/ponto, não custo específico da face.",
        },
        "precos": precos_payload,
    }
    return jsonify(payload)


@kanban_bp.route("/api/kanbans", methods=["POST"])
@login_required
@limiter.limit("30/minute")
def api_kanban_criar():
    id_usuario = _assert_login()
    id_emp = _id_empresa_usuario_or_403()

    payload = request.get_json(silent=True) or {}
    nome = (payload.get("nome") or "").strip()
    descricao = (payload.get("descricao") or "").strip()

    if len(nome) < 2:
        return jsonify({"ok": False, "msg": "Nome do kanban inválido"}), 400

    sql = text("""
        INSERT INTO [Kanban].[Silver].[DimKanban]
            (NomeKanban, Descricao, Ativo, CriadoEm, IDUsuario, IDEmpresaProprietaria)
        OUTPUT INSERTED.IDDimKanban
        VALUES
            (:nome, :descricao, 1, GETDATE(), :id_usuario, :id_emp);
    """)
    novo_id = db.session.execute(
        sql,
        {
            "nome": nome[:100],
            "descricao": descricao[:3000] if descricao else None,
            "id_usuario": id_usuario,
            "id_emp": id_emp,
        },
    ).scalar()

    db.session.commit()
    _invalidar_kanban(id_emp=id_emp, id_kanban=int(novo_id))

    _emitir_evento_kanban(
        int(novo_id),
        "kanban_criado",
        {
            "IDDimKanban": int(novo_id),
            "NomeKanban": nome[:100],
            "Descricao": descricao[:3000] if descricao else None,
            "IDEmpresaProprietaria": id_emp,
        },
    )

    return jsonify({"ok": True, "IDDimKanban": int(novo_id)})


@kanban_bp.route("/api/kanbans/<int:id_kanban>/fases", methods=["POST"])
@login_required
@limiter.limit("60/minute")
def api_fase_criar(id_kanban: int):
    id_usuario = _assert_login()
    id_emp = _id_empresa_usuario_or_403()
    _obter_kanban_autorizado(id_kanban)

    payload = request.get_json(silent=True) or {}
    nome = (payload.get("nome") or "").strip()
    tipo = _normalizar_codigo_dominio(payload.get("tipo")) or _obter_tipo_fase_padrao(id_kanban=id_kanban, id_emp=id_emp)
    ordem = payload.get("ordem")
    cor_hex = (payload.get("cor_hex") or "").strip() or None

    if len(nome) < 2:
        return jsonify({"ok": False, "msg": "Nome da fase inválido"}), 400
    tipos_fase_validos = _obter_tipos_fase_configurados(id_kanban=id_kanban, id_emp=id_emp)
    if tipos_fase_validos and tipo not in tipos_fase_validos:
        return jsonify({"ok": False, "msg": "TipoFase inválido", "tipos_fase_validos": tipos_fase_validos}), 400
    if cor_hex and not _cor_hex_valida(cor_hex):
        return jsonify({"ok": False, "msg": "CorHex inválida. Use #RRGGBB"}), 400

    if ordem is None:
        sql_max = text(f"""
            SELECT ISNULL(MAX(OrdemFase), 0) + 1
            FROM {TABELA_KANBAN_FASE}
            WHERE IDDimKanban = :id_kanban;
        """)
        ordem = int(db.session.execute(sql_max, {"id_kanban": id_kanban}).scalar() or 1)
    else:
        ordem = int(ordem)

    colunas = [
        "IDDimKanban",
        "NomeFase",
        "OrdemFase",
        "TipoFase",
        "Ativo",
        "CriadoEm",
        "IDUsuario",
        "IDEmpresaProprietaria",
    ]
    valores = [
        ":id_kanban",
        ":nome",
        ":ordem",
        ":tipo",
        "1",
        "GETDATE()",
        ":id_usuario",
        ":id_emp",
    ]
    params = {
        "id_kanban": id_kanban,
        "nome": nome[:100],
        "ordem": ordem,
        "tipo": tipo,
        "id_usuario": id_usuario,
        "id_emp": id_emp,
    }

    if _coluna_existe(TABELA_KANBAN_FASE, "CorHex"):
        colunas.append("CorHex")
        valores.append(":cor_hex")
        params["cor_hex"] = cor_hex

    sql = text(
        f"""
        INSERT INTO {TABELA_KANBAN_FASE}
            ({', '.join(colunas)})
        OUTPUT INSERTED.IDDimKanbanFase
        VALUES
            ({', '.join(valores)});
        """
    )
    novo_id = db.session.execute(sql, params).scalar()

    db.session.commit()
    _invalidar_kanban(id_emp=id_emp, id_kanban=id_kanban)

    _emitir_evento_kanban(
        id_kanban,
        "fase_criada",
        {
            "IDDimKanbanFase": int(novo_id),
            "NomeFase": nome[:100],
            "OrdemFase": ordem,
            "TipoFase": tipo,
            "CorHex": cor_hex,
        },
    )

    return jsonify({"ok": True, "IDDimKanbanFase": int(novo_id)})


@kanban_bp.route("/api/fases/<int:id_fase>", methods=["PUT"])
@login_required
@limiter.limit("120/minute")
def api_fase_atualizar(id_fase: int):
    id_usuario = _assert_login()
    id_emp = _id_empresa_usuario_or_403()
    fase = _obter_fase_autorizada(id_fase)
    id_kanban = int(fase.get("IDDimKanban") or 0)

    payload = request.get_json(silent=True) or {}
    nome = payload.get("nome")
    tipo = payload.get("tipo")
    cor_hex = payload.get("cor_hex")

    campos: list[str] = []
    params: dict[str, Any] = {"id_fase": id_fase}

    if nome is not None:
        nome = str(nome).strip()
        if len(nome) < 2:
            return jsonify({"ok": False, "msg": "Nome da fase inválido"}), 400
        campos.append("NomeFase = :nome")
        params["nome"] = nome[:100]

    if tipo is not None:
        tipo = str(tipo).strip().upper()
        tipos_fase_validos = _obter_tipos_fase_configurados(id_kanban=id_kanban, id_emp=id_emp)
        if tipos_fase_validos and tipo not in tipos_fase_validos:
            return jsonify({"ok": False, "msg": "TipoFase inválido", "tipos_fase_validos": tipos_fase_validos}), 400
        campos.append("TipoFase = :tipo")
        params["tipo"] = tipo

    if cor_hex is not None and _coluna_existe(TABELA_KANBAN_FASE, "CorHex"):
        cor_hex = str(cor_hex).strip() or None
        if cor_hex and not _cor_hex_valida(cor_hex):
            return jsonify({"ok": False, "msg": "CorHex inválida. Use #RRGGBB"}), 400
        campos.append("CorHex = :cor_hex")
        params["cor_hex"] = cor_hex

    if not campos:
        return jsonify({"ok": False, "msg": "Nenhuma alteração enviada para a fase"}), 400

    if _coluna_existe(TABELA_KANBAN_FASE, "AtualizadoEm"):
        campos.append("AtualizadoEm = GETDATE()")
    if _coluna_existe(TABELA_KANBAN_FASE, "AtualizadoPor"):
        campos.append("AtualizadoPor = :id_usuario")
        params["id_usuario"] = id_usuario

    try:
        sql = text(f"""
            UPDATE {TABELA_KANBAN_FASE}
            SET {', '.join(campos)}
            WHERE IDDimKanbanFase = :id_fase
              AND Ativo = 1;
        """)
        db.session.execute(sql, params)
        db.session.commit()

        _invalidar_kanban(id_emp=id_emp, id_kanban=id_kanban)
        _emitir_evento_kanban(
            id_kanban,
            "fase_atualizada",
            {
                "IDDimKanbanFase": id_fase,
                "NomeFase": params.get("nome", fase.get("NomeFase")),
                "TipoFase": params.get("tipo", fase.get("TipoFase")),
                "CorHex": params.get("cor_hex", fase.get("CorHex")),
            },
        )
        return jsonify({"ok": True})
    except Exception as exc:
        db.session.rollback()
        current_app.logger.exception("Erro ao atualizar fase id_fase=%s", id_fase)
        return jsonify({"ok": False, "msg": f"Erro ao atualizar fase: {str(exc)}"}), 500



@kanban_bp.route("/api/fases/reordenar", methods=["POST"])
@login_required
@limiter.limit("60/minute")
def api_fases_reordenar():
    _assert_login()
    id_emp = _id_empresa_usuario_or_403()

    payload = request.get_json(silent=True) or {}
    fases = payload.get("fases") or []

    if not isinstance(fases, list) or not fases:
        return jsonify({"ok": False, "msg": "Payload inválido"}), 400

    id_kanban_afetado: int | None = None
    fases_reordenadas: list[dict[str, int]] = []

    for item in fases:
        id_fase = int(item.get("id") or 0)
        ordem = int(item.get("ordem") or 0)
        if not id_fase or not ordem:
            continue

        fase = _obter_fase_autorizada(id_fase)
        id_kanban_fase = int(fase.get("IDDimKanban") or 0)
        if id_kanban_afetado is None:
            id_kanban_afetado = id_kanban_fase
        elif id_kanban_afetado != id_kanban_fase:
            return jsonify({"ok": False, "msg": "Não é permitido reordenar fases de kanbans diferentes"}), 400

        sql = text("""
            UPDATE [Kanban].[Silver].[DimKanbanFase]
            SET OrdemFase = :ordem
            WHERE IDDimKanbanFase = :id_fase;
        """)
        db.session.execute(sql, {"id_fase": id_fase, "ordem": ordem})
        fases_reordenadas.append({"id": id_fase, "ordem": ordem})

    db.session.commit()

    if id_kanban_afetado:
        _invalidar_kanban(id_emp=id_emp, id_kanban=id_kanban_afetado)
        _emitir_evento_kanban(
            id_kanban_afetado,
            "fases_reordenadas",
            {"fases": fases_reordenadas},
        )

    return jsonify({"ok": True})




def _negociacao_preco_foi_alterada(
    ultima: dict[str, Any] | None,
    *,
    id_tabela_preco: int | None,
    custo_atual: Any,
    preco_atual: Any,
    margem_atual_percentual: Any,
    custo_proposto: Any,
    preco_proposto: Any,
    margem_proposta_percentual: Any,
    desconto_proposto: Any,
    periodo_inicio: Any = None,
    periodo_termino: Any = None,
) -> bool:
    """
    Decide se houve mudança real na negociação de preço.

    Fundamento:
    - histórico não deve crescer por reenvio do mesmo payload
    - comparação decimal precisa ser normalizada para evitar falso positivo por formato
    - o período também faz parte da assinatura, porque agora ele é requisito de negócio
    - porém os campos de período ficam opcionais aqui para manter compatibilidade
      com chamadas antigas que ainda não foram atualizadas
    """

    def normalizar_int(valor: Any) -> int | None:
        if valor in (None, ""):
            return None
        try:
            return int(valor)
        except Exception:
            return None

    def normalizar_dec(valor: Any, casas: str = "0.0001") -> Decimal | None:
        dec = _valor_decimal(valor)
        if dec is None:
            return None
        try:
            return dec.quantize(Decimal(casas))
        except Exception:
            return dec

    def normalizar_data(valor: Any) -> date | None:
        return _normalizar_data_reserva_kanban(valor)

    def assinatura_fonte(
        *,
        id_tabela: Any,
        custo_atual_fonte: Any,
        preco_atual_fonte: Any,
        margem_atual_fonte: Any,
        custo_proposto_fonte: Any,
        preco_proposto_fonte: Any,
        margem_proposta_fonte: Any,
        desconto_proposto_fonte: Any,
        periodo_inicio_fonte: Any,
        periodo_termino_fonte: Any,
    ) -> tuple:
        return (
            normalizar_int(id_tabela),
            normalizar_dec(custo_atual_fonte),
            normalizar_dec(preco_atual_fonte),
            normalizar_dec(margem_atual_fonte),
            normalizar_dec(custo_proposto_fonte),
            normalizar_dec(preco_proposto_fonte),
            normalizar_dec(margem_proposta_fonte),
            normalizar_dec(desconto_proposto_fonte),
            normalizar_data(periodo_inicio_fonte),
            normalizar_data(periodo_termino_fonte),
        )

    assinatura_atual = assinatura_fonte(
        id_tabela=id_tabela_preco,
        custo_atual_fonte=custo_atual,
        preco_atual_fonte=preco_atual,
        margem_atual_fonte=margem_atual_percentual,
        custo_proposto_fonte=custo_proposto,
        preco_proposto_fonte=preco_proposto,
        margem_proposta_fonte=margem_proposta_percentual,
        desconto_proposto_fonte=desconto_proposto,
        periodo_inicio_fonte=periodo_inicio,
        periodo_termino_fonte=periodo_termino,
    )

    if not ultima:
        return True

    assinatura_ultima = assinatura_fonte(
        id_tabela=ultima.get("IDDimTabelaPrecosEuromidia"),
        custo_atual_fonte=ultima.get("CustoAtual"),
        preco_atual_fonte=ultima.get("PrecoAtual"),
        margem_atual_fonte=ultima.get("MargemAtual"),
        custo_proposto_fonte=ultima.get("CustoProposto"),
        preco_proposto_fonte=ultima.get("PrecoProposto"),
        margem_proposta_fonte=ultima.get("MargemProposta"),
        desconto_proposto_fonte=ultima.get("DescontoProposto"),
        periodo_inicio_fonte=ultima.get("PeriodoInicio"),
        periodo_termino_fonte=ultima.get("PeriodoTermino"),
    )

    return assinatura_atual != assinatura_ultima


def _registrar_negociacao_preco_card(
    *,
    id_card: int,
    id_kanban: int,
    id_fase_atual: int | None,
    status_card: str | None,
    id_empresa_relacionada: int | None,
    vinculos_preparados: list[dict[str, Any]],
    observacoes_proposta: str | None = None,
) -> None:
    """
    Grava histórico de negociação de preço sem duplicar linha quando nada mudou.

    Fundamento desta versão:
    - a fonte principal é o estado operacional já persistido
    - quando algum campo ainda não vier preenchido no estado salvo, eu faço fallback
      para o payload vinculos_preparados da própria requisição
    - o período participa da assinatura e também do INSERT
    - não gravo histórico duplicado quando nada mudou
    """

    if not vinculos_preparados:
        return

    def _tem_valor_informado(valor: Any) -> bool:
        if valor is None:
            return False
        if isinstance(valor, str) and not valor.strip():
            return False
        return True

    def _para_int_ou_none(valor: Any) -> int | None:
        if valor in (None, ""):
            return None
        try:
            return int(valor)
        except (TypeError, ValueError):
            return None

    def _para_decimal_ou_none(valor: Any) -> Decimal | None:
        if valor in (None, ""):
            return None
        return _valor_decimal(valor)

    def _para_data_ou_none(valor: Any) -> date | None:
        return _normalizar_data_reserva_kanban(valor)

    def _primeiro_valor_preenchido(*valores: Any) -> Any:
        for valor in valores:
            if _tem_valor_informado(valor):
                return valor
        return None

    def _calcular_margem_percentual(custo: Any, preco: Any) -> Decimal | None:
        custo_dec = _valor_decimal(custo)
        preco_dec = _valor_decimal(preco)

        if custo_dec is None:
            return None
        if preco_dec in (None, Decimal("0")):
            return None

        return ((preco_dec - custo_dec) / preco_dec) * Decimal("100")

    def _montar_insert_dinamico(valores: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        colunas: list[str] = []
        marcadores: list[str] = []
        parametros: dict[str, Any] = {}

        def adicionar(
            nome_coluna: str,
            nome_parametro: str,
            valor: Any,
            usar_getdate: bool = False,
        ) -> None:
            if not _coluna_existe(TABELA_CARD_NEGOCIACAO_PRECO, nome_coluna):
                return

            colunas.append(nome_coluna)

            if usar_getdate:
                marcadores.append("GETDATE()")
            else:
                marcadores.append(f":{nome_parametro}")
                parametros[nome_parametro] = valor

        adicionar("IDDimUsuarios", "id_usuario", valores.get("id_usuario"))
        adicionar("IDEmpresaProprietaria", "id_empresa_proprietaria", valores.get("id_empresa_proprietaria"))
        adicionar("IDDimTabelaPrecosEuromidia", "id_tabela_preco", valores.get("id_tabela_preco"))
        adicionar("IDEmpresa", "id_empresa_relacionada", valores.get("id_empresa_relacionada"))
        adicionar("IDFatoKanbanCard", "id_card", valores.get("id_card"))
        adicionar("IDDimKanbanFase", "id_fase_atual", valores.get("id_fase_atual"))
        adicionar("IDDimKanbanStatusCard", "id_status_card", valores.get("id_status_card"))
        adicionar("IDFatoControleContratosEuromidia", "id_controle_contrato", None)
        adicionar("BitAditivoContrato", "bit_aditivo", 0)
        adicionar("ObservacoesProposta", "observacoes_proposta", valores.get("observacoes_proposta"))
        adicionar("IDDimPaineisEuromidia", "id_painel", valores.get("id_painel"))
        adicionar("IDDimFacesPaineis", "id_face", valores.get("id_face"))
        adicionar("DataPrecoProposto", "data_preco_proposto", None, usar_getdate=True)

        adicionar("CustoAtual", "custo_atual", valores.get("custo_atual"))
        adicionar("PrecoAtual", "preco_atual", valores.get("preco_atual"))
        adicionar("MargemAtual", "margem_atual", valores.get("margem_atual"))

        adicionar("CustoAtualRateado", "custo_atual_rateado", valores.get("custo_atual_rateado"))
        adicionar("PrecoAtualRateado", "preco_atual_rateado", valores.get("preco_atual_rateado"))
        adicionar("MargemAtualRateado", "margem_atual_rateado", valores.get("margem_atual_rateado"))

        adicionar("CustoProposto", "custo_proposto", valores.get("custo_proposto"))
        adicionar("PrecoProposto", "preco_proposto", valores.get("preco_proposto"))
        adicionar("MargemProposta", "margem_proposta", valores.get("margem_proposta"))

        adicionar("CustoPropostoRateado", "custo_proposto_rateado", valores.get("custo_proposto_rateado"))
        adicionar("PrecoPropostoRateado", "preco_proposto_rateado", valores.get("preco_proposto_rateado"))

        adicionar("DescontoProposto", "desconto_proposto", valores.get("desconto_proposto"))
        adicionar("PeriodoInicio", "periodo_inicio", valores.get("periodo_inicio"))
        adicionar("PeriodoTermino", "periodo_termino", valores.get("periodo_termino"))

        adicionar("IDDimUsuariosAprovacaoPreco", "id_usuario_aprovacao", None)
        adicionar("DataAprovacaoPreco", "data_aprovacao", None)
        adicionar("PrecoAprovado", "preco_aprovado", None)
        adicionar("DescontoAprovado", "desconto_aprovado", None)
        adicionar("ObservacoesAprovacao", "observacoes_aprovacao", None)
        adicionar("BitAutorizacaoDiretoria", "bit_autorizacao_diretoria", valores.get("bit_autorizacao_diretoria", 0))

        if not colunas:
            raise ValueError(
                "Nenhuma coluna válida encontrada em FatoKanbanNegociacaoPreco para gravar a negociação."
            )

        sql = f"""
            INSERT INTO {TABELA_CARD_NEGOCIACAO_PRECO}
            (
                {", ".join(colunas)}
            )
            VALUES
            (
                {", ".join(marcadores)}
            );
        """
        return sql, parametros

    id_status_card = _obter_id_status_card_por_codigo(status_card)
    id_usuario_atual = _id_usuario()
    id_empresa_proprietaria_negociacao = _resolver_id_empresa_proprietaria_movimento(
        id_kanban=id_kanban,
        id_empresa_padrao=_id_empresa_usuario_or_403(),
    )

    mapa_vinculos_preparados: dict[tuple[int, int], dict[str, Any]] = {}
    for vinculo in vinculos_preparados:
        id_painel_vinculo = _para_int_ou_none(
            _primeiro_valor_preenchido(
                vinculo.get("IDDimPaineisEuromidia"),
                vinculo.get("id_painel"),
                vinculo.get("IDPainel"),
                vinculo.get("idPainel"),
            )
        )
        id_face_vinculo = _para_int_ou_none(
            _primeiro_valor_preenchido(
                vinculo.get("IDDimFacesPaineis"),
                vinculo.get("id_face"),
                vinculo.get("IDFace"),
                vinculo.get("idFace"),
            )
        )

        if not id_painel_vinculo or not id_face_vinculo:
            continue

        mapa_vinculos_preparados[(int(id_painel_vinculo), int(id_face_vinculo))] = vinculo

    estados_atuais = _listar_estado_atual_negociacao_card(int(id_card))
    if not estados_atuais:
        estados_atuais = []

    chaves_processadas: set[tuple[int, int]] = set()

    for estado in estados_atuais:
        id_painel = _para_int_ou_none(estado.get("IDDimPaineisEuromidia"))
        id_face = _para_int_ou_none(estado.get("IDDimFacesPaineis"))
        if not id_painel or not id_face:
            continue

        chave_painel_face = (int(id_painel), int(id_face))
        if chave_painel_face in chaves_processadas:
            continue
        chaves_processadas.add(chave_painel_face)

        vinculo_payload = mapa_vinculos_preparados.get(chave_painel_face, {})

        id_tabela_preco = _para_int_ou_none(
            _primeiro_valor_preenchido(
                estado.get("IDDimTabelaPrecosEuromidia"),
                vinculo_payload.get("IDDimTabelaPrecosEuromidia"),
                vinculo_payload.get("id_tabela_preco"),
                vinculo_payload.get("IDTabelaPreco"),
                vinculo_payload.get("idTabelaPreco"),
            )
        )

        custo_atual = _para_decimal_ou_none(
            _primeiro_valor_preenchido(
                estado.get("CustoTabela"),
                vinculo_payload.get("CustoTabela"),
                vinculo_payload.get("custo_tabela"),
                vinculo_payload.get("custoAtual"),
            )
        )

        preco_atual = _para_decimal_ou_none(
            _primeiro_valor_preenchido(
                estado.get("ValorTabela"),
                vinculo_payload.get("ValorTabela"),
                vinculo_payload.get("valor_tabela"),
                vinculo_payload.get("precoAtual"),
            )
        )

        margem_atual = _calcular_margem_percentual(custo_atual, preco_atual)

        novo_valor = _para_decimal_ou_none(
            _primeiro_valor_preenchido(
                estado.get("NovoValor"),
                vinculo_payload.get("NovoValor"),
                vinculo_payload.get("novo_valor"),
                vinculo_payload.get("novoValor"),
            )
        )

        percentual_desconto = _para_decimal_ou_none(
            _primeiro_valor_preenchido(
                estado.get("PercentualDesconto"),
                vinculo_payload.get("PercentualDesconto"),
                vinculo_payload.get("percentual_desconto"),
                vinculo_payload.get("percentualDesconto"),
                vinculo_payload.get("DescontoProposto"),
                vinculo_payload.get("desconto_proposto"),
            )
        )

        valor_venda_final = _para_decimal_ou_none(
            _primeiro_valor_preenchido(
                estado.get("ValorVendaFinal"),
                vinculo_payload.get("ValorVendaFinal"),
                vinculo_payload.get("valor_venda_final"),
                vinculo_payload.get("valorVendaFinal"),
                vinculo_payload.get("PrecoProposto"),
                vinculo_payload.get("preco_proposto"),
            )
        )

        periodo_inicio_atual = _para_data_ou_none(
            _primeiro_valor_preenchido(
                estado.get("DataInicio"),
                estado.get("PeriodoInicio"),
                vinculo_payload.get("DataInicio"),
                vinculo_payload.get("PeriodoInicio"),
                vinculo_payload.get("periodo_inicio"),
                vinculo_payload.get("data_inicio"),
                vinculo_payload.get("dataInicio"),
            )
        )

        periodo_termino_atual = _para_data_ou_none(
            _primeiro_valor_preenchido(
                estado.get("DataFim"),
                estado.get("PeriodoTermino"),
                vinculo_payload.get("DataFim"),
                vinculo_payload.get("PeriodoTermino"),
                vinculo_payload.get("periodo_termino"),
                vinculo_payload.get("data_fim"),
                vinculo_payload.get("dataFim"),
            )
        )

        preco_proposto = novo_valor
        if preco_proposto is None:
            preco_proposto = valor_venda_final
        if preco_proposto is None:
            preco_proposto = preco_atual

        custo_proposto = custo_atual
        margem_proposta = _calcular_margem_percentual(custo_proposto, preco_proposto)

        estado_completo_para_regra = dict(estado)
        if vinculo_payload:
            for chave, valor in vinculo_payload.items():
                if chave not in estado_completo_para_regra or not _tem_valor_informado(estado_completo_para_regra.get(chave)):
                    estado_completo_para_regra[chave] = valor

        if periodo_inicio_atual is not None:
            estado_completo_para_regra["DataInicio"] = periodo_inicio_atual
            estado_completo_para_regra["PeriodoInicio"] = periodo_inicio_atual

        if periodo_termino_atual is not None:
            estado_completo_para_regra["DataFim"] = periodo_termino_atual
            estado_completo_para_regra["PeriodoTermino"] = periodo_termino_atual

        precisa_aprovacao_diretoria = _estado_precisa_aprovacao_diretoria(estado_completo_para_regra)

        tem_operacao_comercial_ou_periodo = any(
            _tem_valor_informado(valor)
            for valor in (
                id_tabela_preco,
                novo_valor,
                percentual_desconto,
                valor_venda_final,
                preco_atual,
                periodo_inicio_atual,
                periodo_termino_atual,
            )
        )
        if not tem_operacao_comercial_ou_periodo:
            continue

        ultima_negociacao = _buscar_ultima_negociacao_preco_card(
            id_card=int(id_card),
            id_painel=int(id_painel),
            id_face=int(id_face),
        )

        if not _negociacao_preco_foi_alterada(
            ultima_negociacao,
            id_tabela_preco=id_tabela_preco,
            custo_atual=custo_atual,
            preco_atual=preco_atual,
            margem_atual_percentual=margem_atual,
            custo_proposto=custo_proposto,
            preco_proposto=preco_proposto,
            margem_proposta_percentual=margem_proposta,
            desconto_proposto=percentual_desconto,
            periodo_inicio=periodo_inicio_atual,
            periodo_termino=periodo_termino_atual,
        ):
            continue

        valores_insert = {
            "id_usuario": id_usuario_atual,
            "id_empresa_proprietaria": id_empresa_proprietaria_negociacao,
            "id_tabela_preco": id_tabela_preco,
            "id_empresa_relacionada": _para_int_ou_none(id_empresa_relacionada),
            "id_card": int(id_card),
            "id_fase_atual": _para_int_ou_none(id_fase_atual),
            "id_status_card": _para_int_ou_none(id_status_card),
            "observacoes_proposta": observacoes_proposta,
            "id_painel": int(id_painel),
            "id_face": int(id_face),
            "custo_atual": custo_atual,
            "preco_atual": preco_atual,
            "margem_atual": margem_atual,
            "custo_atual_rateado": custo_atual,
            "preco_atual_rateado": preco_atual,
            "margem_atual_rateado": margem_atual,
            "custo_proposto": custo_proposto,
            "preco_proposto": preco_proposto,
            "margem_proposta": margem_proposta,
            "custo_proposto_rateado": custo_proposto,
            "preco_proposto_rateado": preco_proposto,
            "desconto_proposto": percentual_desconto,
            "periodo_inicio": periodo_inicio_atual,
            "periodo_termino": periodo_termino_atual,
            "bit_autorizacao_diretoria": 1 if precisa_aprovacao_diretoria else 0,
        }

        sql_insert, parametros_insert = _montar_insert_dinamico(valores_insert)
        db.session.execute(text(sql_insert), parametros_insert)





    

@kanban_bp.route("/api/cards/<int:id_card>/mover", methods=["POST"])
@login_required
@limiter.limit("120/minute")
def api_card_mover(id_card: int):
    id_usuario = _assert_login()
    id_emp = _id_empresa_usuario_or_403()

    try:
        row = _obter_card_autorizado(id_card)
        if not row:
            return jsonify({"ok": False, "msg": "Card não encontrado."}), 404

        id_kanban = int(row.get("IDDimKanban") or 0)
        id_fase_de = int(row.get("IDDimKanbanFaseAtual") or 0)

        payload = request.get_json(silent=True) or {}
        id_fase_para = int(payload.get("id_fase_para") or 0)
        posicao = str(payload.get("posicao") or "LAST").strip().upper()
        observacao = (payload.get("observacao") or "").strip()
        nota_movimento = (payload.get("nota_movimento") or "").strip()
        versao_concorrencia = payload.get("versao_concorrencia")

        if not id_fase_para:
            return jsonify({"ok": False, "msg": "Fase de destino inválida."}), 400

        _obter_kanban_autorizado(id_kanban)

        fase_destino = db.session.execute(
            text(f"""
                SELECT TOP (1)
                    f.IDDimKanbanFase,
                    f.NomeFase,
                    f.TipoFase,
                    f.IDEmpresaProprietaria
                FROM {TABELA_KANBAN_FASE} f
                WHERE f.IDDimKanbanFase = :id_fase_para
                  AND f.IDDimKanban = :id_kanban
                  AND f.Ativo = 1;
            """),
            {
                "id_fase_para": id_fase_para,
                "id_kanban": id_kanban,
            },
        ).mappings().first()

        if not fase_destino:
            return jsonify({"ok": False, "msg": "Fase de destino não encontrada."}), 404

        snapshot_antes = _obter_snapshot_card_log(id_card, incluir_inativo=True)

        has_ordem = _coluna_existe(TABELA_CARD, "OrdemNaFase")
        has_atualizado = _coluna_existe(TABELA_CARD, "AtualizadoEm")
        has_versao = _card_tem_versao_concorrencia()

        versao_concorrencia_bytes = None
        if has_versao:
            versao_concorrencia_bytes = _rowversion_hex_para_bytes(versao_concorrencia)
            if not versao_concorrencia_bytes:
                detalhe_atual = _obter_card_detalhe_payload(id_card)
                return (
                    jsonify(
                        {
                            "ok": False,
                            "msg": "Versão de concorrência inválida ou ausente.",
                            "card_atual": detalhe_atual.get("card"),
                        }
                    ),
                    409,
                )

        proxima_ordem = None
        if has_ordem:
            sql_next_ordem = text(f"""
                SELECT ISNULL(MAX(fc.OrdemNaFase), 0) + 1
                FROM {TABELA_CARD} fc
                WHERE fc.IDDimKanban = :id_kanban
                  AND fc.IDDimKanbanFaseAtual = :id_fase_para
                  AND fc.Ativo = 1;
            """)
            proxima_ordem = db.session.execute(
                sql_next_ordem,
                {
                    "id_kanban": id_kanban,
                    "id_fase_para": id_fase_para,
                },
            ).scalar()

            try:
                proxima_ordem = int(proxima_ordem or 1)
            except Exception:
                proxima_ordem = 1

        row_upd = None

        if has_ordem and has_atualizado:
            output_versao = ", INSERTED.VersaoConcorrencia" if has_versao else ""
            where_versao = " AND VersaoConcorrencia = :versao_concorrencia" if has_versao else ""
            params_upd = {
                "id_fase_para": id_fase_para,
                "ordem_na_fase": proxima_ordem,
                "id_card": id_card,
            }
            if has_versao:
                params_upd["versao_concorrencia"] = versao_concorrencia_bytes

            sql_upd = text(f"""
                UPDATE {TABELA_CARD}
                SET IDDimKanbanFaseAtual = :id_fase_para,
                    OrdemNaFase = :ordem_na_fase,
                    AtualizadoEm = GETDATE()
                OUTPUT
                    INSERTED.IDFatoKanbanCard,
                    INSERTED.IDDimKanbanFaseAtual,
                    INSERTED.OrdemNaFase,
                    INSERTED.AtualizadoEm{output_versao}
                WHERE IDFatoKanbanCard = :id_card
                  AND Ativo = 1{where_versao};
            """)
            row_upd = db.session.execute(sql_upd, params_upd).mappings().first()

        elif has_ordem and not has_atualizado:
            output_versao = ", INSERTED.VersaoConcorrencia" if has_versao else ""
            where_versao = " AND VersaoConcorrencia = :versao_concorrencia" if has_versao else ""
            params_upd = {
                "id_fase_para": id_fase_para,
                "ordem_na_fase": proxima_ordem,
                "id_card": id_card,
            }
            if has_versao:
                params_upd["versao_concorrencia"] = versao_concorrencia_bytes

            sql_upd = text(f"""
                UPDATE {TABELA_CARD}
                SET IDDimKanbanFaseAtual = :id_fase_para,
                    OrdemNaFase = :ordem_na_fase
                OUTPUT
                    INSERTED.IDFatoKanbanCard,
                    INSERTED.IDDimKanbanFaseAtual,
                    INSERTED.OrdemNaFase{output_versao}
                WHERE IDFatoKanbanCard = :id_card
                  AND Ativo = 1{where_versao};
            """)
            row_upd = db.session.execute(sql_upd, params_upd).mappings().first()

        elif (not has_ordem) and has_atualizado:
            output_versao = ", INSERTED.VersaoConcorrencia" if has_versao else ""
            where_versao = " AND VersaoConcorrencia = :versao_concorrencia" if has_versao else ""
            params_upd = {
                "id_fase_para": id_fase_para,
                "id_card": id_card,
            }
            if has_versao:
                params_upd["versao_concorrencia"] = versao_concorrencia_bytes

            sql_upd = text(f"""
                UPDATE {TABELA_CARD}
                SET IDDimKanbanFaseAtual = :id_fase_para,
                    AtualizadoEm = GETDATE()
                OUTPUT
                    INSERTED.IDFatoKanbanCard,
                    INSERTED.IDDimKanbanFaseAtual,
                    INSERTED.AtualizadoEm{output_versao}
                WHERE IDFatoKanbanCard = :id_card
                  AND Ativo = 1{where_versao};
            """)
            row_upd = db.session.execute(sql_upd, params_upd).mappings().first()

        else:
            output_versao = ", INSERTED.VersaoConcorrencia" if has_versao else ""
            where_versao = " AND VersaoConcorrencia = :versao_concorrencia" if has_versao else ""
            params_upd = {
                "id_fase_para": id_fase_para,
                "id_card": id_card,
            }
            if has_versao:
                params_upd["versao_concorrencia"] = versao_concorrencia_bytes

            sql_upd = text(f"""
                UPDATE {TABELA_CARD}
                SET IDDimKanbanFaseAtual = :id_fase_para
                OUTPUT
                    INSERTED.IDFatoKanbanCard,
                    INSERTED.IDDimKanbanFaseAtual{output_versao}
                WHERE IDFatoKanbanCard = :id_card
                  AND Ativo = 1{where_versao};
            """)
            row_upd = db.session.execute(sql_upd, params_upd).mappings().first()

        if not row_upd:
            detalhe_atual = _obter_card_detalhe_payload(id_card)
            return (
                jsonify(
                    {
                        "ok": False,
                        "msg": "Este card foi alterado ou movido por outro usuário. Recarregue antes de tentar novamente.",
                        "card_atual": detalhe_atual.get("card"),
                    }
                ),
                409,
            )

        nome_fase_destino = str(fase_destino.get("NomeFase") or "").strip()
        tipo_fase_destino = str(fase_destino.get("TipoFase") or "").strip()

        id_status_movimento = _resolver_id_status_card_movimento(
            nome_fase_para=nome_fase_destino,
            card_inativado=False,
        )

        campos_status_pos_mov: list[str] = []
        params_status_pos_mov: dict[str, Any] = {
            "id_card": id_card,
        }

        if _coluna_existe(TABELA_CARD, "IDDimKanbanStatusCard") and id_status_movimento is not None:
            campos_status_pos_mov.append("IDDimKanbanStatusCard = :id_status_destino")
            params_status_pos_mov["id_status_destino"] = int(id_status_movimento)

        if _coluna_existe(TABELA_CARD, "StatusCard"):
            if id_status_movimento == 3:
                campos_status_pos_mov.append("StatusCard = :status_destino")
                params_status_pos_mov["status_destino"] = "CONCLUIDO"
            elif id_status_movimento == 2:
                campos_status_pos_mov.append("StatusCard = :status_destino")
                params_status_pos_mov["status_destino"] = "CANCELADO"
            elif id_status_movimento == 1:
                campos_status_pos_mov.append("StatusCard = :status_destino")
                params_status_pos_mov["status_destino"] = "ATIVO"

        if _coluna_existe(TABELA_CARD, "EncerradoEm"):
            if id_status_movimento == 3:
                campos_status_pos_mov.append("EncerradoEm = ISNULL(EncerradoEm, GETDATE())")
            else:
                campos_status_pos_mov.append("EncerradoEm = NULL")

        if campos_status_pos_mov:
            sql_status_pos_mov = text(f"""
                UPDATE {TABELA_CARD}
                SET {', '.join(campos_status_pos_mov)}
                WHERE IDFatoKanbanCard = :id_card;
            """)
            db.session.execute(sql_status_pos_mov, params_status_pos_mov)

        if _fase_define_status_concluido(nome_fase_destino, tipo_fase_destino):
            _remover_tag_em_atendimento_do_card(
                id_card=int(id_card),
                id_kanban=int(id_kanban),
                id_usuario=int(id_usuario),
            )

        id_empresa_movimento = _resolver_id_empresa_proprietaria_movimento(
            id_kanban=id_kanban,
            id_empresa_padrao=row.get("IDEmpresaProprietaria"),
        )

        observacao_movimento = nota_movimento or observacao

        sql_ins = text(f"""
            INSERT INTO {TABELA_CARD_MOVIMENTO}
            (
                IDFatoKanbanCard,
                IDFaseDe,
                IDFasePara,
                MovidoEm,
                MovidoPor,
                Observacao,
                IDEmpresaProprietaria,
                IDDimKanbanTag,
                IDDimKanbanStatusCard,
                IDDimKanban
            )
            OUTPUT INSERTED.IDFatoKanbanCardMovimento
            VALUES
            (
                :id_card,
                :id_fase_de,
                :id_fase_para,
                GETDATE(),
                :movido_por,
                :obs,
                :id_empresa,
                :id_tag,
                :id_status_card,
                :id_kanban
            );
        """)

        row_movimento = db.session.execute(
            sql_ins,
            {
                "id_card": id_card,
                "id_fase_de": id_fase_de,
                "id_fase_para": id_fase_para,
                "movido_por": id_usuario,
                "obs": observacao_movimento[:2000] if observacao_movimento else None,
                "id_empresa": id_empresa_movimento,
                "id_tag": None,
                "id_status_card": int(id_status_movimento) if id_status_movimento is not None else None,
                "id_kanban": int(id_kanban),
            },
        ).mappings().first()

        _registrar_status_historico_card(
            id_card=int(id_card),
            id_fase=int(id_fase_para),
            id_status_card=int(id_status_movimento) if id_status_movimento is not None else None,
            id_usuario=int(id_usuario),
            id_empresa_proprietaria=int(id_empresa_movimento or 0) or None,
        )

        if observacao_movimento:
            _registrar_observacao_historica_card(
                id_card=int(id_card),
                texto_observacao=observacao_movimento,
                id_usuario=int(id_usuario),
                id_status_card=int(id_status_movimento) if id_status_movimento is not None else None,
                id_fase=int(id_fase_para),
            )

        snapshot_depois = _obter_snapshot_card_log(id_card, incluir_inativo=True)
        _registrar_log_card(
            id_card=id_card,
            id_kanban=id_kanban,
            id_empresa_proprietaria=id_emp,
            id_usuario_acao=id_usuario,
            tipo_evento="CARD_MOVIDO",
            id_fase_de=id_fase_de,
            id_fase_para=id_fase_para,
            observacao=observacao_movimento or "Card movido entre fases.",
            tabela_origem=TABELA_CARD_MOVIMENTO,
            id_registro_origem=int(row_movimento.get("IDFatoKanbanCardMovimento") or 0) if row_movimento else None,
            payload_antes=snapshot_antes,
            payload_depois=snapshot_depois,
        )

        db.session.commit()

        _invalidar_kanban(id_emp=id_emp, id_kanban=id_kanban, id_card=id_card)
        detalhe = _obter_card_detalhe_payload(id_card)

        _emitir_evento_kanban(
            id_kanban,
            "card_movido",
            {
                "id_card": id_card,
                "id_fase_de": id_fase_de,
                "id_fase_para": id_fase_para,
                "ordem_na_fase": proxima_ordem if has_ordem else None,
                "card": detalhe.get("card"),
                "tags": detalhe.get("tags", []),
                "notas": detalhe.get("notas", []),
            },
        )

        return jsonify(
            {
                "ok": True,
                "id_card": id_card,
                "id_fase_de": id_fase_de,
                "id_fase_para": id_fase_para,
                "ordem_na_fase": proxima_ordem if has_ordem else None,
                "card": detalhe.get("card"),
                "tags": detalhe.get("tags", []),
                "notas": detalhe.get("notas", []),
            }
        )

    except Exception as exc:
        db.session.rollback()
        current_app.logger.exception("Erro ao mover card id_card=%s", id_card)
        return jsonify({"ok": False, "msg": f"Erro ao mover card: {str(exc)}"}), 500




    


@kanban_bp.route("/api/kanbans/<int:id_kanban>/tags", methods=["POST"])
@login_required
@limiter.limit("60/minute")
def api_tag_criar(id_kanban: int):
    id_usuario = _assert_login()
    id_emp = _id_empresa_usuario_or_403()
    _obter_kanban_autorizado(id_kanban)

    payload = request.get_json(silent=True) or {}
    nome = (payload.get("nome") or "").strip()
    tipo = _normalizar_codigo_dominio(payload.get("tipo")) or _obter_tipo_tag_padrao(id_kanban=id_kanban, id_emp=id_emp)
    cor = (payload.get("cor_hex") or "").strip()
    icone = (payload.get("icone") or "").strip()

    if len(nome) < 2:
        return jsonify({"ok": False, "msg": "Nome da tag inválido"}), 400
    tipos_tag_validos = _obter_tipos_tag_configurados(id_kanban=id_kanban, id_emp=id_emp)
    if tipos_tag_validos and tipo not in tipos_tag_validos:
        return jsonify({"ok": False, "msg": "TipoTag inválido", "tipos_tag_validos": tipos_tag_validos}), 400
    if cor and (len(cor) != 7 or not cor.startswith("#")):
        return jsonify({"ok": False, "msg": "CorHex inválida. Use #RRGGBB"}), 400

    sql = text("""
        INSERT INTO [Kanban].[Silver].[DimKanbanTag]
            (IDDimKanban, NomeTag, TipoTag, CorHex, Icone,
             AfetaCorCard, PodeVendedorAplicar, PodeAdminAplicar, AplicacaoUnica,
             Ativo, CriadoEm, IDUsuario, IDEmpresaProprietaria)
        OUTPUT INSERTED.IDDimKanbanTag
        VALUES
            (:id_kanban, :nome, :tipo, :cor, :icone,
             0, 1, 1, 0,
             1, GETDATE(), :id_usuario, :id_emp);
    """)
    novo_id = db.session.execute(
        sql,
        {
            "id_kanban": id_kanban,
            "nome": nome[:100],
            "tipo": tipo,
            "cor": cor if cor else None,
            "icone": icone[:50] if icone else None,
            "id_usuario": id_usuario,
            "id_emp": id_emp,
        },
    ).scalar()

    db.session.commit()
    _invalidar_kanban(id_emp=id_emp, id_kanban=id_kanban)
    _emitir_evento_kanban(
        id_kanban,
        "tag_criada",
        {
            "IDDimKanbanTag": int(novo_id),
            "NomeTag": nome[:100],
            "TipoTag": tipo,
            "CorHex": cor if cor else None,
            "Icone": icone[:50] if icone else None,
        },
    )

    return jsonify({"ok": True, "IDDimKanbanTag": int(novo_id)})






@kanban_bp.route("/api/cards/<int:id_card>/tags", methods=["POST"])
@login_required
@limiter.limit("180/minute")
def api_card_tag_adicionar(id_card: int):
    id_usuario = _assert_login()
    card = _obter_card_autorizado(id_card)
    id_emp = _id_empresa_usuario_or_403()
    id_kanban = int(card.get("IDDimKanban") or 0)
    id_fase_atual = int(card.get("IDDimKanbanFaseAtual") or 0)
    payload = request.get_json(silent=True) or {}

    id_tag = int(payload.get("id_tag") or 0)
    if not id_tag:
        return jsonify({"ok": False, "msg": "Tag obrigatória"}), 400

    sql_tag = text("""
        SELECT
            t.IDDimKanbanTag,
            t.NomeTag
        FROM [Kanban].[Silver].[DimKanbanTag] t
        WHERE t.IDDimKanbanTag = :id_tag
          AND t.IDDimKanban = :id_kanban
          AND t.Ativo = 1;
    """)
    tag_row = db.session.execute(
        sql_tag,
        {
            "id_tag": id_tag,
            "id_kanban": id_kanban,
        },
    ).mappings().first()

    if not tag_row:
        return jsonify({"ok": False, "msg": "Tag inválida para este card"}), 400

    sql_dup = text("""
        SELECT TOP (1)
            IDFatoKanbanCardTag
        FROM [Kanban].[Silver].[FatoKanbanCardTag]
        WHERE IDFatoKanbanCard = :id_card
          AND IDDimKanbanTag = :id_tag
          AND RemovidoEm IS NULL;
    """)
    existe = db.session.execute(
        sql_dup,
        {
            "id_card": id_card,
            "id_tag": id_tag,
        },
    ).scalar()

    alterou = False
    retorno_solicitacao: dict[str, Any] | None = None

    try:
        if not existe:
            snapshot_antes = _obter_snapshot_card_log(id_card, incluir_inativo=True)

            sql_insert = text("""
                INSERT INTO [Kanban].[Silver].[FatoKanbanCardTag]
                    (IDFatoKanbanCard, IDDimKanbanTag, AplicadoEm, AplicadoPor, IDEmpresaProprietaria)
                OUTPUT INSERTED.IDFatoKanbanCardTag
                VALUES
                    (:id_card, :id_tag, GETDATE(), :id_usuario, :id_empresa);
            """)
            id_card_tag_inserido = db.session.execute(
                sql_insert,
                {
                    "id_card": id_card,
                    "id_tag": id_tag,
                    "id_usuario": id_usuario,
                    "id_empresa": card.get("IDEmpresaProprietaria"),
                },
            ).scalar()

            nome_tag_aplicada = str(tag_row.get("NomeTag") or "").strip()
            nome_tag_aplicada_normalizada = _normalizar_texto_comparacao(nome_tag_aplicada)

            if (
                int(id_tag) == int(ID_TAG_CONTRATO_EM_AVALIACAO)
                or nome_tag_aplicada_normalizada == _normalizar_texto_comparacao(NOME_TAG_CONTRATO_EM_AVALIACAO)
            ):
                retorno_solicitacao = _criar_solicitacao_contrato_em_avaliacao_para_card(
                    id_card=int(id_card),
                    id_usuario=int(id_usuario),
                    id_empresa_proprietaria=int(id_emp),
                )

            id_empresa_movimento = _resolver_id_empresa_proprietaria_movimento(
                id_kanban=id_kanban,
                id_empresa_padrao=card.get("IDEmpresaProprietaria"),
            )

            _registrar_tag_historico_card(
                id_fato_kanban_card_tag=int(id_card_tag_inserido or 0) or None,
                id_card=int(id_card),
                id_fase=int(id_fase_atual) if id_fase_atual else None,
                id_usuario=int(id_usuario),
                id_empresa_proprietaria=int(id_empresa_movimento or 0) or None,
            )

            snapshot_depois = _obter_snapshot_card_log(id_card, incluir_inativo=True)

            observacao_log = f"Tag adicionada: {nome_tag_aplicada}"
            if retorno_solicitacao and retorno_solicitacao.get("criada"):
                observacao_log += (
                    f" | Solicitação de contrato criada: "
                    f"{retorno_solicitacao.get('id_solicitacao')} "
                    f"({retorno_solicitacao.get('tipo_solicitacao')})"
                )

            _registrar_log_card(
                id_card=id_card,
                id_kanban=id_kanban,
                id_empresa_proprietaria=id_emp,
                id_usuario_acao=id_usuario,
                tipo_evento="TAG_ADICIONADA",
                subtipo_evento=nome_tag_aplicada[:120] or None,
                id_fase_de=id_fase_atual if id_fase_atual else None,
                id_fase_para=id_fase_atual if id_fase_atual else None,
                observacao=observacao_log[:2000],
                tabela_origem="[Kanban].[Silver].[FatoKanbanCardTag]",
                id_registro_origem=int(id_card_tag_inserido or 0) or None,
                payload_antes=snapshot_antes,
                payload_depois=snapshot_depois,
            )

            db.session.commit()
            alterou = True

        _invalidar_kanban(id_emp=id_emp, id_kanban=id_kanban, id_card=id_card)
        detalhe = _obter_card_detalhe_payload(id_card)

        if alterou:
            payload_socket = {
                "id_card": id_card,
                "id_tag": id_tag,
                "id_usuario_acao": id_usuario,
                "card": detalhe.get("card"),
                "tags": detalhe.get("tags", []),
            }

            if retorno_solicitacao:
                payload_socket["solicitacao_contrato"] = retorno_solicitacao

            _emitir_evento_kanban(
                id_kanban,
                "card_tag_adicionada",
                payload_socket,
            )

        msg = "Tag adicionada com sucesso."
        if retorno_solicitacao:
            if retorno_solicitacao.get("criada"):
                msg = (
                    f"Tag adicionada com sucesso. "
                    f"Solicitação {retorno_solicitacao.get('id_solicitacao')} criada com "
                    f"{retorno_solicitacao.get('total_itens')} item(ns)."
                )
            elif retorno_solicitacao.get("motivo"):
                msg = (
                    "Tag adicionada com sucesso. "
                    f"{retorno_solicitacao.get('motivo')}"
                )

        return jsonify(
            {
                "ok": True,
                "msg": msg,
                "id_card": id_card,
                "id_tag": id_tag,
                "card": detalhe.get("card"),
                "tags": detalhe.get("tags", []),
                "solicitacao_contrato": retorno_solicitacao,
            }
        )

    except ValueError as exc:
        db.session.rollback()
        return jsonify({"ok": False, "msg": str(exc)}), 400

    except Exception as exc:
        db.session.rollback()
        current_app.logger.exception(
            "Erro ao adicionar tag no card id_card=%s id_tag=%s",
            id_card,
            id_tag,
        )
        return jsonify({"ok": False, "msg": f"Erro ao adicionar tag: {str(exc)}"}), 500



















@kanban_bp.route("/api/cards/<int:id_card>/tags/<int:id_tag>", methods=["DELETE"])
@login_required
@limiter.limit("180/minute")
def api_card_tag_remover(id_card: int, id_tag: int):
    id_usuario = _assert_login()
    card = _obter_card_autorizado(id_card)
    id_emp = _id_empresa_usuario_or_403()
    id_kanban = int(card.get("IDDimKanban") or 0)
    id_fase_atual = int(card.get("IDDimKanbanFaseAtual") or 0)

    tag_em_atendimento = _obter_tag_em_atendimento(id_kanban)
    if tag_em_atendimento and int(tag_em_atendimento.get("IDDimKanbanTag") or 0) == int(id_tag):
        return jsonify({
            "ok": False,
            "msg": "A tag 'Em Atendimento' é automática e só pode ser removida quando o card for concluído ou removido do kanban.",
        }), 400

    tag_aprovacao_desconto = _obter_tag_por_nome(
        id_kanban,
        NOME_TAG_APROVACAO_DESCONTO,
        somente_ativa=True,
    )
    if tag_aprovacao_desconto and int(tag_aprovacao_desconto.get("IDDimKanbanTag") or 0) == int(id_tag):
        if _card_precisa_aprovacao_diretoria_por_estado_atual(int(id_card)):
            return jsonify({
                "ok": False,
                "msg": "A tag 'Aprovação Desconto' é automática enquanto o preço final estiver em até 12% acima do custo.",
            }), 400

    tag_row = db.session.execute(
        text("""
            SELECT TOP (1)
                IDDimKanbanTag,
                NomeTag
            FROM [Kanban].[Silver].[DimKanbanTag]
            WHERE IDDimKanbanTag = :id_tag;
        """),
        {"id_tag": int(id_tag)},
    ).mappings().first()

    snapshot_antes = _obter_snapshot_card_log(id_card, incluir_inativo=True)
    alterou = _remover_tag_do_card(
        id_card=int(id_card),
        id_tag=int(id_tag),
        id_usuario=int(id_usuario),
    )

    if alterou:
        id_card_tag_removido = db.session.execute(
            text("""
                SELECT TOP (1)
                    IDFatoKanbanCardTag
                FROM [Kanban].[Silver].[FatoKanbanCardTag]
                WHERE IDFatoKanbanCard = :id_card
                  AND IDDimKanbanTag = :id_tag
                  AND RemovidoPor = :id_usuario
                  AND RemovidoEm IS NOT NULL
                ORDER BY RemovidoEm DESC, IDFatoKanbanCardTag DESC;
            """),
            {
                "id_card": int(id_card),
                "id_tag": int(id_tag),
                "id_usuario": int(id_usuario),
            },
        ).scalar()

        id_empresa_movimento = _resolver_id_empresa_proprietaria_movimento(
            id_kanban=id_kanban,
            id_empresa_padrao=card.get("IDEmpresaProprietaria"),
        )

        _registrar_tag_historico_card(
            id_fato_kanban_card_tag=int(id_card_tag_removido or 0) or None,
            id_card=int(id_card),
            id_fase=int(id_fase_atual) if id_fase_atual else None,
            id_usuario=int(id_usuario),
            id_empresa_proprietaria=int(id_empresa_movimento or 0) or None,
        )

        snapshot_depois = _obter_snapshot_card_log(id_card, incluir_inativo=True)
        _registrar_log_card(
            id_card=id_card,
            id_kanban=id_kanban,
            id_empresa_proprietaria=id_emp,
            id_usuario_acao=id_usuario,
            tipo_evento="TAG_REMOVIDA",
            subtipo_evento=str((tag_row or {}).get("NomeTag") or "").strip()[:120] or None,
            id_fase_de=id_fase_atual if id_fase_atual else None,
            id_fase_para=id_fase_atual if id_fase_atual else None,
            observacao=f"Tag removida: {str((tag_row or {}).get('NomeTag') or '').strip()}",
            tabela_origem="[Kanban].[Silver].[FatoKanbanCardTag]",
            id_registro_origem=int(id_card_tag_removido or 0) or None,
            payload_antes=snapshot_antes,
            payload_depois=snapshot_depois,
        )

    db.session.commit()

    _invalidar_kanban(id_emp=id_emp, id_kanban=id_kanban, id_card=id_card)

    if alterou:
        _emitir_evento_kanban(
            id_kanban,
            "card_tag_removida",
            {"id_card": id_card, "id_tag": id_tag},
        )

    return jsonify({"ok": True})







def _obter_contexto_observacao_card(id_card: int) -> dict[str, Any]:
    """
    Busca o contexto atual do card para gravar histórico de observações.

    Retorna:
    - IDFatoKanbanCard
    - IDDimKanban
    - IDDimKanbanFaseAtual
    - NomeFaseAtual
    - IDEmpresaProprietaria do card
    - IDEmpresaDoKanban
    - IDDimKanbanStatusCard, se a coluna existir na FatoKanbanCard
    """
    id_emp = _id_empresa_usuario_or_403()

    select_status = (
        "c.IDDimKanbanStatusCard AS IDDimKanbanStatusCard,"
        if _coluna_existe(TABELA_CARD, "IDDimKanbanStatusCard")
        else "CAST(NULL AS INT) AS IDDimKanbanStatusCard,"
    )

    sql = text(f"""
        SELECT TOP (1)
            c.IDFatoKanbanCard,
            c.IDDimKanban,
            c.IDDimKanbanFaseAtual,
            f.NomeFase AS NomeFaseAtual,
            c.IDEmpresaProprietaria,
            k.IDEmpresaProprietaria AS IDEmpresaDoKanban,
            {select_status}
            c.StatusCard
        FROM {TABELA_CARD} c
        JOIN {TABELA_KANBAN} k
          ON k.IDDimKanban = c.IDDimKanban
        LEFT JOIN {TABELA_KANBAN_FASE} f
          ON f.IDDimKanbanFase = c.IDDimKanbanFaseAtual
        WHERE c.IDFatoKanbanCard = :id_card
          AND k.IDEmpresaProprietaria = :id_emp
          AND k.Ativo = 1;
    """)

    row = db.session.execute(
        sql,
        {
            "id_card": int(id_card),
            "id_emp": int(id_emp),
        },
    ).mappings().first()

    if not row:
        abort(403, "Você não tem permissão para acessar este card")

    return dict(row)


def _registrar_observacao_historica_card(
    *,
    id_card: int,
    texto_observacao: str,
    id_usuario: int,
    id_status_card: int | None = None,
    id_fase: int | None = None,
) -> dict[str, Any] | None:
    """
    Grava o histórico de observações digitadas no campo de notas do card.

    Regras:
    - Observacao = texto digitado
    - IDEmpresaProprietaria = regra do kanban (kanban 1 => empresa 3)
    - IDFatoKanbanCard = card atual
    - IDDimKanbanStatusCard = status atual do card; se não vier por parâmetro,
      tento pegar da FatoKanbanCard e, se não existir, derivo pela fase
    - IDDimKanbanFase = fase atual do card; se vier por parâmetro, uso o informado
    - IDDimUsuarios = usuário logado
    - CriadoEm = GETDATE()
    """
    if not _objeto_existe(TABELA_CARD_OBSERVACOES):
        return None

    texto = str(texto_observacao or "").strip()
    if len(texto) < 2:
        return None

    contexto = _obter_contexto_observacao_card(id_card)
    if not contexto:
        return None

    id_kanban = int(contexto.get("IDDimKanban") or 0)
    id_fase_atual = int(id_fase or contexto.get("IDDimKanbanFaseAtual") or 0)
    nome_fase_atual = str(contexto.get("NomeFaseAtual") or "").strip()

    id_status_atual = id_status_card
    if id_status_atual is None:
        id_status_atual = contexto.get("IDDimKanbanStatusCard")

    if id_status_atual is None:
        id_status_atual = _resolver_id_status_card_movimento(
            nome_fase_para=nome_fase_atual,
            card_inativado=False,
        )

    id_empresa_observacao = _resolver_id_empresa_proprietaria_movimento(
        id_kanban=id_kanban,
        id_empresa_padrao=contexto.get("IDEmpresaProprietaria"),
    )

    sql = text(f"""
        INSERT INTO {TABELA_CARD_OBSERVACOES}
        (
            Observacao,
            IDEmpresaProprietaria,
            IDFatoKanbanCard,
            IDDimKanbanStatusCard,
            IDDimKanbanFase,
            IDDimUsuarios,
            CriadoEm
        )
        OUTPUT
            INSERTED.IDFatoKanbanCardObservacoes,
            INSERTED.Observacao,
            INSERTED.IDEmpresaProprietaria,
            INSERTED.IDFatoKanbanCard,
            INSERTED.IDDimKanbanStatusCard,
            INSERTED.IDDimKanbanFase,
            INSERTED.IDDimUsuarios,
            INSERTED.CriadoEm
        VALUES
        (
            :observacao,
            :id_empresa,
            :id_card,
            :id_status_card,
            :id_fase,
            :id_usuario,
            GETDATE()
        );
    """)

    row = db.session.execute(
        sql,
        {
            "observacao": texto[:1000],
            "id_empresa": int(id_empresa_observacao or 0) or None,
            "id_card": int(id_card),
            "id_status_card": int(id_status_atual) if id_status_atual is not None else None,
            "id_fase": int(id_fase_atual) if id_fase_atual else None,
            "id_usuario": int(id_usuario),
        },
    ).mappings().first()

    if not row:
        return None

    registro = dict(row)

    try:
        id_status_registro = (
            int(registro.get("IDDimKanbanStatusCard"))
            if registro.get("IDDimKanbanStatusCard") is not None
            else None
        )
    except Exception:
        id_status_registro = None

    registro["IDFatoKanbanCardNota"] = registro.get("IDFatoKanbanCardObservacoes")
    registro["TipoNota"] = "INATIVACAO" if id_status_registro == 2 else "OBS"
    registro["Texto"] = registro.get("Observacao")
    registro["CriadoPor"] = registro.get("IDDimUsuarios")
    registro["IDEmpresa"] = None

    return registro








def _registrar_observacao_historica_card(
    *,
    id_card: int,
    texto_observacao: str,
    id_usuario: int,
    id_status_card: int | None = None,
    id_fase: int | None = None,
) -> dict[str, Any] | None:
    """
    Grava o histórico de observações digitadas no campo de notas do card.

    Regras:
    - Observacao = texto digitado
    - IDEmpresaProprietaria = regra do kanban (kanban 1 => empresa 3)
    - IDFatoKanbanCard = card atual
    - IDDimKanbanStatusCard = status atual do card; se não vier por parâmetro,
      tento pegar da FatoKanbanCard e, se não existir, derivo pela fase
    - IDDimKanbanFase = fase atual do card; se vier por parâmetro, uso o informado
    - IDDimUsuarios = usuário logado
    - CriadoEm = GETDATE()
    """
    if not _objeto_existe(TABELA_CARD_OBSERVACOES):
        return None

    texto = str(texto_observacao or "").strip()
    if len(texto) < 2:
        return None

    contexto = _obter_contexto_observacao_card(id_card)
    if not contexto:
        return None

    id_kanban = int(contexto.get("IDDimKanban") or 0)
    id_fase_atual = int(id_fase or contexto.get("IDDimKanbanFaseAtual") or 0)
    nome_fase_atual = str(contexto.get("NomeFaseAtual") or "").strip()

    id_status_atual = id_status_card
    if id_status_atual is None:
        id_status_atual = contexto.get("IDDimKanbanStatusCard")

    if id_status_atual is None:
        id_status_atual = _resolver_id_status_card_movimento(
            nome_fase_para=nome_fase_atual,
            card_inativado=False,
        )

    id_empresa_observacao = _resolver_id_empresa_proprietaria_movimento(
        id_kanban=id_kanban,
        id_empresa_padrao=contexto.get("IDEmpresaProprietaria"),
    )

    sql = text(f"""
        INSERT INTO {TABELA_CARD_OBSERVACOES}
        (
            Observacao,
            IDEmpresaProprietaria,
            IDFatoKanbanCard,
            IDDimKanbanStatusCard,
            IDDimKanbanFase,
            IDDimUsuarios,
            CriadoEm
        )
        OUTPUT
            INSERTED.IDFatoKanbanCardObservacoes,
            INSERTED.Observacao,
            INSERTED.IDEmpresaProprietaria,
            INSERTED.IDFatoKanbanCard,
            INSERTED.IDDimKanbanStatusCard,
            INSERTED.IDDimKanbanFase,
            INSERTED.IDDimUsuarios,
            INSERTED.CriadoEm
        VALUES
        (
            :observacao,
            :id_empresa,
            :id_card,
            :id_status_card,
            :id_fase,
            :id_usuario,
            GETDATE()
        );
    """)

    row = db.session.execute(
        sql,
        {
            "observacao": texto[:1000],
            "id_empresa": int(id_empresa_observacao or 0) or None,
            "id_card": int(id_card),
            "id_status_card": int(id_status_atual) if id_status_atual is not None else None,
            "id_fase": int(id_fase_atual) if id_fase_atual else None,
            "id_usuario": int(id_usuario),
        },
    ).mappings().first()

    if not row:
        return None

    registro = dict(row)

    try:
        id_status_registro = (
            int(registro.get("IDDimKanbanStatusCard"))
            if registro.get("IDDimKanbanStatusCard") is not None
            else None
        )
    except Exception:
        id_status_registro = None

    registro["IDFatoKanbanCardNota"] = registro.get("IDFatoKanbanCardObservacoes")
    registro["TipoNota"] = "INATIVACAO" if id_status_registro == 2 else "OBS"
    registro["Texto"] = registro.get("Observacao")
    registro["CriadoPor"] = registro.get("IDDimUsuarios")
    registro["IDEmpresa"] = None

    return registro



@kanban_bp.route("/api/cards/<int:id_card>/notas", methods=["POST"])
@login_required
@limiter.limit("180/minute")
def api_card_nota_criar(id_card: int):
    id_usuario = _assert_login()
    card = _obter_card_autorizado(id_card)
    id_emp = _id_empresa_usuario_or_403()
    id_kanban = int(card.get("IDDimKanban") or 0)

    payload = request.get_json(silent=True) or {}
    texto = (payload.get("texto") or "").strip()

    if len(texto) < 2:
        return jsonify({"ok": False, "msg": "Texto da nota inválido"}), 400

    row_observacao = _registrar_observacao_historica_card(
        id_card=id_card,
        texto_observacao=texto,
        id_usuario=id_usuario,
    )

    if not row_observacao:
        db.session.rollback()
        return jsonify({"ok": False, "msg": "Não foi possível gravar a observação do card"}), 500

    snapshot_depois = _obter_snapshot_card_log(id_card, incluir_inativo=True)
    _registrar_log_card(
        id_card=id_card,
        id_kanban=id_kanban,
        id_empresa_proprietaria=id_emp,
        id_usuario_acao=id_usuario,
        tipo_evento="CARD_NOTA_CRIADA",
        subtipo_evento="OBS",
        observacao=texto,
        tabela_origem=TABELA_CARD_OBSERVACOES,
        id_registro_origem=int(row_observacao.get("IDFatoKanbanCardObservacoes") or 0),
        payload_depois=snapshot_depois,
    )
    db.session.commit()

    _invalidar_kanban(id_emp=id_emp, id_kanban=id_kanban, id_card=id_card)

    nota_payload = {
        "IDFatoKanbanCardNota": int(row_observacao.get("IDFatoKanbanCardNota") or 0),
        "IDFatoKanbanCardObservacoes": int(row_observacao.get("IDFatoKanbanCardObservacoes") or 0),
        "TipoNota": row_observacao.get("TipoNota") or "OBS",
        "Texto": row_observacao.get("Texto") or texto,
        "CriadoPor": int(row_observacao.get("CriadoPor") or id_usuario),
        "CriadoEm": row_observacao.get("CriadoEm"),
        "IDEmpresa": None,
        "IDEmpresaProprietaria": row_observacao.get("IDEmpresaProprietaria"),
        "IDDimKanbanStatusCard": row_observacao.get("IDDimKanbanStatusCard"),
        "IDDimKanbanFase": row_observacao.get("IDDimKanbanFase"),
    }

    _emitir_evento_kanban(
        id_kanban,
        "card_nota_criada",
        {"id_card": id_card, "nota": nota_payload},
    )

    return jsonify({"ok": True, "nota": nota_payload})


@kanban_bp.route("/api/cards/<int:id_card>/inativar", methods=["POST"])
@login_required
@limiter.limit("120/minute")
def api_card_inativar(id_card: int):
    print(f"[KANBAN][api_card_inativar] INICIO id_card={id_card}")

    id_usuario = _assert_login()
    print(f"[KANBAN][api_card_inativar] id_usuario={id_usuario}")

    card_escopo = _obter_card_autorizado(id_card)
    print(f"[KANBAN][api_card_inativar] card_escopo={card_escopo!r}")

    id_emp = _id_empresa_usuario_or_403()
    print(f"[KANBAN][api_card_inativar] id_emp={id_emp}")

    id_kanban = int(card_escopo.get("IDDimKanban") or 0)
    print(f"[KANBAN][api_card_inativar] id_kanban={id_kanban}")

    payload = request.get_json(silent=True) or {}
    print(f"[KANBAN][api_card_inativar] payload={payload!r}")

    motivo_informado = payload.get("motivo")
    descricao = (payload.get("descricao") or "").strip()

    print(
        f"[KANBAN][api_card_inativar] motivo_informado={motivo_informado!r} descricao={descricao!r}"
    )

    motivo_normalizado = _normalizar_motivo_encerramento_card(motivo_informado)
    print(f"[KANBAN][api_card_inativar] motivo_normalizado={motivo_normalizado!r}")

    if not motivo_normalizado:
        print("[KANBAN][api_card_inativar] motivo inválido -> retorno 400")
        return jsonify({"ok": False, "msg": "Motivo inválido"}), 400

    codigo_motivo = _normalizar_codigo_dominio(motivo_normalizado.get("Codigo"))
    if codigo_motivo in {"OUTROS", "OUTRO_MOTIVO"} and len(descricao) < 2:
        print("[KANBAN][api_card_inativar] motivo OUTROS sem descrição suficiente -> retorno 400")
        return jsonify({"ok": False, "msg": "Descreva o motivo"}), 400

    print("[KANBAN][api_card_inativar] capturando snapshot_antes")
    snapshot_antes = _obter_snapshot_card_log(id_card, incluir_inativo=True)

    sql_card = text(f"""
        SELECT
            c.IDDimKanban,
            c.IDDimKanbanFaseAtual,
            c.IDEmpresaProprietaria,
            c.IDDimKanbanMotivoEncerramento,
            {_sql_select_empresa_relacionada_card('c')}
        FROM {TABELA_CARD} c
        WHERE c.IDFatoKanbanCard = :id_card
          AND c.Ativo = 1;
    """)

    print(f"[KANBAN][api_card_inativar] executando SQL card ativo id_card={id_card}")
    row = db.session.execute(sql_card, {"id_card": id_card}).mappings().first()
    print(f"[KANBAN][api_card_inativar] row={row!r}")

    if not row:
        print("[KANBAN][api_card_inativar] card não encontrado ou já inativo -> retorno 404")
        return jsonify({"ok": False, "msg": "Card não encontrado ou já inativo"}), 404

    if int(row["IDDimKanban"]) != id_kanban:
        print(
            f"[KANBAN][api_card_inativar] card fora do escopo -> row.IDDimKanban={row['IDDimKanban']} id_kanban={id_kanban}"
        )
        return jsonify({"ok": False, "msg": "Card fora do escopo do usuário"}), 403

    id_fase_atual = int(row["IDDimKanbanFaseAtual"] or 0)
    id_fase_para_movimento = 9
    id_empresa_card = row.get("IDEmpresaProprietaria")
    id_motivo_encerramento = int(motivo_normalizado.get("IDDimKanbanMotivoEncerramento") or 0)
    motivo_texto = str(motivo_normalizado.get("Descricao") or "").strip()

    observacao_inativacao = f"[INATIVADO] Motivo: {motivo_texto}" + (f" | {descricao}" if descricao else "")
    print(
        "[KANBAN][api_card_inativar] dados básicos -> "
        f"id_fase_atual={id_fase_atual} id_fase_para_movimento={id_fase_para_movimento} "
        f"id_empresa_card={id_empresa_card} "
        f"id_motivo_encerramento={id_motivo_encerramento} "
        f"motivo_texto={motivo_texto!r} observacao_inativacao={observacao_inativacao!r}"
    )

    id_status_inativacao = _resolver_id_status_card_movimento(card_inativado=True)
    status_inativacao_texto = "CANCELADO"

    print(
        f"[KANBAN][api_card_inativar] id_status_inativacao={id_status_inativacao!r} "
        f"status_inativacao_texto={status_inativacao_texto!r}"
    )

    try:
        campos_update = ["Ativo = 0"]
        params_update = {
            "id_usuario": id_usuario,
            "id_card": id_card,
        }

        if _coluna_existe(TABELA_CARD, "InativadoEm"):
            campos_update.append("InativadoEm = GETDATE()")

        if _coluna_existe(TABELA_CARD, "InativadoPor"):
            campos_update.append("InativadoPor = :id_usuario")

        if _coluna_existe(TABELA_CARD, "StatusCard"):
            campos_update.append("StatusCard = :status_inativacao")
            params_update["status_inativacao"] = status_inativacao_texto[:100]

        if _coluna_existe(TABELA_CARD, "IDDimKanbanStatusCard") and id_status_inativacao is not None:
            campos_update.append("IDDimKanbanStatusCard = :id_status_inativacao")
            params_update["id_status_inativacao"] = int(id_status_inativacao)

        if _coluna_existe(TABELA_CARD, "IDDimKanbanMotivoEncerramento"):
            campos_update.append("IDDimKanbanMotivoEncerramento = :id_motivo_encerramento")
            params_update["id_motivo_encerramento"] = int(id_motivo_encerramento)

        if _coluna_existe(TABELA_CARD, "MotivoEncerramentoObs"):
            campos_update.append("MotivoEncerramentoObs = :motivo_obs")
            params_update["motivo_obs"] = (descricao[:2000] if descricao else observacao_inativacao[:2000])

        if _coluna_existe(TABELA_CARD, "EncerradoEm"):
            campos_update.append("EncerradoEm = ISNULL(EncerradoEm, GETDATE())")

        if _coluna_existe(TABELA_CARD, "EncerradoPor"):
            campos_update.append("EncerradoPor = ISNULL(EncerradoPor, :id_usuario)")

        if _coluna_existe(TABELA_CARD, "AtualizadoEm"):
            campos_update.append("AtualizadoEm = GETDATE()")

        if _coluna_existe(TABELA_CARD, "AtualizadoPor"):
            campos_update.append("AtualizadoPor = :id_usuario")

        print(f"[KANBAN][api_card_inativar] campos_update={campos_update!r}")
        print(f"[KANBAN][api_card_inativar] params_update={params_update!r}")

        sql_upd = text(f"""
            UPDATE {TABELA_CARD}
            SET {', '.join(campos_update)}
            WHERE IDFatoKanbanCard = :id_card
              AND Ativo = 1;
        """)

        print("[KANBAN][api_card_inativar] executando UPDATE do card")
        resultado_upd = db.session.execute(sql_upd, params_update)
        print(
            f"[KANBAN][api_card_inativar] UPDATE executado rowcount={getattr(resultado_upd, 'rowcount', None)}"
        )

        id_empresa_movimento = _resolver_id_empresa_proprietaria_movimento(
            id_kanban=id_kanban,
            id_empresa_padrao=id_empresa_card,
        )

        sql_ins_mov = text(f"""
            INSERT INTO {TABELA_CARD_MOVIMENTO}
            (
                IDFatoKanbanCard,
                IDFaseDe,
                IDFasePara,
                MovidoEm,
                MovidoPor,
                Observacao,
                IDEmpresaProprietaria,
                IDDimKanbanTag,
                IDDimKanbanStatusCard,
                IDDimKanban
            )
            OUTPUT INSERTED.IDFatoKanbanCardMovimento
            VALUES
            (
                :id_card,
                :id_fase_de,
                :id_fase_para,
                GETDATE(),
                :movido_por,
                :obs,
                :id_empresa,
                NULL,
                :id_status_card,
                :id_kanban
            );
        """)

        params_mov = {
            "id_card": id_card,
            "id_fase_de": id_fase_atual,
            "id_fase_para": id_fase_para_movimento,
            "movido_por": id_usuario,
            "obs": observacao_inativacao[:2000],
            "id_empresa": id_empresa_movimento,
            "id_status_card": int(id_status_inativacao) if id_status_inativacao is not None else None,
            "id_kanban": int(id_kanban),
        }
        print(f"[KANBAN][api_card_inativar] params_mov={params_mov!r}")

        print("[KANBAN][api_card_inativar] inserindo movimento")
        row_mov = db.session.execute(sql_ins_mov, params_mov).mappings().first()
        print(f"[KANBAN][api_card_inativar] row_mov={row_mov!r}")

        print("[KANBAN][api_card_inativar] inserindo histórico de encerramento")
        row_hist_enc = _registrar_historico_encerramento_card(
            id_card=id_card,
            id_motivo_encerramento=id_motivo_encerramento,
            nome_motivo=motivo_texto,
            id_fase=id_fase_para_movimento,
            id_usuario=id_usuario,
            observacoes=descricao,
        )
        print(f"[KANBAN][api_card_inativar] row_hist_enc={row_hist_enc!r}")

        _registrar_status_historico_card(
            id_card=int(id_card),
            id_fase=int(id_fase_para_movimento),
            id_status_card=int(id_status_inativacao) if id_status_inativacao is not None else None,
            id_usuario=int(id_usuario),
            id_empresa_proprietaria=int(id_empresa_movimento or 0) or None,
        )

        row_observacao = _registrar_observacao_historica_card(
            id_card=id_card,
            texto_observacao=observacao_inativacao,
            id_usuario=id_usuario,
            id_status_card=id_status_inativacao,
            id_fase=id_fase_para_movimento,
        )
        print(f"[KANBAN][api_card_inativar] row_observacao={row_observacao!r}")

        _remover_tag_em_atendimento_do_card(
            id_card=int(id_card),
            id_kanban=int(id_kanban),
            id_usuario=int(id_usuario),
        )

        print("[KANBAN][api_card_inativar] capturando snapshot_depois")
        snapshot_depois = _obter_snapshot_card_log(id_card, incluir_inativo=True)

        print("[KANBAN][api_card_inativar] registrando log do card")
        _registrar_log_card(
            id_card=id_card,
            id_kanban=id_kanban,
            id_empresa_proprietaria=id_emp,
            id_usuario_acao=id_usuario,
            tipo_evento="CARD_INATIVADO",
            id_fase_de=id_fase_atual,
            id_fase_para=id_fase_para_movimento,
            motivo=motivo_texto,
            observacao=descricao or observacao_inativacao,
            tabela_origem=TABELA_HISTORICO_ENCERRAMENTO_CARD,
            id_registro_origem=int(row_hist_enc.get("IDFatoDimHistoricoEncerramentoCard") or 0),
            payload_antes=snapshot_antes,
            payload_depois=snapshot_depois,
        )
        print("[KANBAN][api_card_inativar] log registrado com sucesso")

        print("[KANBAN][api_card_inativar] commit")
        db.session.commit()

        print("[KANBAN][api_card_inativar] invalidando cache/kanban")
        _invalidar_kanban(id_emp=id_emp, id_kanban=id_kanban, id_card=id_card)

        print("[KANBAN][api_card_inativar] emitindo evento socket")
        _emitir_evento_kanban(
            id_kanban,
            "card_inativado",
            {
                "id_card": id_card,
                "id_fase_de": id_fase_atual,
                "id_fase_para": id_fase_para_movimento,
                "motivo": motivo_texto,
                "motivo_codigo": motivo_normalizado.get("Codigo"),
                "id_motivo_encerramento": id_motivo_encerramento,
                "descricao": descricao or None,
            },
        )

        print(
            f"[KANBAN][api_card_inativar] SUCESSO id_card={id_card} motivo={motivo_texto!r} codigo={motivo_normalizado.get('Codigo')!r}"
        )
        current_app.logger.info(
            "KANBAN: card inativado com sucesso. id_card=%s id_usuario=%s motivo=%s codigo=%s id_motivo=%s",
            id_card,
            id_usuario,
            motivo_texto,
            motivo_normalizado.get("Codigo"),
            id_motivo_encerramento,
        )

        return jsonify(
            {
                "ok": True,
                "motivo": motivo_texto,
                "motivo_codigo": motivo_normalizado.get("Codigo"),
                "id_motivo_encerramento": id_motivo_encerramento,
            }
        )
    except Exception as exc:
        print(f"[KANBAN][api_card_inativar] ERRO -> {exc}")
        db.session.rollback()
        current_app.logger.exception("Erro ao inativar card id_card=%s", id_card)
        return jsonify({"ok": False, "msg": f"Erro ao inativar card: {str(exc)}"}), 500




def _obter_tag_por_nome(id_kanban: int, nome_tag: str, *, somente_ativa: bool = True) -> dict[str, Any] | None:
    nome_limpo = str(nome_tag or "").strip()
    if not id_kanban or not nome_limpo:
        return None

    filtro_ativo = "AND ISNULL(t.Ativo, 1) = 1" if somente_ativa else ""

    sql = text(f"""
        SELECT TOP (1)
            t.IDDimKanbanTag,
            t.IDDimKanban,
            t.NomeTag,
            t.TipoTag,
            t.CorHex,
            ISNULL(t.Ativo, 1) AS Ativo,
            t.IDEmpresaProprietaria
        FROM [Kanban].[Silver].[DimKanbanTag] t
        WHERE t.IDDimKanban = :id_kanban
          AND UPPER(LTRIM(RTRIM(ISNULL(t.NomeTag, '')))) = UPPER(LTRIM(RTRIM(:nome_tag)))
          {filtro_ativo}
        ORDER BY t.IDDimKanbanTag ASC;
    """)

    row = db.session.execute(
        sql,
        {
            "id_kanban": int(id_kanban),
            "nome_tag": nome_limpo,
        },
    ).mappings().first()

    return dict(row) if row else None






@kanban_bp.route("/api/fases/<int:id_fase>/inativar", methods=["POST"])
@login_required
@limiter.limit("60/minute")
def api_fase_inativar(id_fase: int):
    id_usuario = _assert_login()
    fase_escopo = _obter_fase_autorizada(id_fase)
    id_emp = _id_empresa_usuario_or_403()
    id_kanban = int(fase_escopo.get("IDDimKanban") or 0)

    sql_fase = text(f"""
        SELECT f.IDDimKanbanFase, f.IDDimKanban
        FROM {TABELA_KANBAN_FASE} f
        WHERE f.IDDimKanbanFase = :id_fase
          AND f.Ativo = 1;
    """)
    row = db.session.execute(sql_fase, {"id_fase": id_fase}).mappings().first()
    if not row:
        return jsonify({"ok": False, "msg": "Fase não encontrada ou já inativa"}), 404
    if int(row["IDDimKanban"]) != id_kanban:
        return jsonify({"ok": False, "msg": "Fase fora do escopo do usuário"}), 403

    quantidade_cards_ativos = _contar_cards_ativos_fase(id_fase)
    if quantidade_cards_ativos > 0:
        return jsonify(
            {
                "ok": False,
                "msg": "Não é permitido inativar uma fase que ainda possui cards ativos.",
                "QuantidadeCardsAtivos": quantidade_cards_ativos,
            }
        ), 409

    try:
        campos = ["Ativo = 0", "InativadoEm = GETDATE()", "InativadoPor = :id_usuario"]
        if _coluna_existe(TABELA_KANBAN_FASE, "AtualizadoEm"):
            campos.append("AtualizadoEm = GETDATE()")
        if _coluna_existe(TABELA_KANBAN_FASE, "AtualizadoPor"):
            campos.append("AtualizadoPor = :id_usuario")

        sql_upd = text(f"""
            UPDATE {TABELA_KANBAN_FASE}
            SET {', '.join(campos)}
            WHERE IDDimKanbanFase = :id_fase
              AND Ativo = 1;
        """)
        db.session.execute(sql_upd, {"id_usuario": id_usuario, "id_fase": id_fase})
        db.session.commit()

        _invalidar_kanban(id_emp=id_emp, id_kanban=id_kanban)
        _emitir_evento_kanban(id_kanban, "fase_inativada", {"id_fase": id_fase})
        return jsonify({"ok": True})
    except Exception as exc:
        db.session.rollback()
        current_app.logger.exception("Erro ao inativar fase id_fase=%s", id_fase)
        return jsonify({"ok": False, "msg": f"Erro ao inativar fase: {str(exc)}"}), 500



@kanban_bp.route("/api/kanbans/<int:id_kanban>/inativar", methods=["POST"])
@login_required
@limiter.limit("30/minute")
def api_kanban_inativar(id_kanban: int):
    id_usuario = _assert_login()
    kanban = _obter_kanban_autorizado(id_kanban)
    id_emp = _id_empresa_usuario_or_403()

    quantidade_cards_ativos = _contar_cards_ativos_kanban(id_kanban)
    if quantidade_cards_ativos > 0:
        return jsonify(
            {
                "ok": False,
                "msg": "Não é permitido inativar o kanban enquanto existirem cards ativos.",
                "QuantidadeCardsAtivos": quantidade_cards_ativos,
            }
        ), 409

    try:
        sql_upd = text(f"""
            UPDATE {TABELA_KANBAN}
            SET Ativo = 0,
                InativadoEm = GETDATE(),
                InativadoPor = :id_usuario
            WHERE IDDimKanban = :id_kanban
              AND Ativo = 1;
        """)
        db.session.execute(sql_upd, {"id_usuario": id_usuario, "id_kanban": id_kanban})

        campos_fase = [
            "Ativo = 0",
            "InativadoEm = ISNULL(InativadoEm, GETDATE())",
            "InativadoPor = ISNULL(InativadoPor, :id_usuario)",
        ]
        if _coluna_existe(TABELA_KANBAN_FASE, "AtualizadoEm"):
            campos_fase.append("AtualizadoEm = GETDATE()")
        if _coluna_existe(TABELA_KANBAN_FASE, "AtualizadoPor"):
            campos_fase.append("AtualizadoPor = :id_usuario")

        sql_upd_fases = text(f"""
            UPDATE {TABELA_KANBAN_FASE}
            SET {', '.join(campos_fase)}
            WHERE IDDimKanban = :id_kanban
              AND Ativo = 1;
        """)
        db.session.execute(sql_upd_fases, {"id_usuario": id_usuario, "id_kanban": id_kanban})

        db.session.commit()
        _invalidar_kanban(id_emp=id_emp, id_kanban=id_kanban)
        _emitir_evento_kanban(
            id_kanban,
            "kanban_inativado",
            {"IDDimKanban": id_kanban, "NomeKanban": kanban.get("NomeKanban")},
        )
        return jsonify({"ok": True})
    except Exception as exc:
        db.session.rollback()
        current_app.logger.exception("Erro ao inativar kanban id_kanban=%s", id_kanban)
        return jsonify({"ok": False, "msg": f"Erro ao inativar kanban: {str(exc)}"}), 500














def _sql_join_resumo_paineis_card(alias_card: str, alias_resumo: str = "rp") -> str:
    return f"""
        OUTER APPLY (
            SELECT
                COUNT(1) AS QuantidadePaineisVinculados,
                COUNT(DISTINCT TRY_CONVERT(int, pf.IDDimPaineisEuromidia)) AS QuantidadePaineisUnicos,
                CAST(
                    SUM(
                        COALESCE(
                            TRY_CONVERT(decimal(18, 2), pf.ValorVendaFinal),
                            TRY_CONVERT(decimal(18, 2), pf.NovoValor),
                            TRY_CONVERT(decimal(18, 2), pf.ValorTabela),
                            0
                        )
                    )
                    AS decimal(18, 2)
                ) AS ValorTotalPaineis
            FROM [Kanban].[Silver].[FatoKanbanCardPainelFace] pf
            WHERE pf.IDFatoKanbanCard = {alias_card}.IDFatoKanbanCard
              AND ISNULL(pf.Ativo, 1) = 1
        ) {alias_resumo}
    """













def _executar_sql_mapeado(sql: str, parametros: dict | None = None) -> list[dict]:
    resultado = db.session.execute(text(sql), parametros or {})
    return [dict(linha) for linha in resultado.mappings().all()]


"""Eu limpo texto de filtro para evitar espaços sobrando."""
def _normalizar_texto_filtro(texto: str | None) -> str:
    return " ".join((texto or "").strip().split())


"""Eu busco as fases disponíveis para montar o filtro da tela."""
def _listar_fases_historico_cards(id_empresa_proprietaria: int) -> list[dict]:
    sql = """
    SELECT
        f.IDDimKanbanFase AS id_fase,
        f.IDDimKanban AS id_kanban,
        f.NomeFase AS nome_fase,
        f.OrdemFase AS ordem_fase,
        f.TipoFase AS tipo_fase,
        NULLIF(LTRIM(RTRIM(ISNULL(f.CorHex, ''))), '') AS cor_fase,
        NULLIF(LTRIM(RTRIM(ISNULL(f.CorTextoHex, ''))), '') AS cor_texto_fase
    FROM [Kanban].[Silver].[DimKanbanFase] f
    WHERE ISNULL(f.Ativo, 1) = 1
      AND EXISTS (
            SELECT 1
            FROM [Kanban].[Silver].[FatoKanbanCard] c
            WHERE c.IDEmpresaProprietaria = :id_empresa_proprietaria
              AND c.IDDimKanban = f.IDDimKanban
      )
    ORDER BY
        f.IDDimKanban,
        ISNULL(f.OrdemFase, 999999),
        f.NomeFase
    """
    return _executar_sql_mapeado(
        sql,
        {"id_empresa_proprietaria": id_empresa_proprietaria},
    )



def _listar_cards_resumo_historico(
    id_empresa_proprietaria: int,
    termo_busca: str = "",
    id_fase: int | None = None,
    status_card: str | None = None,
    somente_ativos: bool = True,
    offset: int = 0,
    limit: int = 10,
) -> list[dict]:
    termo_busca = _normalizar_texto_filtro(termo_busca)

    try:
        offset = max(int(offset or 0), 0)
    except Exception:
        offset = 0

    try:
        limit = int(limit or 10)
    except Exception:
        limit = 10

    limit = max(1, min(limit, 100))
    termo_like = f"%{termo_busca}%"

    sql = """
    SELECT
        c.IDFatoKanbanCard AS id_card,
        c.Titulo AS titulo,
        c.Descricao AS descricao,
        c.CriadoEm AS criado_em,
        c.AtualizadoEm AS atualizado_em,
        c.EncerradoEm AS encerrado_em,
        c.Ativo AS ativo,
        c.StatusCard AS status_card,
        c.IDDimKanbanStatusCard AS id_status_card,
        c.IDEmpresa AS id_empresa_relacionada,
        c.IDVendedor AS id_vendedor,
        c.IDVendedorUsuario AS id_vendedor_usuario,
        c.IDDimUsuarios AS id_usuario_criador,
        c.IDDimKanban AS id_kanban,
        c.IDDimKanbanFaseAtual AS id_fase_atual,

        NULLIF(LTRIM(RTRIM(ISNULL(emp.RazaoSocial, ''))), '') AS razao_social_empresa_relacionada,
        NULLIF(LTRIM(RTRIM(ISNULL(emp.NomeFantasia, ''))), '') AS nome_fantasia_empresa_relacionada,
        NULLIF(LTRIM(RTRIM(ISNULL(emp.CNPJ, ''))), '') AS cnpj_empresa_relacionada,

        fase.NomeFase AS nome_fase_atual,
        NULLIF(LTRIM(RTRIM(ISNULL(fase.CorHex, ''))), '') AS cor_fase,
        NULLIF(LTRIM(RTRIM(ISNULL(fase.CorTextoHex, ''))), '') AS cor_texto_fase,

        usuario.NomeUsuario AS nome_usuario_responsavel,

        ISNULL(obs.total_observacoes, 0) AS total_observacoes,
        ISNULL(mov.total_movimentacoes, 0) AS total_movimentacoes,
        ISNULL(tag.total_tags_ativas, 0) AS total_tags_ativas,
        ISNULL(item.total_itens_ativos, 0) AS total_itens_ativos,
        ISNULL(preco.total_alteracoes_preco, 0) AS total_alteracoes_preco,
        atividade.ultima_atividade_em AS ultima_atividade_em

    FROM [Kanban].[Silver].[FatoKanbanCard] c

    LEFT JOIN [Integracao].[Silver].[DimEmpresas] emp
        ON emp.IDEmpresa = c.IDEmpresa

    LEFT JOIN [Kanban].[Silver].[DimKanbanFase] fase
        ON fase.IDDimKanbanFase = c.IDDimKanbanFaseAtual
       AND fase.IDDimKanban = c.IDDimKanban

    LEFT JOIN [Integracao].[Silver].[DimUsuarios] usuario
        ON usuario.IDDimUsuarios = COALESCE(c.IDVendedorUsuario, c.IDDimUsuarios)
       AND usuario.IDEmpresaProprietaria = c.IDEmpresaProprietaria

    OUTER APPLY (
        SELECT
            COUNT(1) AS total_observacoes,
            MAX(o.CriadoEm) AS ultima_observacao_em
        FROM [Kanban].[Silver].[FatoKanbanCardObservacoes] o
        WHERE o.IDFatoKanbanCard = c.IDFatoKanbanCard
          AND o.IDEmpresaProprietaria = c.IDEmpresaProprietaria
    ) obs

    OUTER APPLY (
        SELECT
            COUNT(1) AS total_movimentacoes,
            MAX(m.MovidoEm) AS ultimo_movimento_em
        FROM [Kanban].[Silver].[FatoKanbanCardMovimento] m
        WHERE m.IDFatoKanbanCard = c.IDFatoKanbanCard
          AND m.IDEmpresaProprietaria = c.IDEmpresaProprietaria
    ) mov

    OUTER APPLY (
        SELECT
            SUM(CASE WHEN t.RemovidoEm IS NULL THEN 1 ELSE 0 END) AS total_tags_ativas,
            MAX(COALESCE(t.RemovidoEm, t.AplicadoEm)) AS ultimo_evento_tag_em
        FROM [Kanban].[Silver].[FatoKanbanCardTag] t
        WHERE t.IDFatoKanbanCard = c.IDFatoKanbanCard
          AND t.IDEmpresaProprietaria = c.IDEmpresaProprietaria
    ) tag

    OUTER APPLY (
        SELECT
            SUM(
                CASE
                    WHEN ISNULL(i.Ativo, 1) = 1 AND i.RemovidoEm IS NULL THEN 1
                    ELSE 0
                END
            ) AS total_itens_ativos,
            MAX(COALESCE(i.RemovidoEm, i.DataAtualizacao, i.CriadoEm)) AS ultimo_item_em
        FROM [Kanban].[Silver].[FatoKanbanCardPainelFace] i
        WHERE i.IDFatoKanbanCard = c.IDFatoKanbanCard
          AND i.IDEmpresaProprietaria = c.IDEmpresaProprietaria
    ) item

    OUTER APPLY (
        SELECT
            SUM(
                CASE
                    WHEN i.NovoValor IS NOT NULL
                      OR i.PercentualDesconto IS NOT NULL
                      OR i.ValorVendaFinal IS NOT NULL
                    THEN 1
                    ELSE 0
                END
            ) AS total_alteracoes_preco,
            MAX(
                CASE
                    WHEN i.NovoValor IS NOT NULL
                      OR i.PercentualDesconto IS NOT NULL
                      OR i.ValorVendaFinal IS NOT NULL
                    THEN COALESCE(i.DataAtualizacao, i.CriadoEm)
                    ELSE NULL
                END
            ) AS ultima_alteracao_preco_em
        FROM [Kanban].[Silver].[FatoKanbanCardPainelFace] i
        WHERE i.IDFatoKanbanCard = c.IDFatoKanbanCard
          AND i.IDEmpresaProprietaria = c.IDEmpresaProprietaria
    ) preco

    OUTER APPLY (
        SELECT
            MAX(v.data_evento) AS ultima_atividade_em
        FROM (
            VALUES
                (c.CriadoEm),
                (c.AtualizadoEm),
                (c.EncerradoEm),
                (obs.ultima_observacao_em),
                (mov.ultimo_movimento_em),
                (tag.ultimo_evento_tag_em),
                (item.ultimo_item_em),
                (preco.ultima_alteracao_preco_em)
        ) v(data_evento)
    ) atividade

    WHERE c.IDEmpresaProprietaria = :id_empresa_proprietaria
      AND (:id_fase IS NULL OR c.IDDimKanbanFaseAtual = :id_fase)
      AND (:status_card IS NULL OR LTRIM(RTRIM(ISNULL(c.StatusCard, ''))) = :status_card)
      AND (:somente_ativos = 0 OR ISNULL(c.Ativo, 1) = 1)
      AND (
            :termo_busca = ''
            OR ISNULL(c.Titulo, '') LIKE :termo_like
            OR ISNULL(c.Descricao, '') LIKE :termo_like
            OR CAST(c.IDFatoKanbanCard AS VARCHAR(30)) LIKE :termo_like
            OR CAST(ISNULL(c.IDEmpresa, '') AS VARCHAR(30)) LIKE :termo_like
            OR ISNULL(emp.RazaoSocial, '') LIKE :termo_like
            OR ISNULL(emp.NomeFantasia, '') LIKE :termo_like
            OR ISNULL(emp.CNPJ, '') LIKE :termo_like
          )

    ORDER BY
        COALESCE(atividade.ultima_atividade_em, c.AtualizadoEm, c.CriadoEm) DESC,
        c.IDFatoKanbanCard DESC

    OFFSET :offset ROWS
    FETCH NEXT :limit ROWS ONLY
    """

    return _executar_sql_mapeado(
        sql,
        {
            "id_empresa_proprietaria": int(id_empresa_proprietaria),
            "id_fase": id_fase,
            "status_card": status_card,
            "somente_ativos": 1 if somente_ativos else 0,
            "termo_busca": termo_busca,
            "termo_like": termo_like,
            "offset": int(offset),
            "limit": int(limit),
        },
    )






"""Eu busco os status existentes para montar o filtro da tela."""
def _listar_status_historico_cards(id_empresa_proprietaria: int) -> list[dict]:
    sql = """
    SELECT DISTINCT
        LTRIM(RTRIM(c.StatusCard)) AS status_card
    FROM [Kanban].[Silver].[FatoKanbanCard] c
    WHERE c.IDEmpresaProprietaria = :id_empresa_proprietaria
      AND NULLIF(LTRIM(RTRIM(c.StatusCard)), '') IS NOT NULL
    ORDER BY
        LTRIM(RTRIM(c.StatusCard))
    """
    return _executar_sql_mapeado(
        sql,
        {"id_empresa_proprietaria": id_empresa_proprietaria},
    )






"""Eu busco a lista resumida de cards para a tela de histórico com paginação e fase correta."""
def _listar_cards_resumo_historico(
    id_empresa_proprietaria: int,
    termo_busca: str = "",
    id_fase: int | None = None,
    status_card: str | None = None,
    somente_ativos: bool = True,
    offset: int = 0,
    limit: int = 10,
) -> list[dict]:
    termo_busca = _normalizar_texto_filtro(termo_busca)

    try:
        offset = max(int(offset or 0), 0)
    except Exception:
        offset = 0

    try:
        limit = int(limit or 10)
    except Exception:
        limit = 10

    limit = max(1, min(limit, 100))
    termo_like = f"%{termo_busca}%"

    sql = """
    SELECT
        c.IDFatoKanbanCard AS id_card,
        c.Titulo AS titulo,
        c.Descricao AS descricao,
        c.CriadoEm AS criado_em,
        c.AtualizadoEm AS atualizado_em,
        c.EncerradoEm AS encerrado_em,
        c.Ativo AS ativo,
        c.StatusCard AS status_card,
        c.IDDimKanbanStatusCard AS id_status_card,
        c.IDEmpresa AS id_empresa_relacionada,
        c.IDVendedor AS id_vendedor,
        c.IDVendedorUsuario AS id_vendedor_usuario,
        c.IDDimUsuarios AS id_usuario_criador,
        c.IDDimKanban AS id_kanban,
        c.IDDimKanbanFaseAtual AS id_fase_atual,

        fase.NomeFase AS nome_fase_atual,
        NULLIF(LTRIM(RTRIM(ISNULL(fase.CorHex, ''))), '') AS cor_fase,
        NULLIF(LTRIM(RTRIM(ISNULL(fase.CorTextoHex, ''))), '') AS cor_texto_fase,

        usuario.NomeUsuario AS nome_usuario_responsavel,

        ISNULL(obs.total_observacoes, 0) AS total_observacoes,
        ISNULL(mov.total_movimentacoes, 0) AS total_movimentacoes,
        ISNULL(tag.total_tags_ativas, 0) AS total_tags_ativas,
        ISNULL(item.total_itens_ativos, 0) AS total_itens_ativos,
        ISNULL(preco.total_alteracoes_preco, 0) AS total_alteracoes_preco,
        atividade.ultima_atividade_em AS ultima_atividade_em

    FROM [Kanban].[Silver].[FatoKanbanCard] c

    LEFT JOIN [Kanban].[Silver].[DimKanbanFase] fase
        ON fase.IDDimKanbanFase = c.IDDimKanbanFaseAtual

    LEFT JOIN [Integracao].[Silver].[DimUsuarios] usuario
        ON usuario.IDDimUsuarios = COALESCE(c.IDVendedorUsuario, c.IDDimUsuarios)
       AND usuario.IDEmpresaProprietaria = c.IDEmpresaProprietaria

    OUTER APPLY (
        SELECT
            COUNT(1) AS total_observacoes,
            MAX(o.CriadoEm) AS ultima_observacao_em
        FROM [Kanban].[Silver].[FatoKanbanCardObservacoes] o
        WHERE o.IDFatoKanbanCard = c.IDFatoKanbanCard
          AND o.IDEmpresaProprietaria = c.IDEmpresaProprietaria
    ) obs

    OUTER APPLY (
        SELECT
            COUNT(1) AS total_movimentacoes,
            MAX(m.MovidoEm) AS ultimo_movimento_em
        FROM [Kanban].[Silver].[FatoKanbanCardMovimento] m
        WHERE m.IDFatoKanbanCard = c.IDFatoKanbanCard
          AND m.IDEmpresaProprietaria = c.IDEmpresaProprietaria
    ) mov

    OUTER APPLY (
        SELECT
            SUM(CASE WHEN t.RemovidoEm IS NULL THEN 1 ELSE 0 END) AS total_tags_ativas,
            MAX(COALESCE(t.RemovidoEm, t.AplicadoEm)) AS ultimo_evento_tag_em
        FROM [Kanban].[Silver].[FatoKanbanCardTag] t
        WHERE t.IDFatoKanbanCard = c.IDFatoKanbanCard
          AND t.IDEmpresaProprietaria = c.IDEmpresaProprietaria
    ) tag

    OUTER APPLY (
        SELECT
            SUM(
                CASE
                    WHEN ISNULL(i.Ativo, 1) = 1 AND i.RemovidoEm IS NULL THEN 1
                    ELSE 0
                END
            ) AS total_itens_ativos,
            MAX(COALESCE(i.RemovidoEm, i.DataAtualizacao, i.CriadoEm)) AS ultimo_item_em
        FROM [Kanban].[Silver].[FatoKanbanCardPainelFace] i
        WHERE i.IDFatoKanbanCard = c.IDFatoKanbanCard
          AND i.IDEmpresaProprietaria = c.IDEmpresaProprietaria
    ) item

    OUTER APPLY (
        SELECT
            SUM(
                CASE
                    WHEN i.NovoValor IS NOT NULL
                      OR i.PercentualDesconto IS NOT NULL
                      OR i.ValorVendaFinal IS NOT NULL
                    THEN 1
                    ELSE 0
                END
            ) AS total_alteracoes_preco,
            MAX(
                CASE
                    WHEN i.NovoValor IS NOT NULL
                      OR i.PercentualDesconto IS NOT NULL
                      OR i.ValorVendaFinal IS NOT NULL
                    THEN COALESCE(i.DataAtualizacao, i.CriadoEm)
                    ELSE NULL
                END
            ) AS ultima_alteracao_preco_em
        FROM [Kanban].[Silver].[FatoKanbanCardPainelFace] i
        WHERE i.IDFatoKanbanCard = c.IDFatoKanbanCard
          AND i.IDEmpresaProprietaria = c.IDEmpresaProprietaria
    ) preco

    OUTER APPLY (
        SELECT
            MAX(v.data_evento) AS ultima_atividade_em
        FROM (
            VALUES
                (c.CriadoEm),
                (c.AtualizadoEm),
                (c.EncerradoEm),
                (obs.ultima_observacao_em),
                (mov.ultimo_movimento_em),
                (tag.ultimo_evento_tag_em),
                (item.ultimo_item_em),
                (preco.ultima_alteracao_preco_em)
        ) v(data_evento)
    ) atividade

    WHERE c.IDEmpresaProprietaria = :id_empresa_proprietaria
      AND (:id_fase IS NULL OR c.IDDimKanbanFaseAtual = :id_fase)
      AND (:status_card IS NULL OR LTRIM(RTRIM(ISNULL(c.StatusCard, ''))) = :status_card)
      AND (:somente_ativos = 0 OR ISNULL(c.Ativo, 1) = 1)
      AND (
            :termo_busca = ''
            OR ISNULL(c.Titulo, '') LIKE :termo_like
            OR ISNULL(c.Descricao, '') LIKE :termo_like
            OR CAST(c.IDFatoKanbanCard AS VARCHAR(30)) LIKE :termo_like
            OR CAST(ISNULL(c.IDEmpresa, '') AS VARCHAR(30)) LIKE :termo_like
          )

    ORDER BY
        COALESCE(atividade.ultima_atividade_em, c.AtualizadoEm, c.CriadoEm) DESC,
        c.IDFatoKanbanCard DESC

    OFFSET :offset ROWS
    FETCH NEXT :limit ROWS ONLY
    """

    return _executar_sql_mapeado(
        sql,
        {
            "id_empresa_proprietaria": int(id_empresa_proprietaria),
            "id_fase": id_fase,
            "status_card": status_card,
            "somente_ativos": 1 if somente_ativos else 0,
            "termo_busca": termo_busca,
            "termo_like": termo_like,
            "offset": int(offset),
            "limit": int(limit),
        },
    )





def _contar_cards_resumo_historico(
    id_empresa_proprietaria: int,
    termo_busca: str = "",
    id_fase: int | None = None,
    status_card: str | None = None,
    somente_ativos: bool = True,
) -> int:
    termo_busca = _normalizar_texto_filtro(termo_busca)
    termo_like = f"%{termo_busca}%"

    sql = """
    SELECT
        COUNT(1) AS total
    FROM [Kanban].[Silver].[FatoKanbanCard] c

    LEFT JOIN [Integracao].[Silver].[DimEmpresas] emp
        ON emp.IDEmpresa = c.IDEmpresa

    WHERE c.IDEmpresaProprietaria = :id_empresa_proprietaria
      AND (:id_fase IS NULL OR c.IDDimKanbanFaseAtual = :id_fase)
      AND (:status_card IS NULL OR LTRIM(RTRIM(ISNULL(c.StatusCard, ''))) = :status_card)
      AND (:somente_ativos = 0 OR ISNULL(c.Ativo, 1) = 1)
      AND (
            :termo_busca = ''
            OR ISNULL(c.Titulo, '') LIKE :termo_like
            OR ISNULL(c.Descricao, '') LIKE :termo_like
            OR CAST(c.IDFatoKanbanCard AS VARCHAR(30)) LIKE :termo_like
            OR CAST(ISNULL(c.IDEmpresa, '') AS VARCHAR(30)) LIKE :termo_like
            OR ISNULL(emp.RazaoSocial, '') LIKE :termo_like
            OR ISNULL(emp.NomeFantasia, '') LIKE :termo_like
            OR ISNULL(emp.CNPJ, '') LIKE :termo_like
          )
    """

    linhas = _executar_sql_mapeado(
        sql,
        {
            "id_empresa_proprietaria": int(id_empresa_proprietaria),
            "id_fase": id_fase,
            "status_card": status_card,
            "somente_ativos": 1 if somente_ativos else 0,
            "termo_busca": termo_busca,
            "termo_like": termo_like,
        },
    )

    if not linhas:
        return 0

    try:
        return int(linhas[0].get("total") or 0)
    except Exception:
        return 0



@kanban_bp.route("/historico-cards", methods=["GET"])
@login_required
@limiter.limit("60/minute")
def historico_cards_lista():
    _assert_login()
    id_empresa_proprietaria = _id_empresa_usuario_or_403()

    termo_busca = _normalizar_texto_filtro(request.args.get("q") or "")
    status_card = (request.args.get("status") or "").strip() or None

    try:
        id_fase = int(request.args.get("id_fase") or "0")
        if id_fase <= 0:
            id_fase = None
    except Exception:
        id_fase = None

    try:
        page = int(request.args.get("page") or "1")
    except Exception:
        page = 1

    per_page = 10
    page = max(1, page)
    offset = (page - 1) * per_page

    somente_ativos = str(request.args.get("somente_ativos", "1")).strip() != "0"

    total = _contar_cards_resumo_historico(
        id_empresa_proprietaria=id_empresa_proprietaria,
        termo_busca=termo_busca,
        id_fase=id_fase,
        status_card=status_card,
        somente_ativos=somente_ativos,
    )

    total_pages = max(1, (total + per_page - 1) // per_page)

    if page > total_pages:
        page = total_pages
        offset = (page - 1) * per_page

    cards = _listar_cards_resumo_historico(
        id_empresa_proprietaria=id_empresa_proprietaria,
        termo_busca=termo_busca,
        id_fase=id_fase,
        status_card=status_card,
        somente_ativos=somente_ativos,
        offset=offset,
        limit=per_page,
    )

    fases = _listar_fases_historico_cards(id_empresa_proprietaria)
    opcoes_status = _listar_status_historico_cards(id_empresa_proprietaria)

    filtros = {
        "q": termo_busca,
        "id_fase": id_fase,
        "status": status_card or "",
        "somente_ativos": somente_ativos,
        "per_page": per_page,
    }

    inicio = 0 if total == 0 else offset + 1
    fim = min(offset + per_page, total)

    paginacao = {
        "page": page,
        "per_page": per_page,
        "total": total,
        "total_pages": total_pages,
        "inicio": inicio,
        "fim": fim,
    }

    return render_template(
        "kanban/historico_cards_lista.html",
        cards=cards,
        fases=fases,
        opcoes_status=opcoes_status,
        filtros=filtros,
        paginacao=paginacao,
        total_cards=total,
    )














def _normalizar_payload_historico(payload) -> str:
    if payload is None:
        return ""

    if isinstance(payload, str):
        texto = payload.strip()
        return texto

    try:
        return json.dumps(payload, ensure_ascii=False, indent=2)
    except Exception:
        return str(payload).strip()


"""Eu tento padronizar a data para ordenar a timeline corretamente."""
def _normalizar_data_evento_historico(valor):
    if valor is None:
        return None

    if isinstance(valor, datetime):
        return valor

    try:
        return datetime.fromisoformat(str(valor))
    except Exception:
        return None



def _buscar_cabecalho_historico_card(id_card: int, id_empresa_proprietaria: int) -> dict | None:
    sql = """
    SELECT TOP 1
        c.IDFatoKanbanCard AS id_card,
        c.IDDimKanban AS id_kanban,
        c.IDDimKanbanFaseAtual AS id_fase_atual,
        c.Titulo AS titulo,
        c.Descricao AS descricao,
        c.StatusCard AS status_card,
        c.IDDimKanbanStatusCard AS id_status_card,
        c.Ativo AS ativo,
        c.CriadoEm AS criado_em,
        c.AtualizadoEm AS atualizado_em,
        c.EncerradoEm AS encerrado_em,
        c.IDEmpresa AS id_empresa_relacionada,
        c.IDVendedor AS id_vendedor,
        c.IDVendedorUsuario AS id_vendedor_usuario,
        c.IDDimUsuarios AS id_usuario_criador,
        c.IDDimKanbanMotivoEncerramento AS id_motivo_encerramento,
        c.MotivoEncerramentoObs AS motivo_encerramento_obs,

        fase.NomeFase AS nome_fase_atual,
        fase.CorHex AS cor_fase,
        fase.CorTextoHex AS cor_texto_fase,

        usuario.NomeUsuario AS nome_usuario_responsavel,
        motivo.NomeMotivo AS nome_motivo_encerramento

    FROM [Kanban].[Silver].[FatoKanbanCard] c

    OUTER APPLY (
        SELECT TOP (1)
            f.NomeFase,
            f.CorHex,
            f.CorTextoHex
        FROM [Kanban].[Silver].[DimKanbanFase] f
        WHERE f.IDDimKanbanFase = c.IDDimKanbanFaseAtual
        ORDER BY
            CASE
                WHEN f.IDDimKanban = c.IDDimKanban
                 AND ISNULL(f.IDEmpresaProprietaria, c.IDEmpresaProprietaria) = c.IDEmpresaProprietaria THEN 0
                WHEN f.IDDimKanban = c.IDDimKanban THEN 1
                WHEN ISNULL(f.IDEmpresaProprietaria, c.IDEmpresaProprietaria) = c.IDEmpresaProprietaria THEN 2
                ELSE 3
            END,
            f.IDDimKanbanFase
    ) fase

    OUTER APPLY (
        SELECT TOP (1)
            u.NomeUsuario
        FROM [Integracao].[Silver].[DimUsuarios] u
        WHERE u.IDDimUsuarios = COALESCE(c.IDVendedorUsuario, c.IDDimUsuarios)
        ORDER BY
            CASE
                WHEN u.IDEmpresaProprietaria = c.IDEmpresaProprietaria THEN 0
                WHEN u.IDEmpresaProprietaria IS NULL THEN 1
                ELSE 2
            END,
            u.IDDimUsuarios
    ) usuario

    LEFT JOIN [Kanban].[Silver].[DimKanbanMotivoEncerramento] motivo
        ON motivo.IDDimKanbanMotivoEncerramento = c.IDDimKanbanMotivoEncerramento

    WHERE c.IDFatoKanbanCard = :id_card
      AND c.IDEmpresaProprietaria = :id_empresa_proprietaria
    """
    linhas = _executar_sql_mapeado(
        sql,
        {
            "id_card": id_card,
            "id_empresa_proprietaria": id_empresa_proprietaria,
        },
    )
    return linhas[0] if linhas else None





"""Eu busco as movimentações de fase do card."""
def _buscar_movimentacoes_historico_card(id_card: int, id_empresa_proprietaria: int) -> list[dict]:
    sql = """
    SELECT
        m.IDFatoKanbanCardMovimento AS id_movimento,
        m.IDFatoKanbanCard AS id_card,
        m.IDFaseDe AS id_fase_de,
        m.IDFasePara AS id_fase_para,
        m.MovidoEm AS movido_em,
        m.MovidoPor AS id_usuario,
        m.Observacao AS observacao,
        m.IDDimKanbanTag AS id_tag,
        m.IDDimKanbanStatusCard AS id_status_card,
        m.IDDimKanban AS id_kanban,
        m.IDEmpresaProprietaria AS id_empresa_proprietaria_evento,

        COALESCE(
            fase_de.NomeFase,
            CASE
                WHEN m.IDFaseDe IS NOT NULL THEN CONCAT('Fase ID ', CONVERT(varchar(20), m.IDFaseDe))
                ELSE NULL
            END
        ) AS nome_fase_de,

        COALESCE(
            fase_para.NomeFase,
            CASE
                WHEN m.IDFasePara IS NOT NULL THEN CONCAT('Fase ID ', CONVERT(varchar(20), m.IDFasePara))
                ELSE NULL
            END
        ) AS nome_fase_para,

        usuario.NomeUsuario AS nome_usuario

    FROM [Kanban].[Silver].[FatoKanbanCardMovimento] m

    INNER JOIN [Kanban].[Silver].[FatoKanbanCard] card_aut
        ON card_aut.IDFatoKanbanCard = m.IDFatoKanbanCard
       AND card_aut.IDEmpresaProprietaria = :id_empresa_proprietaria

    OUTER APPLY (
        SELECT TOP (1)
            f.NomeFase
        FROM [Kanban].[Silver].[DimKanbanFase] f
        WHERE f.IDDimKanbanFase = m.IDFaseDe
        ORDER BY
            CASE
                WHEN f.IDDimKanban = COALESCE(m.IDDimKanban, card_aut.IDDimKanban)
                 AND ISNULL(f.IDEmpresaProprietaria, card_aut.IDEmpresaProprietaria) = card_aut.IDEmpresaProprietaria THEN 0
                WHEN f.IDDimKanban = COALESCE(m.IDDimKanban, card_aut.IDDimKanban)
                 AND ISNULL(f.IDEmpresaProprietaria, m.IDEmpresaProprietaria) = m.IDEmpresaProprietaria THEN 1
                WHEN f.IDDimKanban = COALESCE(m.IDDimKanban, card_aut.IDDimKanban) THEN 2
                WHEN ISNULL(f.IDEmpresaProprietaria, card_aut.IDEmpresaProprietaria) = card_aut.IDEmpresaProprietaria THEN 3
                ELSE 4
            END,
            f.IDDimKanbanFase
    ) fase_de

    OUTER APPLY (
        SELECT TOP (1)
            f.NomeFase
        FROM [Kanban].[Silver].[DimKanbanFase] f
        WHERE f.IDDimKanbanFase = m.IDFasePara
        ORDER BY
            CASE
                WHEN f.IDDimKanban = COALESCE(m.IDDimKanban, card_aut.IDDimKanban)
                 AND ISNULL(f.IDEmpresaProprietaria, card_aut.IDEmpresaProprietaria) = card_aut.IDEmpresaProprietaria THEN 0
                WHEN f.IDDimKanban = COALESCE(m.IDDimKanban, card_aut.IDDimKanban)
                 AND ISNULL(f.IDEmpresaProprietaria, m.IDEmpresaProprietaria) = m.IDEmpresaProprietaria THEN 1
                WHEN f.IDDimKanban = COALESCE(m.IDDimKanban, card_aut.IDDimKanban) THEN 2
                WHEN ISNULL(f.IDEmpresaProprietaria, card_aut.IDEmpresaProprietaria) = card_aut.IDEmpresaProprietaria THEN 3
                ELSE 4
            END,
            f.IDDimKanbanFase
    ) fase_para

    OUTER APPLY (
        SELECT TOP (1)
            u.NomeUsuario
        FROM [Integracao].[Silver].[DimUsuarios] u
        WHERE u.IDDimUsuarios = m.MovidoPor
        ORDER BY
            CASE
                WHEN u.IDEmpresaProprietaria = card_aut.IDEmpresaProprietaria THEN 0
                WHEN u.IDEmpresaProprietaria = m.IDEmpresaProprietaria THEN 1
                WHEN u.IDEmpresaProprietaria IS NULL THEN 2
                ELSE 3
            END,
            u.IDDimUsuarios
    ) usuario

    WHERE m.IDFatoKanbanCard = :id_card

    ORDER BY
        m.MovidoEm DESC,
        m.IDFatoKanbanCardMovimento DESC
    """
    return _executar_sql_mapeado(
        sql,
        {
            "id_card": id_card,
            "id_empresa_proprietaria": id_empresa_proprietaria,
        },
    )


"""Eu busco as observações do card em ordem da mais recente para a mais antiga."""
def _buscar_observacoes_historico_card(id_card: int, id_empresa_proprietaria: int) -> list[dict]:
    sql = """
    SELECT
        o.IDFatoKanbanCardObservacoes AS id_observacao,
        o.Observacao AS observacao,
        o.IDFatoKanbanCard AS id_card,
        o.IDDimKanbanStatusCard AS id_status_card,
        o.IDDimKanbanFase AS id_fase,
        o.IDDimUsuarios AS id_usuario,
        o.CriadoEm AS criado_em,
        o.IDEmpresaProprietaria AS id_empresa_proprietaria_evento,

        COALESCE(
            fase.NomeFase,
            CASE
                WHEN o.IDDimKanbanFase IS NOT NULL THEN CONCAT('Fase ID ', CONVERT(varchar(20), o.IDDimKanbanFase))
                ELSE NULL
            END
        ) AS nome_fase,

        usuario.NomeUsuario AS nome_usuario

    FROM [Kanban].[Silver].[FatoKanbanCardObservacoes] o

    INNER JOIN [Kanban].[Silver].[FatoKanbanCard] card_aut
        ON card_aut.IDFatoKanbanCard = o.IDFatoKanbanCard
       AND card_aut.IDEmpresaProprietaria = :id_empresa_proprietaria

    OUTER APPLY (
        SELECT TOP (1)
            f.NomeFase
        FROM [Kanban].[Silver].[DimKanbanFase] f
        WHERE f.IDDimKanbanFase = o.IDDimKanbanFase
        ORDER BY
            CASE
                WHEN f.IDDimKanban = card_aut.IDDimKanban
                 AND ISNULL(f.IDEmpresaProprietaria, card_aut.IDEmpresaProprietaria) = card_aut.IDEmpresaProprietaria THEN 0
                WHEN f.IDDimKanban = card_aut.IDDimKanban
                 AND ISNULL(f.IDEmpresaProprietaria, o.IDEmpresaProprietaria) = o.IDEmpresaProprietaria THEN 1
                WHEN f.IDDimKanban = card_aut.IDDimKanban THEN 2
                WHEN ISNULL(f.IDEmpresaProprietaria, card_aut.IDEmpresaProprietaria) = card_aut.IDEmpresaProprietaria THEN 3
                ELSE 4
            END,
            f.IDDimKanbanFase
    ) fase

    OUTER APPLY (
        SELECT TOP (1)
            u.NomeUsuario
        FROM [Integracao].[Silver].[DimUsuarios] u
        WHERE u.IDDimUsuarios = o.IDDimUsuarios
        ORDER BY
            CASE
                WHEN u.IDEmpresaProprietaria = card_aut.IDEmpresaProprietaria THEN 0
                WHEN u.IDEmpresaProprietaria = o.IDEmpresaProprietaria THEN 1
                WHEN u.IDEmpresaProprietaria IS NULL THEN 2
                ELSE 3
            END,
            u.IDDimUsuarios
    ) usuario

    WHERE o.IDFatoKanbanCard = :id_card

    ORDER BY
        o.CriadoEm DESC,
        o.IDFatoKanbanCardObservacoes DESC
    """
    return _executar_sql_mapeado(
        sql,
        {
            "id_card": id_card,
            "id_empresa_proprietaria": id_empresa_proprietaria,
        },
    )


"""Eu busco o histórico de status do card."""
def _buscar_status_historico_card(id_card: int, id_empresa_proprietaria: int) -> list[dict]:
    sql = """
    SELECT
        s.IDDimKanbanCardTagHistorico AS id_status_historico,
        s.IDFatoKanbanCard AS id_card,
        s.IDDimKanbanStatusCard AS id_status_card,
        s.IDDimKanbanFase AS id_fase,
        s.IDDimUsuarios AS id_usuario,
        s.IDEmpresaProprietaria AS id_empresa_proprietaria_evento,

        COALESCE(
            fase.NomeFase,
            CASE
                WHEN s.IDDimKanbanFase IS NOT NULL THEN CONCAT('Fase ID ', CONVERT(varchar(20), s.IDDimKanbanFase))
                ELSE NULL
            END
        ) AS nome_fase,

        usuario.NomeUsuario AS nome_usuario,
        status_card.CodigoStatus AS codigo_status,
        status_card.NomeExibicao AS nome_status

    FROM [Kanban].[Silver].[FatoKanbanCardStatusHistorico] s

    INNER JOIN [Kanban].[Silver].[FatoKanbanCard] card_aut
        ON card_aut.IDFatoKanbanCard = s.IDFatoKanbanCard
       AND card_aut.IDEmpresaProprietaria = :id_empresa_proprietaria

    OUTER APPLY (
        SELECT TOP (1)
            f.NomeFase
        FROM [Kanban].[Silver].[DimKanbanFase] f
        WHERE f.IDDimKanbanFase = s.IDDimKanbanFase
        ORDER BY
            CASE
                WHEN f.IDDimKanban = card_aut.IDDimKanban
                 AND ISNULL(f.IDEmpresaProprietaria, card_aut.IDEmpresaProprietaria) = card_aut.IDEmpresaProprietaria THEN 0
                WHEN f.IDDimKanban = card_aut.IDDimKanban
                 AND ISNULL(f.IDEmpresaProprietaria, s.IDEmpresaProprietaria) = s.IDEmpresaProprietaria THEN 1
                WHEN f.IDDimKanban = card_aut.IDDimKanban THEN 2
                WHEN ISNULL(f.IDEmpresaProprietaria, card_aut.IDEmpresaProprietaria) = card_aut.IDEmpresaProprietaria THEN 3
                ELSE 4
            END,
            f.IDDimKanbanFase
    ) fase

    OUTER APPLY (
        SELECT TOP (1)
            u.NomeUsuario
        FROM [Integracao].[Silver].[DimUsuarios] u
        WHERE u.IDDimUsuarios = s.IDDimUsuarios
        ORDER BY
            CASE
                WHEN u.IDEmpresaProprietaria = card_aut.IDEmpresaProprietaria THEN 0
                WHEN u.IDEmpresaProprietaria = s.IDEmpresaProprietaria THEN 1
                WHEN u.IDEmpresaProprietaria IS NULL THEN 2
                ELSE 3
            END,
            u.IDDimUsuarios
    ) usuario

    LEFT JOIN [Kanban].[Silver].[DimKanbanStatusCard] status_card
        ON status_card.IDDimKanbanStatusCard = s.IDDimKanbanStatusCard

    WHERE s.IDFatoKanbanCard = :id_card

    ORDER BY
        s.IDDimKanbanCardTagHistorico DESC
    """
    return _executar_sql_mapeado(
        sql,
        {
            "id_card": id_card,
            "id_empresa_proprietaria": id_empresa_proprietaria,
        },
    )



"""Eu busco o histórico de encerramento do card."""
def _buscar_encerramento_historico_card(id_card: int, id_empresa_proprietaria: int) -> list[dict]:
    sql = """
    SELECT
        e.IDFatoDimHistoricoEncerramentoCard AS id_historico_encerramento,
        e.NomeMotivo AS nome_motivo,
        e.IDDimKanbanMotivoEncerramento AS id_motivo_encerramento,
        e.IDDimKanbanFase AS id_fase,
        e.IDFatoKanbanCard AS id_card,
        e.IDDimUsuarios AS id_usuario,
        e.DataAtualizacao AS data_atualizacao,
        e.Observacoes AS observacoes,

        COALESCE(
            fase.NomeFase,
            CASE
                WHEN e.IDDimKanbanFase IS NOT NULL THEN CONCAT('Fase ID ', CONVERT(varchar(20), e.IDDimKanbanFase))
                ELSE NULL
            END
        ) AS nome_fase,

        usuario.NomeUsuario AS nome_usuario

    FROM [Kanban].[Silver].[FatoDimHistoricoEncerramentoCard] e

    INNER JOIN [Kanban].[Silver].[FatoKanbanCard] card_aut
        ON card_aut.IDFatoKanbanCard = e.IDFatoKanbanCard
       AND card_aut.IDEmpresaProprietaria = :id_empresa_proprietaria

    OUTER APPLY (
        SELECT TOP (1)
            f.NomeFase
        FROM [Kanban].[Silver].[DimKanbanFase] f
        WHERE f.IDDimKanbanFase = e.IDDimKanbanFase
        ORDER BY
            CASE
                WHEN f.IDDimKanban = card_aut.IDDimKanban
                 AND ISNULL(f.IDEmpresaProprietaria, card_aut.IDEmpresaProprietaria) = card_aut.IDEmpresaProprietaria THEN 0
                WHEN f.IDDimKanban = card_aut.IDDimKanban THEN 1
                WHEN ISNULL(f.IDEmpresaProprietaria, card_aut.IDEmpresaProprietaria) = card_aut.IDEmpresaProprietaria THEN 2
                ELSE 3
            END,
            f.IDDimKanbanFase
    ) fase

    OUTER APPLY (
        SELECT TOP (1)
            u.NomeUsuario
        FROM [Integracao].[Silver].[DimUsuarios] u
        WHERE u.IDDimUsuarios = e.IDDimUsuarios
        ORDER BY
            CASE
                WHEN u.IDEmpresaProprietaria = card_aut.IDEmpresaProprietaria THEN 0
                WHEN u.IDEmpresaProprietaria IS NULL THEN 1
                ELSE 2
            END,
            u.IDDimUsuarios
    ) usuario

    WHERE e.IDFatoKanbanCard = :id_card

    ORDER BY
        e.DataAtualizacao DESC,
        e.IDFatoDimHistoricoEncerramentoCard DESC
    """
    return _executar_sql_mapeado(
        sql,
        {
            "id_card": int(id_card),
            "id_empresa_proprietaria": int(id_empresa_proprietaria),
        },
    )





"""Eu busco os logs técnicos do card."""
def _buscar_logs_historico_card(id_card: int, id_empresa_proprietaria: int) -> list[dict]:
    sql = """
    SELECT
        l.IDFatoKanbanCardLog AS id_log,
        l.IDFatoKanbanCard AS id_card,
        l.IDDimKanban AS id_kanban,
        l.IDEmpresaRelacionada AS id_empresa_relacionada,
        l.IDUsuarioAcao AS id_usuario_acao,
        l.TipoEvento AS tipo_evento,
        l.SubtipoEvento AS subtipo_evento,
        l.OcorridoEm AS ocorrido_em,
        l.IDFaseDe AS id_fase_de,
        l.IDFasePara AS id_fase_para,
        l.IDDimKanbanMotivoAcao AS id_motivo_acao,
        l.TabelaOrigem AS tabela_origem,
        l.IDRegistroOrigem AS id_registro_origem,
        l.TextoLivre AS texto_livre,
        l.PayloadAntes AS payload_antes,
        l.PayloadDepois AS payload_depois,
        l.IDEmpresaProprietaria AS id_empresa_proprietaria_evento,

        usuario.NomeUsuario AS nome_usuario_acao,

        COALESCE(
            fase_de.NomeFase,
            CASE
                WHEN l.IDFaseDe IS NOT NULL THEN CONCAT('Fase ID ', CONVERT(varchar(20), l.IDFaseDe))
                ELSE NULL
            END
        ) AS nome_fase_de,

        COALESCE(
            fase_para.NomeFase,
            CASE
                WHEN l.IDFasePara IS NOT NULL THEN CONCAT('Fase ID ', CONVERT(varchar(20), l.IDFasePara))
                ELSE NULL
            END
        ) AS nome_fase_para

    FROM [Kanban].[Silver].[FatoKanbanCardLog] l

    INNER JOIN [Kanban].[Silver].[FatoKanbanCard] card_aut
        ON card_aut.IDFatoKanbanCard = l.IDFatoKanbanCard
       AND card_aut.IDEmpresaProprietaria = :id_empresa_proprietaria

    OUTER APPLY (
        SELECT TOP (1)
            u.NomeUsuario
        FROM [Integracao].[Silver].[DimUsuarios] u
        WHERE u.IDDimUsuarios = l.IDUsuarioAcao
        ORDER BY
            CASE
                WHEN u.IDEmpresaProprietaria = card_aut.IDEmpresaProprietaria THEN 0
                WHEN u.IDEmpresaProprietaria = l.IDEmpresaProprietaria THEN 1
                WHEN u.IDEmpresaProprietaria IS NULL THEN 2
                ELSE 3
            END,
            u.IDDimUsuarios
    ) usuario

    OUTER APPLY (
        SELECT TOP (1)
            f.NomeFase
        FROM [Kanban].[Silver].[DimKanbanFase] f
        WHERE f.IDDimKanbanFase = l.IDFaseDe
        ORDER BY
            CASE
                WHEN f.IDDimKanban = COALESCE(l.IDDimKanban, card_aut.IDDimKanban)
                 AND ISNULL(f.IDEmpresaProprietaria, card_aut.IDEmpresaProprietaria) = card_aut.IDEmpresaProprietaria THEN 0
                WHEN f.IDDimKanban = COALESCE(l.IDDimKanban, card_aut.IDDimKanban)
                 AND ISNULL(f.IDEmpresaProprietaria, l.IDEmpresaProprietaria) = l.IDEmpresaProprietaria THEN 1
                WHEN f.IDDimKanban = COALESCE(l.IDDimKanban, card_aut.IDDimKanban) THEN 2
                WHEN ISNULL(f.IDEmpresaProprietaria, card_aut.IDEmpresaProprietaria) = card_aut.IDEmpresaProprietaria THEN 3
                ELSE 4
            END,
            f.IDDimKanbanFase
    ) fase_de

    OUTER APPLY (
        SELECT TOP (1)
            f.NomeFase
        FROM [Kanban].[Silver].[DimKanbanFase] f
        WHERE f.IDDimKanbanFase = l.IDFasePara
        ORDER BY
            CASE
                WHEN f.IDDimKanban = COALESCE(l.IDDimKanban, card_aut.IDDimKanban)
                 AND ISNULL(f.IDEmpresaProprietaria, card_aut.IDEmpresaProprietaria) = card_aut.IDEmpresaProprietaria THEN 0
                WHEN f.IDDimKanban = COALESCE(l.IDDimKanban, card_aut.IDDimKanban)
                 AND ISNULL(f.IDEmpresaProprietaria, l.IDEmpresaProprietaria) = l.IDEmpresaProprietaria THEN 1
                WHEN f.IDDimKanban = COALESCE(l.IDDimKanban, card_aut.IDDimKanban) THEN 2
                WHEN ISNULL(f.IDEmpresaProprietaria, card_aut.IDEmpresaProprietaria) = card_aut.IDEmpresaProprietaria THEN 3
                ELSE 4
            END,
            f.IDDimKanbanFase
    ) fase_para

    WHERE l.IDFatoKanbanCard = :id_card

    ORDER BY
        l.OcorridoEm DESC,
        l.IDFatoKanbanCardLog DESC
    """
    linhas = _executar_sql_mapeado(
        sql,
        {
            "id_card": id_card,
            "id_empresa_proprietaria": id_empresa_proprietaria,
        },
    )

    for linha in linhas:
        linha["payload_antes_texto"] = _normalizar_payload_historico(linha.get("payload_antes"))
        linha["payload_depois_texto"] = _normalizar_payload_historico(linha.get("payload_depois"))

    return linhas




def _decimal_para_float_seguro(valor: Any) -> float | None:

    """Eu tento converter qualquer número do banco para float sem estourar a tela."""
    if valor in (None, ""):
        return None

    try:
        return float(valor)
    except Exception:
        try:
            return float(Decimal(str(valor)))
        except Exception:
            return None






def _buscar_historico_precos_card(id_card: int, id_empresa_proprietaria: int) -> list[dict]:
    """Eu busco o histórico de negociação de preços do card já enriquecido e compatível com a tela de histórico."""
    sql = """
    ;WITH NegociacoesBase AS (
        SELECT
            np.*,
            ROW_NUMBER() OVER (
                PARTITION BY
                    ISNULL(np.IDDimPaineisEuromidia, -1),
                    ISNULL(np.IDDimFacesPaineis, -1)
                ORDER BY
                    COALESCE(np.DataAprovacaoPreco, np.DataPrecoProposto, np.PeriodoInicio, np.PeriodoTermino) DESC,
                    np.IDFatoKanbanNegociacaoPreco DESC
            ) AS rn_painel_face
        FROM [Kanban].[Silver].[FatoKanbanNegociacaoPreco] np
        WHERE np.IDFatoKanbanCard = :id_card
          AND np.IDEmpresaProprietaria = :id_empresa_proprietaria
    )
    SELECT
        np.IDFatoKanbanNegociacaoPreco AS id_negociacao_preco,
        np.IDFatoKanbanCard AS id_card,
        np.IDEmpresa AS id_empresa_relacionada,
        np.IDDimPaineisEuromidia AS id_painel,
        np.IDDimFacesPaineis AS id_face,

        cli.RazaoSocial AS razao_social_cliente,
        cli.NomeFantasia AS nome_fantasia_cliente,
        COALESCE(
            NULLIF(LTRIM(RTRIM(cli.NomeFantasia)), ''),
            NULLIF(LTRIM(RTRIM(cli.RazaoSocial)), ''),
            CONCAT('Empresa #', CAST(COALESCE(np.IDEmpresa, card_aut.IDEmpresa) AS VARCHAR(30)))
        ) AS nome_cliente_exibicao,

        np.IDDimKanbanFase AS id_fase,
        fase.NomeFase AS nome_fase,
        COALESCE(fase.CorHex, '#E5E7EB') AS cor_fase,
        COALESCE(fase.CorTextoHex, '#111827') AS cor_texto_fase,

        np.IDDimUsuarios AS id_usuario_solicitante,
        usu_sol.NomeUsuario AS nome_usuario_solicitante,
        usu_sol.Email AS email_usuario_solicitante,

        np.IDDimUsuariosAprovacaoPreco AS id_usuario_aprovador,
        usu_apr.NomeUsuario AS nome_usuario_aprovador,
        usu_apr.Email AS email_usuario_aprovador,

        np.DataPrecoProposto AS data_preco_proposto,
        np.DataAprovacaoPreco AS data_aprovacao_preco,
        np.PeriodoInicio AS periodo_inicio,
        np.PeriodoTermino AS periodo_termino,
        COALESCE(np.DataAprovacaoPreco, np.DataPrecoProposto, np.PeriodoInicio, np.PeriodoTermino) AS data_referencia_preco,

        np.CustoAtual AS custo_original,
        np.PrecoAtual AS preco_original,
        np.MargemAtual AS margem_original,

        np.CustoProposto AS custo_proposto,
        np.PrecoProposto AS preco_proposto,
        np.MargemProposta AS margem_proposta,
        np.DescontoProposto AS desconto_proposto,

        np.PrecoAprovado AS preco_aplicado,
        np.DescontoAprovado AS desconto_aprovado,

        np.ObservacoesProposta AS observacoes_proposta,
        np.ObservacoesAprovacao AS observacoes_aprovacao,

        np.BitAditivoContrato AS bit_aditivo_contrato,
        np.BitAutorizacaoDiretoria AS bit_autorizacao_diretoria,
        np.BitAutorizacaoCoordenador AS bit_autorizacao_coordenador,

        pf.CodPonto AS cod_ponto,
        pf.CodFace AS cod_face,
        pf.TipoPainel AS tipo_painel,

        COALESCE(pf.ValorTabela, np.PrecoAtual) AS valor_tabela,
        COALESCE(np.PrecoProposto, pf.NovoValor, np.PrecoAprovado) AS novo_valor,
        np.DescontoProposto AS percentual_desconto,
        COALESCE(np.PrecoAprovado, np.PrecoProposto, pf.ValorVendaFinal, np.PrecoAtual) AS valor_venda_final,

        CASE
            WHEN COALESCE(np.PrecoAprovado, np.PrecoProposto, pf.ValorVendaFinal, np.PrecoAtual) IS NOT NULL
             AND COALESCE(np.CustoProposto, np.CustoAtual, pf.CustoTabela) IS NOT NULL
            THEN COALESCE(np.PrecoAprovado, np.PrecoProposto, pf.ValorVendaFinal, np.PrecoAtual)
                 - COALESCE(np.CustoProposto, np.CustoAtual, pf.CustoTabela)
            ELSE pf.MargemValor
        END AS margem_valor,

        COALESCE(
            np.MargemProposta,
            CASE
                WHEN COALESCE(np.PrecoAprovado, np.PrecoProposto, pf.ValorVendaFinal, np.PrecoAtual, 0) > 0
                 AND COALESCE(np.CustoProposto, np.CustoAtual, pf.CustoTabela) IS NOT NULL
                THEN (
                    (
                        COALESCE(np.PrecoAprovado, np.PrecoProposto, pf.ValorVendaFinal, np.PrecoAtual)
                        - COALESCE(np.CustoProposto, np.CustoAtual, pf.CustoTabela)
                    )
                    / COALESCE(np.PrecoAprovado, np.PrecoProposto, pf.ValorVendaFinal, np.PrecoAtual)
                ) * 100.0
                ELSE pf.MargemPercentual
            END
        ) AS margem_percentual,

        CASE
            WHEN np.PrecoAprovado IS NOT NULL
              OR np.IDDimUsuariosAprovacaoPreco IS NOT NULL
              OR np.DataAprovacaoPreco IS NOT NULL
            THEN 'APROVADO'
            ELSE 'PENDENTE'
        END AS status_negociacao,

        CASE WHEN np.rn_painel_face = 1 THEN 1 ELSE 0 END AS ativo,
        CAST(NULL AS DATETIME) AS removido_em

    FROM NegociacoesBase np

    INNER JOIN [Kanban].[Silver].[FatoKanbanCard] card_aut
        ON card_aut.IDFatoKanbanCard = np.IDFatoKanbanCard
       AND card_aut.IDEmpresaProprietaria = :id_empresa_proprietaria

    LEFT JOIN [Kanban].[Silver].[DimKanbanFase] fase
        ON fase.IDDimKanbanFase = np.IDDimKanbanFase
       AND (
            fase.IDEmpresaProprietaria = np.IDEmpresaProprietaria
            OR fase.IDEmpresaProprietaria IS NULL
       )

    LEFT JOIN [Integracao].[Silver].[DimUsuarios] usu_sol
        ON usu_sol.IDDimUsuarios = np.IDDimUsuarios
       AND (
            usu_sol.IDEmpresaProprietaria = np.IDEmpresaProprietaria
            OR usu_sol.IDEmpresaProprietaria IS NULL
       )

    LEFT JOIN [Integracao].[Silver].[DimUsuarios] usu_apr
        ON usu_apr.IDDimUsuarios = np.IDDimUsuariosAprovacaoPreco
       AND (
            usu_apr.IDEmpresaProprietaria = np.IDEmpresaProprietaria
            OR usu_apr.IDEmpresaProprietaria IS NULL
       )

    LEFT JOIN [Integracao].[Silver].[DimEmpresas] cli
        ON cli.IDEmpresa = COALESCE(np.IDEmpresa, card_aut.IDEmpresa)
       AND (
            cli.IDEmpresaProprietaria = np.IDEmpresaProprietaria
            OR cli.IDEmpresaProprietaria IS NULL
       )

    OUTER APPLY (
        SELECT TOP (1)
            pf_hist.CodPonto,
            pf_hist.CodFace,
            pf_hist.TipoPainel,
            pf_hist.CustoTabela,
            pf_hist.ValorTabela,
            pf_hist.NovoValor,
            pf_hist.PercentualDesconto,
            pf_hist.ValorVendaFinal,
            pf_hist.MargemValor,
            pf_hist.MargemPercentual,
            pf_hist.Ativo,
            pf_hist.RemovidoEm,
            pf_hist.CriadoEm,
            pf_hist.DataAtualizacao
        FROM [Kanban].[Silver].[FatoKanbanCardPainelFace] pf_hist
        WHERE pf_hist.IDFatoKanbanCard = np.IDFatoKanbanCard
          AND ISNULL(pf_hist.IDDimPaineisEuromidia, 0) = ISNULL(np.IDDimPaineisEuromidia, 0)
          AND ISNULL(pf_hist.IDDimFacesPaineis, 0) = ISNULL(np.IDDimFacesPaineis, 0)
        ORDER BY
            CASE WHEN ISNULL(pf_hist.Ativo, 1) = 1 AND pf_hist.RemovidoEm IS NULL THEN 0 ELSE 1 END,
            COALESCE(pf_hist.DataAtualizacao, pf_hist.CriadoEm, pf_hist.RemovidoEm) DESC,
            pf_hist.IDFatoKanbanCardPainelFace DESC
    ) pf

    ORDER BY
        COALESCE(np.DataAprovacaoPreco, np.DataPrecoProposto, np.PeriodoInicio, np.PeriodoTermino) DESC,
        np.IDFatoKanbanNegociacaoPreco DESC
    """
    return _executar_sql_mapeado(
        sql,
        {
            "id_card": int(id_card),
            "id_empresa_proprietaria": int(id_empresa_proprietaria),
        },
    )





def _montar_resumo_historico_precos(registros: list[dict]) -> dict:
    """Eu monto os KPIs da tela para ficar mais didática e rápida de ler."""
    total = len(registros)
    aprovadas = 0
    pendentes = 0
    com_desconto_proposto = 0
    com_desconto_aprovado = 0
    soma_desconto_proposto = 0.0
    soma_desconto_aprovado = 0.0
    qtd_desconto_proposto = 0
    qtd_desconto_aprovado = 0

    for item in registros:
        status_negociacao = str(item.get("status_negociacao") or "").strip().upper()
        if status_negociacao == "APROVADO":
            aprovadas += 1
        else:
            pendentes += 1

        desconto_proposto = _decimal_para_float_seguro(item.get("desconto_proposto"))
        desconto_aprovado = _decimal_para_float_seguro(item.get("desconto_aprovado"))

        if desconto_proposto not in (None, 0.0):
            com_desconto_proposto += 1
            soma_desconto_proposto += float(desconto_proposto)
            qtd_desconto_proposto += 1

        if desconto_aprovado not in (None, 0.0):
            com_desconto_aprovado += 1
            soma_desconto_aprovado += float(desconto_aprovado)
            qtd_desconto_aprovado += 1

    media_desconto_proposto = (
        soma_desconto_proposto / qtd_desconto_proposto
        if qtd_desconto_proposto > 0 else None
    )
    media_desconto_aprovado = (
        soma_desconto_aprovado / qtd_desconto_aprovado
        if qtd_desconto_aprovado > 0 else None
    )

    return {
        "total": total,
        "aprovadas": aprovadas,
        "pendentes": pendentes,
        "com_desconto_proposto": com_desconto_proposto,
        "com_desconto_aprovado": com_desconto_aprovado,
        "media_desconto_proposto": media_desconto_proposto,
        "media_desconto_aprovado": media_desconto_aprovado,
    }


@kanban_bp.route("/historico-precos/<int:id_card>", methods=["GET"])
@login_required
@limiter.limit("60/minute")
def historico_precos_visualizacao(id_card: int):
    """Eu renderizo a tela didática de histórico de preços do card."""
    _assert_login()
    id_empresa_proprietaria = _id_empresa_usuario_or_403()

    card = _buscar_cabecalho_historico_card(id_card, id_empresa_proprietaria)
    if not card:
        abort(404)

    registros = _buscar_historico_precos_card(id_card, id_empresa_proprietaria)
    resumo = _montar_resumo_historico_precos(registros)
    cliente_referencia = registros[0] if registros else None

    return render_template(
        "kanban/historico_precos_visualizacao.html",
        card=card,
        registros=registros,
        resumo=resumo,
        cliente_referencia=cliente_referencia,
    )

































"""Eu monto os contadores-resumo do histórico do card."""
def _montar_resumo_historico_card(
    movimentacoes: list[dict],
    observacoes: list[dict],
    itens: list[dict],
    historico_precos: list[dict],
    tags: list[dict],
    status_historico: list[dict],
    encerramentos: list[dict],
    logs: list[dict],
) -> dict:
    total_tags_ativas = sum(
        1
        for item in tags
        if not item.get("removido_em")
    )

    total_itens_ativos = sum(
        1
        for item in itens
        if bool(item.get("ativo")) and not item.get("removido_em")
    )

    return {
        "total_movimentacoes": len(movimentacoes),
        "total_observacoes": len(observacoes),
        "total_itens": len(itens),
        "total_itens_ativos": total_itens_ativos,
        "total_tags": len(tags),
        "total_tags_ativas": total_tags_ativas,
        "total_status": len(status_historico),
        "total_encerramentos": len(encerramentos),
        "total_logs": len(logs),
        "total_precos_alterados": len(historico_precos),
    }







def _listar_cards_com_historico_precos(id_empresa_proprietaria: int) -> list[dict]:
    """Eu listo os cards que possuem histórico de preços, trazendo contagem e última movimentação."""
    sql = """
    SELECT
        c.IDFatoKanbanCard AS id_card,
        c.Titulo AS titulo,
        c.IDDimKanban AS id_kanban,
        c.IDDimKanbanFaseAtual AS id_fase_atual,
        c.CriadoEm AS criado_em,
        c.AtualizadoEm AS atualizado_em,
        c.Ativo AS ativo,
        c.IDEmpresa AS id_empresa,

        COUNT(p.IDFatoKanbanNegociacaoPreco) AS total_registros_preco,
        MAX(COALESCE(p.DataAprovacaoPreco, p.DataPrecoProposto)) AS ultima_movimentacao_preco

    FROM [Kanban].[Silver].[FatoKanbanCard] c
    INNER JOIN [Kanban].[Silver].[FatoKanbanNegociacaoPreco] p
        ON p.IDFatoKanbanCard = c.IDFatoKanbanCard

    WHERE c.IDEmpresaProprietaria = :id_empresa_proprietaria

    GROUP BY
        c.IDFatoKanbanCard,
        c.Titulo,
        c.IDDimKanban,
        c.IDDimKanbanFaseAtual,
        c.CriadoEm,
        c.AtualizadoEm,
        c.Ativo,
        c.IDEmpresa

    ORDER BY
        MAX(COALESCE(p.DataAprovacaoPreco, p.DataPrecoProposto)) DESC,
        c.IDFatoKanbanCard DESC;
    """
    return _executar_sql_mapeado(
        sql,
        {
            "id_empresa_proprietaria": int(id_empresa_proprietaria),
        },
    )







def _montar_timeline_historico_card(
    cabecalho: dict,
    movimentacoes: list[dict],
    observacoes: list[dict],
    itens: list[dict],
    tags: list[dict],
    status_historico: list[dict],
    encerramentos: list[dict],
    logs: list[dict],
) -> list[dict]:
    timeline = []

    if cabecalho and cabecalho.get("criado_em"):
        timeline.append(
            {
                "tipo_evento": "CRIACAO",
                "data_evento": cabecalho.get("criado_em"),
                "data_evento_ordenacao": _normalizar_data_evento_historico(cabecalho.get("criado_em")),
                "titulo": "Card criado",
                "descricao": f"Card criado com o título '{cabecalho.get('titulo') or 'Sem título'}'.",
                "usuario": cabecalho.get("nome_usuario_responsavel") or "",
                "icone": "🆕",
            }
        )

    for item in movimentacoes:
        timeline.append(
            {
                "tipo_evento": "MOVIMENTACAO",
                "data_evento": item.get("movido_em"),
                "data_evento_ordenacao": _normalizar_data_evento_historico(item.get("movido_em")),
                "titulo": "Mudança de fase",
                "descricao": f"{item.get('nome_fase_de') or 'Sem fase'} → {item.get('nome_fase_para') or 'Sem fase'}",
                "usuario": item.get("nome_usuario") or "",
                "icone": "🔁",
                "dados": item,
            }
        )

    for item in observacoes:
        timeline.append(
            {
                "tipo_evento": "OBSERVACAO",
                "data_evento": item.get("criado_em"),
                "data_evento_ordenacao": _normalizar_data_evento_historico(item.get("criado_em")),
                "titulo": "Observação adicionada",
                "descricao": item.get("observacao") or "",
                "usuario": item.get("nome_usuario") or "",
                "icone": "📝",
                "dados": item,
            }
        )

    for item in itens:
        data_referencia = item.get("atualizado_em") or item.get("criado_em") or item.get("removido_em")
        descricao_item = f"{item.get('cod_ponto') or '-'} / {item.get('cod_face') or '-'}"

        if item.get("removido_em"):
            titulo = "Item removido"
            icone = "🗑️"
            data_evento = item.get("removido_em")
        elif item.get("novo_valor") is not None or item.get("percentual_desconto") is not None or item.get("valor_venda_final") is not None:
            titulo = "Preço do item alterado"
            icone = "💰"
            data_evento = data_referencia
        else:
            titulo = "Item vinculado ao card"
            icone = "🧩"
            data_evento = data_referencia

        timeline.append(
            {
                "tipo_evento": "ITEM",
                "data_evento": data_evento,
                "data_evento_ordenacao": _normalizar_data_evento_historico(data_evento),
                "titulo": titulo,
                "descricao": descricao_item,
                "usuario": "",
                "icone": icone,
                "dados": item,
            }
        )

    for item in tags:
        if item.get("removido_em"):
            timeline.append(
                {
                    "tipo_evento": "TAG_REMOVIDA",
                    "data_evento": item.get("removido_em"),
                    "data_evento_ordenacao": _normalizar_data_evento_historico(item.get("removido_em")),
                    "titulo": "Tag removida",
                    "descricao": item.get("nome_tag") or "Tag sem nome",
                    "usuario": item.get("nome_usuario_removeu") or "",
                    "icone": "🏷️",
                    "dados": item,
                }
            )

        if item.get("aplicado_em"):
            timeline.append(
                {
                    "tipo_evento": "TAG_APLICADA",
                    "data_evento": item.get("aplicado_em"),
                    "data_evento_ordenacao": _normalizar_data_evento_historico(item.get("aplicado_em")),
                    "titulo": "Tag aplicada",
                    "descricao": item.get("nome_tag") or "Tag sem nome",
                    "usuario": item.get("nome_usuario_aplicou") or "",
                    "icone": "🏷️",
                    "dados": item,
                }
            )

    for item in status_historico:
        timeline.append(
            {
                "tipo_evento": "STATUS",
                "data_evento": None,
                "data_evento_ordenacao": None,
                "titulo": "Registro de status",
                "descricao": f"{item.get('nome_status') or item.get('codigo_status') or ('Status ID ' + str(item.get('id_status_card') or '-'))} na fase {item.get('nome_fase') or 'Sem fase'}",
                "usuario": item.get("nome_usuario") or "",
                "icone": "📌",
                "dados": item,
            }
        )

    for item in encerramentos:
        timeline.append(
            {
                "tipo_evento": "ENCERRAMENTO",
                "data_evento": item.get("data_atualizacao"),
                "data_evento_ordenacao": _normalizar_data_evento_historico(item.get("data_atualizacao")),
                "titulo": "Card encerrado",
                "descricao": item.get("nome_motivo") or "Encerramento sem motivo informado",
                "usuario": item.get("nome_usuario") or "",
                "icone": "⛔",
                "dados": item,
            }
        )

    for item in logs:
        timeline.append(
            {
                "tipo_evento": "LOG",
                "data_evento": item.get("ocorrido_em"),
                "data_evento_ordenacao": _normalizar_data_evento_historico(item.get("ocorrido_em")),
                "titulo": item.get("tipo_evento") or "Log",
                "descricao": item.get("texto_livre") or item.get("subtipo_evento") or "",
                "usuario": item.get("nome_usuario_acao") or "",
                "icone": "📜",
                "dados": item,
            }
        )

    timeline.sort(
        key=lambda item: (
            item.get("data_evento_ordenacao") is None,
            item.get("data_evento_ordenacao") or datetime.min,
        ),
        reverse=True,
    )

    return timeline












"""Eu busco o histórico de tags do card."""
def _buscar_tags_historico_card(id_card: int, id_empresa_proprietaria: int) -> list[dict]:
    sql = """
    SELECT
        ct.IDFatoKanbanCardTag AS id_card_tag,
        ct.IDFatoKanbanCard AS id_card,
        ct.IDDimKanbanTag AS id_tag,
        ct.AplicadoEm AS aplicado_em,
        ct.AplicadoPor AS aplicado_por,
        ct.RemovidoEm AS removido_em,
        ct.RemovidoPor AS removido_por,
        ct.IDEmpresaProprietaria AS id_empresa_proprietaria_evento,

        tag.NomeTag AS nome_tag,
        tag.TipoTag AS tipo_tag,
        tag.CorHex AS cor_tag,
        tag.Icone AS icone_tag,

        usuario_aplicou.NomeUsuario AS nome_usuario_aplicou,
        usuario_removeu.NomeUsuario AS nome_usuario_removeu

    FROM [Kanban].[Silver].[FatoKanbanCardTag] ct

    INNER JOIN [Kanban].[Silver].[FatoKanbanCard] card_aut
        ON card_aut.IDFatoKanbanCard = ct.IDFatoKanbanCard
       AND card_aut.IDEmpresaProprietaria = :id_empresa_proprietaria

    LEFT JOIN [Kanban].[Silver].[DimKanbanTag] tag
        ON tag.IDDimKanbanTag = ct.IDDimKanbanTag

    LEFT JOIN [Integracao].[Silver].[DimUsuarios] usuario_aplicou
        ON usuario_aplicou.IDDimUsuarios = ct.AplicadoPor
       AND (
            usuario_aplicou.IDEmpresaProprietaria = card_aut.IDEmpresaProprietaria
            OR usuario_aplicou.IDEmpresaProprietaria IS NULL
       )

    LEFT JOIN [Integracao].[Silver].[DimUsuarios] usuario_removeu
        ON usuario_removeu.IDDimUsuarios = ct.RemovidoPor
       AND (
            usuario_removeu.IDEmpresaProprietaria = card_aut.IDEmpresaProprietaria
            OR usuario_removeu.IDEmpresaProprietaria IS NULL
       )

    WHERE ct.IDFatoKanbanCard = :id_card

    ORDER BY
        COALESCE(ct.RemovidoEm, ct.AplicadoEm) DESC,
        ct.IDFatoKanbanCardTag DESC
    """
    return _executar_sql_mapeado(
        sql,
        {
            "id_card": int(id_card),
            "id_empresa_proprietaria": int(id_empresa_proprietaria),
        },
    )












@kanban_bp.route("/historico-card/<int:id_card>", methods=["GET"])
@login_required
@limiter.limit("60/minute")
def historico_card_visualizacao(id_card: int):
    _assert_login()
    id_empresa_proprietaria = _id_empresa_usuario_or_403()

    cabecalho = _buscar_cabecalho_historico_card(id_card, id_empresa_proprietaria)
    if not cabecalho:
        abort(404)

    movimentacoes = _buscar_movimentacoes_historico_card(id_card, id_empresa_proprietaria)
    observacoes = _buscar_observacoes_historico_card(id_card, id_empresa_proprietaria)
    itens = _buscar_itens_historico_card(id_card, id_empresa_proprietaria)
    historico_precos = _buscar_historico_precos_card(id_card, id_empresa_proprietaria)
    tags = _buscar_tags_historico_card(id_card, id_empresa_proprietaria)
    status_historico = _buscar_status_historico_card(id_card, id_empresa_proprietaria)
    encerramentos = _buscar_encerramento_historico_card(id_card, id_empresa_proprietaria)
    logs = _buscar_logs_historico_card(id_card, id_empresa_proprietaria)

    resumo = _montar_resumo_historico_card(
        movimentacoes=movimentacoes,
        observacoes=observacoes,
        itens=itens,
        historico_precos=historico_precos,
        tags=tags,
        status_historico=status_historico,
        encerramentos=encerramentos,
        logs=logs,
    )

    timeline = _montar_timeline_historico_card(
        cabecalho=cabecalho,
        movimentacoes=movimentacoes,
        observacoes=observacoes,
        itens=itens,
        tags=tags,
        status_historico=status_historico,
        encerramentos=encerramentos,
        logs=logs,
    )

    return render_template(
        "kanban/historico_card_visualizacao.html",
        card=cabecalho,
        resumo=resumo,
        timeline=timeline,
        movimentacoes=movimentacoes,
        observacoes=observacoes,
        itens=itens,
        historico_precos=historico_precos,
        tags=tags,
        status_historico=status_historico,
        encerramentos=encerramentos,
        logs=logs,
    )







def _sql_join_usuario_relacionado_card(alias_card: str = "c", alias_usuario: str = "usuario") -> str:
    nome_coluna = _nome_coluna_usuario_relacionado_card()
    if nome_coluna:
        return f"""
        LEFT JOIN [Integracao].[Silver].[DimUsuarios] {alias_usuario}
          ON {alias_usuario}.IDDimUsuarios = {alias_card}.{nome_coluna}
         AND ({alias_usuario}.IDEmpresaProprietaria = {alias_card}.IDEmpresaProprietaria OR {alias_usuario}.IDEmpresaProprietaria IS NULL)
        """.strip()

    return f"""
    LEFT JOIN [Integracao].[Silver].[DimUsuarios] {alias_usuario}
      ON 1 = 0
    """.strip()




def _sql_select_nome_usuario_relacionado_card(alias_usuario: str = "usuario") -> str:
    return f"NULLIF(LTRIM(RTRIM(ISNULL({alias_usuario}.NomeUsuario, ''))), '') AS NomeUsuarioResponsavel"




def _obter_vendedores_kanban(id_kanban: int) -> list[dict[str, Any]]:
    nome_coluna_usuario = _nome_coluna_usuario_relacionado_card()
    if not nome_coluna_usuario:
        return []

    sql_vendedores = text(f"""
        SELECT DISTINCT
            usuario.IDDimUsuarios,
            NULLIF(LTRIM(RTRIM(ISNULL(usuario.NomeUsuario, ''))), '') AS NomeUsuario
        FROM {TABELA_CARD} c
        INNER JOIN [Integracao].[Silver].[DimUsuarios] usuario
            ON usuario.IDDimUsuarios = c.{nome_coluna_usuario}
           AND (usuario.IDEmpresaProprietaria = c.IDEmpresaProprietaria OR usuario.IDEmpresaProprietaria IS NULL)
        WHERE c.IDDimKanban = :id_kanban
          AND c.Ativo = 1
          {_sql_filtro_status_card_visiveis('c')}
          AND NULLIF(LTRIM(RTRIM(ISNULL(usuario.NomeUsuario, ''))), '') IS NOT NULL
        ORDER BY NomeUsuario ASC;
    """)
    vendedores = db.session.execute(sql_vendedores, {"id_kanban": int(id_kanban)}).mappings().all()
    return _rows_para_dicts(vendedores)









def _calcular_preco_final_aprovacao_diretoria(
    *,
    preco_tabela: Any,
    novo_valor: Any,
    percentual_desconto: Any,
    valor_venda_final: Any,
) -> Decimal | None:
    """Eu resolvo o preço final efetivo da negociação para decidir se exige diretoria."""
    novo_valor_dec = _valor_decimal(novo_valor)
    if novo_valor_dec is not None:
        return novo_valor_dec

    valor_venda_final_dec = _valor_decimal(valor_venda_final)
    if valor_venda_final_dec is not None:
        return valor_venda_final_dec

    preco_tabela_dec = _valor_decimal(preco_tabela)
    percentual_desconto_dec = _valor_decimal(percentual_desconto)

    if percentual_desconto_dec is not None and preco_tabela_dec is not None:
        return preco_tabela_dec * (Decimal("1") - (percentual_desconto_dec / Decimal("100")))

    return preco_tabela_dec





def _estado_precisa_aprovacao_diretoria(estado: Mapping[str, Any] | dict[str, Any] | None) -> bool:
    """Eu marco diretoria quando o preço final ficar em até 12% acima do custo."""
    if not estado:
        return False

    custo_dec = _valor_decimal(estado.get("CustoTabela"))
    if custo_dec is None or custo_dec <= 0:
        return False

    preco_final_dec = _calcular_preco_final_aprovacao_diretoria(
        preco_tabela=estado.get("ValorTabela"),
        novo_valor=estado.get("NovoValor"),
        percentual_desconto=estado.get("PercentualDesconto"),
        valor_venda_final=estado.get("ValorVendaFinal"),
    )
    if preco_final_dec is None:
        return False

    limite_diretoria = custo_dec * (Decimal("1") + (PERCENTUAL_LIMITE_APROVACAO_DIRETORIA_SOBRE_CUSTO / Decimal("100")))
    return preco_final_dec <= limite_diretoria





def _estados_precisam_aprovacao_diretoria(estados: list[dict[str, Any]] | None) -> bool:
    return any(_estado_precisa_aprovacao_diretoria(estado) for estado in (estados or []))





def _card_precisa_aprovacao_diretoria_por_estado_atual(id_card: int) -> bool:
    return _estados_precisam_aprovacao_diretoria(_listar_estado_atual_negociacao_card(int(id_card)))



def _sincronizar_tag_aprovacao_diretoria_card(
    *,
    id_card: int,
    id_kanban: int,
    estados_atuais: list[dict[str, Any]] | None,
    id_usuario: int,
    id_empresa_proprietaria: int | None,
) -> bool:
    avaliacao = _avaliar_tags_aprovacao_desconto_card(
        id_card=int(id_card),
        estados_atuais=estados_atuais or [],
        id_empresa_proprietaria=int(id_empresa_proprietaria or 0),
    )

    precisa_aprovacao = bool(avaliacao.get("precisa_aprovacao_pendente"))
    tem_desconto_aprovado_ativo = bool(avaliacao.get("tem_desconto_aprovado_ativo"))

    tag_aprovacao = _obter_tag_por_nome(
        int(id_kanban),
        NOME_TAG_APROVACAO_DESCONTO,
        somente_ativa=True,
    )

    if precisa_aprovacao and not tag_aprovacao:
        raise RuntimeError(
            f"A tag automática '{NOME_TAG_APROVACAO_DESCONTO}' não está cadastrada/ativa para este kanban."
        )

    if precisa_aprovacao and tag_aprovacao:
        _aplicar_tag_no_card(
            id_card=int(id_card),
            id_tag=int(tag_aprovacao.get("IDDimKanbanTag") or 0),
            id_usuario=int(id_usuario),
            id_empresa_proprietaria=(
                int(tag_aprovacao.get("IDEmpresaProprietaria") or id_empresa_proprietaria or 0) or None
            ),
        )
        _remover_tag_por_nome_card(
            id_card=int(id_card),
            id_kanban=int(id_kanban),
            nome_tag=NOME_TAG_DESCONTO_APROVADO,
            id_usuario=int(id_usuario),
        )
        return True

    if tag_aprovacao:
        _remover_tag_do_card(
            id_card=int(id_card),
            id_tag=int(tag_aprovacao.get("IDDimKanbanTag") or 0),
            id_usuario=int(id_usuario),
        )

    if tem_desconto_aprovado_ativo:
        _aplicar_tag_por_nome_card(
            id_card=int(id_card),
            id_kanban=int(id_kanban),
            nome_tag=NOME_TAG_DESCONTO_APROVADO,
            id_usuario=int(id_usuario),
            id_empresa_proprietaria=id_empresa_proprietaria,
        )
    else:
        _remover_tag_por_nome_card(
            id_card=int(id_card),
            id_kanban=int(id_kanban),
            nome_tag=NOME_TAG_DESCONTO_APROVADO,
            id_usuario=int(id_usuario),
        )

    return False



HEALTH_CHECK_KANBAN_PADRAO = 1

MAPA_TAGS_HEALTH_CHECK = {
    "novo_contrato": ["novo contrato"],
    "aditivo": ["aditivo"],
    "perda_preco": [
        "perda por preco",
        "perda preço",
        "perda de preco",
        "perda de preço",
        "preco",
        "preço",
    ],
    "perda_concorrente": [
        "perda por concorrente",
        "concorrente",
    ],
    "perda_falta_painel": [
        "perda por falta de painel",
        "falta de painel",
        "sem painel",
    ],
}

MAPA_MOTIVOS_HEALTH_CHECK = {
    "perda_preco": [
        "preco",
        "preço",
        "perda por preco",
        "perda preço",
        "valor alto",
        "valor",
    ],
    "perda_concorrente": [
        "concorrente",
        "concorrencia",
        "concorrência",
    ],
    "perda_falta_painel": [
        "falta de painel",
        "sem painel",
        "sem disponibilidade",
        "sem inventario",
        "sem inventário",
    ],
}


def _normalizar_texto_health_check(valor: Any) -> str:
    """Eu normalizo texto para comparação sem acento, sem espaços duplicados e em minúsculo."""
    if valor is None:
        return ""

    texto = unicodedata.normalize("NFKD", str(valor))
    texto = "".join(ch for ch in texto if not unicodedata.combining(ch))
    texto = " ".join(texto.strip().lower().split())
    return texto







def _montar_dados_vazios_health_check() -> dict[str, Any]:
    """Eu devolvo a estrutura exata esperada pelo template do health check."""
    return {
        "titulo_painel": "Health Check Comercial",
        "atualizado_em": "",
        "periodo_referencia": "",
        "kpis": {
            "novos_contratos": 0,
            "novos_contratos_delta": "",
            "aditivos": 0,
            "aditivos_delta": "",
            "cancelamentos": 0,
            "cancelamentos_delta": "",
            "clientes_atendidos": 0,
            "clientes_atendidos_delta": "",
            "segmentos_atendidos": 0,
            "segmentos_atendidos_delta": "",
            "perdas_preco": 0,
            "perdas_preco_delta": "",
            "perdas_concorrente": 0,
            "perdas_concorrente_delta": "",
            "perdas_falta_painel": 0,
            "perdas_falta_painel_delta": "",
            "descontos_mes": 0,
            "descontos_mes_delta": "",
            "media_desconto": "0,00%",
            "media_desconto_delta": "",
        },
        "resumo_financeiro": {
            "receita_total": "—",
            "receita_total_delta": "",
            "receita_perdida": "—",
            "receita_perdida_delta": "",
            "ticket_medio": "—",
            "ticket_medio_delta": "",
        },
        "series_temporais_mercado": [],
        "segmentos_novos": [],
        "segmentos_aditivos": [],
        "segmentos_cancelamentos": [],
        "desconto_por_segmento": [],
        "vendedores_por_segmento": [],
        "vendedores_mais_desconto": [],
        "ultimas_atualizacoes": [],
    }



def _formatar_moeda_health_check(valor: Any) -> str:
    """Eu formato número como moeda BRL para exibição no painel."""
    try:
        decimal_valor = Decimal(str(valor or 0))
    except Exception:
        decimal_valor = Decimal("0")

    texto = f"{decimal_valor:,.2f}"
    texto = texto.replace(",", "X").replace(".", ",").replace("X", ".")
    return f"R$ {texto}"


def _formatar_percentual_health_check(valor: Any) -> str:
    """Eu formato número percentual no padrão brasileiro com duas casas."""
    try:
        decimal_valor = Decimal(str(valor or 0))
    except Exception:
        decimal_valor = Decimal("0")

    texto = f"{decimal_valor:,.2f}"
    texto = texto.replace(",", "X").replace(".", ",").replace("X", ".")
    return f"{texto}%"


def _montar_placeholders_sql(prefixo: str, valores: list[Any]) -> tuple[str, dict[str, Any]]:
    """Eu monto placeholders nomeados para cláusula IN parametrizada no SQL Server."""
    parametros: dict[str, Any] = {}
    placeholders: list[str] = []

    for indice, valor in enumerate(valores):
        nome_parametro = f"{prefixo}_{indice}"
        parametros[nome_parametro] = valor
        placeholders.append(f":{nome_parametro}")

    return ", ".join(placeholders), parametros


def _obter_periodo_health_check() -> tuple[datetime, datetime, str]:
    """Eu resolvo o período de referência do painel usando querystring ou mês atual."""
    agora = datetime.now()

    try:
        ano = int(request.args.get("ano") or agora.year)
    except Exception:
        ano = agora.year

    try:
        mes = int(request.args.get("mes") or agora.month)
    except Exception:
        mes = agora.month

    mes = max(1, min(12, mes))
    ano = max(2020, min(2100, ano))

    inicio = datetime(ano, mes, 1)

    if mes == 12:
        fim = datetime(ano + 1, 1, 1)
    else:
        fim = datetime(ano, mes + 1, 1)

    referencia = inicio.strftime("%m/%Y")
    return inicio, fim, referencia


def _obter_mapa_tags_health_check(id_kanban: int, id_empresa_proprietaria: int) -> dict[str, list[int]]:
    """Eu carrego as tags ativas do kanban e devolvo um mapa nome_normalizado -> ids."""
    sql = text("""
        SELECT
            IDDimKanbanTag,
            NomeTag
        FROM [Kanban].[Silver].[DimKanbanTag]
        WHERE IDDimKanban = :id_kanban
          AND IDEmpresaProprietaria = :id_empresa_proprietaria
          AND Ativo = 1;
    """)

    rows = db.session.execute(
        sql,
        {
            "id_kanban": int(id_kanban),
            "id_empresa_proprietaria": int(id_empresa_proprietaria),
        },
    ).mappings().all()

    mapa: dict[str, list[int]] = {}

    for row in rows:
        nome_normalizado = _normalizar_texto_health_check(row.get("NomeTag"))
        id_tag = int(row.get("IDDimKanbanTag") or 0)

        if not nome_normalizado or not id_tag:
            continue

        mapa.setdefault(nome_normalizado, []).append(id_tag)

    return mapa


def _obter_mapa_motivos_health_check(id_empresa_proprietaria: int) -> dict[str, list[int]]:
    """Eu carrego os motivos de encerramento e devolvo um mapa nome_normalizado -> ids."""
    sql = text(f"""
        SELECT
            IDDimKanbanMotivoEncerramento,
            NomeMotivo
        FROM {TABELA_MOTIVO_ENCERRAMENTO_CARD}
        WHERE IDEmpresaProprietaria = :id_empresa_proprietaria;
    """)

    rows = db.session.execute(
        sql,
        {"id_empresa_proprietaria": int(id_empresa_proprietaria)},
    ).mappings().all()

    mapa: dict[str, list[int]] = {}

    for row in rows:
        nome_normalizado = _normalizar_texto_health_check(row.get("NomeMotivo"))
        id_motivo = int(row.get("IDDimKanbanMotivoEncerramento") or 0)

        if not nome_normalizado or not id_motivo:
            continue

        mapa.setdefault(nome_normalizado, []).append(id_motivo)

    return mapa


def _resolver_ids_por_alias_health_check(
    mapa_ids_por_nome: dict[str, list[int]],
    aliases: list[str],
) -> list[int]:
    """Eu resolvo ids a partir dos aliases configurados para cada conceito do painel."""
    ids: list[int] = []

    for alias in aliases:
        chave = _normalizar_texto_health_check(alias)
        ids.extend(mapa_ids_por_nome.get(chave, []))

    ids_unicos = sorted({int(item) for item in ids if item})
    return ids_unicos


def _contar_cards_tag_no_mes_health_check(
    *,
    id_kanban: int,
    id_empresa_proprietaria: int,
    inicio_periodo: datetime,
    fim_periodo: datetime,
    ids_tags: list[int],
) -> int:
    """Eu conto cards distintos por tags aplicadas no período."""
    if not ids_tags:
        return 0

    placeholders, parametros_ids = _montar_placeholders_sql("id_tag", ids_tags)

    sql = text(f"""
        SELECT COUNT(DISTINCT ct.IDFatoKanbanCard) AS Quantidade
        FROM [Kanban].[Silver].[FatoKanbanCardTag] ct
        INNER JOIN [Kanban].[Silver].[FatoKanbanCard] c
            ON c.IDFatoKanbanCard = ct.IDFatoKanbanCard
        WHERE c.IDDimKanban = :id_kanban
          AND c.IDEmpresaProprietaria = :id_empresa_proprietaria
          AND ct.IDDimKanbanTag IN ({placeholders})
          AND ct.AplicadoEm >= :inicio_periodo
          AND ct.AplicadoEm < :fim_periodo;
    """)

    parametros = {
        "id_kanban": int(id_kanban),
        "id_empresa_proprietaria": int(id_empresa_proprietaria),
        "inicio_periodo": inicio_periodo,
        "fim_periodo": fim_periodo,
    }
    parametros.update(parametros_ids)

    row = db.session.execute(sql, parametros).mappings().first() or {}
    return int(row.get("Quantidade") or 0)


def _contar_cancelamentos_no_mes_health_check(
    *,
    id_kanban: int,
    id_empresa_proprietaria: int,
    inicio_periodo: datetime,
    fim_periodo: datetime,
) -> int:
    """Eu conto cards encerrados no período."""
    sql = text("""
        SELECT COUNT(DISTINCT c.IDFatoKanbanCard) AS Quantidade
        FROM [Kanban].[Silver].[FatoKanbanCard] c
        WHERE c.IDDimKanban = :id_kanban
          AND c.IDEmpresaProprietaria = :id_empresa_proprietaria
          AND c.EncerradoEm >= :inicio_periodo
          AND c.EncerradoEm < :fim_periodo
          AND (
                ISNULL(c.Ativo, 1) = 0
                OR c.IDDimKanbanMotivoEncerramento IS NOT NULL
          );
    """)

    row = db.session.execute(
        sql,
        {
            "id_kanban": int(id_kanban),
            "id_empresa_proprietaria": int(id_empresa_proprietaria),
            "inicio_periodo": inicio_periodo,
            "fim_periodo": fim_periodo,
        },
    ).mappings().first() or {}

    return int(row.get("Quantidade") or 0)


def _contar_perdas_no_mes_health_check(
    *,
    id_kanban: int,
    id_empresa_proprietaria: int,
    inicio_periodo: datetime,
    fim_periodo: datetime,
    ids_tags: list[int],
    ids_motivos: list[int],
) -> int:
    """Eu conto cards perdidos por motivo ou por tag sem duplicar o mesmo card."""
    subconsultas: list[str] = []
    parametros: dict[str, Any] = {
        "id_kanban": int(id_kanban),
        "id_empresa_proprietaria": int(id_empresa_proprietaria),
        "inicio_periodo": inicio_periodo,
        "fim_periodo": fim_periodo,
    }

    if ids_tags:
        placeholders_tags, parametros_tags = _montar_placeholders_sql("id_tag_perda", ids_tags)
        subconsultas.append(f"""
            SELECT DISTINCT ct.IDFatoKanbanCard AS IDFatoKanbanCard
            FROM [Kanban].[Silver].[FatoKanbanCardTag] ct
            INNER JOIN [Kanban].[Silver].[FatoKanbanCard] c
                ON c.IDFatoKanbanCard = ct.IDFatoKanbanCard
            WHERE c.IDDimKanban = :id_kanban
              AND c.IDEmpresaProprietaria = :id_empresa_proprietaria
              AND ct.IDDimKanbanTag IN ({placeholders_tags})
              AND ct.AplicadoEm >= :inicio_periodo
              AND ct.AplicadoEm < :fim_periodo
        """)
        parametros.update(parametros_tags)

    if ids_motivos:
        placeholders_motivos, parametros_motivos = _montar_placeholders_sql("id_motivo_perda", ids_motivos)
        subconsultas.append(f"""
            SELECT DISTINCT c.IDFatoKanbanCard AS IDFatoKanbanCard
            FROM [Kanban].[Silver].[FatoKanbanCard] c
            WHERE c.IDDimKanban = :id_kanban
              AND c.IDEmpresaProprietaria = :id_empresa_proprietaria
              AND c.IDDimKanbanMotivoEncerramento IN ({placeholders_motivos})
              AND c.EncerradoEm >= :inicio_periodo
              AND c.EncerradoEm < :fim_periodo
        """)
        parametros.update(parametros_motivos)

    if not subconsultas:
        return 0

    sql = text(f"""
        SELECT COUNT(DISTINCT base.IDFatoKanbanCard) AS Quantidade
        FROM (
            {" UNION ".join(subconsultas)}
        ) base;
    """)

    row = db.session.execute(sql, parametros).mappings().first() or {}
    return int(row.get("Quantidade") or 0)


















def _deslocar_primeiro_dia_mes_health_check(data_base: datetime, quantidade_meses: int) -> datetime:
    """Eu ando meses para frente ou para trás e devolvo sempre o primeiro dia do mês."""
    total_meses = (data_base.year * 12 + (data_base.month - 1)) + int(quantidade_meses)
    ano = total_meses // 12
    mes = (total_meses % 12) + 1
    return datetime(ano, mes, 1)


def _formatar_valor_card_serie_health_check(valor: Any, tipo_valor: str) -> str:
    """Eu formato o valor-resumo que aparece no card da série temporal."""
    decimal_valor = _valor_decimal(valor)
    if decimal_valor is None:
        decimal_valor = Decimal("0")

    if tipo_valor == "percentual":
        return _formatar_percentual_health_check(decimal_valor)

    if decimal_valor == decimal_valor.to_integral_value():
        return str(int(decimal_valor))

    texto = f"{decimal_valor:,.2f}"
    return texto.replace(",", "X").replace(".", ",").replace("X", ".")


def _obter_media_desconto_no_mes_health_check(
    *,
    id_kanban: int,
    id_empresa_proprietaria: int,
    inicio_periodo: datetime,
    fim_periodo: datetime,
) -> Decimal:
    """Eu calculo a média de desconto aprovada/proposta do mês."""
    sql = text(f"""
        SELECT
            AVG(
                TRY_CONVERT(
                    decimal(18, 4),
                    COALESCE(
                        NULLIF(np.DescontoAprovado, 0),
                        NULLIF(np.DescontoProposto, 0)
                    )
                )
            ) AS MediaDesconto
        FROM {TABELA_CARD_NEGOCIACAO_PRECO} np
        INNER JOIN {TABELA_CARD} c
            ON c.IDFatoKanbanCard = np.IDFatoKanbanCard
           AND c.IDEmpresaProprietaria = np.IDEmpresaProprietaria
        WHERE c.IDDimKanban = :id_kanban
          AND np.IDEmpresaProprietaria = :id_empresa_proprietaria
          AND COALESCE(np.DataAprovacaoPreco, np.DataPrecoProposto) >= :inicio_periodo
          AND COALESCE(np.DataAprovacaoPreco, np.DataPrecoProposto) < :fim_periodo
          AND COALESCE(np.DescontoAprovado, np.DescontoProposto, 0) > 0;
    """)

    row = db.session.execute(
        sql,
        {
            "id_kanban": int(id_kanban),
            "id_empresa_proprietaria": int(id_empresa_proprietaria),
            "inicio_periodo": inicio_periodo,
            "fim_periodo": fim_periodo,
        },
    ).mappings().first() or {}

    return _valor_decimal(row.get("MediaDesconto")) or Decimal("0")


def _montar_card_serie_temporal_health_check(
    *,
    titulo: str,
    subtitulo: str,
    tipo_valor: str,
    pontos: list[dict[str, Any]],
) -> dict[str, Any]:
    """Eu transformo pontos mensais em um card pronto para o template e para o Chart.js."""
    labels: list[str] = []
    valores: list[float] = []
    datas: list[str] = []

    for ponto in pontos:
        decimal_valor = _valor_decimal(ponto.get("valor")) or Decimal("0")
        labels.append(str(ponto.get("label") or "—"))
        datas.append(str(ponto.get("data") or "—"))
        valores.append(float(decimal_valor))

    if not valores:
        labels = ["—"]
        datas = ["—"]
        valores = [0.0]

    ultimo_valor = valores[-1]
    minimo_valor = min(valores)
    maximo_valor = max(valores)

    return {
        "titulo": titulo,
        "subtitulo": subtitulo,
        "tipo_valor": tipo_valor,
        "labels": labels,
        "valores": valores,
        "total_pontos": len(valores),
        "ultima_data": datas[-1],
        "ultimo_valor_texto": _formatar_valor_card_serie_health_check(ultimo_valor, tipo_valor),
        "minimo_valor_texto": _formatar_valor_card_serie_health_check(minimo_valor, tipo_valor),
        "maximo_valor_texto": _formatar_valor_card_serie_health_check(maximo_valor, tipo_valor),
    }


def _obter_series_temporais_health_check(
    *,
    id_kanban: int,
    id_empresa_proprietaria: int,
    inicio_periodo_referencia: datetime,
    ids_tag_novo_contrato: list[int],
    ids_tag_aditivo: list[int],
    ids_tags_perdas: list[int],
    ids_motivos_perdas: list[int],
    quantidade_meses: int = 12,
) -> list[dict[str, Any]]:
    """Eu monto as séries temporais mensais dos últimos N meses para o health check."""
    quantidade_meses = max(1, int(quantidade_meses))
    inicio_primeiro_mes = _deslocar_primeiro_dia_mes_health_check(
        inicio_periodo_referencia,
        -(quantidade_meses - 1),
    )

    pontos_novos: list[dict[str, Any]] = []
    pontos_aditivos: list[dict[str, Any]] = []
    pontos_cancelamentos: list[dict[str, Any]] = []
    pontos_perdas: list[dict[str, Any]] = []
    pontos_media_desconto: list[dict[str, Any]] = []

    for deslocamento in range(quantidade_meses):
        inicio_mes = _deslocar_primeiro_dia_mes_health_check(inicio_primeiro_mes, deslocamento)
        fim_mes = _deslocar_primeiro_dia_mes_health_check(inicio_mes, 1)

        label_mes = inicio_mes.strftime("%m/%y")
        data_mes = inicio_mes.strftime("%m/%Y")

        valor_novos = _contar_cards_tag_no_mes_health_check(
            id_kanban=id_kanban,
            id_empresa_proprietaria=id_empresa_proprietaria,
            inicio_periodo=inicio_mes,
            fim_periodo=fim_mes,
            ids_tags=ids_tag_novo_contrato,
        )

        valor_aditivos = _contar_cards_tag_no_mes_health_check(
            id_kanban=id_kanban,
            id_empresa_proprietaria=id_empresa_proprietaria,
            inicio_periodo=inicio_mes,
            fim_periodo=fim_mes,
            ids_tags=ids_tag_aditivo,
        )

        valor_cancelamentos = _contar_cancelamentos_no_mes_health_check(
            id_kanban=id_kanban,
            id_empresa_proprietaria=id_empresa_proprietaria,
            inicio_periodo=inicio_mes,
            fim_periodo=fim_mes,
        )

        valor_perdas = _contar_perdas_no_mes_health_check(
            id_kanban=id_kanban,
            id_empresa_proprietaria=id_empresa_proprietaria,
            inicio_periodo=inicio_mes,
            fim_periodo=fim_mes,
            ids_tags=ids_tags_perdas,
            ids_motivos=ids_motivos_perdas,
        )

        valor_media_desconto = _obter_media_desconto_no_mes_health_check(
            id_kanban=id_kanban,
            id_empresa_proprietaria=id_empresa_proprietaria,
            inicio_periodo=inicio_mes,
            fim_periodo=fim_mes,
        )

        pontos_novos.append({
            "label": label_mes,
            "data": data_mes,
            "valor": valor_novos,
        })
        pontos_aditivos.append({
            "label": label_mes,
            "data": data_mes,
            "valor": valor_aditivos,
        })
        pontos_cancelamentos.append({
            "label": label_mes,
            "data": data_mes,
            "valor": valor_cancelamentos,
        })
        pontos_perdas.append({
            "label": label_mes,
            "data": data_mes,
            "valor": valor_perdas,
        })
        pontos_media_desconto.append({
            "label": label_mes,
            "data": data_mes,
            "valor": valor_media_desconto,
        })

    return [
        _montar_card_serie_temporal_health_check(
            titulo="Novos contratos",
            subtitulo="Quantidade mensal dos últimos 12 meses.",
            tipo_valor="numero",
            pontos=pontos_novos,
        ),
        _montar_card_serie_temporal_health_check(
            titulo="Aditivos",
            subtitulo="Quantidade mensal dos últimos 12 meses.",
            tipo_valor="numero",
            pontos=pontos_aditivos,
        ),
        _montar_card_serie_temporal_health_check(
            titulo="Cancelamentos",
            subtitulo="Quantidade mensal dos últimos 12 meses.",
            tipo_valor="numero",
            pontos=pontos_cancelamentos,
        ),
        _montar_card_serie_temporal_health_check(
            titulo="Perdas comerciais",
            subtitulo="Perdas por tag ou motivo nos últimos 12 meses.",
            tipo_valor="numero",
            pontos=pontos_perdas,
        ),
        _montar_card_serie_temporal_health_check(
            titulo="Média de desconto",
            subtitulo="Desconto médio mensal aprovado/proposto nos últimos 12 meses.",
            tipo_valor="percentual",
            pontos=pontos_media_desconto,
        ),
    ]










def _obter_clientes_e_segmentos_atendidos_health_check(
    *,
    id_kanban: int,
    id_empresa_proprietaria: int,
    inicio_periodo: datetime,
    fim_periodo: datetime,
) -> tuple[int, int]:
    """Eu conto clientes e segmentos distintos atendidos no período usando Classe do CNAE."""
    sql = text(f"""
        SELECT
            COUNT(DISTINCT c.IDEmpresa) AS QuantidadeClientes,
            COUNT(DISTINCT NULLIF(LTRIM(RTRIM(ISNULL(seg.Classe, ''))), '')) AS QuantidadeSegmentos
        FROM {TABELA_CARD} c
        LEFT JOIN {TABELA_EMPRESAS} emp
            ON emp.IDEmpresa = c.IDEmpresa
           AND emp.IDEmpresaProprietaria = c.IDEmpresaProprietaria
        LEFT JOIN {TABELA_CNAES} seg
            ON REPLACE(REPLACE(REPLACE(REPLACE(LTRIM(RTRIM(ISNULL(emp.CNAE, ''))), '.', ''), '/', ''), '-', ''), ' ', '')
             = REPLACE(REPLACE(REPLACE(REPLACE(LTRIM(RTRIM(ISNULL(seg.cnaepadrao, ''))), '.', ''), '/', ''), '-', ''), ' ', '')
        WHERE c.IDDimKanban = :id_kanban
          AND c.IDEmpresaProprietaria = :id_empresa_proprietaria
          AND c.IDEmpresa IS NOT NULL
          AND (
                (c.CriadoEm >= :inicio_periodo AND c.CriadoEm < :fim_periodo)
                OR (c.AtualizadoEm >= :inicio_periodo AND c.AtualizadoEm < :fim_periodo)
                OR (c.EncerradoEm >= :inicio_periodo AND c.EncerradoEm < :fim_periodo)
          );
    """)

    row = db.session.execute(
        sql,
        {
            "id_kanban": int(id_kanban),
            "id_empresa_proprietaria": int(id_empresa_proprietaria),
            "inicio_periodo": inicio_periodo,
            "fim_periodo": fim_periodo,
        },
    ).mappings().first() or {}

    return (
        int(row.get("QuantidadeClientes") or 0),
        int(row.get("QuantidadeSegmentos") or 0),
    )


def _obter_resumo_financeiro_health_check(
    *,
    id_kanban: int,
    id_empresa_proprietaria: int,
    inicio_periodo: datetime,
    fim_periodo: datetime,
    ids_tags_perdas: list[int],
    ids_motivos_perdas: list[int],
) -> dict[str, str]:
    """Eu calculo receita total, receita perdida e ticket médio do período."""
    sql_receita = text(f"""
        SELECT
            SUM(
                COALESCE(
                    NULLIF(TRY_CONVERT(decimal(18, 2), np.PrecoAprovado), 0),
                    NULLIF(TRY_CONVERT(decimal(18, 2), np.PrecoProposto), 0),
                    0
                )
            ) AS ReceitaTotal,
            AVG(
                COALESCE(
                    NULLIF(TRY_CONVERT(decimal(18, 2), np.PrecoAprovado), 0),
                    NULLIF(TRY_CONVERT(decimal(18, 2), np.PrecoProposto), 0),
                    NULL
                )
            ) AS TicketMedio
        FROM {TABELA_CARD_NEGOCIACAO_PRECO} np
        INNER JOIN {TABELA_CARD} c
            ON c.IDFatoKanbanCard = np.IDFatoKanbanCard
           AND c.IDEmpresaProprietaria = np.IDEmpresaProprietaria
        WHERE c.IDDimKanban = :id_kanban
          AND np.IDEmpresaProprietaria = :id_empresa_proprietaria
          AND COALESCE(np.DataAprovacaoPreco, np.DataPrecoProposto) >= :inicio_periodo
          AND COALESCE(np.DataAprovacaoPreco, np.DataPrecoProposto) < :fim_periodo;
    """)

    row_receita = db.session.execute(
        sql_receita,
        {
            "id_kanban": int(id_kanban),
            "id_empresa_proprietaria": int(id_empresa_proprietaria),
            "inicio_periodo": inicio_periodo,
            "fim_periodo": fim_periodo,
        },
    ).mappings().first() or {}

    subconsultas: list[str] = []
    parametros_perda: dict[str, Any] = {
        "id_kanban": int(id_kanban),
        "id_empresa_proprietaria": int(id_empresa_proprietaria),
        "inicio_periodo": inicio_periodo,
        "fim_periodo": fim_periodo,
    }

    if ids_tags_perdas:
        placeholders_tags, parametros_tags = _montar_placeholders_sql("id_tag_perda_receita", ids_tags_perdas)
        subconsultas.append(f"""
            SELECT DISTINCT c.IDFatoKanbanCard
            FROM [Kanban].[Silver].[FatoKanbanCardTag] ct
            INNER JOIN {TABELA_CARD} c
                ON c.IDFatoKanbanCard = ct.IDFatoKanbanCard
            WHERE c.IDDimKanban = :id_kanban
              AND c.IDEmpresaProprietaria = :id_empresa_proprietaria
              AND ct.IDDimKanbanTag IN ({placeholders_tags})
              AND ct.AplicadoEm >= :inicio_periodo
              AND ct.AplicadoEm < :fim_periodo
        """)
        parametros_perda.update(parametros_tags)

    if ids_motivos_perdas:
        placeholders_motivos, parametros_motivos = _montar_placeholders_sql("id_motivo_perda_receita", ids_motivos_perdas)
        subconsultas.append(f"""
            SELECT DISTINCT c.IDFatoKanbanCard
            FROM {TABELA_CARD} c
            WHERE c.IDDimKanban = :id_kanban
              AND c.IDEmpresaProprietaria = :id_empresa_proprietaria
              AND c.IDDimKanbanMotivoEncerramento IN ({placeholders_motivos})
              AND c.EncerradoEm >= :inicio_periodo
              AND c.EncerradoEm < :fim_periodo
        """)
        parametros_perda.update(parametros_motivos)

    receita_perdida = Decimal("0")

    if subconsultas:
        sql_perdida = text(f"""
            WITH cards_perdidos AS (
                {" UNION ".join(subconsultas)}
            )
            SELECT
                SUM(
                    COALESCE(
                        TRY_CONVERT(decimal(18, 2), pf.ValorVendaFinal),
                        TRY_CONVERT(decimal(18, 2), pf.NovoValor),
                        TRY_CONVERT(decimal(18, 2), pf.ValorTabela),
                        0
                    )
                ) AS ReceitaPerdida
            FROM cards_perdidos cp
            LEFT JOIN [Kanban].[Silver].[FatoKanbanCardPainelFace] pf
                ON pf.IDFatoKanbanCard = cp.IDFatoKanbanCard
               AND ISNULL(pf.Ativo, 1) = 1
               AND pf.RemovidoEm IS NULL;
        """)

        row_perdida = db.session.execute(sql_perdida, parametros_perda).mappings().first() or {}
        receita_perdida = Decimal(str(row_perdida.get("ReceitaPerdida") or 0))

    receita_total = Decimal(str(row_receita.get("ReceitaTotal") or 0))
    ticket_medio = row_receita.get("TicketMedio")

    return {
        "receita_total": _formatar_moeda_health_check(receita_total),
        "receita_total_delta": "",
        "receita_perdida": _formatar_moeda_health_check(receita_perdida),
        "receita_perdida_delta": "",
        "ticket_medio": _formatar_moeda_health_check(ticket_medio or 0),
        "ticket_medio_delta": "",
    }


def _obter_segmentos_por_evento_health_check(
    *,
    id_kanban: int,
    id_empresa_proprietaria: int,
    inicio_periodo: datetime,
    fim_periodo: datetime,
    ids_tags: list[int] | None = None,
    usar_cancelamentos: bool = False,
    limite: int = 10,
) -> list[dict[str, Any]]:
    """Eu monto ranking de segmentos por novos contratos, aditivos ou cancelamentos."""
    ids_tags = ids_tags or []

    parametros: dict[str, Any] = {
        "id_kanban": int(id_kanban),
        "id_empresa_proprietaria": int(id_empresa_proprietaria),
        "inicio_periodo": inicio_periodo,
        "fim_periodo": fim_periodo,
        "limite": int(limite),
    }

    if usar_cancelamentos:
        sql = text(f"""
            SELECT TOP (:limite)
                NULLIF(LTRIM(RTRIM(ISNULL(seg.Classe, ''))), '') AS NomeSegmento,
                COUNT(DISTINCT c.IDFatoKanbanCard) AS Quantidade
            FROM {TABELA_CARD} c
            LEFT JOIN {TABELA_EMPRESAS} emp
                ON emp.IDEmpresa = c.IDEmpresa
               AND emp.IDEmpresaProprietaria = c.IDEmpresaProprietaria
            LEFT JOIN {TABELA_CNAES} seg
                ON REPLACE(REPLACE(REPLACE(REPLACE(LTRIM(RTRIM(ISNULL(emp.CNAE, ''))), '.', ''), '/', ''), '-', ''), ' ', '')
                 = REPLACE(REPLACE(REPLACE(REPLACE(LTRIM(RTRIM(ISNULL(seg.cnaepadrao, ''))), '.', ''), '/', ''), '-', ''), ' ', '')
            WHERE c.IDDimKanban = :id_kanban
              AND c.IDEmpresaProprietaria = :id_empresa_proprietaria
              AND c.EncerradoEm >= :inicio_periodo
              AND c.EncerradoEm < :fim_periodo
              AND NULLIF(LTRIM(RTRIM(ISNULL(seg.Classe, ''))), '') IS NOT NULL
            GROUP BY NULLIF(LTRIM(RTRIM(ISNULL(seg.Classe, ''))), '')
            ORDER BY Quantidade DESC, NomeSegmento ASC;
        """)
    else:
        if not ids_tags:
            return []

        placeholders, parametros_tags = _montar_placeholders_sql("id_tag_segmento", ids_tags)
        parametros.update(parametros_tags)

        sql = text(f"""
            SELECT TOP (:limite)
                NULLIF(LTRIM(RTRIM(ISNULL(seg.Classe, ''))), '') AS NomeSegmento,
                COUNT(DISTINCT ct.IDFatoKanbanCard) AS Quantidade
            FROM [Kanban].[Silver].[FatoKanbanCardTag] ct
            INNER JOIN {TABELA_CARD} c
                ON c.IDFatoKanbanCard = ct.IDFatoKanbanCard
            LEFT JOIN {TABELA_EMPRESAS} emp
                ON emp.IDEmpresa = c.IDEmpresa
               AND emp.IDEmpresaProprietaria = c.IDEmpresaProprietaria
            LEFT JOIN {TABELA_CNAES} seg
                ON REPLACE(REPLACE(REPLACE(REPLACE(LTRIM(RTRIM(ISNULL(emp.CNAE, ''))), '.', ''), '/', ''), '-', ''), ' ', '')
                 = REPLACE(REPLACE(REPLACE(REPLACE(LTRIM(RTRIM(ISNULL(seg.cnaepadrao, ''))), '.', ''), '/', ''), '-', ''), ' ', '')
            WHERE c.IDDimKanban = :id_kanban
              AND c.IDEmpresaProprietaria = :id_empresa_proprietaria
              AND ct.IDDimKanbanTag IN ({placeholders})
              AND ct.AplicadoEm >= :inicio_periodo
              AND ct.AplicadoEm < :fim_periodo
              AND NULLIF(LTRIM(RTRIM(ISNULL(seg.Classe, ''))), '') IS NOT NULL
            GROUP BY NULLIF(LTRIM(RTRIM(ISNULL(seg.Classe, ''))), '')
            ORDER BY Quantidade DESC, NomeSegmento ASC;
        """)

    rows = db.session.execute(sql, parametros).mappings().all()

    return [
        {
            "nome": row.get("NomeSegmento"),
            "descricao": "Segmento (Classe CNAE)",
            "valor": int(row.get("Quantidade") or 0),
        }
        for row in rows
    ]


def _obter_desconto_por_segmento_health_check(
    *,
    id_kanban: int,
    id_empresa_proprietaria: int,
    inicio_periodo: datetime,
    fim_periodo: datetime,
    limite: int = 20,
) -> list[dict[str, Any]]:
    """Eu monto a tabela de quantidade e média de desconto por segmento."""
    sql = text(f"""
        SELECT TOP (:limite)
            NULLIF(LTRIM(RTRIM(ISNULL(seg.Classe, ''))), '') AS Segmento,
            COUNT(DISTINCT np.IDFatoKanbanNegociacaoPreco) AS Quantidade,
            AVG(
                TRY_CONVERT(
                    decimal(18, 4),
                    COALESCE(
                        NULLIF(np.DescontoAprovado, 0),
                        NULLIF(np.DescontoProposto, 0)
                    )
                )
            ) AS MediaDesconto
        FROM {TABELA_CARD_NEGOCIACAO_PRECO} np
        INNER JOIN {TABELA_CARD} c
            ON c.IDFatoKanbanCard = np.IDFatoKanbanCard
           AND c.IDEmpresaProprietaria = np.IDEmpresaProprietaria
        LEFT JOIN {TABELA_EMPRESAS} emp
            ON emp.IDEmpresa = c.IDEmpresa
           AND emp.IDEmpresaProprietaria = c.IDEmpresaProprietaria
        LEFT JOIN {TABELA_CNAES} seg
            ON REPLACE(REPLACE(REPLACE(REPLACE(LTRIM(RTRIM(ISNULL(emp.CNAE, ''))), '.', ''), '/', ''), '-', ''), ' ', '')
             = REPLACE(REPLACE(REPLACE(REPLACE(LTRIM(RTRIM(ISNULL(seg.cnaepadrao, ''))), '.', ''), '/', ''), '-', ''), ' ', '')
        WHERE c.IDDimKanban = :id_kanban
          AND np.IDEmpresaProprietaria = :id_empresa_proprietaria
          AND COALESCE(np.DataAprovacaoPreco, np.DataPrecoProposto) >= :inicio_periodo
          AND COALESCE(np.DataAprovacaoPreco, np.DataPrecoProposto) < :fim_periodo
          AND COALESCE(np.DescontoAprovado, np.DescontoProposto, 0) > 0
          AND NULLIF(LTRIM(RTRIM(ISNULL(seg.Classe, ''))), '') IS NOT NULL
        GROUP BY NULLIF(LTRIM(RTRIM(ISNULL(seg.Classe, ''))), '')
        ORDER BY Quantidade DESC, Segmento ASC;
    """)

    rows = db.session.execute(
        sql,
        {
            "id_kanban": int(id_kanban),
            "id_empresa_proprietaria": int(id_empresa_proprietaria),
            "inicio_periodo": inicio_periodo,
            "fim_periodo": fim_periodo,
            "limite": int(limite),
        },
    ).mappings().all()

    return [
        {
            "segmento": row.get("Segmento"),
            "quantidade": int(row.get("Quantidade") or 0),
            "media": _formatar_percentual_health_check(row.get("MediaDesconto") or 0),
            "observacao": "Média calculada a partir de desconto proposto/aprovado no período.",
        }
        for row in rows
    ]


def _obter_vendedores_por_segmento_health_check(
    *,
    id_kanban: int,
    id_empresa_proprietaria: int,
    inicio_periodo: datetime,
    fim_periodo: datetime,
    ids_tags_fechamento: list[int],
    limite: int = 15,
) -> list[dict[str, Any]]:
    """Eu monto o ranking de vendedor por segmento usando novos contratos e aditivos."""
    if not ids_tags_fechamento:
        return []

    placeholders, parametros_tags = _montar_placeholders_sql("id_tag_fechamento", ids_tags_fechamento)

    sql = text(f"""
        SELECT TOP (:limite)
            COALESCE(
                NULLIF(LTRIM(RTRIM(ISNULL(usu.NomeUsuario, ''))), ''),
                CONCAT('ID ', CONVERT(varchar(20), COALESCE(c.IDVendedorUsuario, c.IDDimUsuarios)))
            ) AS NomeVendedor,
            NULLIF(LTRIM(RTRIM(ISNULL(seg.Classe, ''))), '') AS Segmento,
            COUNT(DISTINCT ct.IDFatoKanbanCard) AS Quantidade
        FROM [Kanban].[Silver].[FatoKanbanCardTag] ct
        INNER JOIN {TABELA_CARD} c
            ON c.IDFatoKanbanCard = ct.IDFatoKanbanCard
        LEFT JOIN [Integracao].[Silver].[DimUsuarios] usu
            ON usu.IDDimUsuarios = COALESCE(c.IDVendedorUsuario, c.IDDimUsuarios)
           AND (usu.IDEmpresaProprietaria = c.IDEmpresaProprietaria OR usu.IDEmpresaProprietaria IS NULL)
        LEFT JOIN {TABELA_EMPRESAS} emp
            ON emp.IDEmpresa = c.IDEmpresa
           AND emp.IDEmpresaProprietaria = c.IDEmpresaProprietaria
        LEFT JOIN {TABELA_CNAES} seg
            ON REPLACE(REPLACE(REPLACE(REPLACE(LTRIM(RTRIM(ISNULL(emp.CNAE, ''))), '.', ''), '/', ''), '-', ''), ' ', '')
             = REPLACE(REPLACE(REPLACE(REPLACE(LTRIM(RTRIM(ISNULL(seg.cnaepadrao, ''))), '.', ''), '/', ''), '-', ''), ' ', '')
        WHERE c.IDDimKanban = :id_kanban
          AND c.IDEmpresaProprietaria = :id_empresa_proprietaria
          AND ct.IDDimKanbanTag IN ({placeholders})
          AND ct.AplicadoEm >= :inicio_periodo
          AND ct.AplicadoEm < :fim_periodo
          AND NULLIF(LTRIM(RTRIM(ISNULL(seg.Classe, ''))), '') IS NOT NULL
        GROUP BY
            COALESCE(
                NULLIF(LTRIM(RTRIM(ISNULL(usu.NomeUsuario, ''))), ''),
                CONCAT('ID ', CONVERT(varchar(20), COALESCE(c.IDVendedorUsuario, c.IDDimUsuarios)))
            ),
            NULLIF(LTRIM(RTRIM(ISNULL(seg.Classe, ''))), '')
        ORDER BY Quantidade DESC, NomeVendedor ASC, Segmento ASC;
    """)

    parametros = {
        "id_kanban": int(id_kanban),
        "id_empresa_proprietaria": int(id_empresa_proprietaria),
        "inicio_periodo": inicio_periodo,
        "fim_periodo": fim_periodo,
        "limite": int(limite),
    }
    parametros.update(parametros_tags)

    rows = db.session.execute(sql, parametros).mappings().all()

    return [
        {
            "vendedor": row.get("NomeVendedor"),
            "segmento": row.get("Segmento"),
            "quantidade": int(row.get("Quantidade") or 0),
        }
        for row in rows
    ]


def _obter_vendedores_mais_desconto_health_check(
    *,
    id_kanban: int,
    id_empresa_proprietaria: int,
    inicio_periodo: datetime,
    fim_periodo: datetime,
    limite: int = 15,
) -> list[dict[str, Any]]:
    """Eu monto o ranking de vendedores que mais dão desconto por segmento."""
    sql = text(f"""
        SELECT TOP (:limite)
            COALESCE(
                NULLIF(LTRIM(RTRIM(ISNULL(usu.NomeUsuario, ''))), ''),
                CONCAT('ID ', CONVERT(varchar(20), COALESCE(c.IDVendedorUsuario, np.IDDimUsuarios, c.IDDimUsuarios)))
            ) AS NomeVendedor,
            NULLIF(LTRIM(RTRIM(ISNULL(seg.Classe, ''))), '') AS Segmento,
            COUNT(DISTINCT np.IDFatoKanbanNegociacaoPreco) AS Quantidade,
            AVG(
                TRY_CONVERT(
                    decimal(18, 4),
                    COALESCE(
                        NULLIF(np.DescontoAprovado, 0),
                        NULLIF(np.DescontoProposto, 0)
                    )
                )
            ) AS MediaDesconto
        FROM {TABELA_CARD_NEGOCIACAO_PRECO} np
        INNER JOIN {TABELA_CARD} c
            ON c.IDFatoKanbanCard = np.IDFatoKanbanCard
           AND c.IDEmpresaProprietaria = np.IDEmpresaProprietaria
        LEFT JOIN [Integracao].[Silver].[DimUsuarios] usu
            ON usu.IDDimUsuarios = COALESCE(c.IDVendedorUsuario, np.IDDimUsuarios, c.IDDimUsuarios)
           AND (usu.IDEmpresaProprietaria = c.IDEmpresaProprietaria OR usu.IDEmpresaProprietaria IS NULL)
        LEFT JOIN {TABELA_EMPRESAS} emp
            ON emp.IDEmpresa = c.IDEmpresa
           AND emp.IDEmpresaProprietaria = c.IDEmpresaProprietaria
        LEFT JOIN {TABELA_CNAES} seg
            ON REPLACE(REPLACE(REPLACE(REPLACE(LTRIM(RTRIM(ISNULL(emp.CNAE, ''))), '.', ''), '/', ''), '-', ''), ' ', '')
             = REPLACE(REPLACE(REPLACE(REPLACE(LTRIM(RTRIM(ISNULL(seg.cnaepadrao, ''))), '.', ''), '/', ''), '-', ''), ' ', '')
        WHERE c.IDDimKanban = :id_kanban
          AND np.IDEmpresaProprietaria = :id_empresa_proprietaria
          AND COALESCE(np.DataAprovacaoPreco, np.DataPrecoProposto) >= :inicio_periodo
          AND COALESCE(np.DataAprovacaoPreco, np.DataPrecoProposto) < :fim_periodo
          AND COALESCE(np.DescontoAprovado, np.DescontoProposto, 0) > 0
          AND NULLIF(LTRIM(RTRIM(ISNULL(seg.Classe, ''))), '') IS NOT NULL
        GROUP BY
            COALESCE(
                NULLIF(LTRIM(RTRIM(ISNULL(usu.NomeUsuario, ''))), ''),
                CONCAT('ID ', CONVERT(varchar(20), COALESCE(c.IDVendedorUsuario, np.IDDimUsuarios, c.IDDimUsuarios)))
            ),
            NULLIF(LTRIM(RTRIM(ISNULL(seg.Classe, ''))), '')
        ORDER BY Quantidade DESC, MediaDesconto DESC, NomeVendedor ASC;
    """)

    rows = db.session.execute(
        sql,
        {
            "id_kanban": int(id_kanban),
            "id_empresa_proprietaria": int(id_empresa_proprietaria),
            "inicio_periodo": inicio_periodo,
            "fim_periodo": fim_periodo,
            "limite": int(limite),
        },
    ).mappings().all()

    return [
        {
            "vendedor": row.get("NomeVendedor"),
            "segmento": row.get("Segmento"),
            "quantidade": int(row.get("Quantidade") or 0),
            "media_desconto": _formatar_percentual_health_check(row.get("MediaDesconto") or 0),
        }
        for row in rows
    ]







def _deslocar_mes_health_check(data_base: datetime, quantidade_meses: int) -> datetime:
    """Eu ando meses para frente ou para trás e devolvo sempre o primeiro dia do mês."""
    total_meses = (data_base.year * 12 + (data_base.month - 1)) + int(quantidade_meses)
    ano = total_meses // 12
    mes = (total_meses % 12) + 1
    return datetime(ano, mes, 1)


def _formatar_numero_br_health_check(valor: Any, casas: int = 2) -> str:
    """Eu formato número no padrão brasileiro."""
    decimal_valor = _valor_decimal(valor)
    if decimal_valor is None:
        decimal_valor = Decimal("0")

    texto = f"{decimal_valor:,.{casas}f}"
    return texto.replace(",", "X").replace(".", ",").replace("X", ".")


def _formatar_valor_card_serie_health_check(valor: Any, tipo_valor: str) -> str:
    """Eu formato o valor-resumo exibido nos cards dos gráficos."""
    decimal_valor = _valor_decimal(valor)
    if decimal_valor is None:
        decimal_valor = Decimal("0")

    if tipo_valor == "percentual":
        return _formatar_percentual_health_check(decimal_valor)

    if decimal_valor == decimal_valor.to_integral_value():
        return str(int(decimal_valor))

    return _formatar_numero_br_health_check(decimal_valor, 2)


def _montar_card_serie_temporal_health_check(
    *,
    titulo: str,
    subtitulo: str,
    tipo_valor: str,
    pontos: list[dict[str, Any]],
) -> dict[str, Any]:
    """Eu transformo os pontos da série em payload pronto para o template."""
    labels: list[str] = []
    valores: list[float] = []
    datas: list[str] = []

    for ponto in pontos:
        data_texto = str(ponto.get("data_label") or "—")
        valor_decimal = _valor_decimal(ponto.get("valor"))
        if valor_decimal is None:
            valor_decimal = Decimal("0")

        labels.append(data_texto)
        datas.append(data_texto)
        valores.append(float(valor_decimal))

    if not valores:
        labels = ["—"]
        datas = ["—"]
        valores = [0.0]

    ultimo_valor = valores[-1]
    minimo_valor = min(valores)
    maximo_valor = max(valores)

    return {
        "titulo": titulo,
        "subtitulo": subtitulo,
        "tipo_valor": tipo_valor,
        "labels": labels,
        "valores": valores,
        "total_pontos": len(valores),
        "ultima_data": datas[-1],
        "ultimo_valor_texto": _formatar_valor_card_serie_health_check(ultimo_valor, tipo_valor),
        "minimo_valor_texto": _formatar_valor_card_serie_health_check(minimo_valor, tipo_valor),
        "maximo_valor_texto": _formatar_valor_card_serie_health_check(maximo_valor, tipo_valor),
    }


def _obter_pontos_serie_variacao_health_check(
    *,
    nome_tabela: str,
    coluna_data: str,
    coluna_valor: str,
    inicio_periodo: datetime,
) -> list[dict[str, Any]]:
    """Eu busco os pontos diários da série temporal dos últimos 12 meses."""
    sql = text(f"""
        SELECT
            CAST([{coluna_data}] AS date) AS DataReferencia,
            TRY_CONVERT(decimal(18, 6), [{coluna_valor}]) AS ValorVariacao
        FROM {nome_tabela}
        WHERE [{coluna_data}] >= :inicio_periodo
          AND [{coluna_data}] IS NOT NULL
          AND [{coluna_valor}] IS NOT NULL
        ORDER BY [{coluna_data}] ASC;
    """)

    rows = db.session.execute(
        sql,
        {
            "inicio_periodo": inicio_periodo,
        },
    ).mappings().all()

    pontos: list[dict[str, Any]] = []
    for row in rows:
        data_ref = row.get("DataReferencia")
        valor = row.get("ValorVariacao")

        if data_ref is None:
            continue

        if hasattr(data_ref, "strftime"):
            data_label = data_ref.strftime("%d/%m/%y")
        else:
            data_label = str(data_ref)

        pontos.append(
            {
                "data_label": data_label,
                "valor": _valor_decimal(valor) or Decimal("0"),
            }
        )

    return pontos


def _obter_series_temporais_mercado_health_check() -> list[dict[str, Any]]:
    """Eu monto as séries temporais reais dos indicadores de mercado e OOH."""
    hoje = datetime.now()
    inicio_periodo = _deslocar_mes_health_check(datetime(hoje.year, hoje.month, 1), -11)

    pontos_industrial = _obter_pontos_serie_variacao_health_check(
        nome_tabela="[DataMining].[Silver].[FatoCotacaoDiariaIndiceIndustrial]",
        coluna_data="DataCotacao",
        coluna_valor="VarBRL",
        inicio_periodo=inicio_periodo,
    )

    pontos_imobiliario = _obter_pontos_serie_variacao_health_check(
        nome_tabela="[DataMining].[Silver].[FatoCotacaoDiariaIndiceImobiliario]",
        coluna_data="DataCotacao",
        coluna_valor="VarBRL",
        inicio_periodo=inicio_periodo,
    )

    pontos_consumo = _obter_pontos_serie_variacao_health_check(
        nome_tabela="[DataMining].[Silver].[FatoCotacaoDiariaIndiceConsumo]",
        coluna_data="DataCotacao",
        coluna_valor="VarBRL",
        inicio_periodo=inicio_periodo,
    )

    pontos_ooh = _obter_pontos_serie_variacao_health_check(
        nome_tabela="[Integracao].[Silver].[FatoIndiceOOHDiario]",
        coluna_data="Data",
        coluna_valor="VariacaoPercent",
        inicio_periodo=inicio_periodo,
    )

    pontos_ooh_global = _obter_pontos_serie_variacao_health_check(
        nome_tabela="[Integracao].[Silver].[FatoIndiceOOHGlobal]",
        coluna_data="Data",
        coluna_valor="VariacaoPercent",
        inicio_periodo=inicio_periodo,
    )

    return [
        _montar_card_serie_temporal_health_check(
            titulo="Índice Industrial",
            subtitulo="Variação diária do índice industrial nos últimos 12 meses.",
            tipo_valor="numero",
            pontos=pontos_industrial,
        ),
        _montar_card_serie_temporal_health_check(
            titulo="Índice Imobiliário",
            subtitulo="Variação diária do índice imobiliário nos últimos 12 meses.",
            tipo_valor="numero",
            pontos=pontos_imobiliario,
        ),
        _montar_card_serie_temporal_health_check(
            titulo="Índice Consumo",
            subtitulo="Variação diária do índice de consumo nos últimos 12 meses.",
            tipo_valor="numero",
            pontos=pontos_consumo,
        ),
        _montar_card_serie_temporal_health_check(
            titulo="Índice OOH",
            subtitulo="Variação percentual diária do índice OOH nos últimos 12 meses.",
            tipo_valor="percentual",
            pontos=pontos_ooh,
        ),
        _montar_card_serie_temporal_health_check(
            titulo="Índice OOH Global",
            subtitulo="Variação percentual diária do índice OOH global nos últimos 12 meses.",
            tipo_valor="percentual",
            pontos=pontos_ooh_global,
        ),
    ]






@kanban_bp.route("/health-check-comercial", methods=["GET"])
@login_required
def health_check_comercial():
    """Eu alimento o dashboard do health check comercial com dados reais do mês."""
    _assert_login()
    id_empresa_proprietaria = _id_empresa_usuario_or_403()

    try:
        id_kanban = int(request.args.get("id_kanban") or HEALTH_CHECK_KANBAN_PADRAO)
    except Exception:
        id_kanban = HEALTH_CHECK_KANBAN_PADRAO

    _obter_kanban_autorizado(id_kanban)

    dados = _montar_dados_vazios_health_check()

    try:
        inicio_periodo, fim_periodo, periodo_referencia = _obter_periodo_health_check()

        mapa_tags = _obter_mapa_tags_health_check(
            id_kanban=id_kanban,
            id_empresa_proprietaria=id_empresa_proprietaria,
        )
        mapa_motivos = _obter_mapa_motivos_health_check(
            id_empresa_proprietaria=id_empresa_proprietaria,
        )

        ids_tag_novo_contrato = _resolver_ids_por_alias_health_check(
            mapa_tags,
            MAPA_TAGS_HEALTH_CHECK["novo_contrato"],
        )
        ids_tag_aditivo = _resolver_ids_por_alias_health_check(
            mapa_tags,
            MAPA_TAGS_HEALTH_CHECK["aditivo"],
        )
        ids_tag_perda_preco = _resolver_ids_por_alias_health_check(
            mapa_tags,
            MAPA_TAGS_HEALTH_CHECK["perda_preco"],
        )
        ids_tag_perda_concorrente = _resolver_ids_por_alias_health_check(
            mapa_tags,
            MAPA_TAGS_HEALTH_CHECK["perda_concorrente"],
        )
        ids_tag_perda_falta_painel = _resolver_ids_por_alias_health_check(
            mapa_tags,
            MAPA_TAGS_HEALTH_CHECK["perda_falta_painel"],
        )

        ids_motivo_perda_preco = _resolver_ids_por_alias_health_check(
            mapa_motivos,
            MAPA_MOTIVOS_HEALTH_CHECK["perda_preco"],
        )
        ids_motivo_perda_concorrente = _resolver_ids_por_alias_health_check(
            mapa_motivos,
            MAPA_MOTIVOS_HEALTH_CHECK["perda_concorrente"],
        )
        ids_motivo_perda_falta_painel = _resolver_ids_por_alias_health_check(
            mapa_motivos,
            MAPA_MOTIVOS_HEALTH_CHECK["perda_falta_painel"],
        )

        novos_contratos = _contar_cards_tag_no_mes_health_check(
            id_kanban=id_kanban,
            id_empresa_proprietaria=id_empresa_proprietaria,
            inicio_periodo=inicio_periodo,
            fim_periodo=fim_periodo,
            ids_tags=ids_tag_novo_contrato,
        )

        aditivos = _contar_cards_tag_no_mes_health_check(
            id_kanban=id_kanban,
            id_empresa_proprietaria=id_empresa_proprietaria,
            inicio_periodo=inicio_periodo,
            fim_periodo=fim_periodo,
            ids_tags=ids_tag_aditivo,
        )

        cancelamentos = _contar_cancelamentos_no_mes_health_check(
            id_kanban=id_kanban,
            id_empresa_proprietaria=id_empresa_proprietaria,
            inicio_periodo=inicio_periodo,
            fim_periodo=fim_periodo,
        )

        perdas_preco = _contar_perdas_no_mes_health_check(
            id_kanban=id_kanban,
            id_empresa_proprietaria=id_empresa_proprietaria,
            inicio_periodo=inicio_periodo,
            fim_periodo=fim_periodo,
            ids_tags=ids_tag_perda_preco,
            ids_motivos=ids_motivo_perda_preco,
        )

        perdas_concorrente = _contar_perdas_no_mes_health_check(
            id_kanban=id_kanban,
            id_empresa_proprietaria=id_empresa_proprietaria,
            inicio_periodo=inicio_periodo,
            fim_periodo=fim_periodo,
            ids_tags=ids_tag_perda_concorrente,
            ids_motivos=ids_motivo_perda_concorrente,
        )

        perdas_falta_painel = _contar_perdas_no_mes_health_check(
            id_kanban=id_kanban,
            id_empresa_proprietaria=id_empresa_proprietaria,
            inicio_periodo=inicio_periodo,
            fim_periodo=fim_periodo,
            ids_tags=ids_tag_perda_falta_painel,
            ids_motivos=ids_motivo_perda_falta_painel,
        )

        clientes_atendidos, segmentos_atendidos = _obter_clientes_e_segmentos_atendidos_health_check(
            id_kanban=id_kanban,
            id_empresa_proprietaria=id_empresa_proprietaria,
            inicio_periodo=inicio_periodo,
            fim_periodo=fim_periodo,
        )

        sql_descontos = text(f"""
            SELECT
                COUNT(DISTINCT np.IDFatoKanbanNegociacaoPreco) AS Quantidade,
                AVG(
                    TRY_CONVERT(
                        decimal(18, 4),
                        COALESCE(
                            NULLIF(np.DescontoAprovado, 0),
                            NULLIF(np.DescontoProposto, 0)
                        )
                    )
                ) AS MediaDesconto
            FROM {TABELA_CARD_NEGOCIACAO_PRECO} np
            INNER JOIN {TABELA_CARD} c
                ON c.IDFatoKanbanCard = np.IDFatoKanbanCard
               AND c.IDEmpresaProprietaria = np.IDEmpresaProprietaria
            WHERE c.IDDimKanban = :id_kanban
              AND np.IDEmpresaProprietaria = :id_empresa_proprietaria
              AND COALESCE(np.DataAprovacaoPreco, np.DataPrecoProposto) >= :inicio_periodo
              AND COALESCE(np.DataAprovacaoPreco, np.DataPrecoProposto) < :fim_periodo
              AND COALESCE(np.DescontoAprovado, np.DescontoProposto, 0) > 0;
        """)

        row_descontos = db.session.execute(
            sql_descontos,
            {
                "id_kanban": int(id_kanban),
                "id_empresa_proprietaria": int(id_empresa_proprietaria),
                "inicio_periodo": inicio_periodo,
                "fim_periodo": fim_periodo,
            },
        ).mappings().first() or {}

        descontos_mes = int(row_descontos.get("Quantidade") or 0)
        media_desconto = row_descontos.get("MediaDesconto") or 0

        ids_tags_fechamento = sorted(set(ids_tag_novo_contrato + ids_tag_aditivo))
        ids_tags_todas_perdas = sorted(
            set(ids_tag_perda_preco + ids_tag_perda_concorrente + ids_tag_perda_falta_painel)
        )
        ids_motivos_todas_perdas = sorted(
            set(ids_motivo_perda_preco + ids_motivo_perda_concorrente + ids_motivo_perda_falta_painel)
        )

        dados["atualizado_em"] = datetime.now().strftime("%d/%m/%Y %H:%M")
        dados["periodo_referencia"] = periodo_referencia

        dados["kpis"]["novos_contratos"] = novos_contratos
        dados["kpis"]["aditivos"] = aditivos
        dados["kpis"]["cancelamentos"] = cancelamentos
        dados["kpis"]["clientes_atendidos"] = clientes_atendidos
        dados["kpis"]["segmentos_atendidos"] = segmentos_atendidos
        dados["kpis"]["perdas_preco"] = perdas_preco
        dados["kpis"]["perdas_concorrente"] = perdas_concorrente
        dados["kpis"]["perdas_falta_painel"] = perdas_falta_painel
        dados["kpis"]["descontos_mes"] = descontos_mes
        dados["kpis"]["media_desconto"] = _formatar_percentual_health_check(media_desconto)

        dados["series_temporais_mercado"] = _obter_series_temporais_mercado_health_check()

        dados["resumo_financeiro"] = _obter_resumo_financeiro_health_check(
            id_kanban=id_kanban,
            id_empresa_proprietaria=id_empresa_proprietaria,
            inicio_periodo=inicio_periodo,
            fim_periodo=fim_periodo,
            ids_tags_perdas=ids_tags_todas_perdas,
            ids_motivos_perdas=ids_motivos_todas_perdas,
        )

        dados["segmentos_novos"] = _obter_segmentos_por_evento_health_check(
            id_kanban=id_kanban,
            id_empresa_proprietaria=id_empresa_proprietaria,
            inicio_periodo=inicio_periodo,
            fim_periodo=fim_periodo,
            ids_tags=ids_tag_novo_contrato,
            usar_cancelamentos=False,
            limite=10,
        )

        dados["segmentos_aditivos"] = _obter_segmentos_por_evento_health_check(
            id_kanban=id_kanban,
            id_empresa_proprietaria=id_empresa_proprietaria,
            inicio_periodo=inicio_periodo,
            fim_periodo=fim_periodo,
            ids_tags=ids_tag_aditivo,
            usar_cancelamentos=False,
            limite=10,
        )

        dados["segmentos_cancelamentos"] = _obter_segmentos_por_evento_health_check(
            id_kanban=id_kanban,
            id_empresa_proprietaria=id_empresa_proprietaria,
            inicio_periodo=inicio_periodo,
            fim_periodo=fim_periodo,
            usar_cancelamentos=True,
            limite=10,
        )

        dados["desconto_por_segmento"] = _obter_desconto_por_segmento_health_check(
            id_kanban=id_kanban,
            id_empresa_proprietaria=id_empresa_proprietaria,
            inicio_periodo=inicio_periodo,
            fim_periodo=fim_periodo,
            limite=20,
        )

        dados["vendedores_por_segmento"] = _obter_vendedores_por_segmento_health_check(
            id_kanban=id_kanban,
            id_empresa_proprietaria=id_empresa_proprietaria,
            inicio_periodo=inicio_periodo,
            fim_periodo=fim_periodo,
            ids_tags_fechamento=ids_tags_fechamento,
            limite=15,
        )

        dados["vendedores_mais_desconto"] = _obter_vendedores_mais_desconto_health_check(
            id_kanban=id_kanban,
            id_empresa_proprietaria=id_empresa_proprietaria,
            inicio_periodo=inicio_periodo,
            fim_periodo=fim_periodo,
            limite=15,
        )

    except Exception as exc:
        current_app.logger.exception(
            "Erro ao montar health check comercial | id_kanban=%s",
            id_kanban,
        )
        dados["atualizado_em"] = datetime.now().strftime("%d/%m/%Y %H:%M")
        dados["ultimas_atualizacoes"] = [
            {
                "texto": "Falha ao carregar uma ou mais métricas do painel.",
                "meta": str(exc),
            }
        ]

    return render_template(
        "kanban/health_check_comercial.html",
        dados=dados,
    )





def _formatar_decimal_br(valor: Any, casas: int = 2) -> str:
    decimal_valor = _valor_decimal(valor)
    if decimal_valor is None:
        decimal_valor = Decimal("0")

    texto = f"{decimal_valor:,.{casas}f}"
    return texto.replace(",", "X").replace(".", ",").replace("X", ".")




def _aplicar_tag_por_nome_card(
    *,
    id_card: int,
    id_kanban: int,
    nome_tag: str,
    id_usuario: int,
    id_empresa_proprietaria: int | None,
) -> bool:
    tag = _obter_tag_por_nome(int(id_kanban), str(nome_tag or "").strip(), somente_ativa=True)
    if not tag:
        return False

    return _aplicar_tag_no_card(
        id_card=int(id_card),
        id_tag=int(tag.get("IDDimKanbanTag") or 0),
        id_usuario=int(id_usuario),
        id_empresa_proprietaria=(
            int(tag.get("IDEmpresaProprietaria") or id_empresa_proprietaria or 0) or None
        ),
    )




def _remover_tag_por_nome_card(
    *,
    id_card: int,
    id_kanban: int,
    nome_tag: str,
    id_usuario: int,
) -> bool:
    tag = _obter_tag_por_nome(int(id_kanban), str(nome_tag or "").strip(), somente_ativa=False)
    if not tag:
        return False

    return _remover_tag_do_card(
        id_card=int(id_card),
        id_tag=int(tag.get("IDDimKanbanTag") or 0),
        id_usuario=int(id_usuario),
    )





def _preco_final_estado_negociacao(estado: Mapping[str, Any] | dict[str, Any] | None) -> Decimal | None:
    if not estado:
        return None

    return _calcular_preco_final_aprovacao_diretoria(
        preco_tabela=estado.get("ValorTabela"),
        novo_valor=estado.get("NovoValor"),
        percentual_desconto=estado.get("PercentualDesconto"),
        valor_venda_final=estado.get("ValorVendaFinal"),
    )




def _estado_tem_aprovacao_compativel_para_preco_atual(
    *,
    id_card: int,
    estado: Mapping[str, Any] | dict[str, Any],
    id_empresa_proprietaria: int,
) -> bool:
    if not estado:
        return False

    preco_final_estado = _preco_final_estado_negociacao(estado)
    if preco_final_estado is None:
        return False

    id_painel = int(estado.get("IDDimPaineisEuromidia") or 0)
    id_face = int(estado.get("IDDimFacesPaineis") or 0)

    filtro_painel_face = ""
    parametros = {
        "id_card": int(id_card),
        "id_empresa_proprietaria": int(id_empresa_proprietaria),
    }

    if id_painel > 0:
        filtro_painel_face += "\n      AND ISNULL(np.IDDimPaineisEuromidia, 0) = :id_painel"
        parametros["id_painel"] = id_painel

    if id_face > 0:
        filtro_painel_face += "\n      AND ISNULL(np.IDDimFacesPaineis, 0) = :id_face"
        parametros["id_face"] = id_face

    sql = text(f"""
        SELECT TOP (1)
            np.PrecoAprovado,
            np.DataAprovacaoPreco,
            np.IDFatoKanbanNegociacaoPreco
        FROM [Kanban].[Silver].[FatoKanbanNegociacaoPreco] np
        WHERE np.IDFatoKanbanCard = :id_card
          AND np.IDEmpresaProprietaria = :id_empresa_proprietaria
          AND np.PrecoAprovado IS NOT NULL
          AND np.DataAprovacaoPreco IS NOT NULL
          {filtro_painel_face}
        ORDER BY
            np.DataAprovacaoPreco DESC,
            np.IDFatoKanbanNegociacaoPreco DESC;
    """)

    row = db.session.execute(sql, parametros).mappings().first()
    if not row:
        return False

    preco_aprovado = _valor_decimal(row.get("PrecoAprovado"))
    if preco_aprovado is None:
        return False

    diferenca = abs(preco_aprovado - preco_final_estado)
    return diferenca <= Decimal("0.01")




def _avaliar_tags_aprovacao_desconto_card(
    *,
    id_card: int,
    estados_atuais: list[dict[str, Any]] | None,
    id_empresa_proprietaria: int,
) -> dict[str, bool]:
    precisa_aprovacao_pendente = False
    tem_desconto_aprovado_ativo = False

    for estado in (estados_atuais or []):
        if not _estado_precisa_aprovacao_diretoria(estado):
            continue

        if _estado_tem_aprovacao_compativel_para_preco_atual(
            id_card=int(id_card),
            estado=estado,
            id_empresa_proprietaria=int(id_empresa_proprietaria),
        ):
            tem_desconto_aprovado_ativo = True
        else:
            precisa_aprovacao_pendente = True

    return {
        "precisa_aprovacao_pendente": bool(precisa_aprovacao_pendente),
        "tem_desconto_aprovado_ativo": bool(tem_desconto_aprovado_ativo),
    }








def _buscar_cards_lista_aprovacao_preco(id_empresa_proprietaria: int) -> list[dict[str, Any]]:
    sql = text("""
        ;WITH CardsComTag AS (
            SELECT DISTINCT
                c.IDFatoKanbanCard,
                c.IDDimKanban,
                c.IDDimKanbanFaseAtual,
                c.Titulo,
                c.CriadoEm,
                c.AtualizadoEm,
                c.IDEmpresa,
                c.IDVendedorUsuario,
                c.IDDimUsuarios,
                c.IDEmpresaProprietaria
            FROM [Kanban].[Silver].[FatoKanbanCard] c
            INNER JOIN [Kanban].[Silver].[FatoKanbanCardTag] ct
                ON ct.IDFatoKanbanCard = c.IDFatoKanbanCard
               AND ct.RemovidoEm IS NULL
            INNER JOIN [Kanban].[Silver].[DimKanbanTag] t
                ON t.IDDimKanbanTag = ct.IDDimKanbanTag
               AND ISNULL(t.Ativo, 1) = 1
            WHERE c.IDEmpresaProprietaria = :id_empresa_proprietaria
              AND c.Ativo = 1
              AND UPPER(LTRIM(RTRIM(ISNULL(t.NomeTag, '')))) = UPPER(LTRIM(RTRIM(:nome_tag)))
        ),
        UltimaNegociacao AS (
            SELECT
                np.*,
                ROW_NUMBER() OVER (
                    PARTITION BY np.IDFatoKanbanCard
                    ORDER BY
                        COALESCE(np.DataPrecoProposto, np.PeriodoInicio, np.PeriodoTermino, np.DataAprovacaoPreco) DESC,
                        np.IDFatoKanbanNegociacaoPreco DESC
                ) AS rn
            FROM [Kanban].[Silver].[FatoKanbanNegociacaoPreco] np
            WHERE np.IDEmpresaProprietaria = :id_empresa_proprietaria
        )
        SELECT
            cc.IDFatoKanbanCard AS id_card,
            cc.IDDimKanban AS id_kanban,
            cc.Titulo AS titulo,
            cc.CriadoEm AS criado_em,
            cc.AtualizadoEm AS atualizado_em,
            cc.IDEmpresa AS id_empresa,

            fase.NomeFase AS nome_fase,
            COALESCE(
                NULLIF(LTRIM(RTRIM(cli.NomeFantasia)), ''),
                NULLIF(LTRIM(RTRIM(cli.RazaoSocial)), ''),
                CONCAT('Empresa #', CAST(cc.IDEmpresa AS VARCHAR(30)))
            ) AS nome_empresa,
            cli.RazaoSocial AS razao_social,
            cli.NomeFantasia AS nome_fantasia,
            cli.CNPJ AS cnpj,
            usu.NomeUsuario AS nome_usuario_responsavel,

            un.IDFatoKanbanNegociacaoPreco AS id_negociacao_preco,
            un.DataPrecoProposto AS data_preco_proposto,
            un.CustoAtual AS custo_atual,
            un.PrecoAtual AS preco_atual,
            un.PrecoProposto AS preco_proposto,
            un.DescontoProposto AS desconto_proposto,
            un.MargemProposta AS margem_proposta,
            un.IDDimUsuarios AS id_usuario_solicitante
        FROM CardsComTag cc
        LEFT JOIN UltimaNegociacao un
            ON un.IDFatoKanbanCard = cc.IDFatoKanbanCard
           AND un.rn = 1
        LEFT JOIN [Kanban].[Silver].[DimKanbanFase] fase
            ON fase.IDDimKanbanFase = cc.IDDimKanbanFaseAtual
           AND fase.IDEmpresaProprietaria = cc.IDEmpresaProprietaria
        LEFT JOIN [Integracao].[Silver].[DimEmpresas] cli
            ON cli.IDEmpresa = cc.IDEmpresa
           AND cli.IDEmpresaProprietaria = cc.IDEmpresaProprietaria
        LEFT JOIN [Integracao].[Silver].[DimUsuarios] usu
            ON usu.IDDimUsuarios = COALESCE(cc.IDVendedorUsuario, cc.IDDimUsuarios)
           AND usu.IDEmpresaProprietaria = cc.IDEmpresaProprietaria
        ORDER BY
            COALESCE(un.DataPrecoProposto, cc.AtualizadoEm, cc.CriadoEm) DESC,
            cc.IDFatoKanbanCard DESC;
    """)

    rows = db.session.execute(
        sql,
        {
            "id_empresa_proprietaria": int(id_empresa_proprietaria),
            "nome_tag": NOME_TAG_APROVACAO_DESCONTO,
        },
    ).mappings().all()

    return _rows_para_dicts(rows)



def _buscar_cabecalho_aprovacao_preco(id_card: int, id_empresa_proprietaria: int) -> dict[str, Any] | None:
    sql = text("""
        SELECT TOP (1)
            c.IDFatoKanbanCard AS id_card,
            c.IDDimKanban AS id_kanban,
            c.IDDimKanbanFaseAtual AS id_fase_atual,
            c.Titulo AS titulo,
            c.Descricao AS descricao,
            c.CriadoEm AS criado_em,
            c.AtualizadoEm AS atualizado_em,
            c.IDEmpresa AS id_empresa,
            c.Ativo AS ativo,

            fase.NomeFase AS nome_fase,
            fase.CorHex AS cor_fase,
            fase.CorTextoHex AS cor_texto_fase,

            usu.NomeUsuario AS nome_usuario_responsavel,
            usu.Email AS email_usuario_responsavel,

            cli.RazaoSocial AS razao_social,
            cli.NomeFantasia AS nome_fantasia,
            cli.CNPJ AS cnpj,
            cli.CNAE AS cnae_empresa,
            cli.Email AS email_empresa,
            cli.TelefoneContato1 AS telefone_empresa,
            cli.Municipio AS municipio,
            cli.UF AS uf,
            cli.Bairro AS bairro,
            cli.Logradouro AS logradouro,
            cli.Numero AS numero,

            cnae.Descricao AS descricao_cnae,
            cnae.Classe AS classe_cnae,
            cnae.Setor AS setor_cnae,
            cnae.MacroSetor AS macro_setor
        FROM [Kanban].[Silver].[FatoKanbanCard] c
        LEFT JOIN [Kanban].[Silver].[DimKanbanFase] fase
            ON fase.IDDimKanbanFase = c.IDDimKanbanFaseAtual
           AND fase.IDEmpresaProprietaria = c.IDEmpresaProprietaria
        LEFT JOIN [Integracao].[Silver].[DimUsuarios] usu
            ON usu.IDDimUsuarios = COALESCE(c.IDVendedorUsuario, c.IDDimUsuarios)
           AND usu.IDEmpresaProprietaria = c.IDEmpresaProprietaria
        LEFT JOIN [Integracao].[Silver].[DimEmpresas] cli
            ON cli.IDEmpresa = c.IDEmpresa
           AND cli.IDEmpresaProprietaria = c.IDEmpresaProprietaria
        LEFT JOIN [Integracao].[Silver].[DimCnaes] cnae
            ON cnae.cnaepadrao = cli.CNAE
        WHERE c.IDFatoKanbanCard = :id_card
          AND c.IDEmpresaProprietaria = :id_empresa_proprietaria;
    """)

    row = db.session.execute(
        sql,
        {
            "id_card": int(id_card),
            "id_empresa_proprietaria": int(id_empresa_proprietaria),
        },
    ).mappings().first()

    return dict(row) if row else None




def _buscar_itens_pendentes_aprovacao_preco(id_card: int, id_empresa_proprietaria: int) -> list[dict[str, Any]]:
    sql = text("""
        ;WITH UltimaLinhaPorPainelFace AS (
            SELECT
                np.*,
                ROW_NUMBER() OVER (
                    PARTITION BY
                        ISNULL(np.IDDimPaineisEuromidia, -1),
                        ISNULL(np.IDDimFacesPaineis, -1)
                    ORDER BY
                        COALESCE(np.DataPrecoProposto, np.PeriodoInicio, np.PeriodoTermino, np.DataAprovacaoPreco) DESC,
                        np.IDFatoKanbanNegociacaoPreco DESC
                ) AS rn
            FROM [Kanban].[Silver].[FatoKanbanNegociacaoPreco] np
            WHERE np.IDFatoKanbanCard = :id_card
              AND np.IDEmpresaProprietaria = :id_empresa_proprietaria
        )
        SELECT
            ul.IDFatoKanbanNegociacaoPreco AS id_negociacao_preco,
            ul.IDFatoKanbanCard AS id_card,
            ul.IDDimPaineisEuromidia AS id_painel,
            ul.IDDimFacesPaineis AS id_face,
            ul.DataPrecoProposto AS data_preco_proposto,
            ul.PeriodoInicio AS periodo_inicio,
            ul.PeriodoTermino AS periodo_termino,
            ul.ObservacoesProposta AS observacoes_proposta,
            ul.CustoAtual AS custo_atual,
            ul.PrecoAtual AS preco_atual,
            ul.PrecoProposto AS preco_proposto,
            ul.MargemProposta AS margem_proposta,
            ul.DescontoProposto AS desconto_proposto,
            ul.IDDimUsuarios AS id_usuario_solicitante,
            usu.NomeUsuario AS nome_usuario_solicitante,

            pf.CodPonto AS cod_ponto,
            pf.CodFace AS cod_face,
            pf.TipoPainel AS tipo_painel,
            pf.CustoTabela AS custo_tabela_operacional,
            pf.ValorTabela AS valor_tabela_operacional,
            pf.NovoValor AS novo_valor_operacional,
            pf.PercentualDesconto AS percentual_desconto_operacional,
            pf.ValorVendaFinal AS valor_venda_final_operacional
        FROM UltimaLinhaPorPainelFace ul
        LEFT JOIN [Integracao].[Silver].[DimUsuarios] usu
            ON usu.IDDimUsuarios = ul.IDDimUsuarios
           AND usu.IDEmpresaProprietaria = ul.IDEmpresaProprietaria
        LEFT JOIN [Kanban].[Silver].[FatoKanbanCardPainelFace] pf
            ON pf.IDFatoKanbanCard = ul.IDFatoKanbanCard
           AND ISNULL(pf.Ativo, 1) = 1
           AND ISNULL(pf.IDDimPaineisEuromidia, 0) = ISNULL(ul.IDDimPaineisEuromidia, 0)
           AND ISNULL(pf.IDDimFacesPaineis, 0) = ISNULL(ul.IDDimFacesPaineis, 0)
        WHERE ul.rn = 1
          AND ul.PrecoAprovado IS NULL
          AND ul.DataAprovacaoPreco IS NULL
        ORDER BY
            ul.DataPrecoProposto DESC,
            ul.IDFatoKanbanNegociacaoPreco DESC;
    """)

    rows = db.session.execute(
        sql,
        {
            "id_card": int(id_card),
            "id_empresa_proprietaria": int(id_empresa_proprietaria),
        },
    ).mappings().all()

    return _rows_para_dicts(rows)




def _inserir_nota_aprovacao_desconto_card(
    *,
    id_card: int,
    id_empresa_proprietaria: int,
    id_empresa_relacionada: int | None,
    id_usuario: int,
    texto_nota: str,
) -> dict[str, Any] | None:
    if not _objeto_existe(TABELA_CARD_NOTA):
        return None

    sql = text(f"""
        INSERT INTO {TABELA_CARD_NOTA}
        (
            IDFatoKanbanCard,
            TipoNota,
            Texto,
            CriadoEm,
            CriadoPor,
            IDEmpresaProprietaria,
            IDEmpresa
        )
        OUTPUT
            INSERTED.IDFatoKanbanCardNota,
            INSERTED.IDFatoKanbanCard,
            INSERTED.TipoNota,
            INSERTED.Texto,
            INSERTED.CriadoEm,
            INSERTED.CriadoPor,
            INSERTED.IDEmpresaProprietaria,
            INSERTED.IDEmpresa
        VALUES
        (
            :id_card,
            :tipo_nota,
            :texto,
            GETDATE(),
            :id_usuario,
            :id_empresa_proprietaria,
            :id_empresa_relacionada
        );
    """)

    row = db.session.execute(
        sql,
        {
            "id_card": int(id_card),
            "tipo_nota": TIPO_NOTA_APROVACAO_DESCONTO,
            "texto": str(texto_nota or "").strip()[:2000],
            "id_usuario": int(id_usuario),
            "id_empresa_proprietaria": int(id_empresa_proprietaria),
            "id_empresa_relacionada": int(id_empresa_relacionada or 0) or None,
        },
    ).mappings().first()

    return dict(row) if row else None




def _atualizar_item_operacional_aprovado(
    *,
    negociacao: Mapping[str, Any] | dict[str, Any],
    preco_aprovado: Decimal,
    desconto_aprovado_percentual: Decimal | None,
    id_usuario: int,
) -> int:
    id_card = int(negociacao.get("IDFatoKanbanCard") or 0)
    id_painel = int(negociacao.get("IDDimPaineisEuromidia") or 0)
    id_face = int(negociacao.get("IDDimFacesPaineis") or 0)

    custo_base = _valor_decimal(
        negociacao.get("CustoProposto")
        if negociacao.get("CustoProposto") not in (None, "")
        else negociacao.get("CustoAtual")
    ) or Decimal("0")

    margem_valor = preco_aprovado - custo_base
    margem_percentual = None
    if preco_aprovado > 0:
        margem_percentual = (margem_valor / preco_aprovado) * Decimal("100")

    params = {
        "id_card": id_card,
        "preco_aprovado": preco_aprovado,
        "desconto_aprovado": desconto_aprovado_percentual,
        "margem_valor": margem_valor,
        "margem_percentual": margem_percentual,
        "id_usuario": int(id_usuario),
    }

    where_extra = ""
    if id_painel > 0:
        where_extra += "\n          AND ISNULL(IDDimPaineisEuromidia, 0) = :id_painel"
        params["id_painel"] = id_painel
    if id_face > 0:
        where_extra += "\n          AND ISNULL(IDDimFacesPaineis, 0) = :id_face"
        params["id_face"] = id_face

    sql = text(f"""
        UPDATE [Kanban].[Silver].[FatoKanbanCardPainelFace]
        SET
            NovoValor = :preco_aprovado,
            PercentualDesconto = :desconto_aprovado,
            ValorVendaFinal = :preco_aprovado,
            MargemValor = :margem_valor,
            MargemPercentual = :margem_percentual,
            DataAtualizacao = GETDATE(),
            IDUsuario = :id_usuario
        WHERE IDFatoKanbanCard = :id_card
          AND ISNULL(Ativo, 1) = 1
          {where_extra};
    """)

    resultado = db.session.execute(sql, params)
    rowcount = int(getattr(resultado, "rowcount", 0) or 0)

    if rowcount > 0:
        return rowcount

    sql_fallback = text("""
        UPDATE [Kanban].[Silver].[FatoKanbanCardPainelFace]
        SET
            NovoValor = :preco_aprovado,
            PercentualDesconto = :desconto_aprovado,
            ValorVendaFinal = :preco_aprovado,
            MargemValor = :margem_valor,
            MargemPercentual = :margem_percentual,
            DataAtualizacao = GETDATE(),
            IDUsuario = :id_usuario
        WHERE IDFatoKanbanCard = :id_card
          AND ISNULL(Ativo, 1) = 1
          AND (
                SELECT COUNT(1)
                FROM [Kanban].[Silver].[FatoKanbanCardPainelFace] pf2
                WHERE pf2.IDFatoKanbanCard = :id_card
                  AND ISNULL(pf2.Ativo, 1) = 1
          ) = 1;
    """)
    resultado_fallback = db.session.execute(sql_fallback, params)
    return int(getattr(resultado_fallback, "rowcount", 0) or 0)












def _buscar_negociacao_preco_para_aprovacao(
    *,
    id_card: int,
    id_negociacao_preco: int,
    id_empresa_proprietaria: int,
) -> dict[str, Any] | None:
    sql = text("""
        SELECT TOP (1)
            *
        FROM [Kanban].[Silver].[FatoKanbanNegociacaoPreco]
        WHERE IDFatoKanbanNegociacaoPreco = :id_negociacao_preco
          AND IDFatoKanbanCard = :id_card
          AND IDEmpresaProprietaria = :id_empresa_proprietaria;
    """)

    row = db.session.execute(
        sql,
        {
            "id_negociacao_preco": int(id_negociacao_preco),
            "id_card": int(id_card),
            "id_empresa_proprietaria": int(id_empresa_proprietaria),
        },
    ).mappings().first()

    return dict(row) if row else None





@kanban_bp.route("/aprovacao-preco", methods=["GET"])
@login_required
@limiter.limit("60/minute")
def lista_aprovacao_preco():
    _assert_login()
    id_empresa_proprietaria = _id_empresa_usuario_or_403()

    cards = _buscar_cards_lista_aprovacao_preco(int(id_empresa_proprietaria))

    return render_template(
        "kanban/lista_aprovacao_preco.html",
        cards=cards,
        nome_tag=NOME_TAG_APROVACAO_DESCONTO,
    )





@kanban_bp.route("/aprovacao-preco/<int:id_card>", methods=["GET"])
@login_required
@limiter.limit("60/minute")
def aprovacao_preco_detalhe(id_card: int):
    _assert_login()
    id_empresa_proprietaria = _id_empresa_usuario_or_403()

    _obter_card_autorizado(int(id_card))

    card = _buscar_cabecalho_aprovacao_preco(int(id_card), int(id_empresa_proprietaria))
    if not card:
        abort(404)

    pendencias = _buscar_itens_pendentes_aprovacao_preco(int(id_card), int(id_empresa_proprietaria))
    historico_precos = _buscar_historico_precos_card(int(id_card), int(id_empresa_proprietaria))
    tags_ativas = _obter_tags_do_card(int(id_card))

    return render_template(
        "kanban/aprovacao_preco_detalhe.html",
        card=card,
        pendencias=pendencias,
        historico_precos=historico_precos,
        tags_ativas=tags_ativas,
    )














@kanban_bp.route("/api/aprovacao-preco/<int:id_card>/aprovar", methods=["POST"])
@login_required
@limiter.limit("120/minute")
def api_aprovacao_preco_aprovar(id_card: int):
    id_usuario = _assert_login()
    id_empresa_proprietaria = _id_empresa_usuario_or_403()
    card_escopo = _obter_card_autorizado(int(id_card))
    id_kanban = int(card_escopo.get("IDDimKanban") or 0)

    payload = request.get_json(silent=True) or {}

    try:
        id_negociacao_preco = int(payload.get("id_negociacao_preco") or 0)
    except Exception:
        id_negociacao_preco = 0

    preco_aprovado = _valor_decimal(payload.get("preco_aprovado"))
    observacoes_aprovacao = (payload.get("observacoes_aprovacao") or "").strip()

    if id_negociacao_preco <= 0:
        return jsonify({"ok": False, "msg": "ID da negociação é obrigatório."}), 400

    if preco_aprovado is None or preco_aprovado <= 0:
        return jsonify({"ok": False, "msg": "Informe um preço aprovado válido."}), 400

    negociacao = _buscar_negociacao_preco_para_aprovacao(
        id_card=int(id_card),
        id_negociacao_preco=int(id_negociacao_preco),
        id_empresa_proprietaria=int(id_empresa_proprietaria),
    )

    if not negociacao:
        return jsonify({"ok": False, "msg": "Negociação não encontrada."}), 404

    if negociacao.get("PrecoAprovado") not in (None, "") or negociacao.get("DataAprovacaoPreco") is not None:
        return jsonify({"ok": False, "msg": "Essa negociação já foi aprovada."}), 409

    preco_base_desconto = _valor_decimal(
        negociacao.get("PrecoProposto")
        if negociacao.get("PrecoProposto") not in (None, "")
        else negociacao.get("PrecoAtual")
    )
    custo_base = _valor_decimal(
        negociacao.get("CustoProposto")
        if negociacao.get("CustoProposto") not in (None, "")
        else negociacao.get("CustoAtual")
    ) or Decimal("0")

    desconto_aprovado_percentual = None
    desconto_valor = None
    if preco_base_desconto is not None and preco_base_desconto > 0:
        desconto_valor = preco_base_desconto - preco_aprovado
        desconto_aprovado_percentual = (desconto_valor / preco_base_desconto) * Decimal("100")

    margem_valor = preco_aprovado - custo_base
    margem_percentual = None
    if preco_aprovado > 0:
        margem_percentual = (margem_valor / preco_aprovado) * Decimal("100")

    etapa = "inicio"
    try:
        etapa = "update_negociacao_preco"
        sql_update_negociacao = text("""
            UPDATE [Kanban].[Silver].[FatoKanbanNegociacaoPreco]
            SET
                IDDimUsuariosAprovacaoPreco = :id_usuario_aprovacao,
                DataAprovacaoPreco = GETDATE(),
                PrecoAprovado = :preco_aprovado,
                DescontoAprovado = :desconto_aprovado,
                ObservacoesAprovacao = :observacoes_aprovacao,
                BitAutorizacaoDiretoria = 1
            WHERE IDFatoKanbanNegociacaoPreco = :id_negociacao_preco
              AND IDFatoKanbanCard = :id_card
              AND IDEmpresaProprietaria = :id_empresa_proprietaria;
        """)
        db.session.execute(
            sql_update_negociacao,
            {
                "id_usuario_aprovacao": int(id_usuario),
                "preco_aprovado": preco_aprovado,
                "desconto_aprovado": desconto_aprovado_percentual,
                "observacoes_aprovacao": observacoes_aprovacao or None,
                "id_negociacao_preco": int(id_negociacao_preco),
                "id_card": int(id_card),
                "id_empresa_proprietaria": int(id_empresa_proprietaria),
            },
        )

        etapa = "update_operacional"
        _atualizar_item_operacional_aprovado(
            negociacao=negociacao,
            preco_aprovado=preco_aprovado,
            desconto_aprovado_percentual=desconto_aprovado_percentual,
            id_usuario=int(id_usuario),
        )

        etapa = "sincronizar_tags"
        estados_atuais = _listar_estado_atual_negociacao_card(int(id_card))
        _sincronizar_tag_aprovacao_diretoria_card(
            id_card=int(id_card),
            id_kanban=int(id_kanban),
            estados_atuais=estados_atuais,
            id_usuario=int(id_usuario),
            id_empresa_proprietaria=int(id_empresa_proprietaria),
        )

        nome_usuario = getattr(current_user, "NomeUsuario", None) or getattr(current_user, "nome", None) or f"Usuário #{int(id_usuario)}"
        texto_nota = (
            f"Desconto aprovado: { _formatar_decimal_br(desconto_aprovado_percentual, 2) if desconto_aprovado_percentual is not None else '0,00' }% | "
            f"Valor do desconto: R$ { _formatar_decimal_br(desconto_valor, 2) if desconto_valor is not None else '0,00' } | "
            f"Preço aprovado: R$ { _formatar_decimal_br(preco_aprovado, 2) } | "
            f"Margem aprovada: { _formatar_decimal_br(margem_percentual, 2) if margem_percentual is not None else '0,00' }% | "
            f"Aprovado por: {nome_usuario}"
        )
        if observacoes_aprovacao:
            texto_nota += f" | Observações: {observacoes_aprovacao}"

        etapa = "gravar_nota_tabela_oficial"
        _inserir_nota_aprovacao_desconto_card(
            id_card=int(id_card),
            id_empresa_proprietaria=int(id_empresa_proprietaria),
            id_empresa_relacionada=int(negociacao.get("IDEmpresa") or 0) or None,
            id_usuario=int(id_usuario),
            texto_nota=texto_nota,
        )

        etapa = "espelhar_nota_observacoes"
        _registrar_observacao_historica_card(
            id_card=int(id_card),
            texto_observacao=texto_nota,
            id_usuario=int(id_usuario),
        )

        etapa = "registrar_log"
        snapshot_depois = _obter_snapshot_card_log(int(id_card), incluir_inativo=True)
        _registrar_log_card(
            id_card=int(id_card),
            id_kanban=int(id_kanban),
            id_empresa_proprietaria=int(id_empresa_proprietaria),
            id_usuario_acao=int(id_usuario),
            tipo_evento="CARD_PRECO_APROVADO",
            subtipo_evento="APROVACAO_DESCONTO",
            observacao=texto_nota,
            tabela_origem=TABELA_CARD_NEGOCIACAO_PRECO,
            id_registro_origem=int(id_negociacao_preco),
            payload_depois=snapshot_depois,
        )

        etapa = "commit"
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        current_app.logger.exception(
            "Erro ao aprovar preço do card. etapa=%s id_card=%s id_negociacao=%s",
            etapa,
            id_card,
            id_negociacao_preco,
        )
        return jsonify({"ok": False, "msg": f"Erro ao aprovar preço: {str(exc)}"}), 500

    _invalidar_kanban(id_emp=int(id_empresa_proprietaria), id_kanban=int(id_kanban), id_card=int(id_card))
    _emitir_evento_kanban(int(id_kanban), "card_atualizado", {"id_card": int(id_card)})

    return jsonify(
        {
            "ok": True,
            "msg": "Preço aprovado com sucesso.",
            "id_card": int(id_card),
            "id_negociacao_preco": int(id_negociacao_preco),
            "tags": _obter_tags_do_card(int(id_card)),
            "nota": texto_nota,
            "historico_precos_url": url_for("kanban.historico_precos_visualizacao", id_card=int(id_card)),
            "historico_card_url": url_for("kanban.historico_card_visualizacao", id_card=int(id_card)),
        }
    )















def _normalizar_cnpj(valor: Any) -> str:
    """Eu removo qualquer caractere não numérico do CNPJ."""
    return re.sub(r"\D+", "", str(valor or ""))[:14]


def _formatar_cnpj(valor: Any) -> str:
    """Eu devolvo o CNPJ mascarado quando ele tiver 14 dígitos."""
    cnpj = _normalizar_cnpj(valor)
    if len(cnpj) != 14:
        return str(valor or "")
    return f"{cnpj[0:2]}.{cnpj[2:5]}.{cnpj[5:8]}/{cnpj[8:12]}-{cnpj[12:14]}"


def _texto_ou_none(valor: Any, tamanho_maximo: int | None = None) -> str | None:
    """Eu normalizo textos opcionais e corto no tamanho da coluna quando necessário."""
    if valor is None:
        return None
    texto = str(valor).strip()
    if not texto:
        return None
    if tamanho_maximo is not None:
        texto = texto[: int(tamanho_maximo)]
    return texto


def _int_ou_none(valor: Any) -> int | None:
    """Eu converto números inteiros opcionais."""
    if valor in (None, ""):
        return None
    try:
        return int(str(valor).strip())
    except Exception:
        return None


def _bigint_ou_none(valor: Any) -> int | None:
    """Eu converto bigint opcional."""
    if valor in (None, ""):
        return None
    texto = str(valor).strip().replace(".", "").replace(",", "")
    if not texto:
        return None
    try:
        return int(texto)
    except Exception:
        return None


def _decimal_ou_none(valor: Any) -> Decimal | None:
    """Eu converto decimal opcional aceitando vírgula ou ponto."""
    if valor in (None, ""):
        return None
    texto = str(valor).strip().replace(",", ".")
    if not texto:
        return None
    try:
        return Decimal(texto)
    except (InvalidOperation, ValueError):
        return None


def _bit_ou_none(valor: Any) -> int | None:
    """Eu converto valores lógicos para bit do SQL Server."""
    if valor in (None, ""):
        return None

    if isinstance(valor, bool):
        return 1 if valor else 0

    texto = str(valor).strip().lower()
    if texto in {"1", "true", "t", "sim", "s", "yes", "y"}:
        return 1
    if texto in {"0", "false", "f", "nao", "não", "n", "no"}:
        return 0

    return None


def _serializar_valor_json_empresa(valor: Any) -> Any:
    """Eu converto tipos do SQL Server para JSON seguro."""
    if isinstance(valor, datetime):
        return valor.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(valor, date):
        return valor.strftime("%Y-%m-%d")
    if isinstance(valor, Decimal):
        return float(valor)
    return valor


def _serializar_empresa(row: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """Eu transformo a linha da empresa em dicionário JSON."""
    if not row:
        return None

    dados = {}
    for chave, valor in dict(row).items():
        if chave == "BitCliente" and valor is not None:
            dados[chave] = bool(valor)
            continue
        dados[chave] = _serializar_valor_json_empresa(valor)

    if dados.get("CNPJ"):
        dados["CNPJ"] = _formatar_cnpj(dados["CNPJ"])

    return dados


def _cache_delete_safe(chave: str) -> None:
    """Eu removo uma chave de cache sem estourar erro no fluxo principal."""
    try:
        cache.delete(chave)
    except Exception:
        pass


def _chave_cache_empresa_cadastro_por_id(id_empresa: int) -> str:
    """Eu monto a chave de cache da consulta completa da empresa por IDEmpresa."""
    return _chave_cache_json("kanban:api:empresa_cadastro:id", int(id_empresa or 0))


def _chave_cache_empresa_cadastro_por_cnpj(cnpj_normalizado: str) -> str:
    """Eu monto a chave de cache da consulta completa da empresa por CNPJ limpo."""
    return _chave_cache_json("kanban:api:empresa_cadastro:cnpj", _normalizar_cnpj(cnpj_normalizado))


def _invalidar_cache_empresa_cadastro(id_empresa: int | None = None, cnpj_normalizado: str | None = None) -> None:
    """Eu invalido os caches do cadastro completo da empresa por ID e por CNPJ."""
    if id_empresa:
        _cache_delete_safe(_chave_cache_empresa_cadastro_por_id(int(id_empresa)))

    cnpj_limpo = _normalizar_cnpj(cnpj_normalizado)
    if cnpj_limpo:
        _cache_delete_safe(_chave_cache_empresa_cadastro_por_cnpj(cnpj_limpo))


def _listar_empresas_proprietarias_para_cadastro() -> list[dict[str, Any]]:
    """Eu listo empresas proprietárias ativas para preencher o select do modal."""
    chave = _chave_cache_json("kanban:api:empresas_proprietarias:lista")
    em_cache = _cache_json_get(chave)
    if em_cache is not None:
        return em_cache

    sql = text(f"""
        SELECT
            IDEmpresaProprietaria,
            RazaoSocial,
            CNPJ
        FROM {TABELA_EMPRESAS_PROPRIETARIAS}
        WHERE ISNULL(BitAtivo, 1) = 1
        ORDER BY RazaoSocial ASC;
    """)

    rows = db.session.execute(sql).mappings().all()
    lista = []
    for row in rows:
        item = dict(row)
        item["CNPJ"] = _formatar_cnpj(item.get("CNPJ"))
        lista.append(item)

    _cache_json_set(chave, lista, TIMEOUT_CACHE_LONGO)
    return lista


def _buscar_empresa_completa_por_id(id_empresa: int) -> dict[str, Any] | None:
    """Eu busco a empresa completa pelo IDEmpresa usando cache curto."""
    id_empresa = int(id_empresa or 0)
    if id_empresa <= 0:
        return None

    chave = _chave_cache_empresa_cadastro_por_id(id_empresa)
    em_cache = _cache_json_get(chave)
    if isinstance(em_cache, dict):
        return em_cache

    sql = text(f"""
        SELECT TOP 1
            e.IDEmpresa,
            e.IDEmpresaProprietaria,
            e.CNPJ,
            e.UF,
            e.CEP,
            e.CodigoPorte,
            e.Pais,
            e.Email,
            e.Porte,
            e.Bairro,
            e.Numero,
            e.TelefoneContato1,
            e.Municipio,
            e.Logradouro,
            e.CNAE,
            e.Complemento,
            e.RazaoSocial,
            e.NomeFantasia,
            e.CapitalSocial,
            e.TelefoneContato2,
            e.NaturezaJuridica,
            e.DescricaoCnae,
            e.DataInicioAtividades,
            e.DataSituacaoEspecial,
            e.DataOpcaoPeloSimples,
            e.DataSituacaoCadastral,
            e.DataExclusaoSimples,
            e.IdentificadorMatrizFilial,
            e.DescricaoSituacaoCadastral,
            e.DescricaoMotivoSituacaoCadastral,
            e.DescricaoIdentificadorMatrizFilial,
            e.DescricaoTipoLogradouro,
            e.DataAtualizacao,
            e.Latitude,
            e.Longitude,
            e.BitCliente,
            c.Setor,
            c.Classe
        FROM {TABELA_EMPRESAS} e
        LEFT JOIN {TABELA_CNAES} c
          ON c.cnaepadrao = e.CNAE
        WHERE e.IDEmpresa = :id_empresa;
    """)

    row = db.session.execute(sql, {"id_empresa": id_empresa}).mappings().first()
    empresa = _serializar_empresa(row)
    if empresa:
        _cache_json_set(chave, empresa, TIMEOUT_CACHE_CURTO)
        cnpj_limpo = _normalizar_cnpj(empresa.get("CNPJ"))
        if cnpj_limpo:
            _cache_json_set(_chave_cache_empresa_cadastro_por_cnpj(cnpj_limpo), empresa, TIMEOUT_CACHE_CURTO)
    return empresa





def _buscar_empresa_completa_por_cnpj_normalizado(cnpj_normalizado: str) -> dict[str, Any] | None:
    """Eu busco a empresa pelo CNPJ já limpo, comparando diretamente com a coluna."""
    cnpj_normalizado = _normalizar_cnpj(cnpj_normalizado)
    if len(cnpj_normalizado) != 14:
        return None

    chave = _chave_cache_json("kanban:api:empresa_cadastro:cnpj", cnpj_normalizado)
    em_cache = _cache_json_get(chave)
    if isinstance(em_cache, dict):
        return em_cache

    sql = text(f"""
        SELECT TOP 1
            e.IDEmpresa,
            e.IDEmpresaProprietaria,
            e.CNPJ,
            e.UF,
            e.CEP,
            e.CodigoPorte,
            e.Pais,
            e.Email,
            e.Porte,
            e.Bairro,
            e.Numero,
            e.TelefoneContato1,
            e.Municipio,
            e.Logradouro,
            e.CNAE,
            e.Complemento,
            e.RazaoSocial,
            e.NomeFantasia,
            e.CapitalSocial,
            e.TelefoneContato2,
            e.NaturezaJuridica,
            e.DescricaoCnae,
            e.DataInicioAtividades,
            e.DataSituacaoEspecial,
            e.DataOpcaoPeloSimples,
            e.DataSituacaoCadastral,
            e.DataExclusaoSimples,
            e.IdentificadorMatrizFilial,
            e.DescricaoSituacaoCadastral,
            e.DescricaoMotivoSituacaoCadastral,
            e.DescricaoIdentificadorMatrizFilial,
            e.DescricaoTipoLogradouro,
            e.DataAtualizacao,
            e.Latitude,
            e.Longitude,
            e.BitCliente,
            c.Setor,
            c.Classe
        FROM {TABELA_EMPRESAS} e
        LEFT JOIN {TABELA_CNAES} c
          ON c.cnaepadrao = e.CNAE
        WHERE e.CNPJ = :cnpj
        ORDER BY e.DataAtualizacao DESC, e.IDEmpresa DESC;
    """)

    row = db.session.execute(sql, {"cnpj": cnpj_normalizado}).mappings().first()
    empresa = _serializar_empresa(row)

    if empresa:
        _cache_json_set(chave, empresa, TIMEOUT_CACHE_CURTO)

    return empresa


def _mapear_empresa_minha_receita(dados_api: dict[str, Any], cnpj_consultado: str) -> dict[str, Any] | None:
    """Eu converto o payload da API Minha Receita para o formato da DimEmpresas."""
    if not isinstance(dados_api, dict):
        return None

    cnpj_api = _normalizar_cnpj(dados_api.get("cnpj") or cnpj_consultado)
    if len(cnpj_api) != 14:
        return None

    porte_valor = dados_api.get("porte")
    if isinstance(porte_valor, dict):
        porte_texto = porte_valor.get("descricao") or porte_valor.get("porte")
    else:
        porte_texto = porte_valor

    natureza_valor = dados_api.get("natureza_juridica")
    if isinstance(natureza_valor, dict):
        natureza_texto = natureza_valor.get("descricao") or natureza_valor.get("nome")
    else:
        natureza_texto = natureza_valor

    retorno = {
        "IDEmpresa": None,
        "IDEmpresaProprietaria": None,
        "CNPJ": _formatar_cnpj(cnpj_api),
        "UF": _texto_ou_none(dados_api.get("uf"), 3),
        "CEP": _texto_ou_none(dados_api.get("cep"), 10),
        "CodigoPorte": _int_ou_none(
            dados_api.get("codigo_porte")
            or (porte_valor.get("codigo") if isinstance(porte_valor, dict) else None)
        ),
        "Pais": _texto_ou_none(dados_api.get("pais") or "Brasil", 50),
        "Email": _texto_ou_none(dados_api.get("email"), 100),
        "Porte": _texto_ou_none(porte_texto, 200),
        "Bairro": _texto_ou_none(dados_api.get("bairro"), 100),
        "Numero": _texto_ou_none(dados_api.get("numero"), 20),
        "TelefoneContato1": _texto_ou_none(
            dados_api.get("ddd_telefone_1")
            or dados_api.get("telefone")
            or dados_api.get("telefone_1"),
            20,
        ),
        "Municipio": _texto_ou_none(dados_api.get("municipio"), 100),
        "Logradouro": _texto_ou_none(dados_api.get("logradouro"), 150),
        "CNAE": _texto_ou_none(
            dados_api.get("cnae_fiscal")
            or dados_api.get("cnae")
            or dados_api.get("atividade_principal"),
            20,
        ),
        "Complemento": _texto_ou_none(dados_api.get("complemento"), 100),
        "RazaoSocial": _texto_ou_none(
            dados_api.get("razao_social")
            or dados_api.get("nome_empresarial")
            or dados_api.get("razao"),
            150,
        ),
        "NomeFantasia": _texto_ou_none(dados_api.get("nome_fantasia"), 150),
        "CapitalSocial": _bigint_ou_none(dados_api.get("capital_social")),
        "TelefoneContato2": _texto_ou_none(
            dados_api.get("ddd_telefone_2")
            or dados_api.get("telefone_2"),
            20,
        ),
        "NaturezaJuridica": _texto_ou_none(natureza_texto, 100),
        "DescricaoCnae": _texto_ou_none(
            dados_api.get("cnae_fiscal_descricao")
            or dados_api.get("descricao_cnae")
            or dados_api.get("atividade_principal_descricao"),
            150,
        ),
        "DataInicioAtividades": _serializar_valor_json_empresa(
            _para_data_sql_ou_none(dados_api.get("data_inicio_atividade"))
        ),
        "DataSituacaoEspecial": _serializar_valor_json_empresa(
            _para_data_sql_ou_none(dados_api.get("data_situacao_especial"))
        ),
        "DataOpcaoPeloSimples": _serializar_valor_json_empresa(
            _para_data_sql_ou_none(dados_api.get("data_opcao_pelo_simples"))
        ),
        "DataSituacaoCadastral": _serializar_valor_json_empresa(
            _para_data_sql_ou_none(dados_api.get("data_situacao_cadastral"))
        ),
        "DataExclusaoSimples": _serializar_valor_json_empresa(
            _para_data_sql_ou_none(dados_api.get("data_exclusao_simples"))
        ),
        "IdentificadorMatrizFilial": _int_ou_none(
            dados_api.get("identificador_matriz_filial")
            or dados_api.get("matriz_filial")
        ),
        "DescricaoSituacaoCadastral": _texto_ou_none(
            dados_api.get("descricao_situacao_cadastral")
            or dados_api.get("situacao_cadastral"),
            20,
        ),
        "DescricaoMotivoSituacaoCadastral": _texto_ou_none(
            dados_api.get("descricao_motivo_situacao_cadastral")
            or dados_api.get("motivo_situacao_cadastral"),
            20,
        ),
        "DescricaoIdentificadorMatrizFilial": _texto_ou_none(
            dados_api.get("descricao_identificador_matriz_filial"),
            20,
        ),
        "DescricaoTipoLogradouro": _texto_ou_none(
            dados_api.get("descricao_tipo_logradouro")
            or dados_api.get("tipo_logradouro"),
            20,
        ),
        "DataAtualizacao": None,
        "Latitude": _serializar_valor_json_empresa(_decimal_ou_none(dados_api.get("latitude"))),
        "Longitude": _serializar_valor_json_empresa(_decimal_ou_none(dados_api.get("longitude"))),
        "BitCliente": False,
        "Setor": None,
        "Classe": None,
    }

    return retorno




def _buscar_empresa_na_api_minha_receita(cnpj_normalizado: str) -> dict[str, Any] | None:
    """Eu consulto a API externa somente quando não encontro o CNPJ no banco."""
    cnpj_normalizado = _normalizar_cnpj(cnpj_normalizado)
    if len(cnpj_normalizado) != 14:
        return None

    chave = _chave_cache_json("kanban:api:minha_receita:cnpj", cnpj_normalizado)
    em_cache = _cache_json_get(chave)
    if isinstance(em_cache, dict):
        return em_cache

    try:
        resposta = requests.get(
            f"{URL_API_MINHA_RECEITA.rstrip('/')}/{cnpj_normalizado}",
            timeout=(2, 4),
            headers={"Accept": "application/json"},
        )
    except Exception as erro:
        current_app.logger.warning(
            "KANBAN EMPRESA: falha ao consultar Minha Receita para CNPJ=%s erro=%s",
            cnpj_normalizado,
            erro,
        )
        return None

    if not resposta.ok:
        current_app.logger.warning(
            "KANBAN EMPRESA: Minha Receita retornou HTTP=%s para CNPJ=%s",
            resposta.status_code,
            cnpj_normalizado,
        )
        return None

    try:
        dados_api = resposta.json()
    except Exception as erro:
        current_app.logger.warning(
            "KANBAN EMPRESA: falha ao converter JSON da Minha Receita para CNPJ=%s erro=%s",
            cnpj_normalizado,
            erro,
        )
        return None

    empresa = _mapear_empresa_minha_receita(dados_api, cnpj_normalizado)
    if empresa:
        _cache_json_set(chave, empresa, TIMEOUT_CACHE_MEDIO)

    return empresa


def _invalidar_cache_empresas(id_empresa: int | None = None, cnpj_normalizado: str | None = None) -> None:
    """Eu invalido os caches principais de empresa usados no modal do card."""
    _cache_delete_safe(_chave_cache_json("kanban:api:empresas:lista"))
    _cache_delete_safe(_chave_cache_json("kanban:api:empresas_proprietarias:lista"))
    _invalidar_cache_empresa_cadastro(id_empresa=id_empresa, cnpj_normalizado=cnpj_normalizado)


@kanban_bp.route("/api/empresas-proprietarias", methods=["GET"])
@login_required
@limiter.limit("60/minute")
def api_empresas_proprietarias_lista():
    _assert_login()
    return jsonify(
        {
            "ok": True,
            "empresas_proprietarias": _listar_empresas_proprietarias_para_cadastro(),
        }
    )


@kanban_bp.route("/api/empresas/cadastro", methods=["GET", "POST"])
@login_required
@limiter.limit("120/minute")
def api_empresa_cadastro_compat():
    """Rota única para consulta por IDEmpresa ou por CNPJ, e compatibilidade com POST antigo."""
    _assert_login()

    if request.method == "POST":
        return api_empresa_cadastro_salvar()

    id_empresa = _int_ou_none(request.args.get("id_empresa")) or 0
    cnpj_normalizado = _normalizar_cnpj(request.args.get("cnpj"))

    if id_empresa > 0:
        return api_empresa_cadastro_por_id(id_empresa)

    if len(cnpj_normalizado) == 14:
        return api_empresa_cadastro_buscar_por_cnpj()

    return jsonify(
        {
            "ok": False,
            "erro": "Informe id_empresa ou cnpj para consultar o cadastro da empresa.",
        }
    ), 400


@kanban_bp.route("/api/empresas/cadastro/<int:id_empresa>", methods=["GET"])
@login_required
@limiter.limit("120/minute")
def api_empresa_cadastro_por_id(id_empresa: int):
    _assert_login()

    empresa = _buscar_empresa_completa_por_id(id_empresa)
    if not empresa:
        return jsonify({"ok": False, "erro": "Empresa não encontrada."}), 404

    return jsonify(
        {
            "ok": True,
            "origem": "banco",
            "empresa": empresa,
            "empresas_proprietarias": _listar_empresas_proprietarias_para_cadastro(),
        }
    )



@kanban_bp.route("/api/empresas/cadastro/buscar-por-cnpj", methods=["GET"])
@login_required
@limiter.limit("120/minute")
def api_empresa_cadastro_buscar_por_cnpj():
    _assert_login()

    try:
        cnpj_normalizado = _normalizar_cnpj(request.args.get("cnpj"))
        if len(cnpj_normalizado) != 14:
            return jsonify(
                {
                    "ok": True,
                    "encontrado": False,
                    "origem": None,
                    "empresa": None,
                    "empresas_proprietarias": _listar_empresas_proprietarias_para_cadastro(),
                }
            )

        empresa_banco = _buscar_empresa_completa_por_cnpj_normalizado(cnpj_normalizado)
        if empresa_banco:
            return jsonify(
                {
                    "ok": True,
                    "encontrado": True,
                    "origem": "banco",
                    "empresa": empresa_banco,
                    "empresas_proprietarias": _listar_empresas_proprietarias_para_cadastro(),
                }
            )

        empresa_api = _buscar_empresa_na_api_minha_receita(cnpj_normalizado)
        if empresa_api:
            return jsonify(
                {
                    "ok": True,
                    "encontrado": True,
                    "origem": "minha_receita",
                    "empresa": empresa_api,
                    "empresas_proprietarias": _listar_empresas_proprietarias_para_cadastro(),
                }
            )

        return jsonify(
            {
                "ok": True,
                "encontrado": False,
                "origem": None,
                "empresa": None,
                "empresas_proprietarias": _listar_empresas_proprietarias_para_cadastro(),
            }
        )

    except Exception as erro:
        current_app.logger.exception(
            "KANBAN EMPRESA: erro ao consultar cadastro por CNPJ. cnpj=%s",
            request.args.get("cnpj"),
        )
        return jsonify(
            {
                "ok": False,
                "erro": f"Erro interno ao consultar empresa: {erro}",
            }
        ), 500




    


@kanban_bp.route("/api/empresas/cadastro/salvar", methods=["POST"])
@login_required
@limiter.limit("60/minute")
def api_empresa_cadastro_salvar():
    _assert_login()
    id_empresa_usuario = _id_empresa_usuario_or_403()

    payload = request.get_json(silent=True) or {}

    cnpj_normalizado = _normalizar_cnpj(payload.get("CNPJ"))
    if len(cnpj_normalizado) != 14:
        return jsonify({"ok": False, "erro": "CNPJ é obrigatório e deve ter 14 dígitos."}), 400

    id_empresa_informado = _int_ou_none(payload.get("IDEmpresa")) or 0

    dados_sql = {
        "IDEmpresa": id_empresa_informado or None,
        "IDEmpresaProprietaria": _int_ou_none(payload.get("IDEmpresaProprietaria")) or id_empresa_usuario,
        "CNPJ": cnpj_normalizado,
        "UF": _texto_ou_none(payload.get("UF"), 3),
        "CEP": _texto_ou_none(payload.get("CEP"), 10),
        "CodigoPorte": _int_ou_none(payload.get("CodigoPorte")),
        "Pais": _texto_ou_none(payload.get("Pais"), 50),
        "Email": _texto_ou_none(payload.get("Email"), 100),
        "Porte": _texto_ou_none(payload.get("Porte"), 200),
        "Bairro": _texto_ou_none(payload.get("Bairro"), 100),
        "Numero": _texto_ou_none(payload.get("Numero"), 20),
        "TelefoneContato1": _texto_ou_none(payload.get("TelefoneContato1"), 20),
        "Municipio": _texto_ou_none(payload.get("Municipio"), 100),
        "Logradouro": _texto_ou_none(payload.get("Logradouro"), 150),
        "CNAE": _texto_ou_none(payload.get("CNAE"), 20),
        "Complemento": _texto_ou_none(payload.get("Complemento"), 100),
        "RazaoSocial": _texto_ou_none(payload.get("RazaoSocial"), 150),
        "NomeFantasia": _texto_ou_none(payload.get("NomeFantasia"), 150),
        "CapitalSocial": _bigint_ou_none(payload.get("CapitalSocial")),
        "TelefoneContato2": _texto_ou_none(payload.get("TelefoneContato2"), 20),
        "NaturezaJuridica": _texto_ou_none(payload.get("NaturezaJuridica"), 100),
        "DescricaoCnae": _texto_ou_none(payload.get("DescricaoCnae"), 150),
        "DataInicioAtividades": _para_data_sql_ou_none(payload.get("DataInicioAtividades")),
        "DataSituacaoEspecial": _para_data_sql_ou_none(payload.get("DataSituacaoEspecial")),
        "DataOpcaoPeloSimples": _para_data_sql_ou_none(payload.get("DataOpcaoPeloSimples")),
        "DataSituacaoCadastral": _para_data_sql_ou_none(payload.get("DataSituacaoCadastral")),
        "DataExclusaoSimples": _para_data_sql_ou_none(payload.get("DataExclusaoSimples")),
        "IdentificadorMatrizFilial": _int_ou_none(payload.get("IdentificadorMatrizFilial")),
        "DescricaoSituacaoCadastral": _texto_ou_none(payload.get("DescricaoSituacaoCadastral"), 20),
        "DescricaoMotivoSituacaoCadastral": _texto_ou_none(payload.get("DescricaoMotivoSituacaoCadastral"), 20),
        "DescricaoIdentificadorMatrizFilial": _texto_ou_none(payload.get("DescricaoIdentificadorMatrizFilial"), 20),
        "DescricaoTipoLogradouro": _texto_ou_none(payload.get("DescricaoTipoLogradouro"), 20),
        "Latitude": _decimal_ou_none(payload.get("Latitude")),
        "Longitude": _decimal_ou_none(payload.get("Longitude")),
        "BitCliente": _bit_ou_none(payload.get("BitCliente")),
    }

    sql_lock_cnpj = text(f"""
        SELECT TOP 1 IDEmpresa
        FROM {TABELA_EMPRESAS} WITH (UPDLOCK, HOLDLOCK)
        WHERE CNPJ = :cnpj;
    """)

    sql_lock_id = text(f"""
        SELECT TOP 1 IDEmpresa
        FROM {TABELA_EMPRESAS} WITH (UPDLOCK, HOLDLOCK)
        WHERE IDEmpresa = :id_empresa;
    """)

    try:
        row_existente_cnpj = db.session.execute(sql_lock_cnpj, {"cnpj": cnpj_normalizado}).mappings().first()

        row_existente_id = None
        if not row_existente_cnpj and id_empresa_informado:
            row_existente_id = db.session.execute(
                sql_lock_id,
                {"id_empresa": id_empresa_informado},
            ).mappings().first()

        id_empresa_alvo = 0
        if row_existente_cnpj:
            id_empresa_alvo = int(row_existente_cnpj.get("IDEmpresa") or 0)
        elif row_existente_id:
            id_empresa_alvo = int(row_existente_id.get("IDEmpresa") or 0)

        if id_empresa_alvo > 0:
            dados_sql["IDEmpresa"] = id_empresa_alvo

            sql_update = text(f"""
                UPDATE {TABELA_EMPRESAS}
                   SET IDEmpresaProprietaria = :IDEmpresaProprietaria,
                       CNPJ = :CNPJ,
                       UF = :UF,
                       CEP = :CEP,
                       CodigoPorte = :CodigoPorte,
                       Pais = :Pais,
                       Email = :Email,
                       Porte = :Porte,
                       Bairro = :Bairro,
                       Numero = :Numero,
                       TelefoneContato1 = :TelefoneContato1,
                       Municipio = :Municipio,
                       Logradouro = :Logradouro,
                       CNAE = :CNAE,
                       Complemento = :Complemento,
                       RazaoSocial = :RazaoSocial,
                       NomeFantasia = :NomeFantasia,
                       CapitalSocial = :CapitalSocial,
                       TelefoneContato2 = :TelefoneContato2,
                       NaturezaJuridica = :NaturezaJuridica,
                       DescricaoCnae = :DescricaoCnae,
                       DataInicioAtividades = :DataInicioAtividades,
                       DataSituacaoEspecial = :DataSituacaoEspecial,
                       DataOpcaoPeloSimples = :DataOpcaoPeloSimples,
                       DataSituacaoCadastral = :DataSituacaoCadastral,
                       DataExclusaoSimples = :DataExclusaoSimples,
                       IdentificadorMatrizFilial = :IdentificadorMatrizFilial,
                       DescricaoSituacaoCadastral = :DescricaoSituacaoCadastral,
                       DescricaoMotivoSituacaoCadastral = :DescricaoMotivoSituacaoCadastral,
                       DescricaoIdentificadorMatrizFilial = :DescricaoIdentificadorMatrizFilial,
                       DescricaoTipoLogradouro = :DescricaoTipoLogradouro,
                       Latitude = :Latitude,
                       Longitude = :Longitude,
                       BitCliente = :BitCliente,
                       DataAtualizacao = GETDATE()
                 WHERE IDEmpresa = :IDEmpresa;
            """)

            db.session.execute(sql_update, dados_sql)

        else:
            sql_insert = text(f"""
                INSERT INTO {TABELA_EMPRESAS} (
                    IDEmpresaProprietaria,
                    CNPJ,
                    UF,
                    CEP,
                    CodigoPorte,
                    Pais,
                    Email,
                    Porte,
                    Bairro,
                    Numero,
                    TelefoneContato1,
                    Municipio,
                    Logradouro,
                    CNAE,
                    Complemento,
                    RazaoSocial,
                    NomeFantasia,
                    CapitalSocial,
                    TelefoneContato2,
                    NaturezaJuridica,
                    DescricaoCnae,
                    DataInicioAtividades,
                    DataSituacaoEspecial,
                    DataOpcaoPeloSimples,
                    DataSituacaoCadastral,
                    DataExclusaoSimples,
                    IdentificadorMatrizFilial,
                    DescricaoSituacaoCadastral,
                    DescricaoMotivoSituacaoCadastral,
                    DescricaoIdentificadorMatrizFilial,
                    DescricaoTipoLogradouro,
                    DataAtualizacao,
                    Latitude,
                    Longitude,
                    BitCliente
                )
                VALUES (
                    :IDEmpresaProprietaria,
                    :CNPJ,
                    :UF,
                    :CEP,
                    :CodigoPorte,
                    :Pais,
                    :Email,
                    :Porte,
                    :Bairro,
                    :Numero,
                    :TelefoneContato1,
                    :Municipio,
                    :Logradouro,
                    :CNAE,
                    :Complemento,
                    :RazaoSocial,
                    :NomeFantasia,
                    :CapitalSocial,
                    :TelefoneContato2,
                    :NaturezaJuridica,
                    :DescricaoCnae,
                    :DataInicioAtividades,
                    :DataSituacaoEspecial,
                    :DataOpcaoPeloSimples,
                    :DataSituacaoCadastral,
                    :DataExclusaoSimples,
                    :IdentificadorMatrizFilial,
                    :DescricaoSituacaoCadastral,
                    :DescricaoMotivoSituacaoCadastral,
                    :DescricaoIdentificadorMatrizFilial,
                    :DescricaoTipoLogradouro,
                    GETDATE(),
                    :Latitude,
                    :Longitude,
                    :BitCliente
                );

                SELECT CAST(SCOPE_IDENTITY() AS int) AS IDEmpresa;
            """)

            novo_id = db.session.execute(sql_insert, dados_sql).scalar()
            id_empresa_alvo = int(novo_id or 0)

        db.session.commit()

    except Exception as erro:
        db.session.rollback()
        current_app.logger.exception("KANBAN EMPRESA: erro ao salvar cadastro de empresa.")
        return jsonify({"ok": False, "erro": f"Erro ao salvar empresa: {erro}"}), 500

    _invalidar_cache_empresas(id_empresa=id_empresa_alvo, cnpj_normalizado=cnpj_normalizado)

    empresa_salva = _buscar_empresa_completa_por_id(id_empresa_alvo)
    if not empresa_salva:
        return jsonify({"ok": False, "erro": "Empresa salva, mas não foi possível reler o cadastro."}), 500

    return jsonify(
        {
            "ok": True,
            "empresa": empresa_salva,
            "empresas_proprietarias": _listar_empresas_proprietarias_para_cadastro(),
        }
    )











@kanban_bp.route("/api/ocupacao/calendario", methods=["GET"])
@login_required
def api_kanban_ocupacao_calendario():
    cod_face = (request.args.get("cod_face") or request.args.get("codface") or "").strip().upper()
    mes_ref = (request.args.get("mes_ref") or "").strip()
    meses = request.args.get("meses", 24)

    try:
        meses = int(meses)
    except Exception:
        meses = 24

    if meses <= 0:
        meses = 24

    if not cod_face:
        return jsonify({"ok": False, "erro": "cod_face obrigatório"}), 400

    sql = text("""
        DECLARE @CodFace varchar(20) = UPPER(LTRIM(RTRIM(:cod_face)));

        DECLARE @Inicio date =
        CASE
            WHEN :mes_ref IS NOT NULL AND LEN(:mes_ref) = 7
            THEN TRY_CONVERT(date, CONCAT(:mes_ref, '-01'))
            ELSE DATEFROMPARTS(YEAR(GETDATE()), MONTH(GETDATE()), 1)
        END;

        IF @Inicio IS NULL
            SET @Inicio = DATEFROMPARTS(YEAR(GETDATE()), MONTH(GETDATE()), 1);

        DECLARE @Fim date = DATEADD(MONTH, :meses, @Inicio);

        DECLARE @CodPonto int =
        (
            SELECT TOP (1) fo.CodPonto
            FROM [Integracao].[Silver].[FatoOcupacaoPaineisEuromidia] fo
            WHERE UPPER(LTRIM(RTRIM(COALESCE(fo.CodFace, '')))) = @CodFace
              AND fo.CodPonto IS NOT NULL
            ORDER BY fo.DataAtualizacao DESC
        );

        ;WITH
        Painel AS (
            SELECT TOP (1)
                p.CodPonto,
                TipoPainel = UPPER(LTRIM(RTRIM(p.Tipo))),
                QuantidadeFaces = NULLIF(p.QuantidadeFaces, 0),
                BitAtivo = COALESCE(p.BitAtivo, 1)
            FROM [Integracao].[Silver].[DimPaineisEuromidia] p
            WHERE p.CodPonto = @CodPonto
        ),
        Capacidade AS (
            SELECT
                CodPonto = (SELECT CodPonto FROM Painel),
                TipoPainel = COALESCE((SELECT TipoPainel FROM Painel), 'DESCONHECIDO'),
                BitAtivo = COALESCE((SELECT BitAtivo FROM Painel), 0),

                EhDigital =
                    CASE
                        WHEN COALESCE((SELECT TipoPainel FROM Painel), '') LIKE '%DIGITAL%' THEN 1
                        ELSE 0
                    END,

                CapacidadeSlots =
                    CASE
                        WHEN COALESCE((SELECT TipoPainel FROM Painel), '') LIKE '%DIGITAL%'
                        THEN COALESCE((SELECT QuantidadeFaces FROM Painel), 16)
                        ELSE 1
                    END
        ),
        OcupacoesBase AS (
            SELECT
                fo.CodFace,
                DataInicio = CAST(fo.DataInicio AS date),
                DataFim = CAST(fo.DataFim AS date),
                SpanQtd = fo.SpanQtd,
                Cota = fo.Cota,
                NumeroContrato = fo.NumeroContrato,
                NumeroPrevia = fo.NumeroPrevia,
                DataAtualizacao = fo.DataAtualizacao
            FROM [Integracao].[Silver].[FatoOcupacaoPaineisEuromidia] fo
            WHERE UPPER(LTRIM(RTRIM(COALESCE(fo.CodFace, '')))) = @CodFace
              AND fo.DataInicio IS NOT NULL
              AND fo.DataFim IS NOT NULL
              AND fo.CanceladoEm IS NULL
              AND fo.Status IN ('ATIVO', 'RESERVADO')
        ),
        OcupacoesDedup AS (
            SELECT *
            FROM (
                SELECT
                    b.*,
                    rn = ROW_NUMBER() OVER (
                        PARTITION BY
                            b.CodFace,
                            b.DataInicio,
                            b.DataFim,
                            ISNULL(b.NumeroContrato, ''),
                            ISNULL(b.NumeroPrevia, '')
                        ORDER BY b.DataAtualizacao DESC
                    )
                FROM OcupacoesBase b
            ) x
            WHERE x.rn = 1
        ),
        OcupacoesValidas AS (
            SELECT
                d.DataInicio,
                d.DataFim,
                SlotsConsumidos =
                    CASE
                        WHEN (SELECT EhDigital FROM Capacidade) = 1
                        THEN COALESCE(NULLIF(d.SpanQtd, 0), 1)
                        ELSE 1
                    END
            FROM OcupacoesDedup d
        ),
        UsoPorDia AS (
            SELECT
                c.[Data],
                SlotsOcupados =
                    CASE
                        WHEN cap.EhDigital = 1
                        THEN COALESCE(SUM(o.SlotsConsumidos), 0)
                        ELSE CASE WHEN COUNT(o.SlotsConsumidos) > 0 THEN 1 ELSE 0 END
                    END
            FROM [Integracao].[Silver].[DimCalendario] c
            CROSS JOIN Capacidade cap
            LEFT JOIN OcupacoesValidas o
                   ON c.[Data] >= o.DataInicio
                  AND c.[Data] <= o.DataFim
            WHERE c.[Data] >= @Inicio
              AND c.[Data] < @Fim
            GROUP BY c.[Data], cap.EhDigital
        )
        SELECT
            Data = CONVERT(varchar(10), c.[Data], 23),
            CodPonto = cap.CodPonto,
            TipoPainel = cap.TipoPainel,
            EhDigital = cap.EhDigital,
            CapacidadeSlots = cap.CapacidadeSlots,
            SlotsOcupados = u.SlotsOcupados,

            SlotsDisponiveis =
                CASE
                    WHEN cap.CapacidadeSlots - u.SlotsOcupados < 0 THEN 0
                    ELSE cap.CapacidadeSlots - u.SlotsOcupados
                END,

            OcupacaoPct =
                CASE
                    WHEN cap.CapacidadeSlots > 0
                    THEN CAST(u.SlotsOcupados * 100.0 / cap.CapacidadeSlots AS decimal(9,2))
                    ELSE NULL
                END,

            DiaDisponivel =
                CASE
                    WHEN cap.BitAtivo = 0 THEN 0
                    WHEN (cap.CapacidadeSlots - u.SlotsOcupados) > 0 THEN 1
                    ELSE 0
                END,

            StatusDia =
                CASE
                    WHEN cap.BitAtivo = 0 THEN 'INDISPONIVEL'
                    WHEN cap.EhDigital = 0 AND u.SlotsOcupados = 0 THEN 'DISPONIVEL'
                    WHEN cap.EhDigital = 0 AND u.SlotsOcupados = 1 THEN 'OCUPADO'
                    WHEN cap.EhDigital = 1 AND u.SlotsOcupados = 0 THEN 'LIVRE'
                    WHEN cap.EhDigital = 1 AND u.SlotsOcupados < cap.CapacidadeSlots THEN 'PARCIAL'
                    ELSE 'LOTADO'
                END
        FROM [Integracao].[Silver].[DimCalendario] c
        JOIN UsoPorDia u
          ON u.[Data] = c.[Data]
        CROSS JOIN Capacidade cap
        WHERE c.[Data] >= @Inicio
          AND c.[Data] < @Fim
        ORDER BY c.[Data];
    """)

    try:
        rows = db.session.execute(
            sql,
            {
                "cod_face": cod_face,
                "mes_ref": mes_ref,
                "meses": meses,
            },
        ).all()
    except Exception as erro:
        current_app.logger.exception(
            "KANBAN OCUPACAO: erro ao consultar calendário da face %s.",
            cod_face,
        )
        return jsonify(
            {
                "ok": False,
                "erro": f"Não foi possível consultar a ocupação da face {cod_face}: {erro}",
            }
        ), 500

    calendario = {}
    for r in rows:
        chave = (r.Data or "").strip()
        if not chave:
            continue

        calendario[chave] = {
            "status": (r.StatusDia or "").strip(),
            "disp": int(r.SlotsDisponiveis or 0),
            "cap": int(r.CapacidadeSlots or 0),
            "ocup": int(r.SlotsOcupados or 0),
            "pct": float(r.OcupacaoPct) if r.OcupacaoPct is not None else None,
            "dia_disponivel": int(r.DiaDisponivel or 0),
            "eh_digital": int(r.EhDigital or 0),
            "codponto": int(r.CodPonto) if r.CodPonto is not None else None,
            "tipo": (r.TipoPainel or "").strip(),
        }

    return jsonify({"ok": True, "cal": calendario})













    TABELA_KANBAN = "[Kanban].[Silver].[DimKanban]"
TABELA_KANBAN_FASE = "[Kanban].[Silver].[DimKanbanFase]"
TABELA_CARD = "[Kanban].[Silver].[FatoKanbanCard]"
TABELA_CARD_MOVIMENTO = "[Kanban].[Silver].[FatoKanbanCardMovimento]"
TABELA_CARD_NOTA = "[Kanban].[Silver].[FatoKanbanCardNota]"
TABELA_CARD_LOG = "[Kanban].[Silver].[FatoKanbanCardLog]"
TABELA_EMPRESAS = "[Integracao].[Silver].[DimEmpresas]"
TABELA_CNAES = "[Integracao].[Silver].[DimCnaes]"
TABELA_TIPO_CLIENTE_DESCONTO = "[Kanban].[Silver].[DimKanbanTipoClienteDesconto]"
MAPA_TIPO_CLIENTE_DESCONTO_PADRAO = {
    1: "Cliente Direto",
    2: "Agência",
    3: "Planejador",
}


def _sql_select_tipo_cliente_desconto_card(alias_card: str = "c") -> str:
    colunas: list[str] = []

    for nome_coluna in ("BitClienteDireto", "BitAgencia", "BitPlanejador"):
        if _coluna_existe(TABELA_CARD, nome_coluna):
            colunas.append(f"{alias_card}.{nome_coluna} AS {nome_coluna}")
        else:
            colunas.append(f"CAST(0 AS bit) AS {nome_coluna}")

    return ",\n            ".join(colunas)


def _obter_tipos_cliente_desconto(*, incluir_inativos: bool = False) -> list[dict[str, Any]]:
    chave = _chave_cache_json("kanban:dominio:tipo_cliente_desconto", incluir_inativos)
    em_cache = _cache_json_get(chave)
    if em_cache is not None:
        return em_cache

    resultado: list[dict[str, Any]] = []

    if _objeto_existe(TABELA_TIPO_CLIENTE_DESCONTO):
        where_ativo = "" if incluir_inativos else "WHERE ISNULL(BitAtivo, 1) = 1"
        sql = text(f"""
            SELECT
                IDDimKanbanTipoClienteDesconto,
                TipoCliente,
                ISNULL(BitAtivo, 1) AS BitAtivo
            FROM {TABELA_TIPO_CLIENTE_DESCONTO}
            {where_ativo}
            ORDER BY IDDimKanbanTipoClienteDesconto ASC;
        """)
        rows = db.session.execute(sql).mappings().all()

        for row in rows:
            try:
                id_tipo = int(row.get("IDDimKanbanTipoClienteDesconto") or 0)
            except Exception:
                id_tipo = 0

            if not id_tipo:
                continue

            nome_tipo = str(row.get("TipoCliente") or "").strip()
            if not nome_tipo:
                nome_tipo = MAPA_TIPO_CLIENTE_DESCONTO_PADRAO.get(id_tipo, f"Tipo {id_tipo}")

            resultado.append(
                {
                    "IDDimKanbanTipoClienteDesconto": id_tipo,
                    "TipoCliente": nome_tipo,
                    "BitAtivo": int(row.get("BitAtivo") or 0),
                }
            )
    else:
        for id_tipo, nome_tipo in MAPA_TIPO_CLIENTE_DESCONTO_PADRAO.items():
            resultado.append(
                {
                    "IDDimKanbanTipoClienteDesconto": int(id_tipo),
                    "TipoCliente": nome_tipo,
                    "BitAtivo": 1,
                }
            )

    _cache_json_set(chave, resultado, TIMEOUT_CACHE_LONGO)
    return resultado


def _resolver_id_tipo_cliente_desconto_por_bits(card: Mapping[str, Any] | dict[str, Any] | None) -> int | None:
    if not card:
        return None

    def _bit(nome_campo: str) -> int:
        try:
            return int(card.get(nome_campo) or 0)
        except Exception:
            return 0

    if _bit("BitClienteDireto") == 1:
        return 1
    if _bit("BitAgencia") == 1:
        return 2
    if _bit("BitPlanejador") == 1:
        return 3

    return None


def _montar_bits_tipo_cliente_desconto(id_tipo_cliente_desconto: Any) -> dict[str, int]:
    try:
        id_tipo = int(id_tipo_cliente_desconto) if id_tipo_cliente_desconto not in (None, "", 0) else None
    except Exception:
        id_tipo = None

    return {
        "BitClienteDireto": 1 if id_tipo == 1 else 0,
        "BitAgencia": 1 if id_tipo == 2 else 0,
        "BitPlanejador": 1 if id_tipo == 3 else 0,
    }


def _aplicar_tipo_cliente_desconto_no_card_dict(card_dict: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(card_dict, dict):
        return card_dict

    id_tipo = _resolver_id_tipo_cliente_desconto_por_bits(card_dict)
    mapa_tipos = {
        int(item.get("IDDimKanbanTipoClienteDesconto") or 0): str(item.get("TipoCliente") or "").strip()
        for item in _obter_tipos_cliente_desconto(incluir_inativos=True)
        if int(item.get("IDDimKanbanTipoClienteDesconto") or 0) > 0
    }

    card_dict["IDDimKanbanTipoClienteDesconto"] = id_tipo
    card_dict["TipoClienteDesconto"] = mapa_tipos.get(id_tipo, "")
    return card_dict


def _obter_cards_kanban(id_kanban: int) -> list[dict[str, Any]]:
    sql_cards = text(f"""
        SELECT
            c.IDFatoKanbanCard,
            c.IDDimKanban,
            c.IDDimKanbanFaseAtual,
            c.Titulo,
            c.StatusCard,
            c.CriadoEm,
            c.AtualizadoEm,
            c.IDEmpresaProprietaria,
            {_sql_select_tipo_cliente_desconto_card('c')},
            {_sql_select_empresa_relacionada_card('c')},
            {_sql_select_usuario_relacionado_card('c')},
            e.RazaoSocial AS EmpresaRazaoSocial,
            e.CNPJ AS EmpresaCNPJ,
            e.CNAE AS EmpresaCNAE,
            cn.Classe AS EmpresaClasse,
            cn.Setor AS EmpresaSetor
        FROM {TABELA_CARD} c
        {_sql_join_empresa_relacionada_card('c', 'e', 'cn')}
        WHERE c.IDDimKanban = :id_kanban
          AND c.Ativo = 1
          {_sql_filtro_status_card_visiveis('c')}
          {_sql_filtro_cards_nao_concluidos_no_quadro('c')}
        ORDER BY
            CASE WHEN c.AtualizadoEm IS NULL THEN c.CriadoEm ELSE c.AtualizadoEm END DESC,
            c.IDFatoKanbanCard DESC;
    """)
    rows = db.session.execute(sql_cards, {"id_kanban": id_kanban}).mappings().all()
    cards = _rows_para_dicts(rows)
    for card in cards:
        _aplicar_tipo_cliente_desconto_no_card_dict(card)
    return cards




@kanban_bp.route("/api/kanbans/<int:id_kanban>/cards", methods=["POST"])
@login_required
@limiter.limit("120/minute")
def api_card_criar(id_kanban: int):
    etapa = "inicio"
    novo_id = None

    try:
        print("\n" + "=" * 120)
        print(f"[KANBAN][CRIAR_CARD] INICIO id_kanban={id_kanban}")
        print(f"[KANBAN][CRIAR_CARD] method={request.method} path={request.path}")
        print(f"[KANBAN][CRIAR_CARD] content_type={request.content_type}")
        print(f"[KANBAN][CRIAR_CARD] is_json={request.is_json}")

        etapa = "autenticacao"
        id_usuario = _assert_login()
        id_emp = _id_empresa_usuario_or_403()
        _obter_kanban_autorizado(id_kanban)
        print(f"[KANBAN][CRIAR_CARD] autenticacao_ok id_usuario={id_usuario} id_emp={id_emp}")

        etapa = "payload"
        payload = request.get_json(silent=True) or {}

        titulo = (payload.get("titulo") or "").strip()
        descricao = payload.get("descricao")
        id_fase = int(payload.get("id_fase") or 0)
        id_empresa_relacionada = payload.get("id_empresa")
        id_tipo_cliente_desconto = payload.get("id_tipo_cliente_desconto")

        print(
            "[KANBAN][CRIAR_CARD] payload_tratado "
            f"titulo={titulo!r} "
            f"descricao={descricao!r} "
            f"id_fase={id_fase!r} "
            f"id_empresa_relacionada={id_empresa_relacionada!r} "
            f"id_tipo_cliente_desconto={id_tipo_cliente_desconto!r}"
        )

        etapa = "validacao_titulo"
        if len(titulo) < 2:
            print("[KANBAN][CRIAR_CARD] ERRO validacao_titulo: titulo invalido")
            return jsonify({"ok": False, "msg": "Título inválido"}), 400

        etapa = "validacao_fase_obrigatoria"
        if not id_fase:
            print("[KANBAN][CRIAR_CARD] ERRO validacao_fase_obrigatoria: fase ausente")
            return jsonify({"ok": False, "msg": "Fase obrigatória"}), 400

        etapa = "validacao_fase_kanban"
        fase_valida = _validar_fase_do_kanban(id_kanban, id_fase)
        print(f"[KANBAN][CRIAR_CARD] fase_valida={fase_valida}")
        if not fase_valida:
            print("[KANBAN][CRIAR_CARD] ERRO validacao_fase_kanban: fase invalida para o kanban")
            return jsonify({"ok": False, "msg": "Fase inválida para este kanban"}), 400

        etapa = "validacao_empresa_relacionada"
        id_empresa_relacionada_int = None
        if id_empresa_relacionada not in (None, ""):
            try:
                id_empresa_relacionada_int = int(id_empresa_relacionada)
                print(f"[KANBAN][CRIAR_CARD] id_empresa_relacionada_int={id_empresa_relacionada_int}")
            except Exception as exc:
                print(f"[KANBAN][CRIAR_CARD] ERRO empresa invalida ao converter: {exc}")
                return jsonify({"ok": False, "msg": "Empresa inválida"}), 400

            sql_emp = text(f"""
                SELECT 1
                FROM {TABELA_EMPRESAS}
                WHERE IDEmpresa = :id_empresa;
            """)
            empresa_existe = db.session.execute(
                sql_emp,
                {"id_empresa": id_empresa_relacionada_int},
            ).scalar()

            print(f"[KANBAN][CRIAR_CARD] empresa_existe={empresa_existe}")

            if not empresa_existe:
                print("[KANBAN][CRIAR_CARD] ERRO empresa nao encontrada")
                return jsonify({"ok": False, "msg": "Empresa não encontrada"}), 400

        etapa = "validacao_tipo_cliente_desconto"
        id_tipo_cliente_desconto_int = None
        if id_tipo_cliente_desconto not in (None, "", 0):
            try:
                id_tipo_cliente_desconto_int = int(id_tipo_cliente_desconto)
            except Exception:
                return jsonify({"ok": False, "msg": "Tipo de cliente inválido"}), 400

            tipos_validos = {
                int(item.get("IDDimKanbanTipoClienteDesconto") or 0)
                for item in _obter_tipos_cliente_desconto()
            }
            if id_tipo_cliente_desconto_int not in tipos_validos:
                return jsonify({"ok": False, "msg": "Tipo de cliente inválido"}), 400

        mapa_bits_tipo_cliente = _montar_bits_tipo_cliente_desconto(id_tipo_cliente_desconto_int)

        etapa = "descobrir_colunas_dinamicas"
        nome_coluna_empresa = _nome_coluna_empresa_relacionada_card()

        coluna_id_dim_usuarios_existe = _coluna_existe(TABELA_CARD, "IDDimUsuarios")
        coluna_iddimkanbanorigem_existe = _coluna_existe(TABELA_CARD, "IDDimKanbanOrigem")

        print(
            "[KANBAN][CRIAR_CARD] colunas_dinamicas "
            f"nome_coluna_empresa={nome_coluna_empresa!r} "
            f"coluna_id_dim_usuarios_existe={coluna_id_dim_usuarios_existe} "
            f"coluna_iddimkanbanorigem_existe={coluna_iddimkanbanorigem_existe}"
        )

        etapa = "montagem_insert"
        print(f"[KANBAN][CRIAR_CARD] id_usuario_logado={id_usuario}")
        print("[KANBAN][CRIAR_CARD] vou gravar id_usuario em IDVendedorUsuario e, se existir, também em IDDimUsuarios")

        status_card_inicial = _obter_status_card_para_fase(id_fase)
        id_status_card_inicial = _obter_id_status_card_por_codigo(status_card_inicial)

        colunas = [
            "IDDimKanban",
            "IDDimKanbanFaseAtual",
            "Titulo",
            "Descricao",
            "IDVendedorUsuario",
            "StatusCard",
            "CriadoEm",
            "Ativo",
            "IDEmpresaProprietaria",
        ]
        valores = [
            ":id_kanban",
            ":id_fase",
            ":titulo",
            ":descricao",
            ":id_usuario",
            ":status_card_inicial",
            "GETDATE()",
            "1",
            ":id_emp",
        ]
        params = {
            "id_kanban": id_kanban,
            "id_fase": id_fase,
            "titulo": titulo[:200],
            "descricao": descricao,
            "id_usuario": int(id_usuario),
            "id_emp": id_emp,
            "status_card_inicial": status_card_inicial,
        }

        if nome_coluna_empresa:
            colunas.append(nome_coluna_empresa)
            valores.append(":id_empresa_relacionada")
            params["id_empresa_relacionada"] = id_empresa_relacionada_int

        if _coluna_existe(TABELA_CARD, "BitClienteDireto"):
            colunas.append("BitClienteDireto")
            valores.append(":bit_cliente_direto")
            params["bit_cliente_direto"] = int(mapa_bits_tipo_cliente["BitClienteDireto"])

        if _coluna_existe(TABELA_CARD, "BitAgencia"):
            colunas.append("BitAgencia")
            valores.append(":bit_agencia")
            params["bit_agencia"] = int(mapa_bits_tipo_cliente["BitAgencia"])

        if _coluna_existe(TABELA_CARD, "BitPlanejador"):
            colunas.append("BitPlanejador")
            valores.append(":bit_planejador")
            params["bit_planejador"] = int(mapa_bits_tipo_cliente["BitPlanejador"])

        if coluna_id_dim_usuarios_existe:
            colunas.append("IDDimUsuarios")
            valores.append(":id_usuario")

        if _coluna_existe(TABELA_CARD, "IDDimKanbanTipoClienteDesconto"):
            colunas.append("IDDimKanbanTipoClienteDesconto")
            valores.append(":id_tipo_cliente_desconto")
            params["id_tipo_cliente_desconto"] = id_tipo_cliente_desconto_int

        if _coluna_existe(TABELA_CARD, "IDDimKanbanStatusCard") and id_status_card_inicial is not None:
            colunas.append("IDDimKanbanStatusCard")
            valores.append(":id_status_card_inicial")
            params["id_status_card_inicial"] = int(id_status_card_inicial)

        if coluna_iddimkanbanorigem_existe:
            colunas.append("IDDimKanbanOrigem")
            valores.append("NULL")

        sql_insert = f"""
            INSERT INTO {TABELA_CARD}
                ({', '.join(colunas)})
            OUTPUT INSERTED.IDFatoKanbanCard
            VALUES
                ({', '.join(valores)});
        """

        print(f"[KANBAN][CRIAR_CARD] tabela_card={TABELA_CARD}")
        print(f"[KANBAN][CRIAR_CARD] colunas_insert={colunas}")
        print(f"[KANBAN][CRIAR_CARD] valores_insert={valores}")
        print(f"[KANBAN][CRIAR_CARD] params_insert={params}")
        print(f"[KANBAN][CRIAR_CARD] sql_insert=\n{sql_insert}")

        etapa = "executar_insert"
        novo_id = db.session.execute(text(sql_insert), params).scalar()
        print(f"[KANBAN][CRIAR_CARD] novo_id={novo_id}")

        if not novo_id:
            raise RuntimeError("O INSERT não retornou IDFatoKanbanCard.")

        etapa = "aplicar_tag_em_atendimento"
        _garantir_tag_em_atendimento_no_card(
            id_card=int(novo_id),
            id_kanban=int(id_kanban),
            id_usuario=int(id_usuario),
            id_empresa_proprietaria=id_emp,
            falhar_se_nao_existir=True,
        )

        etapa = "snapshot_depois"
        snapshot_depois = _obter_snapshot_card_log(int(novo_id), incluir_inativo=True)
        print(f"[KANBAN][CRIAR_CARD] snapshot_depois={snapshot_depois}")

        etapa = "registrar_log_criacao"
        _registrar_log_card(
            id_card=int(novo_id),
            id_kanban=int(id_kanban),
            id_empresa_proprietaria=int(id_emp),
            id_usuario_acao=int(id_usuario),
            tipo_evento="CARD_CRIADO",
            subtipo_evento="CRIACAO",
            id_fase_de=None,
            id_fase_para=int(id_fase) if id_fase else None,
            observacao="Card criado via quadro Kanban",
            payload_antes=None,
            payload_depois=snapshot_depois,
        )

        etapa = "registrar_historico_status"
        _registrar_status_historico_card(
            id_card=int(novo_id),
            id_fase=int(id_fase),
            id_status_card=id_status_card_inicial,
            id_usuario=int(id_usuario),
            id_empresa_proprietaria=id_emp,
        )

        etapa = "db_commit"
        db.session.commit()
        print(f"[KANBAN][CRIAR_CARD] COMMIT OK novo_id={novo_id}")

        etapa = "invalidar_cache"
        _invalidar_kanban(id_emp=id_emp, id_kanban=id_kanban, id_card=int(novo_id))
        print(f"[KANBAN][CRIAR_CARD] cache invalidado id_emp={id_emp} id_kanban={id_kanban} id_card={novo_id}")

        etapa = "detalhe"
        detalhe = _obter_card_detalhe_payload(int(novo_id))
        print(f"[KANBAN][CRIAR_CARD] detalhe_card carregado ok id_card={novo_id}")

        etapa = "emitir_evento_socket"
        _emitir_evento_kanban(
            id_kanban,
            "card_criado",
            {
                "card": detalhe.get("card"),
                "tags": detalhe.get("tags", []),
                "notas": detalhe.get("notas", []),
                "painel_faces": detalhe.get("painel_faces", detalhe.get("paineis_vinculados", [])),
            },
        )
        print(f"[KANBAN][CRIAR_CARD] evento socket emitido id_kanban={id_kanban}")

        print(f"[KANBAN][CRIAR_CARD] FIM OK novo_id={novo_id}")
        print("=" * 120 + "\n")

        return jsonify(
            {
                "ok": True,
                "msg": "Card criado com sucesso.",
                "id": int(novo_id),
                "card": detalhe.get("card"),
                "tags": detalhe.get("tags", []),
                "notas": detalhe.get("notas", []),
                "painel_faces": detalhe.get("painel_faces", detalhe.get("paineis_vinculados", [])),
            }
        ), 201

    except ValueError as exc:
        db.session.rollback()
        print(f"[KANBAN][CRIAR_CARD] ROLLBACK por ValueError etapa={etapa} erro={exc}")
        current_app.logger.exception("Erro de validação ao criar card. etapa=%s", etapa)
        return jsonify({"ok": False, "msg": str(exc)}), 400

    except Exception as exc:
        db.session.rollback()
        print(f"[KANBAN][CRIAR_CARD] ROLLBACK por Exception etapa={etapa} erro={exc}")
        current_app.logger.exception("Erro ao criar card no kanban %s. etapa=%s", id_kanban, etapa)
        return jsonify({"ok": False, "msg": f"Erro ao criar card: {str(exc)}"}), 500






@kanban_bp.route("/api/cards/<int:id_card>", methods=["PUT"])
@login_required
@limiter.limit("120/minute")
def api_card_atualizar(id_card: int):
    id_usuario = _assert_login()
    id_emp = _id_empresa_usuario_or_403()

    try:
        card_atual = _obter_card_autorizado(id_card)
        if not card_atual:
            return jsonify({"ok": False, "msg": "Card não encontrado."}), 404

        id_kanban = int(card_atual.get("IDDimKanban") or 0)
        id_fase_atual = int(card_atual.get("IDDimKanbanFaseAtual") or 0)

        payload = request.get_json(silent=True) or {}

        titulo = (payload.get("titulo") or "").strip()
        descricao = payload.get("descricao")
        id_empresa_relacionada = payload.get("id_empresa")
        id_tipo_cliente_desconto = payload.get("id_tipo_cliente_desconto") if "id_tipo_cliente_desconto" in payload else None
        tipo_cliente_desconto_informado = "id_tipo_cliente_desconto" in payload
        versao_concorrencia = payload.get("versao_concorrencia")
        painel_faces_payload = payload.get("painel_faces")

        if not titulo:
            return jsonify({"ok": False, "msg": "Título do card é obrigatório."}), 400

        has_versao = _card_tem_versao_concorrencia()
        versao_concorrencia_bytes = None

        if has_versao:
            versao_concorrencia_bytes = _rowversion_hex_para_bytes(versao_concorrencia)
            if not versao_concorrencia_bytes:
                detalhe_atual = _obter_card_detalhe_payload(id_card)
                return (
                    jsonify(
                        {
                            "ok": False,
                            "msg": "Versão de concorrência inválida ou ausente.",
                            "card_atual": detalhe_atual.get("card"),
                        }
                    ),
                    409,
                )

        id_tipo_cliente_desconto_int = None
        mapa_bits_tipo_cliente = None
        if tipo_cliente_desconto_informado:
            if id_tipo_cliente_desconto not in (None, "", 0):
                try:
                    id_tipo_cliente_desconto_int = int(id_tipo_cliente_desconto)
                except Exception:
                    return jsonify({"ok": False, "msg": "Tipo de cliente inválido."}), 400

                tipos_validos = {
                    int(item.get("IDDimKanbanTipoClienteDesconto") or 0)
                    for item in _obter_tipos_cliente_desconto()
                }
                if id_tipo_cliente_desconto_int not in tipos_validos:
                    return jsonify({"ok": False, "msg": "Tipo de cliente inválido."}), 400

            mapa_bits_tipo_cliente = _montar_bits_tipo_cliente_desconto(id_tipo_cliente_desconto_int)

        snapshot_antes = _obter_snapshot_card_log(id_card, incluir_inativo=True)

        campos_update: list[str] = []
        params_update: dict[str, Any] = {
            "id_card": int(id_card),
            "titulo": titulo[:300],
            "descricao": descricao,
        }

        if _coluna_existe(TABELA_CARD, "Titulo"):
            campos_update.append("Titulo = :titulo")

        if _coluna_existe(TABELA_CARD, "Descricao"):
            campos_update.append("Descricao = :descricao")

        nome_coluna_empresa = _nome_coluna_empresa_relacionada_card()
        if nome_coluna_empresa:
            campos_update.append(f"{nome_coluna_empresa} = :id_empresa_relacionada")
            params_update["id_empresa_relacionada"] = (
                int(id_empresa_relacionada) if id_empresa_relacionada not in (None, "", 0) else None
            )

        if _coluna_existe(TABELA_CARD, "AtualizadoEm"):
            campos_update.append("AtualizadoEm = GETDATE()")

        if tipo_cliente_desconto_informado and mapa_bits_tipo_cliente is not None:
            if _coluna_existe(TABELA_CARD, "BitClienteDireto"):
                campos_update.append("BitClienteDireto = :bit_cliente_direto")
                params_update["bit_cliente_direto"] = int(mapa_bits_tipo_cliente["BitClienteDireto"])

            if _coluna_existe(TABELA_CARD, "BitAgencia"):
                campos_update.append("BitAgencia = :bit_agencia")
                params_update["bit_agencia"] = int(mapa_bits_tipo_cliente["BitAgencia"])

            if _coluna_existe(TABELA_CARD, "BitPlanejador"):
                campos_update.append("BitPlanejador = :bit_planejador")
                params_update["bit_planejador"] = int(mapa_bits_tipo_cliente["BitPlanejador"])

        output_versao = ", INSERTED.VersaoConcorrencia" if has_versao else ""
        where_versao = " AND VersaoConcorrencia = :versao_concorrencia" if has_versao else ""

        if has_versao:
            params_update["versao_concorrencia"] = versao_concorrencia_bytes

        if not campos_update:
            return jsonify({"ok": False, "msg": "Nenhum campo disponível para atualização no card."}), 400

        sql_upd = text(f"""
            UPDATE {TABELA_CARD}
            SET {', '.join(campos_update)}
            OUTPUT
                INSERTED.IDFatoKanbanCard,
                INSERTED.IDDimKanban,
                INSERTED.IDDimKanbanFaseAtual,
                INSERTED.Titulo,
                INSERTED.Descricao,
                INSERTED.StatusCard,
                INSERTED.IDEmpresaProprietaria
            WHERE IDFatoKanbanCard = :id_card
              AND Ativo = 1{where_versao};
        """)

        row_upd = db.session.execute(sql_upd, params_update).mappings().first()

        if not row_upd:
            detalhe_atual = _obter_card_detalhe_payload(id_card)
            return (
                jsonify(
                    {
                        "ok": False,
                        "msg": "Este card foi alterado por outro usuário. Reabra o card antes de salvar novamente.",
                        "card_atual": detalhe_atual.get("card"),
                    }
                ),
                409,
            )

        vinculos_preparados: list[dict[str, Any]] = []
        reservas_criadas = 0

        if isinstance(painel_faces_payload, list):

            def _assinatura_estado_operacional(estados: list[dict[str, Any]]) -> tuple:
                def _n_int(valor: Any) -> int | None:
                    if valor in (None, ""):
                        return None
                    try:
                        return int(valor)
                    except Exception:
                        return None

                def _n_dec(valor: Any) -> str | None:
                    dec = _valor_decimal(valor)
                    return None if dec is None else format(dec.quantize(Decimal("0.0001")), "f")

                def _n_data(valor: Any) -> str | None:
                    data = _normalizar_data_reserva_kanban(valor)
                    return None if data is None else data.isoformat()

                itens_norm = []
                for item in estados or []:
                    itens_norm.append(
                        (
                            _n_int(item.get("IDDimPaineisEuromidia")),
                            _n_int(item.get("IDDimFacesPaineis")),
                            _n_int(item.get("IDDimTabelaPrecosEuromidia")),
                            _n_dec(item.get("CustoTabela")),
                            _n_int(item.get("AnoCusto")),
                            _n_dec(item.get("ValorTabela")),
                            str(item.get("PeriodoExibicao") or "").strip(),
                            _n_int(item.get("ExibicoesDia")),
                            _n_dec(item.get("ValorVendaFinal")),
                            _n_dec(item.get("MargemValor")),
                            _n_dec(item.get("MargemPercentual")),
                            _n_data(item.get("PeriodoInicio")),
                            _n_data(item.get("PeriodoTermino")),
                        )
                    )
                return tuple(sorted(itens_norm))

            estado_antes_operacional = _listar_estado_atual_negociacao_card(id_card)
            assinatura_antes_operacional = _assinatura_estado_operacional(estado_antes_operacional)

            vinculos_preparados = _preparar_vinculos_painel_faces(painel_faces_payload, id_emp)
            _salvar_vinculos_painel_face_card(
                id_card=id_card,
                vinculos_preparados=vinculos_preparados,
                id_empresa_proprietaria=id_emp,
            )

            try:
                reservas_criadas = _criar_reservas_painel_faces_kanban(
                    id_card=int(id_card),
                    titulo_card=str(row_upd.get("Titulo") or titulo or card_atual.get("Titulo") or "").strip(),
                    id_empresa_relacionada=(
                        params_update.get("id_empresa_relacionada")
                        if "id_empresa_relacionada" in params_update
                        else _obter_id_empresa_relacionada_card(card_atual)
                    ),
                    painel_faces_payload=painel_faces_payload,
                    vinculos_preparados=vinculos_preparados,
                    id_usuario=int(id_usuario),
                    id_empresa_proprietaria=int(id_emp),
                )
            except ValueError:
                raise
            except Exception as exc:
                current_app.logger.exception(
                    "Erro ao criar reservas do card %s após salvar vínculos", id_card
                )
                raise RuntimeError(f"Falha ao criar reservas dos painéis/faces: {str(exc)}") from exc

            estado_depois_operacional = _listar_estado_atual_negociacao_card(id_card)
            assinatura_depois_operacional = _assinatura_estado_operacional(estado_depois_operacional)

            if assinatura_depois_operacional != assinatura_antes_operacional:
                _registrar_negociacao_preco_card(
                    id_card=id_card,
                    id_kanban=id_kanban,
                    id_fase_atual=id_fase_atual,
                    status_card=row_upd.get("StatusCard"),
                    id_empresa_relacionada=(
                        params_update.get("id_empresa_relacionada")
                        if "id_empresa_relacionada" in params_update
                        else _obter_id_empresa_relacionada_card(card_atual)
                    ),
                    vinculos_preparados=vinculos_preparados,
                    observacoes_proposta=descricao,
                )

        snapshot_depois = _obter_snapshot_card_log(id_card, incluir_inativo=True)
        _registrar_log_card(
            id_card=int(id_card),
            id_kanban=int(id_kanban),
            id_empresa_proprietaria=int(id_emp),
            id_usuario_acao=int(id_usuario),
            tipo_evento="CARD_ATUALIZADO",
            subtipo_evento="EDICAO",
            id_fase_de=int(id_fase_atual) if id_fase_atual else None,
            id_fase_para=int(row_upd.get("IDDimKanbanFaseAtual") or id_fase_atual or 0) or None,
            observacao="Card atualizado via quadro Kanban",
            payload_antes=snapshot_antes,
            payload_depois=snapshot_depois,
        )

        db.session.commit()

        _invalidar_kanban(id_emp=id_emp, id_kanban=id_kanban, id_card=id_card)

        detalhe = _obter_card_detalhe_payload(id_card)
        _emitir_evento_kanban(
            id_kanban,
            "card_atualizado",
            {
                "card": detalhe.get("card"),
                "tags": detalhe.get("tags", []),
                "notas": detalhe.get("notas", []),
                "painel_faces": detalhe.get("painel_faces", detalhe.get("paineis_vinculados", [])),
            },
        )

        return jsonify(
            {
                "ok": True,
                "msg": (
                    f"Card atualizado com sucesso. {int(reservas_criadas)} reserva(s) criada(s)."
                    if int(reservas_criadas or 0) > 0
                    else "Card atualizado com sucesso."
                ),
                "reservas_criadas": int(reservas_criadas or 0),
                "card": detalhe.get("card"),
                "tags": detalhe.get("tags", []),
                "notas": detalhe.get("notas", []),
                "painel_faces": detalhe.get("painel_faces", detalhe.get("paineis_vinculados", [])),
            }
        )

    except ValueError as exc:
        db.session.rollback()
        return jsonify({"ok": False, "msg": str(exc)}), 400

    except Exception as exc:
        db.session.rollback()
        current_app.logger.exception("Erro ao atualizar card id_card=%s", id_card)
        return jsonify({"ok": False, "msg": f"Erro ao atualizar card: {str(exc)}"}), 500