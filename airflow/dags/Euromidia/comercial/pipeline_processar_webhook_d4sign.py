import json
import logging
import os
import unicodedata
from datetime import timedelta
from typing import Any

import pendulum
from sqlalchemy import text

try:
    from airflow.decorators import dag, task
    from airflow.operators.python import get_current_context
except ImportError:
    from airflow.sdk import dag, task, get_current_context

from SqlServer import HookSqlServer


TABELA_WEBHOOK_EVENTO_D4 = "[Integracao].[Silver].[FatoContratoD4WebhookEvento]"
TABELA_CONTRATO_D4 = "[Integracao].[Silver].[FatoContratoD4]"
TABELA_SIGNATARIO_D4 = "[Integracao].[Silver].[FatoContratoD4Signatario]"
TABELA_HISTORICO_D4 = "[Integracao].[Silver].[DimHistoricoContratosD4]"
TABELA_CONTROLE_CONTRATOS = "[Integracao].[Silver].[FatoControleContratosEuromidia]"
TABELA_CONTROLE_CONTRATOS_ITENS = "[Integracao].[Silver].[FatoControleContratosItensEuromidia]"
TABELA_STATUS_CONTRATOS = "[Integracao].[Silver].[DimStatusContratos]"
TABELA_SOLICITACAO_CONTRATO = "[Integracao].[Silver].[FatoSolicitacaoContratoEuromidia]"

ID_EMPRESA_PROPRIETARIA_EUROMIDIA = 3

ID_STATUS_D4_AGUARDANDO_ASSINATURAS = 3
ID_STATUS_D4_FINALIZADO = 4
ID_STATUS_D4_CANCELADO = 6

ID_STATUS_CONTRATO_DOCUMENTO_GERADO = 3
ID_STATUS_CONTRATO_PENDENTE_ENVIO = 4
ID_STATUS_CONTRATO_ENVIADO_ASSINATURA = 5
ID_STATUS_CONTRATO_EM_ASSINATURA = 6
ID_STATUS_CONTRATO_ATIVO = 7
ID_STATUS_CONTRATO_CANCELADO = 9

def obter_int_env(nome_variavel: str, padrao: int) -> int:
    """Leio inteiro do ambiente sem quebrar o parse da DAG se vier vazio ou inválido."""
    try:
        return int(str(os.getenv(nome_variavel, str(padrao)) or str(padrao)).strip())
    except Exception:
        return int(padrao)


ID_STATUS_ASSINATURA_D4_PENDENTE = obter_int_env("D4SIGN_ID_STATUS_ASSINATURA_PENDENTE", 1)
ID_STATUS_ASSINATURA_D4_VISUALIZOU = obter_int_env("D4SIGN_ID_STATUS_ASSINATURA_VISUALIZOU", 2)
ID_STATUS_ASSINATURA_D4_ASSINADO = obter_int_env("D4SIGN_ID_STATUS_ASSINATURA_ASSINADO", 3)
ID_STATUS_ASSINATURA_D4_EMAIL_ERRO = obter_int_env("D4SIGN_ID_STATUS_ASSINATURA_EMAIL_ERRO", 4)


TAGS_DAG = ["Euromidia", "Contratos", "D4Sign", "Webhook", "SQLServer"]

DOCUMENTACAO_DAG = """
# Processador de webhook D4Sign

Esta DAG não consulta todos os contratos da D4Sign.

Fluxo:
1. O Flask recebe o webhook da D4Sign.
2. O Flask insere o payload em `[Integracao].[Silver].[FatoContratoD4WebhookEvento]`.
3. O Flask dispara esta DAG via API do Airflow, enviando `id_evento_webhook` no `dag_run.conf`.
4. A DAG lê exatamente esse evento, atualiza:
   - `[Integracao].[Silver].[FatoContratoD4]`
   - `[Integracao].[Silver].[FatoControleContratosEuromidia]`
   - `[Integracao].[Silver].[FatoControleContratosItensEuromidia]`
   - `[Integracao].[Silver].[FatoContratoD4Signatario]`
   - `[Integracao].[Silver].[DimHistoricoContratosD4]`
5. A DAG marca o evento como `PROCESSADO`.

Regra importante:
- `Finished document` da D4Sign vira contrato `Ativo` na Euromídia.
- `Signed` da D4Sign vira contrato `Em Assinatura` na Euromídia.
- `Canceled` da D4Sign vira contrato `Cancelado` na Euromídia.

Esta DAG roda a cada 1 minuto e também pode ser acionada sob demanda pelo Flask.
"""


def normalizar_texto(valor: Any) -> str:
    """Normalizo texto para comparar mensagens sem acento, caixa e espaços extras."""
    if valor is None:
        return ""

    texto = str(valor).strip().lower()
    texto = " ".join(texto.split())
    texto_sem_acento = unicodedata.normalize("NFKD", texto)
    return "".join(
        caractere for caractere in texto_sem_acento
        if not unicodedata.combining(caractere)
    )


def converter_int(valor: Any) -> int | None:
    """Converto valor para inteiro quando isso faz sentido."""
    if valor is None or isinstance(valor, bool):
        return None

    if isinstance(valor, int):
        return valor

    texto = str(valor).strip()
    if not texto:
        return None

    try:
        return int(float(texto.replace(",", ".")))
    except Exception:
        return None


def limitar_texto(valor: Any, limite: int) -> str | None:
    """Converto para texto e limito tamanho antes de gravar no SQL Server."""
    if valor is None:
        return None

    texto = str(valor).strip()
    if not texto:
        return None

    return texto[:limite]


def buscar_valor_recursivo(dados: Any, nomes_chaves: list[str]) -> Any:
    """Procuro um campo no payload mesmo que ele venha dentro de outro objeto."""
    nomes = {normalizar_texto(nome) for nome in nomes_chaves}

    if isinstance(dados, dict):
        for chave, valor in dados.items():
            if normalizar_texto(chave) in nomes and valor not in (None, ""):
                return valor

            encontrado = buscar_valor_recursivo(valor, nomes_chaves)
            if encontrado not in (None, ""):
                return encontrado

    if isinstance(dados, list):
        for item in dados:
            encontrado = buscar_valor_recursivo(item, nomes_chaves)
            if encontrado not in (None, ""):
                return encontrado

    return None


def carregar_payload(payload_json: str | None) -> dict[str, Any]:
    """Carrego o PayloadJson do webhook."""
    if not payload_json:
        return {}

    try:
        dados = json.loads(payload_json)
    except Exception:
        return {}

    return dados if isinstance(dados, dict) else {"payload": dados}


