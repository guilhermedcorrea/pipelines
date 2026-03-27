import json
import re
import unicodedata
from collections.abc import Mapping
from typing import Any
from decimal import Decimal, InvalidOperation

from flask import Blueprint, abort, current_app, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import text
from flask_socketio import disconnect, emit, join_room, leave_room
from ..extensions import cache, db, limiter, socketio



"""Kanban Euromidia Comercial"""


kanban_bp = Blueprint("kanban", __name__)






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








def _salvar_vinculos_painel_face_card(
    id_card: int,
    vinculos_preparados: list[dict] | None = None,
    id_usuario: int | None = None,
    id_empresa_proprietaria: int | None = None,
    itens_painel_face: list[dict] | None = None,
) -> None:
    """
    Salva o histórico de vínculos de painel/face do card sem apagar registros antigos.

    Regra:
    - não faz DELETE físico
    - mantém histórico
    - encerra logicamente vínculos ativos que não existem mais no estado atual
    - insere novos vínculos quando o estado atual ainda não existir como linha ativa
    - evita duplicar linha ativa idêntica
    - não insere linha vazia
    - gera Ordem automaticamente quando não vier preenchida

    Compatibilidade:
    - aceita tanto 'vinculos_preparados' quanto 'itens_painel_face'
    - se os dois vierem preenchidos, prioriza 'vinculos_preparados'
    """

    if isinstance(vinculos_preparados, list):
        itens_entrada = vinculos_preparados
    elif isinstance(itens_painel_face, list):
        itens_entrada = itens_painel_face
    else:
        itens_entrada = []

    def obter_primeiro(item: dict, chaves: tuple[str, ...]) -> object:
        """
        Retorna o primeiro valor encontrado entre várias chaves possíveis.
        """
        for chave in chaves:
            if chave in item:
                valor = item.get(chave)
                if valor is not None and valor != "":
                    return valor
        return None

    def normalizar_texto(valor: object) -> str | None:
        if valor is None:
            return None

        texto = str(valor).strip()
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

    def normalizar_decimal(valor: object) -> Decimal | None:
        if valor is None or valor == "":
            return None

        if isinstance(valor, Decimal):
            return valor

        if isinstance(valor, (int, float)):
            try:
                return Decimal(str(valor))
            except (InvalidOperation, TypeError, ValueError):
                return None

        texto = str(valor).strip()
        if not texto:
            return None

        if "," in texto:
            texto = texto.replace(".", "").replace(",", ".")
        else:
            texto = texto

        try:
            return Decimal(texto)
        except (InvalidOperation, TypeError, ValueError):
            return None

    def linha_tem_conteudo_minimo(linha: dict) -> bool:
        """
        Evita inserir linha completamente vazia.
        Considero conteúdo mínimo qualquer identificação operacional/comercial do vínculo.
        """
        campos_relevantes = (
            "IDDimPaineisEuromidia",
            "IDDimFacesPaineis",
            "CodPonto",
            "CodFace",
            "TipoPainel",
            "IDDimTabelaPrecosEuromidia",
            "PeriodoExibicao",
            "Tabela",
            "ValorVendaFinal",
            "NovoValor",
            "ValorTabela",
            "CustoTabela",
        )

        return any(linha.get(campo) is not None for campo in campos_relevantes)

    def normalizar_linha(item: dict, ordem_padrao: int) -> dict:
        """
        Converte o payload para o formato canônico.

        Também aceita chaves alternativas porque o front/preparação
        pode estar mandando nomes diferentes.
        """
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
            "AnoCusto": normalizar_inteiro(
                obter_primeiro(item, ("AnoCusto", "ano_custo"))
            ),
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
                    ),
                )
            ),
            "PeriodoExibicao": normalizar_texto(
                obter_primeiro(item, ("PeriodoExibicao", "periodo_exibicao", "Periodo", "periodo"))
            ),
            "ExibicoesDia": normalizar_inteiro(
                obter_primeiro(item, ("ExibicoesDia", "exibicoes_dia"))
            ),
            "ValorTabela": normalizar_decimal(
                obter_primeiro(item, ("ValorTabela", "valor_tabela"))
            ),
            "Tabela": normalizar_texto(
                obter_primeiro(item, ("Tabela", "tabela", "NomeTabela", "nome_tabela"))
            ),
            "PoliticaTrocas": normalizar_texto(
                obter_primeiro(item, ("PoliticaTrocas", "politica_trocas"))
            ),
            "ValorTroca": normalizar_decimal(
                obter_primeiro(item, ("ValorTroca", "valor_troca"))
            ),
            "NovoValor": normalizar_decimal(
                obter_primeiro(item, ("NovoValor", "novo_valor", "ValorNegociado", "valor_negociado"))
            ),
            "PercentualDesconto": normalizar_decimal(
                obter_primeiro(item, ("PercentualDesconto", "percentual_desconto", "DescontoPercentual"))
            ),
            "ValorVendaFinal": normalizar_decimal(
                obter_primeiro(item, ("ValorVendaFinal", "valor_venda_final", "ValorVenda", "valor_venda"))
            ),
            "MargemValor": normalizar_decimal(
                obter_primeiro(item, ("MargemValor", "margem_valor"))
            ),
            "MargemPercentual": normalizar_decimal(
                obter_primeiro(item, ("MargemPercentual", "margem_percentual"))
            ),
        }

        """
        Ordem é obrigatória no banco.
        Se não vier do front, eu gero pela posição do item.
        """
        if linha["Ordem"] is None:
            linha["Ordem"] = ordem_padrao

        return linha

    def assinatura_comparacao(linha: dict) -> tuple:
        return (
            linha.get("Ordem"),
            linha.get("IDDimPaineisEuromidia"),
            linha.get("IDDimFacesPaineis"),
            linha.get("CodPonto"),
            linha.get("CodFace"),
            linha.get("TipoPainel"),
            linha.get("AnoCusto"),
            linha.get("CustoTabela"),
            linha.get("IDDimTabelaPrecosEuromidia"),
            linha.get("PeriodoExibicao"),
            linha.get("ExibicoesDia"),
            linha.get("ValorTabela"),
            linha.get("Tabela"),
            linha.get("PoliticaTrocas"),
            linha.get("ValorTroca"),
            linha.get("NovoValor"),
            linha.get("PercentualDesconto"),
            linha.get("ValorVendaFinal"),
            linha.get("MargemValor"),
            linha.get("MargemPercentual"),
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
            Ativo
        FROM [Kanban].[Silver].[FatoKanbanCardPainelFace]
        WHERE IDFatoKanbanCard = :id_card
          AND Ativo = 1
        """
    )

    resultado_ativos = db.session.execute(sql_buscar_ativos, {"id_card": id_card})
    linhas_ativas = [dict(linha._mapping) for linha in resultado_ativos]

    linhas_ativas_normalizadas: list[dict] = []
    for linha in linhas_ativas:
        linhas_ativas_normalizadas.append(
            {
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
            }
        )

    itens_normalizados: list[dict] = []
    for indice, item in enumerate(itens_entrada, start=1):
        if not isinstance(item, dict):
            continue

        linha = normalizar_linha(item, ordem_padrao=indice)

        """
        Se a linha veio completamente vazia, não tento inserir.
        Isso evita exatamente o cenário do erro atual:
        vários campos None sendo empurrados para o banco.
        """
        if not linha_tem_conteudo_minimo(linha):
            continue

        itens_normalizados.append(linha)

    mapa_ativos_por_assinatura: dict[tuple, list[dict]] = {}
    for linha in linhas_ativas_normalizadas:
        chave = assinatura_comparacao(linha)
        mapa_ativos_por_assinatura.setdefault(chave, []).append(linha)

    ids_ativos_que_permanecem: set[int] = set()
    itens_para_inserir: list[dict] = []

    for item_novo in itens_normalizados:
        chave_item_novo = assinatura_comparacao(item_novo)
        ativos_iguais = mapa_ativos_por_assinatura.get(chave_item_novo, [])

        if ativos_iguais:
            linha_existente = ativos_iguais.pop(0)
            id_linha_existente = linha_existente.get("IDFatoKanbanCardPainelFace")

            if id_linha_existente is not None:
                ids_ativos_que_permanecem.add(int(id_linha_existente))
        else:
            itens_para_inserir.append(item_novo)

    ids_ativos_para_encerrar: list[int] = []
    for linha_ativa in linhas_ativas_normalizadas:
        id_linha = linha_ativa.get("IDFatoKanbanCardPainelFace")
        if id_linha is None:
            continue

        if int(id_linha) not in ids_ativos_que_permanecem:
            ids_ativos_para_encerrar.append(int(id_linha))

    if ids_ativos_para_encerrar:
        sql_encerrar = text(
            """
            UPDATE [Kanban].[Silver].[FatoKanbanCardPainelFace]
               SET Ativo = 0,
                   DataAtualizacao = GETDATE(),
                   RemovidoEm = GETDATE(),
                   RemovidoPor = :id_usuario
             WHERE IDFatoKanbanCardPainelFace = :id_fato_kanban_card_painel_face
               AND Ativo = 1
            """
        )

        for id_linha in ids_ativos_para_encerrar:
            db.session.execute(
                sql_encerrar,
                {
                    "id_usuario": id_usuario,
                    "id_fato_kanban_card_painel_face": id_linha,
                },
            )

    if itens_para_inserir:
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
                IDEmpresaProprietaria
            )
            VALUES
            (
                :id_card,
                :ordem,
                :id_dim_paineis_euromidia,
                :id_dim_faces_paineis,
                :cod_ponto,
                :cod_face,
                :tipo_painel,
                :ano_custo,
                :custo_tabela,
                :id_dim_tabela_precos_euromidia,
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
                :id_empresa_proprietaria
            )
            """
        )

        for item in itens_para_inserir:
            db.session.execute(
                sql_inserir,
                {
                    "id_card": id_card,
                    "ordem": item.get("Ordem"),
                    "id_dim_paineis_euromidia": item.get("IDDimPaineisEuromidia"),
                    "id_dim_faces_paineis": item.get("IDDimFacesPaineis"),
                    "cod_ponto": item.get("CodPonto"),
                    "cod_face": item.get("CodFace"),
                    "tipo_painel": item.get("TipoPainel"),
                    "ano_custo": item.get("AnoCusto"),
                    "custo_tabela": item.get("CustoTabela"),
                    "id_dim_tabela_precos_euromidia": item.get("IDDimTabelaPrecosEuromidia"),
                    "periodo_exibicao": item.get("PeriodoExibicao"),
                    "exibicoes_dia": item.get("ExibicoesDia"),
                    "valor_tabela": item.get("ValorTabela"),
                    "tabela": item.get("Tabela"),
                    "politica_trocas": item.get("PoliticaTrocas"),
                    "valor_troca": item.get("ValorTroca"),
                    "novo_valor": item.get("NovoValor"),
                    "percentual_desconto": item.get("PercentualDesconto"),
                    "valor_venda_final": item.get("ValorVendaFinal"),
                    "margem_valor": item.get("MargemValor"),
                    "margem_percentual": item.get("MargemPercentual"),
                    "id_usuario": id_usuario,
                    "id_empresa_proprietaria": id_empresa_proprietaria,
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
        ORDER BY
            CASE WHEN c.AtualizadoEm IS NULL THEN c.CriadoEm ELSE c.AtualizadoEm END DESC,
            c.IDFatoKanbanCard DESC;
    """)
    cards = db.session.execute(sql_cards, {"id_kanban": id_kanban}).mappings().all()
    return _rows_para_dicts(cards)


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
                id_dim_face=int(face_item.get("IDDimFacesPaineis") or 0) if face_item.get("IDDimFacesPaineis") is not None else None,
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
                "id_dim_face": int(face_item.get("IDDimFacesPaineis") or 0) if face_item.get("IDDimFacesPaineis") is not None else None,
                "cod_ponto": int(painel_item.get("CodPonto") or 0) if painel_item.get("CodPonto") is not None else None,
                "cod_face": cod_face_item,
                "tipo_painel": _normalizar_texto(painel_item.get("Tipo")) or None,
                "ano_custo": int(custo_item.get("Ano") or 0) if custo_item and custo_item.get("Ano") is not None else None,
                "custo_tabela": metricas.get("Custo"),
                "id_preco": int(preco_item.get("IDDimTabelaPrecosEuromidia") or 0) if preco_item else None,
                "periodo_exibicao": preco_item.get("PeriodoExibicao") if preco_item else None,
                "exibicoes_dia": int(preco_item.get("ExibicoesDia") or 0) if preco_item and preco_item.get("ExibicoesDia") is not None else None,
                "valor_tabela": metricas.get("ValorTabela"),
                "tabela": preco_item.get("Tabela") if preco_item else None,
                "politica_trocas": preco_item.get("PoliticaTrocas") if preco_item else None,
                "valor_troca": _decimal_para_float(preco_item.get("ValorTroca")) if preco_item else None,
                "novo_valor": metricas.get("NovoValor"),
                "percentual_desconto": metricas.get("PercentualDesconto"),
                "valor_venda_final": metricas.get("ValorVendaFinal"),
                "margem_valor": metricas.get("MargemValor"),
                "margem_percentual": metricas.get("MargemPercentual"),
                "id_usuario": _id_usuario(),
                "id_empresa": int(id_empresa_proprietaria),
            }
        )

    return vinculos_preparados





def _registrar_negociacao_preco_card(
    *,
    id_card: int,
    id_kanban: int,
    id_fase_atual: int | None,
    status_card: str | None,
    id_empresa_relacionada: int | None,
    vinculos_preparados: list[dict[str, Any]],
) -> None:
    """
    Registra histórico de negociação de preço do card.

    Regras:
    - a tabela [Kanban].[Silver].[FatoKanbanNegociacaoPreco] é histórica
    - não apaga registros anteriores
    - cada negociação relevante gera uma nova linha
    - só grava quando existir operação comercial real
    - ObservacoesProposta recebe apenas o texto digitado pelo usuário no campo Notas
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
        if valor is None or valor == "":
            return None
        try:
            return int(valor)
        except (TypeError, ValueError):
            return None

    def _para_decimal_ou_none(valor: Any):
        if valor is None or valor == "":
            return None
        return valor

    id_status_card = _obter_id_status_card_por_codigo(status_card)
    id_usuario_atual = _id_usuario()

    id_empresa_proprietaria_negociacao = _resolver_id_empresa_proprietaria_movimento(
        id_kanban=id_kanban,
        id_empresa_padrao=_id_empresa_usuario_or_403(),
    )

    sql_insert = text("""
        INSERT INTO [Kanban].[Silver].[FatoKanbanNegociacaoPreco]
        (
            IDDimUsuarios,
            IDEmpresaProprietaria,
            IDDimTabelaPrecosEuromidia,
            IDEmpresa,
            IDFatoKanbanCard,
            IDDimKanbanFase,
            IDDimKanbanStatusCard,
            IDFatoControleContratosEuromidia,
            BitAditivoContrato,
            ObservacoesProposta,
            IDDimPaineisEuromidia,
            IDDimFacesPaineis,
            DataPrecoProposto,
            CustoRateado,
            PrecoProposto,
            DescontoProposto,
            PeriodoInicio,
            PeriodoTermino,
            IDDimUsuariosAprovacaoPreco,
            DataAprovacaoPreco,
            PrecoAprovado,
            DescontoAprovado,
            ObservacoesAprovacao
        )
        VALUES
        (
            :id_usuario,
            :id_empresa_proprietaria,
            :id_tabela_preco,
            :id_empresa_relacionada,
            :id_card,
            :id_fase_atual,
            :id_status_card,
            NULL,
            0,
            :observacoes_proposta,
            :id_painel,
            :id_face,
            GETDATE(),
            :custo_rateado,
            :preco_proposto,
            :desconto_proposto,
            :periodo_inicio,
            :periodo_termino,
            NULL,
            NULL,
            NULL,
            NULL,
            NULL
        );
    """)

    for vinculo in vinculos_preparados:
        id_painel = _para_int_ou_none(vinculo.get("id_painel"))
        id_face = _para_int_ou_none(vinculo.get("id_dim_face"))
        id_tabela_preco = _para_int_ou_none(vinculo.get("id_preco"))

        novo_valor = vinculo.get("novo_valor")
        percentual_desconto = vinculo.get("percentual_desconto")
        valor_venda_final = vinculo.get("valor_venda_final")
        custo_rateado = vinculo.get("custo_tabela")

        tem_operacao_comercial = any(
            _tem_valor_informado(valor)
            for valor in (
                id_tabela_preco,
                novo_valor,
                percentual_desconto,
                valor_venda_final,
            )
        )

        if not id_painel or not id_face or not tem_operacao_comercial:
            continue

        preco_proposto = (
            novo_valor
            if _tem_valor_informado(novo_valor)
            else valor_venda_final
        )

        db.session.execute(
            sql_insert,
            {
                "id_usuario": id_usuario_atual,
                "id_empresa_proprietaria": id_empresa_proprietaria_negociacao,
                "id_tabela_preco": id_tabela_preco,
                "id_empresa_relacionada": _para_int_ou_none(id_empresa_relacionada),
                "id_card": int(id_card),
                "id_fase_atual": _para_int_ou_none(id_fase_atual),
                "id_status_card": _para_int_ou_none(id_status_card),
                "observacoes_proposta": None,
                "id_painel": id_painel,
                "id_face": id_face,
                "custo_rateado": _para_decimal_ou_none(custo_rateado),
                "preco_proposto": _para_decimal_ou_none(preco_proposto),
                "desconto_proposto": _para_decimal_ou_none(percentual_desconto),
                "periodo_inicio": None,
                "periodo_termino": None,
            },
        )



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
            p.Logradouro,
            p.Cidade,
            p.UF,
            p.Bairro,
            p.Numero,
            p.QuantidadeFaces
        FROM [Kanban].[Silver].[FatoKanbanCardPainelFace] r
        LEFT JOIN [Integracao].[Silver].[DimPaineisEuromidia] p
          ON TRY_CONVERT(int, p.IDDimPaineisEuromidia) = TRY_CONVERT(int, r.IDDimPaineisEuromidia)
        WHERE r.IDFatoKanbanCard = :id_card
          AND r.Ativo = 1
        ORDER BY r.Ordem ASC, r.IDFatoKanbanCardPainelFace ASC;
    """)
    rows = db.session.execute(sql, {"id_card": int(id_card)}).mappings().all()
    return _rows_para_dicts(rows)


def _obter_card_detalhe_payload(id_card: int) -> dict[str, Any]:
    print(f"[KANBAN][_obter_card_detalhe_payload] INICIO id_card={id_card}")
    current_app.logger.info("KANBAN: _obter_card_detalhe_payload iniciado. id_card=%s", id_card)

    card_escopo = _obter_card_autorizado(id_card)
    print(f"[KANBAN][_obter_card_detalhe_payload] card_escopo={card_escopo!r}")

    id_kanban = int(card_escopo.get("IDDimKanban") or 0)
    print(f"[KANBAN][_obter_card_detalhe_payload] id_kanban={id_kanban}")

    """
    Leio a versão diretamente da coluna real.
    Aqui eu não dependo de _card_tem_versao_concorrencia(),
    porque a tabela FatoKanbanCard já foi validada e possui essa coluna,
    e o card 36 já mostrou valor real no banco.
    """

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
            e.RazaoSocial AS EmpresaRazaoSocial,
            e.CNPJ AS EmpresaCNPJ,
            e.CNAE AS EmpresaCNAE,
            cn.Classe AS EmpresaClasse,
            cn.Setor AS EmpresaSetor
        FROM {TABELA_CARD} c
        {_sql_join_empresa_relacionada_card('c', 'e', 'cn')}
        WHERE c.IDFatoKanbanCard = :id_card
          AND c.Ativo = 1;
    """)

    card = db.session.execute(sql, {"id_card": int(id_card)}).mappings().first()

    if not card:
        abort(404, "Card não encontrado")

    card_dict = dict(card)

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

    """
    Primeiro tento converter o valor bruto retornado pelo driver.
    Se o pyodbc / SQLAlchemy devolver bytes, bytearray ou memoryview,
    _rowversion_para_hex deve resolver.
    """
    versao_hex = _rowversion_para_hex(valor_versao_bruta)

    """
    Se a conversão do bruto falhar, uso o valor já convertido pelo SQL Server.
    Isso evita perder a versão por incompatibilidade de tipo no driver.
    """
    if not versao_hex:
        versao_hex = _normalizar_hex_sql(valor_versao_hex_sql)

    """
    Só tento gerar/forçar uma versão se realmente não consegui ler nenhuma.
    Em cenário normal isso nem deveria acontecer, porque a linha já existe
    e já possui VersaoConcorrencia no banco.
    """
    if not versao_hex:
        print(
            f"[KANBAN][_obter_card_detalhe_payload] versão não lida no SELECT. "
            f"Tentando garantir versão para id_card={id_card}"
        )
        current_app.logger.warning(
            "KANBAN: versão do card não veio do SELECT. Tentando garantir versão. id_card=%s",
            id_card,
        )
        versao_hex = _garantir_versao_concorrencia_card(
            id_card=id_card,
            id_kanban=id_kanban,
        )

    print(f"[KANBAN][_obter_card_detalhe_payload] versao_hex_final={versao_hex!r}")
    current_app.logger.info(
        "KANBAN: detalhe card id=%s | versao_hex_final=%r",
        id_card,
        versao_hex,
    )

    """
    O front procura a versão em mais de um nome.
    Então eu preencho todos os aliases para não depender de um nome só.
    """
    card_dict["VersaoConcorrenciaHex"] = versao_hex
    card_dict["VersaoConcorrencia"] = versao_hex
    card_dict["versaoConcorrencia"] = versao_hex
    card_dict["versao_concorrencia"] = versao_hex

    sql_tags = text("""
        SELECT
            t.IDDimKanbanTag,
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

    select_empresa_nota = (
        "IDEmpresa,"
        if _coluna_existe(TABELA_CARD_NOTA, "IDEmpresa")
        else "CAST(NULL AS int) AS IDEmpresa,"
    )

    sql_notas = text(f"""
        SELECT
            IDFatoKanbanCardNota,
            TipoNota,
            Texto,
            CriadoEm,
            CriadoPor,
            {select_empresa_nota}
            IDEmpresaProprietaria
        FROM {TABELA_CARD_NOTA}
        WHERE IDFatoKanbanCard = :id_card
        ORDER BY CriadoEm DESC;
    """)
    notas = db.session.execute(sql_notas, {"id_card": int(id_card)}).mappings().all()

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

    chave = _chave_cache_json(
        "kanban:api:dados",
        id_emp,
        id_kanban,
        limite_inicial_por_fase,
        _versao_empresa(id_emp),
        _versao_kanban(id_kanban),
    )
    em_cache = _cache_json_get(chave)
    if em_cache is not None:
        return jsonify(em_cache)

    fases_base = _obter_fases_kanban(id_kanban)
    tags_catalogo = _obter_tags_kanban(id_kanban)
    paineis_catalogo = _obter_paineis_catalogo() if kanban_cfg["MostrarPainelFaceNoCard"] else []

    sql_totais = text(f"""
        SELECT
            c.IDDimKanbanFaseAtual AS IDDimKanbanFase,
            COUNT(1) AS QuantidadeCardsTotal
        FROM {TABELA_CARD} c
        WHERE c.IDDimKanban = :id_kanban
          AND c.Ativo = 1
          {_sql_filtro_status_card_visiveis('c')}
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
                e.RazaoSocial AS EmpresaRazaoSocial,
                e.CNPJ AS EmpresaCNPJ,
                e.CNAE AS EmpresaCNAE,
                cn.Classe AS EmpresaClasse,
                cn.Setor AS EmpresaSetor,
                ROW_NUMBER() OVER (
                    PARTITION BY c.IDDimKanbanFaseAtual
                    ORDER BY
                        CASE WHEN c.AtualizadoEm IS NULL THEN c.CriadoEm ELSE c.AtualizadoEm END DESC,
                        c.IDFatoKanbanCard DESC
                ) AS RowNumFase
            FROM {TABELA_CARD} c
            {_sql_join_empresa_relacionada_card('c', 'e', 'cn')}
            WHERE c.IDDimKanban = :id_kanban
              AND c.Ativo = 1
              {_sql_filtro_status_card_visiveis('c')}
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
            EmpresaRazaoSocial,
            EmpresaCNPJ,
            EmpresaCNAE,
            EmpresaClasse,
            EmpresaSetor,
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
    ids_cards_iniciais: list[int] = []
    mapa_carregados_por_fase: dict[int, int] = {}

    for row in rows_cards_iniciais:
        card_dict = dict(row)
        card_dict["VersaoConcorrenciaHex"] = _rowversion_para_hex(card_dict.pop("VersaoConcorrencia", None))
        card_dict.pop("RowNumFase", None)

        id_card = int(card_dict.get("IDFatoKanbanCard") or 0)
        id_fase = int(card_dict.get("IDDimKanbanFaseAtual") or 0)

        if id_card:
            ids_cards_iniciais.append(id_card)

        mapa_carregados_por_fase[id_fase] = mapa_carregados_por_fase.get(id_fase, 0) + 1
        cards_iniciais.append(card_dict)

    card_tags_iniciais: list[dict[str, Any]] = []

    if ids_cards_iniciais:
        params_card_tags = {f"id_card_{i}": int(v) for i, v in enumerate(ids_cards_iniciais)}
        placeholders = ", ".join(f":id_card_{i}" for i in range(len(ids_cards_iniciais)))

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
        "card_tags": card_tags_iniciais,
        "paineis": paineis_catalogo,
        "limit_inicial_por_fase": limite_inicial_por_fase,
        "carga_parcial": True,
    }

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

    chave = _chave_cache_json(
        "kanban:api:cards:fase",
        id_emp,
        id_kanban,
        id_fase,
        offset,
        limit,
        _versao_kanban(id_kanban),
    )
    em_cache = _cache_json_get(chave)
    if em_cache is not None:
        return jsonify(em_cache)

    sql_total = text(f"""
        SELECT COUNT(1)
        FROM {TABELA_CARD} c
        WHERE c.IDDimKanban = :id_kanban
          AND c.IDDimKanbanFaseAtual = :id_fase
          AND c.Ativo = 1
          {_sql_filtro_status_card_visiveis('c')};
    """)
    total = int(db.session.execute(sql_total, {"id_kanban": id_kanban, "id_fase": id_fase}).scalar() or 0)

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
            e.RazaoSocial AS EmpresaRazaoSocial,
            e.CNPJ AS EmpresaCNPJ,
            e.CNAE AS EmpresaCNAE,
            cn.Classe AS EmpresaClasse,
            cn.Setor AS EmpresaSetor
        FROM {TABELA_CARD} c
        {_sql_join_empresa_relacionada_card('c', 'e', 'cn')}
        WHERE c.IDDimKanban = :id_kanban
          AND c.IDDimKanbanFaseAtual = :id_fase
          AND c.Ativo = 1
          {_sql_filtro_status_card_visiveis('c')}
        ORDER BY
            CASE WHEN c.AtualizadoEm IS NULL THEN c.CriadoEm ELSE c.AtualizadoEm END DESC,
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
        card["VersaoConcorrenciaHex"] = _rowversion_para_hex(card.pop("VersaoConcorrencia", None))
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
    _cache_json_set(chave, payload, TIMEOUT_CACHE_CURTO)
    return jsonify(payload)





@kanban_bp.route("/api/cards/<int:id_card>", methods=["GET"])
@login_required
@limiter.limit("180/minute")
def api_card_detalhe(id_card: int):
    _assert_login()

    id_emp = _id_empresa_usuario_or_403()
    card_escopo = _obter_card_autorizado(id_card)
    id_kanban = int(card_escopo.get("IDDimKanban") or 0)

    chave = _chave_cache_json(
        "kanban:api:card:detalhe",
        id_emp,
        id_kanban,
        id_card,
        _versao_kanban(id_kanban),
        _versao_card(id_card),
    )

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
        print(f"[KANBAN][CRIAR_CARD] payload_bruto={payload}")

        titulo = (payload.get("titulo") or "").strip()
        descricao = payload.get("descricao")
        id_fase = int(payload.get("id_fase") or 0)
        id_empresa_relacionada = payload.get("id_empresa")

        print(
            "[KANBAN][CRIAR_CARD] payload_tratado "
            f"titulo={titulo!r} "
            f"descricao={descricao!r} "
            f"id_fase={id_fase!r} "
            f"id_empresa_relacionada={id_empresa_relacionada!r}"
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

        if coluna_id_dim_usuarios_existe:
            colunas.append("IDDimUsuarios")
            valores.append(":id_dim_usuarios")
            params["id_dim_usuarios"] = int(id_usuario)

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

        etapa = "snapshot_depois"
        snapshot_depois = _obter_snapshot_card_log(int(novo_id), incluir_inativo=True)
        print(f"[KANBAN][CRIAR_CARD] snapshot_depois={snapshot_depois}")

        etapa = "registrar_log_card"
        _registrar_log_card(
            id_card=int(novo_id),
            id_kanban=id_kanban,
            id_empresa_proprietaria=id_emp,
            id_usuario_acao=id_usuario,
            tipo_evento="CARD_CRIADO",
            id_fase_para=id_fase,
            payload_depois=snapshot_depois,
            observacao="Card criado no kanban.",
            tabela_origem=TABELA_CARD,
            id_registro_origem=int(novo_id),
        )
        print("[KANBAN][CRIAR_CARD] log_card_ok")

        etapa = "commit"
        db.session.commit()
        print("[KANBAN][CRIAR_CARD] commit_ok")

    except Exception as exc:
        db.session.rollback()
        print(
            f"[KANBAN][CRIAR_CARD] ERRO antes_pos_processamento "
            f"etapa={etapa} novo_id={novo_id} erro={repr(exc)}"
        )
        current_app.logger.exception(
            "Erro ao criar card no kanban. etapa=%s id_kanban=%s novo_id=%s",
            etapa,
            id_kanban,
            novo_id,
        )
        return jsonify(
            {
                "ok": False,
                "msg": f"Erro ao criar card na etapa '{etapa}': {str(exc)}",
                "etapa": etapa,
                "novo_id": int(novo_id) if novo_id else None,
            }
        ), 500

    try:
        etapa = "invalidar_cache"
        _invalidar_kanban(id_emp=id_emp, id_kanban=id_kanban, id_card=int(novo_id))
        print("[KANBAN][CRIAR_CARD] invalidar_cache_ok")

        etapa = "obter_detalhe"
        detalhe = _obter_card_detalhe_payload(int(novo_id))
        print(
            f"[KANBAN][CRIAR_CARD] detalhe_keys="
            f"{list(detalhe.keys()) if isinstance(detalhe, dict) else type(detalhe)}"
        )
        print(
            f"[KANBAN][CRIAR_CARD] detalhe_card="
            f"{detalhe.get('card') if isinstance(detalhe, dict) else None}"
        )

        etapa = "emitir_socket"
        payload_socket = {
            "card": detalhe["card"],
            "id_fase": id_fase,
            "tags": detalhe["tags"],
            "notas": detalhe["notas"],
            "paineis_vinculados": detalhe.get("paineis_vinculados", []),
        }
        print(f"[KANBAN][CRIAR_CARD] payload_socket={payload_socket}")

        _emitir_evento_kanban(
            id_kanban,
            "card_criado",
            payload_socket,
        )
        print("[KANBAN][CRIAR_CARD] emitir_socket_ok")

        print(f"[KANBAN][CRIAR_CARD] SUCESSO id_card={novo_id}")
        print("=" * 120 + "\n")

        return jsonify(
            {
                "ok": True,
                "IDFatoKanbanCard": int(novo_id),
                "card": detalhe["card"],
            }
        )

    except Exception as exc:
        print(
            f"[KANBAN][CRIAR_CARD] ERRO pos_commit "
            f"etapa={etapa} novo_id={novo_id} erro={repr(exc)}"
        )
        current_app.logger.exception(
            "Card criado, mas houve falha no pos-processamento. etapa=%s id_kanban=%s novo_id=%s",
            etapa,
            id_kanban,
            novo_id,
        )

        return jsonify(
            {
                "ok": True,
                "IDFatoKanbanCard": int(novo_id),
                "msg": f"Card criado com sucesso, mas houve falha após gravar na etapa '{etapa}': {str(exc)}",
                "etapa_pos_commit": etapa,
            }
        ), 200






@kanban_bp.route("/api/cards/<int:id_card>", methods=["PUT"])
@login_required
@limiter.limit("180/minute")
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
                INSERTED.IDEmpresaProprietaria{output_versao}
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

        if isinstance(painel_faces_payload, list):
            vinculos_preparados = _preparar_vinculos_painel_faces(
                painel_faces_payload=painel_faces_payload,
                id_empresa_proprietaria=id_emp,
            )

            """
            Esta função já deve existir no seu fluxo comercial atual.
            Se o nome estiver um pouco diferente no seu arquivo, ajuste só o nome.
            A ideia é manter a tabela operacional do card.
            """
            _salvar_vinculos_painel_face_card(
                id_card=id_card,
                vinculos_preparados=vinculos_preparados,
                id_empresa_proprietaria=id_emp,
            )

            """
            Além da tabela operacional, eu agora também gravo o histórico
            comercial na FatoKanbanNegociacaoPreco.
            """
            _registrar_negociacao_preco_card(
                id_card=id_card,
                id_kanban=id_kanban,
                id_fase_atual=id_fase_atual,
                status_card=row_upd.get("StatusCard") or card_atual.get("StatusCard"),
                id_empresa_relacionada=(
                    int(id_empresa_relacionada) if id_empresa_relacionada not in (None, "", 0) else None
                ),
                vinculos_preparados=vinculos_preparados,
            )

        snapshot_depois = _obter_snapshot_card_log(id_card, incluir_inativo=True)
        _registrar_log_card(
            id_card=id_card,
            id_kanban=id_kanban,
            id_empresa_proprietaria=id_emp,
            id_usuario_acao=id_usuario,
            tipo_evento="CARD_ATUALIZADO",
            observacao="Card atualizado pelo modal.",
            tabela_origem=TABELA_CARD,
            id_registro_origem=int(id_card),
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
                "id_card": id_card,
                "card": detalhe.get("card"),
                "tags": detalhe.get("tags", []),
                "notas": detalhe.get("notas", []),
                "painel_faces": detalhe.get("painel_faces", detalhe.get("paineis_vinculados", [])),
            },
        )

        return jsonify(
            {
                "ok": True,
                "msg": "Card atualizado com sucesso.",
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





@kanban_bp.route("/api/cards/<int:id_card>/mover", methods=["POST"])
@login_required
@limiter.limit("180/minute")
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
        versao_concorrencia = payload.get("versao_concorrencia")

        if not id_fase_para:
            return jsonify({"ok": False, "msg": "Fase de destino inválida."}), 400

        _obter_kanban_autorizado(id_kanban)

        fase_destino = db.session.execute(
            text(f"""
                SELECT TOP (1)
                    f.IDDimKanbanFase
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

        status_destino = str(row.get("StatusCard") or _obter_status_card_padrao()).strip().upper()
        id_status_destino = _obter_id_status_card_por_codigo(status_destino)

        campos_status_pos_mov: list[str] = []
        params_status_pos_mov: dict[str, Any] = {
            "id_card": id_card,
            "status_destino": status_destino,
        }

        if _coluna_existe(TABELA_CARD, "StatusCard"):
            campos_status_pos_mov.append("StatusCard = :status_destino")

        if _coluna_existe(TABELA_CARD, "IDDimKanbanStatusCard") and id_status_destino is not None:
            campos_status_pos_mov.append("IDDimKanbanStatusCard = :id_status_destino")
            params_status_pos_mov["id_status_destino"] = int(id_status_destino)

        if _coluna_existe(TABELA_CARD, "EncerradoEm"):
            if _status_card_eh_final(status_destino):
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

        id_empresa_movimento = _resolver_id_empresa_proprietaria_movimento(
            id_kanban=id_kanban,
            id_empresa_padrao=row.get("IDEmpresaProprietaria"),
        )

        """
        É AQUI que entra o sql_ins que você perguntou.
        """
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
                IDDimKanbanStatusCard
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
                :id_status_card
            );
        """)

        row_movimento = db.session.execute(
            sql_ins,
            {
                "id_card": id_card,
                "id_fase_de": id_fase_de,
                "id_fase_para": id_fase_para,
                "movido_por": id_usuario,
                "obs": observacao[:2000] if observacao else None,
                "id_empresa": id_empresa_movimento,
                "id_tag": None,
                "id_status_card": int(id_status_destino) if id_status_destino is not None else None,
            },
        ).mappings().first()

        snapshot_depois = _obter_snapshot_card_log(id_card, incluir_inativo=True)
        _registrar_log_card(
            id_card=id_card,
            id_kanban=id_kanban,
            id_empresa_proprietaria=id_emp,
            id_usuario_acao=id_usuario,
            tipo_evento="CARD_MOVIDO",
            id_fase_de=id_fase_de,
            id_fase_para=id_fase_para,
            observacao=observacao or "Card movido entre fases.",
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
                "card": detalhe["card"],
                "tags": detalhe["tags"],
                "notas": detalhe["notas"],
            },
        )

        return jsonify(
            {
                "ok": True,
                "id_card": id_card,
                "id_fase_de": id_fase_de,
                "id_fase_para": id_fase_para,
                "ordem_na_fase": proxima_ordem if has_ordem else None,
                "card": detalhe["card"],
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
    payload = request.get_json(silent=True) or {}

    id_tag = int(payload.get("id_tag") or 0)
    if not id_tag:
        return jsonify({"ok": False, "msg": "Tag obrigatória"}), 400

    sql_tag = text("""
        SELECT t.IDDimKanbanTag
        FROM [Kanban].[Silver].[DimKanbanTag] t
        WHERE t.IDDimKanbanTag = :id_tag
          AND t.IDDimKanban = :id_kanban
          AND t.Ativo = 1;
    """)
    tag_ok = db.session.execute(
        sql_tag,
        {"id_tag": id_tag, "id_kanban": id_kanban},
    ).scalar()
    if not tag_ok:
        return jsonify({"ok": False, "msg": "Tag inválida para este card"}), 400

    sql_dup = text("""
        SELECT 1
        FROM [Kanban].[Silver].[FatoKanbanCardTag]
        WHERE IDFatoKanbanCard = :id_card
          AND IDDimKanbanTag = :id_tag
          AND RemovidoEm IS NULL;
    """)
    existe = db.session.execute(sql_dup, {"id_card": id_card, "id_tag": id_tag}).scalar()
    if existe:
        return jsonify({"ok": True})

    sql = text("""
        INSERT INTO [Kanban].[Silver].[FatoKanbanCardTag]
            (IDFatoKanbanCard, IDDimKanbanTag, AplicadoEm, AplicadoPor, IDEmpresaProprietaria)
        VALUES
            (:id_card, :id_tag, GETDATE(), :id_usuario, :id_empresa);
    """)
    db.session.execute(
        sql,
        {
            "id_card": id_card,
            "id_tag": id_tag,
            "id_usuario": id_usuario,
            "id_empresa": card.get("IDEmpresaProprietaria"),
        },
    )
    db.session.commit()

    _invalidar_kanban(id_emp=id_emp, id_kanban=id_kanban, id_card=id_card)
    _emitir_evento_kanban(
        id_kanban,
        "card_tag_adicionada",
        {"id_card": id_card, "id_tag": id_tag},
    )

    return jsonify({"ok": True})


@kanban_bp.route("/api/cards/<int:id_card>/tags/<int:id_tag>", methods=["DELETE"])
@login_required
@limiter.limit("180/minute")
def api_card_tag_remover(id_card: int, id_tag: int):
    id_usuario = _assert_login()
    card = _obter_card_autorizado(id_card)
    id_emp = _id_empresa_usuario_or_403()
    id_kanban = int(card.get("IDDimKanban") or 0)

    sql = text("""
        UPDATE [Kanban].[Silver].[FatoKanbanCardTag]
        SET RemovidoEm = GETDATE(),
            RemovidoPor = :id_usuario
        WHERE IDFatoKanbanCard = :id_card
          AND IDDimKanbanTag = :id_tag
          AND RemovidoEm IS NULL;
    """)
    db.session.execute(sql, {"id_card": id_card, "id_tag": id_tag, "id_usuario": id_usuario})
    db.session.commit()

    _invalidar_kanban(id_emp=id_emp, id_kanban=id_kanban, id_card=id_card)
    _emitir_evento_kanban(
        id_kanban,
        "card_tag_removida",
        {"id_card": id_card, "id_tag": id_tag},
    )

    return jsonify({"ok": True})


@kanban_bp.route("/api/cards/<int:id_card>/notas", methods=["POST"])
@login_required
@limiter.limit("180/minute")
def api_card_nota_criar(id_card: int):
    id_usuario = _assert_login()
    card = _obter_card_autorizado(id_card)
    id_emp = _id_empresa_usuario_or_403()
    id_kanban = int(card.get("IDDimKanban") or 0)
    id_empresa_relacionada = _obter_id_empresa_relacionada_card(card)

    payload = request.get_json(silent=True) or {}
    texto = (payload.get("texto") or "").strip()
    tipo = (payload.get("tipo") or "OBS").strip().upper()

    if len(texto) < 2:
        return jsonify({"ok": False, "msg": "Texto da nota inválido"}), 400

    colunas = ["IDFatoKanbanCard", "TipoNota", "Texto", "CriadoEm", "CriadoPor", "IDEmpresaProprietaria"]
    valores = [":id_card", ":tipo", ":texto", "GETDATE()", ":criado_por", ":id_empresa"]
    params = {
        "id_card": id_card,
        "tipo": tipo[:50],
        "texto": texto,
        "criado_por": id_usuario,
        "id_empresa": card.get("IDEmpresaProprietaria"),
    }

    if _coluna_existe(TABELA_CARD_NOTA, "IDEmpresa"):
        colunas.append("IDEmpresa")
        valores.append(":id_empresa_relacionada")
        params["id_empresa_relacionada"] = id_empresa_relacionada

    sql = text(
        f"""
        INSERT INTO {TABELA_CARD_NOTA}
            ({', '.join(colunas)})
        OUTPUT INSERTED.IDFatoKanbanCardNota, INSERTED.CriadoEm
        VALUES
            ({', '.join(valores)});
        """
    )
    row_nota = db.session.execute(sql, params).mappings().first()

    snapshot_depois = _obter_snapshot_card_log(id_card, incluir_inativo=True)
    _registrar_log_card(
        id_card=id_card,
        id_kanban=id_kanban,
        id_empresa_proprietaria=id_emp,
        id_usuario_acao=id_usuario,
        tipo_evento="CARD_NOTA_CRIADA",
        subtipo_evento=tipo[:50],
        observacao=texto,
        tabela_origem=TABELA_CARD_NOTA,
        id_registro_origem=int(row_nota.get("IDFatoKanbanCardNota") or 0) if row_nota else None,
        payload_depois=snapshot_depois,
    )
    db.session.commit()

    _invalidar_kanban(id_emp=id_emp, id_kanban=id_kanban, id_card=id_card)
    nota_payload = {
        "IDFatoKanbanCardNota": int(row_nota.get("IDFatoKanbanCardNota") or 0) if row_nota else None,
        "TipoNota": tipo[:50],
        "Texto": texto,
        "CriadoPor": id_usuario,
        "CriadoEm": row_nota.get("CriadoEm") if row_nota else None,
        "IDEmpresa": id_empresa_relacionada,
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

    motivo_normalizado = _normalizar_motivo_inativacao_card(motivo_informado)
    print(f"[KANBAN][api_card_inativar] motivo_normalizado={motivo_normalizado!r}")

    if not motivo_normalizado:
        print("[KANBAN][api_card_inativar] motivo inválido -> retorno 400")
        return jsonify({"ok": False, "msg": "Motivo inválido"}), 400

    if _normalizar_codigo_dominio(motivo_normalizado.get("Codigo")) == "OUTRO_MOTIVO" and len(descricao) < 2:
        print("[KANBAN][api_card_inativar] motivo OUTRO_MOTIVO sem descrição suficiente -> retorno 400")
        return jsonify({"ok": False, "msg": "Descreva o motivo"}), 400

    print("[KANBAN][api_card_inativar] capturando snapshot_antes")
    snapshot_antes = _obter_snapshot_card_log(id_card, incluir_inativo=True)

    sql_card = text(f"""
        SELECT
            IDDimKanban,
            IDDimKanbanFaseAtual,
            IDEmpresaProprietaria,
            {_sql_select_empresa_relacionada_card('c')}
        FROM {TABELA_CARD} c
        WHERE IDFatoKanbanCard = :id_card
          AND Ativo = 1;
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

    id_fase_atual = int(row["IDDimKanbanFaseAtual"])
    id_empresa_card = row.get("IDEmpresaProprietaria")
    motivo_texto = str(motivo_normalizado.get("Descricao") or "").strip()

    observacao_inativacao = f"[INATIVADO] Motivo: {motivo_texto}" + (f" | {descricao}" if descricao else "")
    print(
        "[KANBAN][api_card_inativar] dados básicos -> "
        f"id_fase_atual={id_fase_atual} id_empresa_card={id_empresa_card} "
        f"motivo_texto={motivo_texto!r} observacao_inativacao={observacao_inativacao!r}"
    )

    status_inativacao = _obter_status_card_inativacao()
    id_status_inativacao = _obter_id_status_card_por_codigo(status_inativacao)

    print(
        f"[KANBAN][api_card_inativar] status_inativacao={status_inativacao!r} "
        f"id_status_inativacao={id_status_inativacao!r}"
    )

    try:
        campos_update = [
            "Ativo = 0",
            "InativadoEm = GETDATE()",
            "InativadoPor = :id_usuario",
        ]
        params_update = {
            "id_usuario": id_usuario,
            "id_card": id_card,
        }

        if _coluna_existe(TABELA_CARD, "StatusCard"):
            campos_update.append("StatusCard = :status_inativacao")
            params_update["status_inativacao"] = status_inativacao

        if _coluna_existe(TABELA_CARD, "IDDimKanbanStatusCard") and id_status_inativacao is not None:
            campos_update.append("IDDimKanbanStatusCard = :id_status_inativacao")
            params_update["id_status_inativacao"] = int(id_status_inativacao)

        if _coluna_existe(TABELA_CARD, "MotivoEncerramentoObs"):
            campos_update.append("MotivoEncerramentoObs = :motivo_obs")
            params_update["motivo_obs"] = observacao_inativacao[:2000]

        if _coluna_existe(TABELA_CARD, "EncerradoEm"):
            campos_update.append("EncerradoEm = ISNULL(EncerradoEm, GETDATE())")

        if _coluna_existe(TABELA_CARD, "AtualizadoEm"):
            campos_update.append("AtualizadoEm = GETDATE()")

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

        sql_ins_mov = text(f"""
            INSERT INTO {TABELA_CARD_MOVIMENTO}
                (IDFatoKanbanCard, IDFaseDe, IDFasePara, MovidoEm, MovidoPor, Observacao, IDEmpresaProprietaria)
            OUTPUT INSERTED.IDFatoKanbanCardMovimento
            VALUES
                (:id_card, :id_fase_de, NULL, GETDATE(), :movido_por, :obs, :id_empresa);
        """)

        params_mov = {
            "id_card": id_card,
            "id_fase_de": id_fase_atual,
            "movido_por": id_usuario,
            "obs": observacao_inativacao[:2000],
            "id_empresa": id_empresa_card,
        }
        print(f"[KANBAN][api_card_inativar] params_mov={params_mov!r}")

        print("[KANBAN][api_card_inativar] inserindo movimento")
        row_mov = db.session.execute(sql_ins_mov, params_mov).mappings().first()
        print(f"[KANBAN][api_card_inativar] row_mov={row_mov!r}")

        if _objeto_existe(TABELA_CARD_NOTA):
            print("[KANBAN][api_card_inativar] tabela de nota existe -> preparando insert")

            colunas_nota = [
                "IDFatoKanbanCard",
                "TipoNota",
                "Texto",
                "CriadoEm",
                "CriadoPor",
                "IDEmpresaProprietaria",
            ]
            valores_nota = [
                ":id_card",
                ":tipo_nota",
                ":texto_nota",
                "GETDATE()",
                ":criado_por",
                ":id_empresa",
            ]
            params_nota = {
                "id_card": id_card,
                "tipo_nota": "INATIVACAO",
                "texto_nota": observacao_inativacao[:4000],
                "criado_por": id_usuario,
                "id_empresa": id_empresa_card,
            }

            if _coluna_existe(TABELA_CARD_NOTA, "IDEmpresa"):
                id_empresa_relacionada = _obter_id_empresa_relacionada_card(row)
                print(
                    f"[KANBAN][api_card_inativar] coluna IDEmpresa existe em nota -> id_empresa_relacionada={id_empresa_relacionada!r}"
                )
                colunas_nota.append("IDEmpresa")
                valores_nota.append(":id_empresa_relacionada")
                params_nota["id_empresa_relacionada"] = id_empresa_relacionada

            print(f"[KANBAN][api_card_inativar] colunas_nota={colunas_nota!r}")
            print(f"[KANBAN][api_card_inativar] valores_nota={valores_nota!r}")
            print(f"[KANBAN][api_card_inativar] params_nota={params_nota!r}")

            sql_nota = text(f"""
                INSERT INTO {TABELA_CARD_NOTA}
                    ({', '.join(colunas_nota)})
                VALUES
                    ({', '.join(valores_nota)});
            """)

            print("[KANBAN][api_card_inativar] inserindo nota de inativação")
            db.session.execute(sql_nota, params_nota)
            print("[KANBAN][api_card_inativar] nota inserida com sucesso")
        else:
            print("[KANBAN][api_card_inativar] tabela de nota NÃO existe -> pulando insert de nota")

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
            motivo=motivo_texto,
            observacao=observacao_inativacao,
            tabela_origem=TABELA_CARD_MOVIMENTO,
            id_registro_origem=int(row_mov.get("IDFatoKanbanCardMovimento") or 0) if row_mov else None,
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
                "motivo": motivo_texto,
                "motivo_codigo": motivo_normalizado["Codigo"],
                "descricao": descricao or None,
            },
        )

        print(
            f"[KANBAN][api_card_inativar] SUCESSO id_card={id_card} motivo={motivo_texto!r} codigo={motivo_normalizado['Codigo']!r}"
        )
        current_app.logger.info(
            "KANBAN: card inativado com sucesso. id_card=%s id_usuario=%s motivo=%s codigo=%s",
            id_card,
            id_usuario,
            motivo_texto,
            motivo_normalizado["Codigo"],
        )

        return jsonify(
            {
                "ok": True,
                "motivo": motivo_texto,
                "motivo_codigo": motivo_normalizado["Codigo"],
            }
        )
    except Exception as exc:
        print(f"[KANBAN][api_card_inativar] ERRO -> {exc}")
        db.session.rollback()
        current_app.logger.exception("Erro ao inativar card id_card=%s", id_card)
        return jsonify({"ok": False, "msg": f"Erro ao inativar card: {str(exc)}"}), 500





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