def obter_conf_dag() -> dict[str, Any]:
    """Leio a configuração enviada pelo Flask ao disparar a DAG."""
    try:
        contexto = get_current_context()
        dag_run = contexto.get("dag_run")
        conf = getattr(dag_run, "conf", None) or {}
        return conf if isinstance(conf, dict) else {}
    except Exception:
        return {}


def extrair_uuid_documento(evento: dict[str, Any], payload: dict[str, Any]) -> str | None:
    """Extraio o UUID do documento usando tabela e payload."""
    return limitar_texto(
        evento.get("UUIDDocumentoD4")
        or buscar_valor_recursivo(
            payload,
            [
                "uuid",
                "uuidDoc",
                "uuid_document",
                "uuid_documento",
                "UUIDDocumentoD4",
                "uuidDocument",
                "document_uuid",
            ],
        ),
        100,
    )


def extrair_tipo_evento(evento: dict[str, Any], payload: dict[str, Any]) -> str | None:
    """Extraio o tipo/mensagem do evento."""
    return limitar_texto(
        evento.get("TipoEventoD4")
        or buscar_valor_recursivo(
            payload,
            [
                "message",
                "event",
                "evento",
                "eventName",
                "event_name",
                "tipoEvento",
                "tipo_evento",
                "status",
            ],
        ),
        150,
    )


def extrair_nome_documento(evento: dict[str, Any], payload: dict[str, Any]) -> str | None:
    """Extraio o nome do documento."""
    return limitar_texto(
        evento.get("NomeDocumentoD4")
        or buscar_valor_recursivo(
            payload,
            [
                "document_name",
                "documentName",
                "nameDoc",
                "name_document",
                "nome_documento",
                "NomeDocumentoD4",
                "name",
            ],
        ),
        255,
    )


def extrair_data_evento(evento: dict[str, Any], payload: dict[str, Any]) -> str | None:
    """Extraio a data do evento como texto ISO."""
    return limitar_texto(
        evento.get("DataEventoD4Texto")
        or buscar_valor_recursivo(
            payload,
            [
                "event_datetime",
                "eventDatetime",
                "event_date_time",
                "signed_at",
                "date",
                "data",
                "created_at",
                "datetime",
            ],
        ),
        100,
    )



def valor_bool_para_bit(valor: Any) -> int:
    """Converto valores booleanos/textuais comuns para 0 ou 1."""
    if isinstance(valor, bool):
        return 1 if valor else 0

    if isinstance(valor, int):
        return 1 if valor == 1 else 0

    texto = normalizar_texto(valor)
    if texto in {"1", "true", "sim", "yes", "y", "on", "s", "ok", "enviado", "sent", "assinado", "signed", "visualizado", "viewed"}:
        return 1

    return 0


def coletar_dicionarios_com_email(dados: Any) -> list[dict[str, Any]]:
    """Coleto qualquer objeto do payload que pareça representar um signatário."""
    encontrados: list[dict[str, Any]] = []

    if isinstance(dados, dict):
        tem_email = buscar_valor_recursivo(dados, ["email", "EmailSignatario"])
        if tem_email:
            encontrados.append(dados)

        for valor in dados.values():
            encontrados.extend(coletar_dicionarios_com_email(valor))

    elif isinstance(dados, list):
        for item in dados:
            encontrados.extend(coletar_dicionarios_com_email(item))

    return encontrados


def primeiro_valor_signatario(dados: dict[str, Any], nomes_chaves: list[str], limite: int | None = None) -> Any:
    """Pego o primeiro campo existente no signatário, mesmo com nomes diferentes."""
    valor = buscar_valor_recursivo(dados, nomes_chaves)
    if limite is not None:
        return limitar_texto(valor, limite)
    return valor


def resolver_status_assinatura_signatario(
    *,
    tipo_evento: str | None,
    payload: dict[str, Any],
    signatario: dict[str, Any],
    bit_email_enviado: int,
    bit_visualizou: int,
    bit_assinado: int,
) -> int:
    """Resolvo status do signatário sem travar em um único layout da D4Sign."""
    texto_evento = normalizar_texto(tipo_evento)
    texto_status = normalizar_texto(
        primeiro_valor_signatario(
            signatario,
            [
                "status",
                "status_assinatura",
                "signature_status",
                "statusSigner",
                "status_signer",
                "message",
            ],
        )
    )

    texto_base = f"{texto_evento} {texto_status}"

    if bit_assinado or "signed" in texto_base or "assinado" in texto_base or "finished document" in texto_base:
        return ID_STATUS_ASSINATURA_D4_ASSINADO

    if bit_visualizou or "view" in texto_base or "visualiz" in texto_base or "read" in texto_base or "opened" in texto_base:
        return ID_STATUS_ASSINATURA_D4_VISUALIZOU

    if "erro" in texto_base or "error" in texto_base or "failed" in texto_base:
        return ID_STATUS_ASSINATURA_D4_EMAIL_ERRO

    if bit_email_enviado:
        return ID_STATUS_ASSINATURA_D4_PENDENTE

    return ID_STATUS_ASSINATURA_D4_PENDENTE


def extrair_signatarios(payload: dict[str, Any], tipo_evento: str | None, data_evento: str | None) -> list[dict[str, Any]]:
    """Extraio o máximo possível dos signatários do webhook D4Sign."""
    candidatos: list[dict[str, Any]] = []

    for chave in [
        "signers",
        "signer",
        "signatarios",
        "signatario",
        "recipients",
        "recipient",
        "subscribers",
        "subscriber",
        "participants",
        "participant",
    ]:
        valor = payload.get(chave)
        if isinstance(valor, list):
            candidatos.extend([item for item in valor if isinstance(item, dict)])
        elif isinstance(valor, dict):
            candidatos.append(valor)

    candidatos.extend(coletar_dicionarios_com_email(payload))

    tipo_normalizado = normalizar_texto(tipo_evento)
    resultado: list[dict[str, Any]] = []
    emails_vistos: set[str] = set()

    for signatario in candidatos:
        email = limitar_texto(
            primeiro_valor_signatario(
                signatario,
                [
                    "email",
                    "EmailSignatario",
                    "email_signatario",
                    "emailSigner",
                    "signer_email",
                ],
            ),
            255,
        )

        if not email:
            continue

        email_chave = email.strip().lower()
        if email_chave in emails_vistos:
            continue

        emails_vistos.add(email_chave)

        data_envio = primeiro_valor_signatario(
            signatario,
            [
                "sent_at",
                "send_at",
                "sended_at",
                "email_sent_at",
                "data_envio",
                "DataEnvioD4",
                "created_at",
                "createdAt",
            ],
            100,
        )

        data_primeira_visualizacao = primeiro_valor_signatario(
            signatario,
            [
                "first_viewed_at",
                "first_view_at",
                "first_opened_at",
                "viewed_at",
                "opened_at",
                "visualized_at",
                "DataPrimeiraVisualizacaoD4",
                "data_primeira_visualizacao",
            ],
            100,
        )

        data_ultima_visualizacao = primeiro_valor_signatario(
            signatario,
            [
                "last_viewed_at",
                "last_view_at",
                "last_opened_at",
                "viewed_at",
                "opened_at",
                "visualized_at",
                "DataUltimaVisualizacaoD4",
                "data_ultima_visualizacao",
            ],
            100,
        )

        data_assinatura = primeiro_valor_signatario(
            signatario,
            [
                "signed_at",
                "signedAt",
                "signature_date",
                "signatureDate",
                "date_signed",
                "DataAssinaturaD4",
                "data_assinatura",
            ],
            100,
        )

        bit_assinado = 1 if (
            data_assinatura
            or "signed" in tipo_normalizado
            or "assinado" in tipo_normalizado
            or "finished document" in tipo_normalizado
            or converter_int(payload.get("type_post")) in {1, 4}
        ) else 0

        bit_visualizou = 1 if (
            data_primeira_visualizacao
            or data_ultima_visualizacao
            or "view" in tipo_normalizado
            or "visualiz" in tipo_normalizado
            or "opened" in tipo_normalizado
            or "read" in tipo_normalizado
        ) else 0

        bit_email_enviado = 1 if (
            data_envio
            or valor_bool_para_bit(primeiro_valor_signatario(signatario, ["email_sent", "BitEmailEnviado", "sent"]))
            or "sent" in tipo_normalizado
            or "enviado" in tipo_normalizado
        ) else 0

        telefone = limitar_texto(
            primeiro_valor_signatario(
                signatario,
                [
                    "phone",
                    "telephone",
                    "telefone",
                    "celular",
                    "mobile",
                    "whatsapp",
                    "phone_number",
                    "TelefoneSignatario",
                ],
            ),
            50,
        )

        ip_assinatura = limitar_texto(
            primeiro_valor_signatario(
                signatario,
                [
                    "ip",
                    "ip_address",
                    "ipAddress",
                    "signature_ip",
                    "IpAssinaturaD4",
                    "ip_assinatura",
                ],
            )
            or primeiro_valor_signatario(
                payload,
                ["ip", "ip_address", "ipAddress", "signature_ip", "IpAssinaturaD4"],
            ),
            100,
        )

        geolocalizacao = primeiro_valor_signatario(
            signatario,
            [
                "geolocation",
                "geo_location",
                "location",
                "assinatura_geolocalizacao",
                "GeolocalizacaoAssinaturaD4",
            ],
        )

        if isinstance(geolocalizacao, (dict, list)):
            geolocalizacao = json.dumps(geolocalizacao, ensure_ascii=False, default=str)

        user_agent = limitar_texto(
            primeiro_valor_signatario(
                signatario,
                [
                    "user_agent",
                    "userAgent",
                    "signature_user_agent",
                    "UserAgentAssinaturaD4",
                ],
            )
            or primeiro_valor_signatario(
                payload,
                ["user_agent", "userAgent", "signature_user_agent", "UserAgentAssinaturaD4"],
            ),
            500,
        )

        status_envio = limitar_texto(
            primeiro_valor_signatario(
                signatario,
                [
                    "email_status",
                    "status_email",
                    "StatusEnvioEmailD4",
                    "send_status",
                    "status_envio",
                ],
            ),
            100,
        )

        mensagem_envio = limitar_texto(
            primeiro_valor_signatario(
                signatario,
                [
                    "email_message",
                    "message_email",
                    "MensagemEnvioEmailD4",
                    "send_message",
                    "mensagem_envio",
                    "error_message",
                ],
            ),
            1000,
        )

        bit_contato_principal = valor_bool_para_bit(
            primeiro_valor_signatario(
                signatario,
                [
                    "main",
                    "principal",
                    "is_main",
                    "main_contact",
                    "BitContatoPrincipal",
                    "contato_principal",
                ],
            )
        )

        registro = {
            "KeySignerD4": limitar_texto(
                primeiro_valor_signatario(
                    signatario,
                    [
                        "uuid",
                        "key_signer",
                        "keySigner",
                        "signer_key",
                        "uuid_signer",
                        "id",
                        "KeySignerD4",
                    ],
                ),
                100,
            ),
            "EmailSignatario": email,
            "NomeSignatario": limitar_texto(
                primeiro_valor_signatario(
                    signatario,
                    [
                        "name",
                        "nome",
                        "full_name",
                        "fullName",
                        "signer_name",
                        "NomeSignatario",
                    ],
                ),
                255,
            ),
            "DocumentoSignatario": limitar_texto(
                primeiro_valor_signatario(
                    signatario,
                    [
                        "identification_number",
                        "document",
                        "documento",
                        "cpf",
                        "cnpj",
                        "DocumentoSignatario",
                    ],
                ),
                100,
            ),
            "TelefoneSignatario": telefone,
            "BitContatoPrincipal": bit_contato_principal,
            "IDDimStatusAssinaturaD4": resolver_status_assinatura_signatario(
                tipo_evento=tipo_evento,
                payload=payload,
                signatario=signatario,
                bit_email_enviado=bit_email_enviado,
                bit_visualizou=bit_visualizou,
                bit_assinado=bit_assinado,
            ),
            "BitEmailEnviado": bit_email_enviado,
            "StatusEnvioEmailD4": status_envio,
            "MensagemEnvioEmailD4": mensagem_envio,
            "BitVisualizouDocumento": bit_visualizou,
            "DataPrimeiraVisualizacaoD4": limitar_texto(data_primeira_visualizacao, 100),
            "DataUltimaVisualizacaoD4": limitar_texto(data_ultima_visualizacao, 100),
            "BitAssinado": bit_assinado,
            "DataEnvioD4": limitar_texto(data_envio, 100),
            "DataAssinaturaD4": limitar_texto(data_assinatura or (data_evento if bit_assinado else None), 100),
            "IpAssinaturaD4": ip_assinatura,
            "GeolocalizacaoAssinaturaD4": limitar_texto(geolocalizacao, 500),
            "UserAgentAssinaturaD4": user_agent,
            "SegundosAtePrimeiraVisualizacao": converter_int(
                primeiro_valor_signatario(signatario, ["seconds_to_first_view", "SegundosAtePrimeiraVisualizacao"])
            ),
            "SegundosAteAssinatura": converter_int(
                primeiro_valor_signatario(signatario, ["seconds_to_signature", "SegundosAteAssinatura"])
            ),
        }

        resultado.append(registro)

    return resultado


def resolver_status_por_evento(tipo_evento: str | None, payload: dict[str, Any]) -> dict[str, Any]:
    """Transformo o evento da D4Sign nos status que serão gravados nas tabelas."""
    tipo_normalizado = normalizar_texto(tipo_evento)
    type_post = converter_int(payload.get("type_post"))

    if type_post == 1 or "finished document" in tipo_normalizado or "finalizado" in tipo_normalizado:
        return {
            "IDDimStatusD4": ID_STATUS_D4_FINALIZADO,
            "IDFaseD4": ID_STATUS_D4_FINALIZADO,
            "NomeFaseD4": "Finalizado",
            "IDDimStatusContratos": ID_STATUS_CONTRATO_ATIVO,
            "EventoHistorico": "Finished document",
        }

    if type_post == 4 or "signed" in tipo_normalizado or "assinado" in tipo_normalizado:
        return {
            "IDDimStatusD4": ID_STATUS_D4_AGUARDANDO_ASSINATURAS,
            "IDFaseD4": ID_STATUS_D4_AGUARDANDO_ASSINATURAS,
            "NomeFaseD4": "Aguardando Assinaturas",
            "IDDimStatusContratos": ID_STATUS_CONTRATO_EM_ASSINATURA,
            "EventoHistorico": "Signed",
        }

    if type_post == 3 or "cancel" in tipo_normalizado or "cancelado" in tipo_normalizado:
        return {
            "IDDimStatusD4": ID_STATUS_D4_CANCELADO,
            "IDFaseD4": ID_STATUS_D4_CANCELADO,
            "NomeFaseD4": "Cancelado",
            "IDDimStatusContratos": ID_STATUS_CONTRATO_CANCELADO,
            "EventoHistorico": "Canceled",
        }

    return {
        "IDDimStatusD4": None,
        "IDFaseD4": None,
        "NomeFaseD4": limitar_texto(tipo_evento, 90),
        "IDDimStatusContratos": None,
        "EventoHistorico": limitar_texto(tipo_evento, 150) or "Webhook D4Sign",
    }


def reservar_eventos(conexao, id_evento_webhook: int | None, limite_eventos: int) -> list[dict[str, Any]]:
    """Marco eventos pendentes como PROCESSANDO para evitar processamento duplicado."""
    if id_evento_webhook:
        sql = text(f"""
            ;WITH eventos AS (
                SELECT TOP (1)
                    *
                FROM {TABELA_WEBHOOK_EVENTO_D4} WITH (READPAST, UPDLOCK, ROWLOCK)
                WHERE IDFatoContratoD4WebhookEvento = :IDEvento
                  AND BitProcessado = 0
                  AND StatusProcessamento IN ('PENDENTE', 'REPROCESSAR')
                ORDER BY
                    COALESCE(
                        TRY_CONVERT(datetime2(7), DataEventoD4Texto, 126),
                        TRY_CONVERT(datetime2(7), JSON_VALUE(PayloadJson, '$.event_datetime'), 126),
                        DataRecebimento
                    ) ASC,
                    IDFatoContratoD4WebhookEvento ASC
            )
            UPDATE eventos
            SET
                StatusProcessamento = 'PROCESSANDO',
                MensagemErro = NULL,
                DataProcessamento = NULL
            OUTPUT
                INSERTED.IDFatoContratoD4WebhookEvento,
                INSERTED.IDFatoContratoD4,
                INSERTED.UUIDDocumentoD4,
                INSERTED.TipoEventoD4,
                INSERTED.NomeDocumentoD4,
                INSERTED.DataEventoD4Texto,
                INSERTED.PayloadJson,
                INSERTED.DataRecebimento;
        """)
        return list(conexao.execute(sql, {"IDEvento": int(id_evento_webhook)}).mappings().all())

    sql = text(f"""
        ;WITH eventos AS (
            SELECT TOP (:LimiteEventos)
                *
            FROM {TABELA_WEBHOOK_EVENTO_D4} WITH (READPAST, UPDLOCK, ROWLOCK)
            WHERE BitProcessado = 0
              AND StatusProcessamento IN ('PENDENTE', 'REPROCESSAR')
            ORDER BY
                COALESCE(
                    TRY_CONVERT(datetime2(7), DataEventoD4Texto, 126),
                    TRY_CONVERT(datetime2(7), JSON_VALUE(PayloadJson, '$.event_datetime'), 126),
                    DataRecebimento
                ) ASC,
                IDFatoContratoD4WebhookEvento ASC
        )
        UPDATE eventos
        SET
            StatusProcessamento = 'PROCESSANDO',
            MensagemErro = NULL,
            DataProcessamento = NULL
        OUTPUT
            INSERTED.IDFatoContratoD4WebhookEvento,
            INSERTED.IDFatoContratoD4,
            INSERTED.UUIDDocumentoD4,
            INSERTED.TipoEventoD4,
            INSERTED.NomeDocumentoD4,
            INSERTED.DataEventoD4Texto,
            INSERTED.PayloadJson,
            INSERTED.DataRecebimento;
    """)
    return list(conexao.execute(sql, {"LimiteEventos": int(limite_eventos)}).mappings().all())


def buscar_contrato_por_uuid(conexao, uuid_documento: str) -> dict[str, Any] | None:
    """Encontro o contrato D4 interno pelo UUID enviado no webhook."""
    sql = text(f"""
        SELECT TOP (1)
            IDFatoContratoD4,
            IDDimStatusD4,
            IDEmpresa,
            IDDimCofreD4,
            IDFatoControleContratosEuromidia,
            IDFatoKanbanCard,
            IDDimStatusContratos,
            IDDimModeloContratoD4,
            IDDimTipoDocumento,
            UUIDDocumentoD4,
            UUIDCofreD4,
            NomeDocumentoD4,
            NomeCofreD4,
            IDFaseD4,
            NomeFaseD4,
            TipoArquivoD4,
            QuantidadePaginas,
            TamanhoArquivoD4,
            StatusComentarioD4,
            CanceladoPorD4,
            DataCriacao,
            DataAtualizacao,
            BitAtivo
        FROM {TABELA_CONTRATO_D4}
        WHERE UUIDDocumentoD4 = :UUIDDocumentoD4
          AND ISNULL(BitAtivo, 1) = 1
        ORDER BY IDFatoContratoD4 DESC;
    """)
    linha = conexao.execute(sql, {"UUIDDocumentoD4": uuid_documento}).mappings().first()
    return dict(linha) if linha else None


def atualizar_contrato_d4(
    conexao,
    *,
    contrato: dict[str, Any],
    status_evento: dict[str, Any],
    nome_documento: str | None,
) -> None:
    """Atualizo a visão atual do documento na FatoContratoD4."""
    sql = text(f"""
        UPDATE {TABELA_CONTRATO_D4}
        SET
            IDDimStatusD4 = COALESCE(:IDDimStatusD4, IDDimStatusD4),
            IDFaseD4 = COALESCE(:IDFaseD4, IDFaseD4),
            NomeFaseD4 = COALESCE(:NomeFaseD4, NomeFaseD4),
            IDDimStatusContratos = COALESCE(:IDDimStatusContratos, IDDimStatusContratos),
            NomeDocumentoD4 = COALESCE(:NomeDocumentoD4, NomeDocumentoD4),
            DataAtualizacao = SYSDATETIME()
        WHERE IDFatoContratoD4 = :IDFatoContratoD4;
    """)
    conexao.execute(
        sql,
        {
            "IDFatoContratoD4": contrato["IDFatoContratoD4"],
            "IDDimStatusD4": status_evento.get("IDDimStatusD4"),
            "IDFaseD4": status_evento.get("IDFaseD4"),
            "NomeFaseD4": status_evento.get("NomeFaseD4"),
            "IDDimStatusContratos": status_evento.get("IDDimStatusContratos"),
            "NomeDocumentoD4": nome_documento,
        },
    )


def resolver_status_final_euromidia(status_atual: Any, status_novo: Any) -> int | None:
    """Resolvo o status final sem deixar webhook atrasado voltar a esteira."""
    id_status_atual = converter_int(status_atual)
    id_status_novo = converter_int(status_novo)

    if id_status_novo is None:
        return id_status_atual

    if id_status_novo == ID_STATUS_CONTRATO_CANCELADO:
        return ID_STATUS_CONTRATO_CANCELADO

    if id_status_novo == ID_STATUS_CONTRATO_ATIVO:
        return ID_STATUS_CONTRATO_ATIVO

    if id_status_atual is None:
        return id_status_novo

    if id_status_atual == ID_STATUS_CONTRATO_CANCELADO:
        return ID_STATUS_CONTRATO_CANCELADO

    if id_status_atual == ID_STATUS_CONTRATO_ATIVO:
        return ID_STATUS_CONTRATO_ATIVO

    if id_status_novo > id_status_atual:
        return id_status_novo

    return id_status_atual


def buscar_status_atual_controle_euromidia(conexao, id_fato_controle_contrato: int) -> int | None:
    """Leio o status atual da tabela principal da esteira do contrato."""
    sql = text(f"""
        SELECT TOP (1)
            IDDimStatusContratos
        FROM {TABELA_CONTROLE_CONTRATOS} WITH (UPDLOCK, ROWLOCK)
        WHERE IDFatoControleContratosEuromidia = :IDFatoControleContratosEuromidia
          AND ISNULL(BitAtivo, 1) = 1;
    """)
    linha = conexao.execute(
        sql,
        {"IDFatoControleContratosEuromidia": id_fato_controle_contrato},
    ).mappings().first()

    if not linha:
        return None

    return converter_int(linha.get("IDDimStatusContratos"))



def inserir_historico_d4(
    conexao,
    *,
    contrato: dict[str, Any],
    status_evento: dict[str, Any],
    data_evento: str | None,
) -> dict[str, Any]:
    """Insiro histórico do webhook sem duplicar o último status idêntico."""
    id_controle = converter_int(contrato.get("IDFatoControleContratosEuromidia"))
    id_status_contrato = converter_int(status_evento.get("IDDimStatusContratos") or contrato.get("IDDimStatusContratos"))
    id_status_d4 = converter_int(status_evento.get("IDDimStatusD4") or contrato.get("IDDimStatusD4"))

    if not id_controle:
        return {"historico_inserido": False, "motivo": "sem_id_controle"}

    sql = text(f"""
        ;WITH UltimoHistorico AS (
            SELECT TOP (1)
                IDDimStatusContratos,
                IDDimStatusD4
            FROM {TABELA_HISTORICO_D4} WITH (READPAST)
            WHERE IDEmpresaProprietaria = :IDEmpresaProprietaria
              AND IDFatoControleContratosEuromidia = :IDFatoControleContratosEuromidia
            ORDER BY DataStatus DESC, IDDimHistoricoContratos DESC
        )
        INSERT INTO {TABELA_HISTORICO_D4}
        (
            IDEmpresaProprietaria,
            IDFatoControleContratosEuromidia,
            IDDimStatusContratos,
            IDDimStatusD4,
            DataStatus
        )
        OUTPUT INSERTED.IDDimHistoricoContratos AS IDDimHistoricoContratos
        SELECT
            :IDEmpresaProprietaria,
            :IDFatoControleContratosEuromidia,
            :IDDimStatusContratos,
            :IDDimStatusD4,
            COALESCE(TRY_CONVERT(datetime2(3), :DataEvento, 126), SYSDATETIME())
        WHERE NOT EXISTS (
            SELECT 1
            FROM UltimoHistorico
            WHERE ISNULL(IDDimStatusContratos, -1) = ISNULL(:IDDimStatusContratos, -1)
              AND ISNULL(IDDimStatusD4, -1) = ISNULL(:IDDimStatusD4, -1)
        );
    """)

    linha = conexao.execute(
        sql,
        {
            "IDEmpresaProprietaria": ID_EMPRESA_PROPRIETARIA_EUROMIDIA,
            "IDFatoControleContratosEuromidia": id_controle,
            "IDDimStatusContratos": id_status_contrato,
            "IDDimStatusD4": id_status_d4,
            "DataEvento": data_evento,
        },
    ).mappings().first()

    return {
        "historico_inserido": bool(linha),
        "id_historico": int(linha["IDDimHistoricoContratos"]) if linha and linha.get("IDDimHistoricoContratos") is not None else None,
        "id_controle": id_controle,
        "id_status_contrato": id_status_contrato,
        "id_status_d4": id_status_d4,
    }


def propagar_status_contrato_euromidia(
    conexao,
    *,
    id_fato_controle_contrato: int | None,
    id_status_contrato: int | None,
    id_fato_kanban_card: int | None = None,
) -> dict[str, Any]:
    """Atualizo cabeçalho, itens e solicitação para a tela acompanhar o webhook."""
    id_controle = converter_int(id_fato_controle_contrato)
    id_card = converter_int(id_fato_kanban_card)
    id_status_novo = converter_int(id_status_contrato)

    if not id_controle or id_status_novo is None:
        return {
            "propagou": False,
            "motivo": "sem_id_controle_ou_sem_status",
            "id_controle": id_controle,
            "id_status_novo": id_status_novo,
        }

    status_atual = buscar_status_atual_controle_euromidia(conexao, id_controle)
    status_final = resolver_status_final_euromidia(status_atual, id_status_novo)

    if status_final is None:
        return {
            "propagou": False,
            "motivo": "status_final_nulo",
            "id_controle": id_controle,
            "status_atual": status_atual,
            "id_status_novo": id_status_novo,
        }

    resultado_cabecalho = conexao.execute(
        text(f"""
            UPDATE {TABELA_CONTROLE_CONTRATOS}
            SET
                IDDimStatusContratos = :IDDimStatusContratos,
                DataAtualizacao = SYSDATETIME()
            WHERE IDFatoControleContratosEuromidia = :IDFatoControleContratosEuromidia
              AND ISNULL(BitAtivo, 1) = 1
              AND (
                    IDDimStatusContratos IS NULL
                    OR IDDimStatusContratos <> :IDDimStatusContratos
              );
        """),
        {
            "IDFatoControleContratosEuromidia": id_controle,
            "IDDimStatusContratos": status_final,
        },
    )

    resultado_itens = conexao.execute(
        text(f"""
            UPDATE itens
            SET
                itens.Status = status_contrato.Status,
                itens.DataAtualizacao = SYSDATETIME()
            FROM {TABELA_CONTROLE_CONTRATOS_ITENS} itens
            INNER JOIN {TABELA_STATUS_CONTRATOS} status_contrato
                ON status_contrato.IDDimStatusContratos = :IDDimStatusContratos
               AND status_contrato.IDEmpresaProprietaria = :IDEmpresaProprietaria
            WHERE itens.IDFatoControleContratoEuromidia = :IDFatoControleContratosEuromidia
              AND ISNULL(itens.BitAtivo, 1) = 1
              AND (
                    itens.Status IS NULL
                    OR LTRIM(RTRIM(itens.Status)) <> LTRIM(RTRIM(status_contrato.Status))
              );
        """),
        {
            "IDFatoControleContratosEuromidia": id_controle,
            "IDDimStatusContratos": status_final,
            "IDEmpresaProprietaria": ID_EMPRESA_PROPRIETARIA_EUROMIDIA,
        },
    )

    resultado_solicitacao = conexao.execute(
        text(f"""
            UPDATE solicitacao
            SET
                solicitacao.IDDimStatusContratos = :IDDimStatusContratos,
                solicitacao.DataAtualizacao = SYSDATETIME()
            FROM {TABELA_SOLICITACAO_CONTRATO} solicitacao
            WHERE ISNULL(solicitacao.BitAtivo, 1) = 1
              AND (
                    solicitacao.IDFatoControleContratosEuromidia = :IDFatoControleContratosEuromidia
                    OR (
                        :IDFatoKanbanCard IS NOT NULL
                        AND solicitacao.IDFatoKanbanCard = :IDFatoKanbanCard
                    )
              )
              AND (
                    solicitacao.IDDimStatusContratos IS NULL
                    OR solicitacao.IDDimStatusContratos <> :IDDimStatusContratos
              );
        """),
        {
            "IDFatoControleContratosEuromidia": id_controle,
            "IDFatoKanbanCard": id_card,
            "IDDimStatusContratos": status_final,
        },
    )

    return {
        "propagou": True,
        "id_controle": id_controle,
        "id_card": id_card,
        "status_atual": status_atual,
        "status_evento": id_status_novo,
        "status_final": status_final,
        "cabecalho_atualizado": int(resultado_cabecalho.rowcount or 0),
        "itens_atualizados": int(resultado_itens.rowcount or 0),
        "solicitacoes_atualizadas": int(resultado_solicitacao.rowcount or 0),
    }


def upsert_signatario_d4(
    conexao,
    *,
    contrato: dict[str, Any],
    signatario: dict[str, Any],
    payload_json: str,
) -> str:
    """Atualizo ou crio signatário, preenchendo todos os campos disponíveis."""
    email = signatario.get("EmailSignatario")
    if not email:
        return "ignorado_sem_email"

    params = {
        "IDFatoContratoD4": contrato.get("IDFatoContratoD4"),
        "IDFatoControleContratosEuromidia": contrato.get("IDFatoControleContratosEuromidia"),
        "IDFatoKanbanCard": contrato.get("IDFatoKanbanCard"),
        "IDEmpresa": contrato.get("IDEmpresa"),
        "KeySignerD4": signatario.get("KeySignerD4"),
        "EmailSignatario": email,
        "NomeSignatario": signatario.get("NomeSignatario"),
        "DocumentoSignatario": signatario.get("DocumentoSignatario"),
        "TelefoneSignatario": signatario.get("TelefoneSignatario"),
        "BitContatoPrincipal": int(signatario.get("BitContatoPrincipal") or 0),
        "IDDimStatusAssinaturaD4": signatario.get("IDDimStatusAssinaturaD4"),
        "BitEmailEnviado": int(signatario.get("BitEmailEnviado") or 0),
        "StatusEnvioEmailD4": signatario.get("StatusEnvioEmailD4"),
        "MensagemEnvioEmailD4": signatario.get("MensagemEnvioEmailD4"),
        "BitVisualizouDocumento": int(signatario.get("BitVisualizouDocumento") or 0),
        "DataPrimeiraVisualizacaoD4": signatario.get("DataPrimeiraVisualizacaoD4"),
        "DataUltimaVisualizacaoD4": signatario.get("DataUltimaVisualizacaoD4"),
        "BitAssinado": int(signatario.get("BitAssinado") or 0),
        "DataEnvioD4": signatario.get("DataEnvioD4"),
        "DataAssinaturaD4": signatario.get("DataAssinaturaD4"),
        "IpAssinaturaD4": signatario.get("IpAssinaturaD4"),
        "GeolocalizacaoAssinaturaD4": signatario.get("GeolocalizacaoAssinaturaD4"),
        "UserAgentAssinaturaD4": signatario.get("UserAgentAssinaturaD4"),
        "SegundosAtePrimeiraVisualizacao": signatario.get("SegundosAtePrimeiraVisualizacao"),
        "SegundosAteAssinatura": signatario.get("SegundosAteAssinatura"),
        "PayloadJson": payload_json,
    }

    sql_update = text(f"""
        UPDATE {TABELA_SIGNATARIO_D4}
        SET
            KeySignerD4 = COALESCE(:KeySignerD4, KeySignerD4),
            NomeSignatario = COALESCE(:NomeSignatario, NomeSignatario),
            DocumentoSignatario = COALESCE(:DocumentoSignatario, DocumentoSignatario),
            TelefoneSignatario = COALESCE(:TelefoneSignatario, TelefoneSignatario),
            BitContatoPrincipal = CASE WHEN :BitContatoPrincipal = 1 THEN 1 ELSE BitContatoPrincipal END,
            IDDimStatusAssinaturaD4 = COALESCE(:IDDimStatusAssinaturaD4, IDDimStatusAssinaturaD4),
            BitEmailEnviado = CASE WHEN :BitEmailEnviado = 1 THEN 1 ELSE BitEmailEnviado END,
            StatusEnvioEmailD4 = COALESCE(:StatusEnvioEmailD4, StatusEnvioEmailD4),
            MensagemEnvioEmailD4 = COALESCE(:MensagemEnvioEmailD4, MensagemEnvioEmailD4),
            BitVisualizouDocumento = CASE WHEN :BitVisualizouDocumento = 1 THEN 1 ELSE BitVisualizouDocumento END,
            DataPrimeiraVisualizacaoD4 = COALESCE(
                DataPrimeiraVisualizacaoD4,
                TRY_CONVERT(datetime2(7), :DataPrimeiraVisualizacaoD4, 126)
            ),
            DataUltimaVisualizacaoD4 = COALESCE(
                TRY_CONVERT(datetime2(7), :DataUltimaVisualizacaoD4, 126),
                DataUltimaVisualizacaoD4
            ),
            BitAssinado = CASE WHEN :BitAssinado = 1 THEN 1 ELSE BitAssinado END,
            DataEnvioD4 = COALESCE(
                DataEnvioD4,
                TRY_CONVERT(datetime2(7), :DataEnvioD4, 126)
            ),
            DataAssinaturaD4 = CASE
                WHEN :BitAssinado = 1
                    THEN COALESCE(
                        TRY_CONVERT(datetime2(7), :DataAssinaturaD4, 126),
                        DataAssinaturaD4,
                        SYSDATETIME()
                    )
                ELSE DataAssinaturaD4
            END,
            IpAssinaturaD4 = COALESCE(:IpAssinaturaD4, IpAssinaturaD4),
            GeolocalizacaoAssinaturaD4 = COALESCE(:GeolocalizacaoAssinaturaD4, GeolocalizacaoAssinaturaD4),
            UserAgentAssinaturaD4 = COALESCE(:UserAgentAssinaturaD4, UserAgentAssinaturaD4),
            SegundosAtePrimeiraVisualizacao = COALESCE(
                :SegundosAtePrimeiraVisualizacao,
                CASE
                    WHEN DataEnvioD4 IS NOT NULL
                     AND COALESCE(TRY_CONVERT(datetime2(7), :DataPrimeiraVisualizacaoD4, 126), DataPrimeiraVisualizacaoD4) IS NOT NULL
                        THEN DATEDIFF(
                            SECOND,
                            DataEnvioD4,
                            COALESCE(TRY_CONVERT(datetime2(7), :DataPrimeiraVisualizacaoD4, 126), DataPrimeiraVisualizacaoD4)
                        )
                    ELSE SegundosAtePrimeiraVisualizacao
                END
            ),
            SegundosAteAssinatura = COALESCE(
                :SegundosAteAssinatura,
                CASE
                    WHEN DataEnvioD4 IS NOT NULL
                     AND COALESCE(TRY_CONVERT(datetime2(7), :DataAssinaturaD4, 126), DataAssinaturaD4) IS NOT NULL
                        THEN DATEDIFF(
                            SECOND,
                            DataEnvioD4,
                            COALESCE(TRY_CONVERT(datetime2(7), :DataAssinaturaD4, 126), DataAssinaturaD4)
                        )
                    ELSE SegundosAteAssinatura
                END
            ),
            PayloadUltimaConsultaD4 = :PayloadJson,
            DataUltimaConsultaD4 = SYSDATETIME(),
            DataAtualizacao = SYSDATETIME(),
            BitAtivo = 1
        WHERE IDFatoContratoD4 = :IDFatoContratoD4
          AND LOWER(LTRIM(RTRIM(EmailSignatario))) = LOWER(LTRIM(RTRIM(:EmailSignatario)));
    """)
    resultado = conexao.execute(sql_update, params)

    if resultado.rowcount and resultado.rowcount > 0:
        return "update"

    sql_insert = text(f"""
        INSERT INTO {TABELA_SIGNATARIO_D4}
        (
            IDFatoContratoD4,
            IDFatoControleContratosEuromidia,
            IDFatoKanbanCard,
            IDEmpresa,
            KeySignerD4,
            EmailSignatario,
            NomeSignatario,
            DocumentoSignatario,
            TelefoneSignatario,
            BitContatoPrincipal,
            IDDimStatusAssinaturaD4,
            BitEmailEnviado,
            StatusEnvioEmailD4,
            MensagemEnvioEmailD4,
            BitVisualizouDocumento,
            DataPrimeiraVisualizacaoD4,
            DataUltimaVisualizacaoD4,
            BitAssinado,
            DataEnvioD4,
            DataAssinaturaD4,
            IpAssinaturaD4,
            GeolocalizacaoAssinaturaD4,
            UserAgentAssinaturaD4,
            SegundosAtePrimeiraVisualizacao,
            SegundosAteAssinatura,
            PayloadUltimaConsultaD4,
            DataUltimaConsultaD4,
            DataCriacao,
            DataAtualizacao,
            BitAtivo
        )
        VALUES
        (
            :IDFatoContratoD4,
            :IDFatoControleContratosEuromidia,
            :IDFatoKanbanCard,
            :IDEmpresa,
            :KeySignerD4,
            :EmailSignatario,
            :NomeSignatario,
            :DocumentoSignatario,
            :TelefoneSignatario,
            :BitContatoPrincipal,
            :IDDimStatusAssinaturaD4,
            :BitEmailEnviado,
            :StatusEnvioEmailD4,
            :MensagemEnvioEmailD4,
            :BitVisualizouDocumento,
            TRY_CONVERT(datetime2(7), :DataPrimeiraVisualizacaoD4, 126),
            TRY_CONVERT(datetime2(7), :DataUltimaVisualizacaoD4, 126),
            :BitAssinado,
            TRY_CONVERT(datetime2(7), :DataEnvioD4, 126),
            CASE
                WHEN :BitAssinado = 1
                    THEN COALESCE(TRY_CONVERT(datetime2(7), :DataAssinaturaD4, 126), SYSDATETIME())
                ELSE TRY_CONVERT(datetime2(7), :DataAssinaturaD4, 126)
            END,
            :IpAssinaturaD4,
            :GeolocalizacaoAssinaturaD4,
            :UserAgentAssinaturaD4,
            :SegundosAtePrimeiraVisualizacao,
            :SegundosAteAssinatura,
            :PayloadJson,
            SYSDATETIME(),
            SYSDATETIME(),
            SYSDATETIME(),
            1
        );
    """)
    conexao.execute(sql_insert, params)
    return "insert"


def marcar_evento_processado(conexao, *, id_evento: int, id_fato_contrato_d4: int) -> None:
    """Marco o evento como processado."""
    sql = text(f"""
        UPDATE {TABELA_WEBHOOK_EVENTO_D4}
        SET
            IDFatoContratoD4 = :IDFatoContratoD4,
            BitProcessado = 1,
            StatusProcessamento = 'PROCESSADO',
            MensagemErro = NULL,
            DataProcessamento = SYSDATETIME()
        WHERE IDFatoContratoD4WebhookEvento = :IDEvento;
    """)
    conexao.execute(sql, {"IDFatoContratoD4": id_fato_contrato_d4, "IDEvento": id_evento})


def marcar_evento_erro(conexao, *, id_evento: int, status: str, mensagem: str) -> None:
    """Marco evento como erro/documento não encontrado."""
    sql = text(f"""
        UPDATE {TABELA_WEBHOOK_EVENTO_D4}
        SET
            BitProcessado = 1,
            StatusProcessamento = :StatusProcessamento,
            MensagemErro = :MensagemErro,
            DataProcessamento = SYSDATETIME()
        WHERE IDFatoContratoD4WebhookEvento = :IDEvento;
    """)
    conexao.execute(
        sql,
        {
            "IDEvento": id_evento,
            "StatusProcessamento": limitar_texto(status, 50),
            "MensagemErro": limitar_texto(mensagem, 2000),
        },
    )


def processar_evento(conexao, evento: dict[str, Any]) -> dict[str, Any]:
    """Processo um evento de webhook e atualizo as tabelas finais."""
    id_evento = int(evento["IDFatoContratoD4WebhookEvento"])
    payload_json = str(evento.get("PayloadJson") or "{}")
    payload = carregar_payload(payload_json)

    uuid_documento = extrair_uuid_documento(evento, payload)
    tipo_evento = extrair_tipo_evento(evento, payload)
    nome_documento = extrair_nome_documento(evento, payload)
    data_evento = extrair_data_evento(evento, payload)

    if not uuid_documento:
        marcar_evento_erro(
            conexao,
            id_evento=id_evento,
            status="ERRO",
            mensagem="Não consegui extrair UUIDDocumentoD4 do webhook.",
        )
        return {"id_evento": id_evento, "status": "erro_sem_uuid"}

    contrato = buscar_contrato_por_uuid(conexao, uuid_documento)
    if not contrato:
        marcar_evento_erro(
            conexao,
            id_evento=id_evento,
            status="DOCUMENTO_NAO_ENCONTRADO",
            mensagem=f"UUIDDocumentoD4 não encontrado na FatoContratoD4: {uuid_documento}",
        )
        return {"id_evento": id_evento, "status": "documento_nao_encontrado", "uuid": uuid_documento}

    status_evento = resolver_status_por_evento(tipo_evento, payload)
    signatarios = extrair_signatarios(payload, tipo_evento, data_evento)

    atualizar_contrato_d4(
        conexao,
        contrato=contrato,
        status_evento=status_evento,
        nome_documento=nome_documento,
    )

    propagacao_euromidia = propagar_status_contrato_euromidia(
        conexao,
        id_fato_controle_contrato=contrato.get("IDFatoControleContratosEuromidia"),
        id_fato_kanban_card=contrato.get("IDFatoKanbanCard"),
        id_status_contrato=status_evento.get("IDDimStatusContratos"),
    )

    inserir_historico_d4(
        conexao,
        contrato=contrato,
        status_evento=status_evento,
        data_evento=data_evento,
    )

    acoes_signatarios: list[str] = []
    for signatario in signatarios:
        acao = upsert_signatario_d4(
            conexao,
            contrato=contrato,
            signatario=signatario,
            payload_json=payload_json,
        )
        acoes_signatarios.append(acao)

    marcar_evento_processado(
        conexao,
        id_evento=id_evento,
        id_fato_contrato_d4=int(contrato["IDFatoContratoD4"]),
    )

    return {
        "id_evento": id_evento,
        "uuid": uuid_documento,
        "id_fato_contrato_d4": int(contrato["IDFatoContratoD4"]),
        "tipo_evento": tipo_evento,
        "status": "processado",
        "id_status_d4": status_evento.get("IDDimStatusD4"),
        "id_status_contrato": status_evento.get("IDDimStatusContratos"),
        "propagacao_euromidia": propagacao_euromidia,
        "signatarios": len(signatarios),
        "acoes_signatarios": acoes_signatarios,
    }


@dag(
    dag_id="pipeline_processar_webhook_d4sign",
    description="Processa eventos recebidos via webhook D4Sign e atualiza contrato, signatários e histórico.",
    doc_md=DOCUMENTACAO_DAG,
    tags=TAGS_DAG,
    schedule="*/1 * * * *",
    start_date=pendulum.datetime(2026, 6, 9, tz="America/Sao_Paulo"),
    catchup=False,
    max_active_runs=1,
    default_args={
        "owner": "Euromidia",
        "retries": 1,
        "retry_delay": timedelta(minutes=1),
    },
)
def pipeline_processar_webhook_d4sign():
    @task(task_id="processar_eventos_webhook")
    def processar_eventos_webhook() -> dict[str, Any]:
        """Reservo eventos pendentes e atualizo as tabelas finais."""
        conf = obter_conf_dag()
        id_evento_webhook = converter_int(conf.get("id_evento_webhook"))

        limite_eventos = converter_int(os.getenv("D4SIGN_WEBHOOK_LIMITE_EVENTOS_POR_EXECUCAO", "50")) or 50
        limite_eventos = max(1, min(limite_eventos, 500))

        hook_sql = HookSqlServer()
        engine = hook_sql.obter_engine()

        with engine.begin() as conexao:
            eventos = reservar_eventos(
                conexao,
                id_evento_webhook=id_evento_webhook,
                limite_eventos=limite_eventos,
            )

        logging.info("Eventos reservados para processamento: %s", len(eventos))

        resultados: list[dict[str, Any]] = []
        erros: list[dict[str, Any]] = []

        for evento in eventos:
            id_evento = int(evento["IDFatoContratoD4WebhookEvento"])

            try:
                with engine.begin() as conexao:
                    resultado = processar_evento(conexao, dict(evento))
                    resultados.append(resultado)

            except Exception as erro:
                logging.exception("Erro ao processar webhook D4Sign. id_evento=%s", id_evento)
                erros.append({"id_evento": id_evento, "erro": str(erro)})

                with engine.begin() as conexao:
                    marcar_evento_erro(
                        conexao,
                        id_evento=id_evento,
                        status="ERRO",
                        mensagem=str(erro),
                    )

        resumo = {
            "id_evento_conf": id_evento_webhook,
            "eventos_reservados": len(eventos),
            "eventos_processados": len(resultados),
            "eventos_com_erro": len(erros),
            "resultados": resultados[:50],
            "erros": erros[:20],
        }

        logging.info("Resumo pipeline_processar_webhook_d4sign: %s", resumo)
        return resumo

    processar_eventos_webhook()


pipeline_processar_webhook_d4sign()
