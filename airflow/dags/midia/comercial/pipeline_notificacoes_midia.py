from __future__ import annotations

import html
import json
import logging
import os
import smtplib
import ssl
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from email.message import EmailMessage
from email.utils import formataddr
from typing import Any

import pendulum
from airflow.exceptions import AirflowFailException
from sqlalchemy import bindparam, text

try:
    from airflow.sdk import dag, get_current_context, task
except ImportError:
    from airflow.decorators import dag, task
    from airflow.operators.python import get_current_context

try:
    from airflow.sdk.bases.hook import BaseHook
except ImportError:
    from airflow.hooks.base import BaseHook

from hooks.BancodeDados.SqlServer import HookSqlServer


logger = logging.getLogger(__name__)

DAG_ID = "pipeline_notificacoes_midia"
DAG_ID_CANCELAMENTO = "pipeline_cancela_reserva"
SQLSERVER_CONN_ID = os.getenv(
    "MIDIA_SQLSERVER_CONN_ID", "mssql_integracao"
).strip()
SMTP_CONN_ID = os.getenv("MIDIA_SMTP_CONN_ID", "smtp_default").strip()
SMTP_USUARIO_PADRAO = "notificacoes@midia.com.br"
TIMEZONE_SAO_PAULO = pendulum.timezone("America/Sao_Paulo")

ORIGEM_NOTIFICACAO = "AIRFLOW"
SISTEMA_ORIGEM = "PAINEIS_MIDIA"
MODULO_ORIGEM = "RESERVAS"
TIPO_REFERENCIA = "RESERVA"

EVENTO_CANCELAMENTO_MANUAL = "RESERVA CANCELADA MANUALMENTE"
EVENTO_CANCELAMENTO_AUTOMATICO = "RESERVA CANCELADA AUTOMATICAMENTE"
EVENTO_ALERTA_24_HORAS = "RESERVA PRAZO 24 HORAS"

# Valores usados por execucoes anteriores. Eles permanecem somente para que a
# DAG normalize registros antigos sem perder a idempotencia das notificacoes
# que ja foram enfileiradas.
EVENTO_CANCELAMENTO_MANUAL_LEGADO = "RESERVA_CANCELADA_MANUALMENTE"
EVENTO_CANCELAMENTO_AUTOMATICO_LEGADO = "RESERVA_CANCELADA_AUTOMATICAMENTE"
EVENTO_ALERTA_24_HORAS_LEGADO = "RESERVA_PRAZO_24_HORAS"

EVENTOS_PROCESSADOS = (
    EVENTO_CANCELAMENTO_MANUAL,
    EVENTO_CANCELAMENTO_AUTOMATICO,
    EVENTO_ALERTA_24_HORAS,
)

HORAS_PRAZO_RESERVA = 48
HORAS_ALERTA_RESERVA = 24


def _inteiro_ambiente(nome: str, padrao: int, minimo: int, maximo: int) -> int:
    try:
        valor = int(os.getenv(nome, str(padrao)).strip())
    except (TypeError, ValueError):
        valor = padrao
    return max(minimo, min(valor, maximo))


LOOKBACK_CANCELAMENTO_HORAS = _inteiro_ambiente(
    "MIDIA_NOTIFICACOES_LOOKBACK_CANCELAMENTO_HORAS",
    padrao=24,
    minimo=1,
    maximo=24 * 30,
)
LIMITE_EMAILS_POR_EXECUCAO = _inteiro_ambiente(
    "MIDIA_NOTIFICACOES_LIMITE_EMAILS",
    padrao=100,
    minimo=1,
    maximo=1000,
)


DOC_MD = """
# Pipeline Notificações Midia

## Objetivo

Monitora as reservas da tabela
`Integracao.Silver.FatoOcupacaoPaineisMidia`, cria uma fila idempotente em
`Integracao.Silver.FatoNotificacoes` e envia os e-mails pendentes.

## Eventos

1. **Cancelamento manual**: envia para o usuário de `CanceladoPorIDUsuario`.
2. **Cancelamento automático**: envia para o usuário de `CriadoPorIDUsuario`.
3. **Metade do prazo**: quando uma reserva comum continua aberta após 24 horas,
   avisa o criador de que faltam apenas 24 horas para o fim do prazo de 48
   horas.

Reservas de preferência de renovação não recebem o aviso de 24 horas porque o
DAG `pipeline_cancela_reserva` não aplica a elas a regra comum de 48 horas.

## Idempotência

Cada evento recebe uma `ChaveIdempotencia`. O registro é criado antes do envio e
o e-mail só é marcado com `BitEnviado = 1` depois que o servidor SMTP confirma o
envio. Reexecuções ignoram os registros já enviados.

Quando este DAG é chamado por `pipeline_cancela_reserva`, recebe os IDs
devolvidos pelo `OUTPUT INSERTED` do cancelamento e procura o evento automático
somente nesses registros. A tabela de reservas é lida com isolamento de leitura
confirmada, sem `NOLOCK`, para impedir e-mail baseado em transação ainda não
confirmada.

Os campos de vínculo são gravados assim:

- `Origem = AIRFLOW`
- `SistemaOrigem = PAINEIS_MIDIA`
- `ModuloOrigem = RESERVAS`
- `TipoReferencia = RESERVA`
- `IDReferencia = IDFatoOcupacaoPaineisMidia`

## Configuração

- SQL Server: Airflow Connection `mssql_integracao` ou variável
  `MIDIA_SQLSERVER_CONN_ID`.
- SMTP: usa primeiro as mesmas variáveis do Flask (`SMTP`, `porta`,
  `criptografia` e `SENHA_EMAIL`) e o usuário
  `notificacoes@midia.com.br`. A Airflow Connection `smtp_default` é
  somente fallback para valores que realmente não estiverem no ambiente.
- Na primeira execução, considera cancelamentos ocorridos nas últimas 24 horas;
  esse alcance pode ser alterado por
  `MIDIA_NOTIFICACOES_LOOKBACK_CANCELAMENTO_HORAS`.
- Agendamento: a cada 5 minutos.
"""


SQL_VALIDAR_TABELA_NOTIFICACOES = """
USE [Integracao];

SET NOCOUNT ON;
SET XACT_ABORT ON;

IF OBJECT_ID(N'Silver.FatoNotificacoes', N'U') IS NULL
BEGIN
    THROW 51000, 'A tabela Integracao.Silver.FatoNotificacoes nao existe.', 1;
END;

IF COL_LENGTH(N'Silver.FatoNotificacoes', N'IDFatoNotificacoes') IS NULL
   OR COL_LENGTH(N'Silver.FatoNotificacoes', N'Origem') IS NULL
   OR COL_LENGTH(N'Silver.FatoNotificacoes', N'TipoEvendo') IS NULL
   OR COL_LENGTH(N'Silver.FatoNotificacoes', N'IDUsuarioDestino') IS NULL
   OR COL_LENGTH(N'Silver.FatoNotificacoes', N'EmailDestino') IS NULL
   OR COL_LENGTH(N'Silver.FatoNotificacoes', N'EmailCopia') IS NULL
   OR COL_LENGTH(N'Silver.FatoNotificacoes', N'ChaveIdempotencia') IS NULL
   OR COL_LENGTH(N'Silver.FatoNotificacoes', N'SistemaOrigem') IS NULL
   OR COL_LENGTH(N'Silver.FatoNotificacoes', N'ModuloOrigem') IS NULL
   OR COL_LENGTH(N'Silver.FatoNotificacoes', N'TipoReferencia') IS NULL
   OR COL_LENGTH(N'Silver.FatoNotificacoes', N'IDReferencia') IS NULL
   OR COL_LENGTH(N'Silver.FatoNotificacoes', N'Assunto') IS NULL
   OR COL_LENGTH(N'Silver.FatoNotificacoes', N'CorpoEmail') IS NULL
   OR COL_LENGTH(N'Silver.FatoNotificacoes', N'CorpoTexto') IS NULL
   OR COL_LENGTH(N'Silver.FatoNotificacoes', N'PayloadJson') IS NULL
   OR COL_LENGTH(N'Silver.FatoNotificacoes', N'BitEnviado') IS NULL
   OR COL_LENGTH(N'Silver.FatoNotificacoes', N'DataEmail') IS NULL
BEGIN
    THROW 51000, 'A tabela Silver.FatoNotificacoes existe, mas nao possui todas as colunas esperadas.', 1;
END;

IF NOT EXISTS
(
    SELECT 1
    FROM sys.columns AS coluna
    INNER JOIN sys.types AS tipo
        ON tipo.user_type_id = coluna.user_type_id
    WHERE
        coluna.object_id = OBJECT_ID(N'Silver.FatoNotificacoes')
        AND coluna.name = N'IDReferencia'
        AND tipo.name = N'int'
)
BEGIN
    THROW 51001, 'A coluna Silver.FatoNotificacoes.IDReferencia precisa ser INT.', 1;
END;

IF EXISTS
(
    SELECT 1
    FROM sys.columns AS coluna
    WHERE
        coluna.object_id = OBJECT_ID(N'Silver.FatoNotificacoes')
        AND coluna.name = N'ChaveIdempotencia'
        AND coluna.is_nullable = 1
)
BEGIN
    THROW 51002, 'A coluna Silver.FatoNotificacoes.ChaveIdempotencia precisa ser NOT NULL.', 1;
END;
"""


SQL_NORMALIZAR_EVENTOS_LEGADOS = f"""
UPDATE [Integracao].[Silver].[FatoNotificacoes]
SET
    TipoEvendo = CASE TipoEvendo
        WHEN N'{EVENTO_CANCELAMENTO_MANUAL_LEGADO}'
            THEN N'{EVENTO_CANCELAMENTO_MANUAL}'
        WHEN N'{EVENTO_CANCELAMENTO_AUTOMATICO_LEGADO}'
            THEN N'{EVENTO_CANCELAMENTO_AUTOMATICO}'
        WHEN N'{EVENTO_ALERTA_24_HORAS_LEGADO}'
            THEN N'{EVENTO_ALERTA_24_HORAS}'
        ELSE TipoEvendo
    END,
    PayloadJson = CASE
        WHEN PayloadJson IS NULL THEN NULL
        ELSE REPLACE(
            REPLACE(
                REPLACE(
                    PayloadJson,
                    N'{EVENTO_CANCELAMENTO_MANUAL_LEGADO}',
                    N'{EVENTO_CANCELAMENTO_MANUAL}'
                ),
                N'{EVENTO_CANCELAMENTO_AUTOMATICO_LEGADO}',
                N'{EVENTO_CANCELAMENTO_AUTOMATICO}'
            ),
            N'{EVENTO_ALERTA_24_HORAS_LEGADO}',
            N'{EVENTO_ALERTA_24_HORAS}'
        )
    END,
    ChaveIdempotencia = REPLACE(
        REPLACE(
            REPLACE(
                ChaveIdempotencia,
                '{EVENTO_CANCELAMENTO_MANUAL_LEGADO}',
                '{EVENTO_CANCELAMENTO_MANUAL}'
            ),
            '{EVENTO_CANCELAMENTO_AUTOMATICO_LEGADO}',
            '{EVENTO_CANCELAMENTO_AUTOMATICO}'
        ),
        '{EVENTO_ALERTA_24_HORAS_LEGADO}',
        '{EVENTO_ALERTA_24_HORAS}'
    )
WHERE
    Origem = N'{ORIGEM_NOTIFICACAO}'
    AND SistemaOrigem = N'{SISTEMA_ORIGEM}'
    AND ModuloOrigem = N'{MODULO_ORIGEM}'
    AND TipoEvendo IN
    (
        N'{EVENTO_CANCELAMENTO_MANUAL_LEGADO}',
        N'{EVENTO_CANCELAMENTO_AUTOMATICO_LEGADO}',
        N'{EVENTO_ALERTA_24_HORAS_LEGADO}'
    );
"""


SQL_BUSCAR_EVENTOS = f"""
;WITH BaseReservas AS
(
    SELECT
        reserva.IDFatoOcupacaoPaineisMidia AS IDReserva,
        reserva.Referencia,
        reserva.CodPonto,
        reserva.CodFace,
        reserva.Cota,
        reserva.DataInicio,
        reserva.DataFim,
        reserva.MarcaExibida,
        reserva.Vendedor,
        reserva.CriadoEm,
        COALESCE(
            reserva.ExpiraEm,
            DATEADD(HOUR, {HORAS_PRAZO_RESERVA}, reserva.CriadoEm)
        ) AS ExpiraEmCalculado,
        reserva.CanceladoEm,
        reserva.Observacao,
        reserva.Status,
        reserva.Origem,
        reserva.TipoVinculoOrigem,
        reserva.IDFatoControleContratosItemOrigem,
        COALESCE(reserva.CriadoPorIDUsuario, vendedor.IDDimUsuarios)
            AS IDUsuarioCriador,
        reserva.CanceladoPorIDUsuario AS IDUsuarioCancelador,
        criador.NomeUsuario AS NomeCriador,
        criador.Email AS EmailCriador,
        cancelador.NomeUsuario AS NomeCancelador,
        cancelador.Email AS EmailCancelador,
        CASE
            WHEN
                UPPER(LTRIM(RTRIM(ISNULL(cancelador.NomeUsuario, N''))))
                    COLLATE Latin1_General_CI_AI = N'INTEGRACAO'
                OR
                (
                    UPPER(ISNULL(reserva.Observacao, N''))
                        COLLATE Latin1_General_CI_AI LIKE N'%CANCEL%'
                    AND UPPER(ISNULL(reserva.Observacao, N''))
                        COLLATE Latin1_General_CI_AI LIKE N'%AUTOMATIC%'
                )
            THEN 1
            ELSE 0
        END AS CancelamentoAutomatico,
        CASE
            WHEN
                UPPER(LTRIM(RTRIM(ISNULL(reserva.TipoVinculoOrigem, N''))))
                    COLLATE Latin1_General_CI_AI LIKE N'%PREFERENCIA%RENOVACAO%CONTRATO%'
                OR EXISTS
                (
                    SELECT 1
                    FROM [Integracao].[Silver].[FatoControleContratosItensMidia] AS item_pref
                    WHERE
                        item_pref.IDFatoControleContratosItensMidia =
                            reserva.IDFatoControleContratosItemOrigem
                        AND ISNULL(item_pref.BitPreferencia, 0) = 1
                        AND ISNULL(item_pref.BitAtivo, 1) = 1
                )
            THEN 1
            ELSE 0
        END AS ReservaPreferencia
    FROM [Integracao].[Silver].[FatoOcupacaoPaineisMidia] AS reserva
    LEFT JOIN [Integracao].[dbo].[Vendedores] AS vendedor
        ON vendedor.IDVendedor = reserva.IDVendedor
    LEFT JOIN [Integracao].[Silver].[DimUsuarios] AS criador
        ON criador.IDDimUsuarios =
            COALESCE(reserva.CriadoPorIDUsuario, vendedor.IDDimUsuarios)
    LEFT JOIN [Integracao].[Silver].[DimUsuarios] AS cancelador
        ON cancelador.IDDimUsuarios = reserva.CanceladoPorIDUsuario
    WHERE
        UPPER(LTRIM(RTRIM(ISNULL(reserva.Origem, N''))))
            COLLATE Latin1_General_CI_AI = N'RESERVA'
),
Eventos AS
(
    SELECT
        N'{EVENTO_CANCELAMENTO_MANUAL}' AS TipoEvento,
        base.IDUsuarioCancelador AS IDUsuarioDestino,
        base.EmailCancelador AS EmailDestino,
        base.NomeCancelador AS NomeDestinatario,
        base.*
    FROM BaseReservas AS base
    WHERE
        base.CanceladoEm IS NOT NULL
        AND base.CancelamentoAutomatico = 0
        AND base.IDUsuarioCancelador IS NOT NULL
        AND base.CanceladoEm >= DATEADD(HOUR, :lookback_cancelamento_negativo, SYSDATETIME())

    UNION ALL

    SELECT
        N'{EVENTO_CANCELAMENTO_AUTOMATICO}' AS TipoEvento,
        base.IDUsuarioCriador AS IDUsuarioDestino,
        base.EmailCriador AS EmailDestino,
        base.NomeCriador AS NomeDestinatario,
        base.*
    FROM BaseReservas AS base
    WHERE
        base.CanceladoEm IS NOT NULL
        AND base.CancelamentoAutomatico = 1
        AND base.IDUsuarioCriador IS NOT NULL
        AND
        (
            (
                :filtrar_ids_cancelados = 1
                AND base.IDReserva IN :ids_reservas_canceladas
            )
            OR
            (
                :filtrar_ids_cancelados = 0
                AND base.CanceladoEm >= DATEADD(
                    HOUR,
                    :lookback_cancelamento_negativo,
                    SYSDATETIME()
                )
            )
        )

    UNION ALL

    SELECT
        N'{EVENTO_ALERTA_24_HORAS}' AS TipoEvento,
        base.IDUsuarioCriador AS IDUsuarioDestino,
        base.EmailCriador AS EmailDestino,
        base.NomeCriador AS NomeDestinatario,
        base.*
    FROM BaseReservas AS base
    WHERE
        base.CanceladoEm IS NULL
        AND ISNULL(
                UPPER(LTRIM(RTRIM(base.Status))),
                N''
            ) COLLATE Latin1_General_CI_AI <> N'CANCELADO'
        AND base.ReservaPreferencia = 0
        AND base.CriadoEm IS NOT NULL
        AND SYSDATETIME() >= DATEADD(HOUR, {HORAS_ALERTA_RESERVA}, base.CriadoEm)
        AND SYSDATETIME() < DATEADD(HOUR, {HORAS_PRAZO_RESERVA}, base.CriadoEm)
        AND base.IDUsuarioCriador IS NOT NULL
)
SELECT
    TipoEvento,
    IDReserva,
    IDUsuarioDestino,
    EmailDestino,
    NomeDestinatario,
    Referencia,
    CodPonto,
    CodFace,
    Cota,
    DataInicio,
    DataFim,
    MarcaExibida,
    Vendedor,
    CriadoEm,
    ExpiraEmCalculado,
    CanceladoEm,
    Observacao,
    NomeCriador,
    NomeCancelador,
    CancelamentoAutomatico,
    ReservaPreferencia
FROM Eventos
ORDER BY
    CASE WHEN TipoEvento = N'{EVENTO_ALERTA_24_HORAS}' THEN 1 ELSE 0 END,
    COALESCE(CanceladoEm, CriadoEm),
    IDReserva;
"""


SQL_INSERIR_NOTIFICACAO = """
SET NOCOUNT ON;
SET XACT_ABORT ON;

DECLARE @QuantidadeInserida INT = 0;

IF NOT EXISTS
(
    SELECT 1
    FROM [Integracao].[Silver].[FatoNotificacoes] WITH (UPDLOCK, HOLDLOCK)
    WHERE ChaveIdempotencia = :chave_idempotencia
)
BEGIN
    INSERT INTO [Integracao].[Silver].[FatoNotificacoes]
    (
        Origem,
        TipoEvendo,
        IDUsuarioDestino,
        EmailDestino,
        EmailCopia,
        ChaveIdempotencia,
        SistemaOrigem,
        ModuloOrigem,
        TipoReferencia,
        IDReferencia,
        Assunto,
        CorpoEmail,
        CorpoTexto,
        PayloadJson,
        BitEnviado,
        DataEmail
    )
    VALUES
    (
        :origem,
        :tipo_evento,
        :id_usuario_destino,
        :email_destino,
        :email_copia,
        :chave_idempotencia,
        :sistema_origem,
        :modulo_origem,
        :tipo_referencia,
        :id_referencia,
        :assunto,
        :corpo_email,
        :corpo_texto,
        :payload_json,
        0,
        NULL
    );

    SET @QuantidadeInserida = @@ROWCOUNT;
END;

SELECT @QuantidadeInserida AS QuantidadeInserida;
"""


SQL_LISTAR_NOTIFICACOES_PENDENTES = """
SELECT TOP (:limite_lote)
       IDFatoNotificacoes,
       TipoEvendo,
       IDUsuarioDestino,
       EmailDestino,
       EmailCopia,
       ChaveIdempotencia,
       IDReferencia,
       Assunto,
       CorpoEmail,
       CorpoTexto
FROM [Integracao].[Silver].[FatoNotificacoes] WITH (READPAST)
WHERE
    Origem = :origem
    AND SistemaOrigem = :sistema_origem
    AND ModuloOrigem = :modulo_origem
    AND TipoEvendo IN
    (
        :evento_manual,
        :evento_automatico,
        :evento_alerta
    )
    AND
    (
        :filtrar_ids_cancelados = 0
        OR
        (
            TipoEvendo = :evento_automatico
            AND IDReferencia IN :ids_reservas_canceladas
        )
    )
    AND ISNULL(BitEnviado, 0) = 0
ORDER BY IDFatoNotificacoes;
"""


SQL_MARCAR_NOTIFICACAO_ENVIADA = """
UPDATE [Integracao].[Silver].[FatoNotificacoes]
SET
    BitEnviado = 1,
    DataEmail = SYSDATETIME()
WHERE
    IDFatoNotificacoes = :id_notificacao
    AND ChaveIdempotencia = :chave_idempotencia
    AND ISNULL(BitEnviado, 0) = 0;
"""


def _primeiro_valor_ambiente(*nomes: str, padrao: str | None = None) -> str | None:
    for nome in nomes:
        valor = os.getenv(nome)
        if valor is not None and valor.strip():
            return valor.strip()
    return padrao


def _booleano(valor: Any, padrao: bool = False) -> bool:
    if valor is None:
        return padrao
    return str(valor).strip().lower() in {"1", "true", "t", "yes", "y", "sim", "s"}


@dataclass(frozen=True)
class ConfiguracaoSmtp:
    host: str
    port: int
    username: str | None
    password: str | None
    remetente: str
    nome_remetente: str
    starttls: bool
    usar_ssl: bool
    autenticar: bool
    timeout_segundos: int


def _obter_configuracao_smtp() -> ConfiguracaoSmtp:
    # Mantém a mesma configuração já usada com sucesso pelo Flask. A ordem é
    # intencional: as quatro variáveis existentes no .env devem prevalecer
    # sobre valores antigos ou incorretos cadastrados no smtp_default.
    host = _primeiro_valor_ambiente(
        "SMTP",
        "MIDIA_SMTP_HOST",
        "MAIL_SERVER",
        "SMTP_HOST",
        "SMTP_SERVER",
    )
    port_texto = _primeiro_valor_ambiente(
        "porta", "MIDIA_SMTP_PORT", "MAIL_PORT", "SMTP_PORT"
    )
    username = _primeiro_valor_ambiente(
        "MIDIA_SMTP_USERNAME",
        padrao=SMTP_USUARIO_PADRAO,
    )
    password = _primeiro_valor_ambiente(
        "SENHA_EMAIL",
        "MIDIA_SMTP_PASSWORD",
        "MAIL_PASSWORD",
        "SMTP_PASSWORD",
    )
    remetente = _primeiro_valor_ambiente(
        "MIDIA_SMTP_FROM",
        padrao=username,
    )
    criptografia = _primeiro_valor_ambiente(
        "criptografia", "MIDIA_SMTP_CRIPTOGRAFIA"
    )

    conexao_airflow = None
    origem_configuracao = "variaveis de ambiente"
    if SMTP_CONN_ID:
        try:
            conexao_airflow = BaseHook.get_connection(SMTP_CONN_ID)
        except Exception as erro:
            logger.info(
                "Airflow Connection SMTP '%s' não disponível; serão usadas as variáveis de ambiente. Detalhe: %s",
                SMTP_CONN_ID,
                erro,
            )

    if conexao_airflow is not None:
        # smtp_default é apenas fallback. Em especial, nunca substitui o
        # usuário padrão pela grafia incorreta que existia na Connection
        # ("otificacoes@...", sem o primeiro "n") e nunca passa por cima da
        # SENHA_EMAIL atual do ambiente.
        valores_connection_usados: list[str] = []

        if not host and conexao_airflow.host:
            host = conexao_airflow.host
            valores_connection_usados.append("host")
        if not port_texto and conexao_airflow.port:
            port_texto = str(conexao_airflow.port)
            valores_connection_usados.append("porta")
        if not password and conexao_airflow.password:
            password = conexao_airflow.password
            valores_connection_usados.append("senha")

        if valores_connection_usados:
            origem_configuracao = (
                "variaveis de ambiente com fallback da Airflow Connection "
                f"{SMTP_CONN_ID!r} para {', '.join(valores_connection_usados)}"
            )

    host = host or "smtp.office365.com"
    try:
        port = int(port_texto or "587")
    except ValueError as erro:
        raise AirflowFailException(
            f"Porta SMTP inválida: {port_texto!r}."
        ) from erro

    autenticar = _booleano(
        _primeiro_valor_ambiente("MIDIA_SMTP_AUTH"), padrao=True
    )
    criptografia_normalizada = (criptografia or "").strip().upper()
    padrao_ssl = criptografia_normalizada in {"SSL", "SMTPS"}
    padrao_starttls = (
        criptografia_normalizada in {"STARTTLS", "TLS"}
        if criptografia_normalizada
        else True
    )

    usar_ssl = _booleano(
        _primeiro_valor_ambiente("MIDIA_SMTP_SSL", "MAIL_USE_SSL"),
        padrao=padrao_ssl,
    )
    starttls = _booleano(
        _primeiro_valor_ambiente(
            "MIDIA_SMTP_STARTTLS", "MAIL_USE_TLS", "SMTP_STARTTLS"
        ),
        padrao=padrao_starttls,
    )

    if not remetente:
        remetente = username

    if not remetente:
        raise AirflowFailException(
            "Remetente SMTP não configurado. Defina MIDIA_SMTP_FROM ou MAIL_DEFAULT_SENDER."
        )

    if autenticar and (not username or not password):
        raise AirflowFailException(
            "Senha SMTP não configurada. A DAG procurou primeiro SENHA_EMAIL no "
            ".env/ambiente do Airflow e depois a senha da Airflow Connection "
            f"'{SMTP_CONN_ID}'."
        )

    configuracao = ConfiguracaoSmtp(
        host=host,
        port=port,
        username=username,
        password=password,
        remetente=remetente,
        nome_remetente=_primeiro_valor_ambiente(
            "MIDIA_SMTP_FROM_NAME", padrao="Euromídia"
        )
        or "Euromídia",
        starttls=starttls,
        usar_ssl=usar_ssl,
        autenticar=autenticar,
        timeout_segundos=_inteiro_ambiente(
            "MIDIA_SMTP_TIMEOUT", padrao=30, minimo=5, maximo=180
        ),
    )

    logger.info(
        "Configuracao SMTP carregada de %s | host=%s | porta=%s | login=%s | remetente=%s | STARTTLS=%s | SSL=%s",
        origem_configuracao,
        configuracao.host,
        configuracao.port,
        configuracao.username,
        configuracao.remetente,
        configuracao.starttls,
        configuracao.usar_ssl,
    )
    return configuracao


def _formatar_data_hora(valor: Any) -> str:
    if valor is None:
        return "Não informado"
    if isinstance(valor, datetime):
        return valor.strftime("%d/%m/%Y %H:%M")
    if isinstance(valor, date):
        return valor.strftime("%d/%m/%Y")
    return str(valor)


def _formatar_data(valor: Any) -> str:
    if valor is None:
        return "Não informado"
    if isinstance(valor, (datetime, date)):
        return valor.strftime("%d/%m/%Y")
    return str(valor)


def _texto(valor: Any, padrao: str = "Não informado") -> str:
    if valor is None:
        return padrao
    resultado = str(valor).strip()
    return resultado or padrao


def _html(valor: Any, padrao: str = "Não informado") -> str:
    return html.escape(_texto(valor, padrao), quote=True)


def _montar_chave_idempotencia(evento: dict[str, Any]) -> str:
    tipo_evento = _texto(evento.get("TipoEvento"), "EVENTO")
    partes = [
        SISTEMA_ORIGEM,
        MODULO_ORIGEM,
        tipo_evento,
        str(int(evento["IDReserva"])),
    ]

    if evento.get("TipoEvento") in (
        EVENTO_CANCELAMENTO_MANUAL,
        EVENTO_CANCELAMENTO_AUTOMATICO,
    ):
        cancelado_em = evento.get("CanceladoEm")
        if hasattr(cancelado_em, "isoformat"):
            partes.append(cancelado_em.isoformat(timespec="microseconds"))
        else:
            partes.append(_texto(cancelado_em, "SEM_DATA_CANCELAMENTO"))
    else:
        criado_em = evento.get("CriadoEm")
        if hasattr(criado_em, "isoformat"):
            partes.append(criado_em.isoformat(timespec="microseconds"))

    return "|".join(partes)[:300]


def _conteudo_evento(evento: dict[str, Any]) -> tuple[str, str, str]:
    tipo_evento = evento["TipoEvento"]
    id_reserva = int(evento["IDReserva"])
    nome_destinatario = _texto(evento.get("NomeDestinatario"), "Usuário")

    if tipo_evento == EVENTO_CANCELAMENTO_MANUAL:
        assunto = f"Reserva {id_reserva} foi cancelada com sucesso"
        titulo = "Reserva cancelada"
        chamada = (
            f"Olá, {nome_destinatario}. A reserva {id_reserva} foi cancelada "
            "manualmente por você."
        )
        destaque = "Cancelamento realizado com sucesso"
        cor_destaque = "#DC2626"
    elif tipo_evento == EVENTO_CANCELAMENTO_AUTOMATICO:
        assunto = f"Reserva {id_reserva} foi cancelada com sucesso"
        titulo = "Reserva cancelada automaticamente"
        chamada = (
            f"Olá, {nome_destinatario}. A reserva {id_reserva}, criada por você, "
            "foi cancelada automaticamente pelo sistema."
        )
        destaque = "Prazo ou validade da reserva encerrado"
        cor_destaque = "#DC2626"
    elif tipo_evento == EVENTO_ALERTA_24_HORAS:
        assunto = f"Reserva {id_reserva}: faltam apenas 24 horas para expirar"
        titulo = "O prazo da reserva está acabando"
        chamada = (
            f"Olá, {nome_destinatario}. Faltam apenas 24 horas para o fim do "
            f"prazo da reserva {id_reserva}. Se ela continuar aberta até o fim "
            "do bloqueio, será cancelada automaticamente."
        )
        destaque = "Faltam 24 horas para o cancelamento automático"
        cor_destaque = "#D97706"
    else:
        raise ValueError(f"Tipo de evento não suportado: {tipo_evento!r}")

    motivo = _texto(evento.get("Observacao"), "Conforme a regra da reserva")
    url_base = (
        _primeiro_valor_ambiente(
            "MIDIA_BASE_URL", padrao="http://189.45.251.100:5000"
        )
        or "http://189.45.251.100:5000"
    ).rstrip("/")
    logo_url = _primeiro_valor_ambiente(
        "MIDIA_LOGO_URL",
        padrao=f"{url_base}/static/imagens/logoeuro.png",
    )
    link_reservas = (
        f"{url_base}/admin/vencimentos-campanhas?bi_semana=&dt_ini=&dt_fim="
        "&status=-9001&marca=&vendedor=&q="
    )

    linhas_tabela = (
        ("Nº Reserva", str(id_reserva)),
        ("Vendedor", _texto(evento.get("Vendedor"))),
        ("CodFace", _texto(evento.get("CodFace"))),
        ("Cota", _texto(evento.get("Cota"))),
        ("Início", _formatar_data(evento.get("DataInicio"))),
        ("Fim", _formatar_data(evento.get("DataFim"))),
        ("Marca", _texto(evento.get("MarcaExibida"))),
        ("Fim do bloqueio", _formatar_data_hora(evento.get("ExpiraEmCalculado"))),
    )
    linhas_html = "".join(
        "<tr>"
        f"<td style='padding:10px 12px;border:1px solid #E5E7EB;font-weight:600;"
        f"color:#374151;background:#F9FAFB'>{_html(rotulo)}</td>"
        f"<td style='padding:10px 12px;border:1px solid #E5E7EB;color:#111827'>"
        f"{_html(valor)}</td>"
        "</tr>"
        for rotulo, valor in linhas_tabela
    )

    if tipo_evento in (
        EVENTO_CANCELAMENTO_MANUAL,
        EVENTO_CANCELAMENTO_AUTOMATICO,
    ):
        detalhe_extra_html = (
            "<p style='margin:18px 0 0;color:#4B5563;font-size:14px;line-height:1.6'>"
            f"<strong>Data do cancelamento:</strong> {_html(_formatar_data_hora(evento.get('CanceladoEm')))}"
            "</p>"
        )
        if tipo_evento == EVENTO_CANCELAMENTO_AUTOMATICO:
            detalhe_extra_html += (
                "<p style='margin:8px 0 0;color:#4B5563;font-size:14px;line-height:1.6'>"
                f"<strong>Motivo registrado:</strong> {_html(motivo)}"
                "</p>"
            )
    else:
        detalhe_extra_html = ""

    # Em clientes de e-mail (principalmente Outlook), apenas max-width/max-height
    # pode ser ignorado. Por isso o tamanho é definido também no atributo HTML
    # width e o logo fica dentro de uma célula própria, alinhada à direita.
    logo_html = (
        f"<img src='{html.escape(logo_url, quote=True)}' alt='Euromídia Comunicação' "
        "width='165' "
        "style='display:block;width:165px;max-width:165px;height:auto;"
        "border:0;outline:none;text-decoration:none;margin:0 0 0 auto'>"
        if logo_url
        else (
            "<div style='font-size:20px;font-weight:800;color:#FFFFFF;"
            "text-align:right;white-space:nowrap'>EUROMÍDIA</div>"
        )
    )

    corpo_email = f"""<!doctype html>
<html lang="pt-BR">
<body style="margin:0;padding:0;background:#F3F4F6;font-family:Arial,Helvetica,sans-serif">
  <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background:#F3F4F6;padding:24px 12px">
    <tr>
      <td align="center">
        <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="max-width:680px;background:#FFFFFF;border-radius:12px;overflow:hidden;box-shadow:0 4px 18px rgba(0,0,0,.08)">
          <tr>
            <td style="height:8px;background:#FFD000;font-size:0;line-height:0">&nbsp;</td>
          </tr>
          <tr>
            <td style="padding:20px 28px;background:#0B0C8F">
              <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="width:100%;border-collapse:collapse">
                <tr>
                  <td align="left" valign="middle" style="padding-right:18px">
                    <div style="margin:0;color:#FFFFFF;font-size:24px;line-height:1.2;font-weight:800">{_html(titulo)}</div>
                    <div style="margin-top:5px;color:#FFFFFF;font-size:12px;line-height:1.4;font-weight:600;opacity:.95">Notificação automática do sistema de reservas</div>
                  </td>
                  <td align="right" valign="middle" width="180" style="width:180px;min-width:180px">{logo_html}</td>
                </tr>
              </table>
            </td>
          </tr>
          <tr>
            <td style="padding:30px 32px">
              <p style="margin:0;color:#4B5563;font-size:16px;line-height:1.65">{_html(chamada)}</p>
              <div style="margin:22px 0;padding:14px 16px;border-left:5px solid {cor_destaque};background:#F9FAFB;color:#1F2937;font-weight:700">{_html(destaque)}</div>
              <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="border-collapse:collapse;font-size:14px">{linhas_html}</table>
              {detalhe_extra_html}
              <div style="margin-top:24px;text-align:center">
                <a href="{html.escape(link_reservas, quote=True)}" style="display:inline-block;padding:12px 20px;border-radius:7px;background:#4B4BDB;color:#FFFFFF;text-decoration:none;font-weight:700">Ver reservas</a>
              </div>
            </td>
          </tr>
          <tr>
            <td style="padding:18px 24px;background:#F9FAFB;color:#6B7280;font-size:12px;text-align:center">Mensagem automática do sistema de Painéis Euromídia.</td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>"""

    corpo_texto_linhas = [
        titulo,
        "",
        chamada,
        "",
        destaque,
        *(f"{rotulo}: {valor}" for rotulo, valor in linhas_tabela),
    ]
    if tipo_evento in (
        EVENTO_CANCELAMENTO_MANUAL,
        EVENTO_CANCELAMENTO_AUTOMATICO,
    ):
        corpo_texto_linhas.append(
            f"Data do cancelamento: {_formatar_data_hora(evento.get('CanceladoEm'))}"
        )
    if tipo_evento == EVENTO_CANCELAMENTO_AUTOMATICO:
        corpo_texto_linhas.append(f"Motivo registrado: {motivo}")
    corpo_texto_linhas.extend(["", f"Ver reservas: {link_reservas}"])

    return assunto, corpo_email, "\n".join(corpo_texto_linhas)


def _separar_emails(valor: Any) -> list[str]:
    if valor is None:
        return []
    candidatos = str(valor).replace(";", ",").split(",")
    emails: list[str] = []
    for candidato in candidatos:
        email = candidato.strip()
        if email and "@" in email and email not in emails:
            emails.append(email)
    return emails


def _enviar_email_smtp(
    configuracao: ConfiguracaoSmtp,
    email_destino: str,
    email_copia: str | None,
    assunto: str,
    corpo_texto: str,
    corpo_email: str,
) -> None:
    destinatarios = _separar_emails(email_destino)
    destinatarios_normalizados = {email.lower() for email in destinatarios}
    copias = [
        email
        for email in _separar_emails(email_copia)
        if email.lower() not in destinatarios_normalizados
    ]

    if not destinatarios:
        raise ValueError("A notificação não possui EmailDestino válido.")

    mensagem = EmailMessage()
    mensagem["Subject"] = assunto
    mensagem["From"] = formataddr(
        (configuracao.nome_remetente, configuracao.remetente)
    )
    mensagem["To"] = ", ".join(destinatarios)
    if copias:
        mensagem["Cc"] = ", ".join(copias)
    mensagem.set_content(corpo_texto or "Notificação Euromídia.")
    mensagem.add_alternative(corpo_email, subtype="html")

    contexto_ssl = ssl.create_default_context()
    if configuracao.usar_ssl:
        servidor_smtp = smtplib.SMTP_SSL(
            configuracao.host,
            configuracao.port,
            timeout=configuracao.timeout_segundos,
            context=contexto_ssl,
        )
    else:
        servidor_smtp = smtplib.SMTP(
            configuracao.host,
            configuracao.port,
            timeout=configuracao.timeout_segundos,
        )

    with servidor_smtp as servidor:
        servidor.ehlo()
        if configuracao.starttls and not configuracao.usar_ssl:
            servidor.starttls(context=contexto_ssl)
            servidor.ehlo()
        if configuracao.autenticar:
            servidor.login(configuracao.username, configuracao.password)
        recusados = servidor.send_message(
            mensagem,
            to_addrs=[*destinatarios, *copias],
        )
        if recusados:
            raise smtplib.SMTPRecipientsRefused(recusados)


def _json_padrao(valor: Any) -> str:
    if hasattr(valor, "isoformat"):
        return valor.isoformat()
    return str(valor)


def _normalizar_ids_reserva(valor: Any) -> list[int]:
    if valor is None:
        return []

    valor_normalizado = valor
    if isinstance(valor, str):
        texto_ids = valor.strip()
        if not texto_ids:
            return []
        try:
            valor_normalizado = json.loads(texto_ids)
        except json.JSONDecodeError:
            valor_normalizado = [
                parte.strip()
                for parte in texto_ids.strip("[](){}").split(",")
                if parte.strip()
            ]

    if not isinstance(valor_normalizado, (list, tuple, set)):
        valor_normalizado = [valor_normalizado]

    ids_normalizados: list[int] = []
    for item in valor_normalizado:
        try:
            id_reserva = int(str(item).strip())
        except (TypeError, ValueError):
            continue

        if id_reserva > 0 and id_reserva not in ids_normalizados:
            ids_normalizados.append(id_reserva)

    return ids_normalizados


def _obter_filtro_do_disparo() -> tuple[bool, list[int], str]:
    contexto = get_current_context()
    dag_run = contexto.get("dag_run")
    conf = getattr(dag_run, "conf", None) or {}
    origem_disparo = str(conf.get("origem_disparo") or "").strip()

    if origem_disparo != DAG_ID_CANCELAMENTO:
        return False, [0], origem_disparo or "AGENDAMENTO"

    ids_reservas_canceladas = _normalizar_ids_reserva(
        conf.get("ids_reservas_canceladas")
    )
    if not ids_reservas_canceladas:
        raise AirflowFailException(
            f"O DAG {DAG_ID_CANCELAMENTO} disparou {DAG_ID}, mas não informou "
            "nenhum ID de reserva efetivamente cancelado. O envio foi interrompido."
        )

    logger.info(
        "Disparo recebido de %s para as reservas canceladas: %s.",
        origem_disparo,
        ids_reservas_canceladas,
    )

    return True, ids_reservas_canceladas, origem_disparo


@dag(
    dag_id=DAG_ID,
    description=(
        "Pipeline Notificações Midia: envia alertas de 24h e e-mails de "
        "cancelamento manual ou automático de reservas."
    ),
    schedule="*/5 * * * *",
    start_date=pendulum.datetime(2026, 7, 14, 0, 0, tz=TIMEZONE_SAO_PAULO),
    catchup=False,
    # Permite que o disparo vindo do cancelamento comece mesmo quando existir
    # uma execução agendada do monitor de 24 horas. A tarefa de envio abaixo é
    # serializada entre DagRuns para evitar dois processos enviando a mesma
    # notificação simultaneamente.
    max_active_runs=2,
    dagrun_timeout=timedelta(minutes=10),
    tags=[
        "midia",
        "paineis",
        "reservas",
        "notificacoes",
        "email",
        "sql-server",
    ],
    doc_md=DOC_MD,
    default_args={
        "owner": "integracao",
        "retries": 2,
        "retry_delay": timedelta(minutes=1),
    },
)
def pipeline_notificacoes_midia():
    @task(task_id="preparar_notificacoes")
    def preparar_notificacoes() -> dict[str, Any]:
        hook_sql_server = HookSqlServer(conn_id=SQLSERVER_CONN_ID)
        engine = hook_sql_server.obter_engine()
        email_copia = _primeiro_valor_ambiente("MIDIA_EMAIL_COPIA")
        (
            filtrar_ids_cancelados,
            ids_reservas_canceladas,
            origem_disparo,
        ) = _obter_filtro_do_disparo()

        consulta_buscar_eventos = text(SQL_BUSCAR_EVENTOS).bindparams(
            bindparam("ids_reservas_canceladas", expanding=True)
        )

        try:
            with engine.begin() as conexao:
                conexao.execute(text(SQL_VALIDAR_TABELA_NOTIFICACOES))
                normalizadas = conexao.execute(
                    text(SQL_NORMALIZAR_EVENTOS_LEGADOS)
                ).rowcount
                if int(normalizadas or 0) > 0:
                    logger.info(
                        "%s notificacao(oes) antiga(s) tiveram o tipo de evento normalizado para o formato com espacos.",
                        normalizadas,
                    )

                eventos = [
                    dict(linha)
                    for linha in conexao.execute(
                        consulta_buscar_eventos,
                        {
                            "lookback_cancelamento_negativo": -LOOKBACK_CANCELAMENTO_HORAS,
                            "filtrar_ids_cancelados": int(filtrar_ids_cancelados),
                            "ids_reservas_canceladas": ids_reservas_canceladas,
                        },
                    ).mappings().all()
                ]

                if filtrar_ids_cancelados:
                    ids_eventos_cancelamento = {
                        int(evento["IDReserva"])
                        for evento in eventos
                        if evento.get("TipoEvento")
                        == EVENTO_CANCELAMENTO_AUTOMATICO
                    }
                    ids_sem_evento = sorted(
                        set(ids_reservas_canceladas) - ids_eventos_cancelamento
                    )
                    if ids_sem_evento:
                        raise AirflowFailException(
                            "O cancelamento foi confirmado, mas não foi possível "
                            "preparar a notificação automática para as reservas: "
                            f"{ids_sem_evento}. Verifique o criador, o e-mail e os "
                            "vínculos de usuário dessas reservas."
                        )

                inseridas = 0
                por_tipo = {evento: 0 for evento in EVENTOS_PROCESSADOS}

                for evento in eventos:
                    tipo_evento = str(evento["TipoEvento"])
                    chave_idempotencia = _montar_chave_idempotencia(evento)
                    assunto, corpo_email, corpo_texto = _conteudo_evento(evento)
                    payload_json = json.dumps(
                        evento,
                        ensure_ascii=False,
                        default=_json_padrao,
                    )

                    quantidade = conexao.execute(
                        text(SQL_INSERIR_NOTIFICACAO),
                        {
                            "origem": ORIGEM_NOTIFICACAO,
                            "tipo_evento": tipo_evento,
                            "id_usuario_destino": evento.get("IDUsuarioDestino"),
                            "email_destino": evento.get("EmailDestino"),
                            "email_copia": email_copia,
                            "chave_idempotencia": chave_idempotencia,
                            "sistema_origem": SISTEMA_ORIGEM,
                            "modulo_origem": MODULO_ORIGEM,
                            "tipo_referencia": TIPO_REFERENCIA,
                            "id_referencia": int(evento["IDReserva"]),
                            "assunto": assunto,
                            "corpo_email": corpo_email,
                            "corpo_texto": corpo_texto,
                            "payload_json": payload_json,
                        },
                    ).scalar_one()

                    if int(quantidade or 0) > 0:
                        inseridas += 1
                        por_tipo[tipo_evento] = por_tipo.get(tipo_evento, 0) + 1

            resumo = {
                "eventos_encontrados": len(eventos),
                "notificacoes_inseridas": inseridas,
                "notificacoes_ja_existentes": len(eventos) - inseridas,
                "inseridas_por_tipo": por_tipo,
                "lookback_cancelamento_horas": LOOKBACK_CANCELAMENTO_HORAS,
                "origem_disparo": origem_disparo,
                "filtro_cancelamentos_confirmados": filtrar_ids_cancelados,
                "ids_cancelamentos_confirmados": (
                    ids_reservas_canceladas if filtrar_ids_cancelados else []
                ),
            }
            logger.info("Preparação das notificações concluída: %s", resumo)
            return resumo
        finally:
            engine.dispose()

    @task(
        task_id="enviar_notificacoes_pendentes",
        max_active_tis_per_dag=1,
    )
    def enviar_notificacoes_pendentes(
        resumo_preparacao: dict[str, Any],
    ) -> dict[str, Any]:
        filtrar_ids_cancelados = bool(
            resumo_preparacao.get("filtro_cancelamentos_confirmados")
        )
        ids_reservas_canceladas = _normalizar_ids_reserva(
            resumo_preparacao.get("ids_cancelamentos_confirmados")
        )
        if filtrar_ids_cancelados and not ids_reservas_canceladas:
            raise AirflowFailException(
                "O envio sincronizado foi solicitado sem IDs de cancelamento confirmados."
            )

        if not ids_reservas_canceladas:
            ids_reservas_canceladas = [0]

        limite_lote = (
            max(LIMITE_EMAILS_POR_EXECUCAO, len(ids_reservas_canceladas))
            if filtrar_ids_cancelados
            else LIMITE_EMAILS_POR_EXECUCAO
        )
        consulta_pendentes = text(SQL_LISTAR_NOTIFICACOES_PENDENTES).bindparams(
            bindparam("ids_reservas_canceladas", expanding=True)
        )

        hook_sql_server = HookSqlServer(conn_id=SQLSERVER_CONN_ID)
        engine = hook_sql_server.obter_engine()

        try:
            with engine.begin() as conexao:
                normalizadas = conexao.execute(
                    text(SQL_NORMALIZAR_EVENTOS_LEGADOS)
                ).rowcount
                if int(normalizadas or 0) > 0:
                    logger.info(
                        "%s notificacao(oes) pendente(s) tiveram o tipo de evento normalizado antes do envio.",
                        normalizadas,
                    )

                pendentes = [
                    dict(linha)
                    for linha in conexao.execute(
                        consulta_pendentes,
                        {
                            "limite_lote": limite_lote,
                            "origem": ORIGEM_NOTIFICACAO,
                            "sistema_origem": SISTEMA_ORIGEM,
                            "modulo_origem": MODULO_ORIGEM,
                            "evento_manual": EVENTO_CANCELAMENTO_MANUAL,
                            "evento_automatico": EVENTO_CANCELAMENTO_AUTOMATICO,
                            "evento_alerta": EVENTO_ALERTA_24_HORAS,
                            "filtrar_ids_cancelados": int(
                                filtrar_ids_cancelados
                            ),
                            "ids_reservas_canceladas": ids_reservas_canceladas,
                        },
                    ).mappings().all()
                ]

            if not pendentes:
                resumo = {"pendentes": 0, "enviadas": 0, "falhas": 0}
                logger.info("Não há notificações de reserva pendentes. %s", resumo)
                return resumo

            configuracao_smtp = _obter_configuracao_smtp()
            enviadas = 0
            falhas: list[dict[str, Any]] = []

            for notificacao in pendentes:
                id_notificacao = int(notificacao["IDFatoNotificacoes"])
                chave_idempotencia = str(notificacao["ChaveIdempotencia"])

                try:
                    _enviar_email_smtp(
                        configuracao=configuracao_smtp,
                        email_destino=_texto(notificacao.get("EmailDestino"), ""),
                        email_copia=notificacao.get("EmailCopia"),
                        assunto=_texto(notificacao.get("Assunto"), "Notificação Euromídia"),
                        corpo_texto=_texto(notificacao.get("CorpoTexto"), ""),
                        corpo_email=_texto(
                            notificacao.get("CorpoEmail"),
                            "<p>Notificação Euromídia.</p>",
                        ),
                    )

                    with engine.begin() as conexao:
                        resultado = conexao.execute(
                            text(SQL_MARCAR_NOTIFICACAO_ENVIADA),
                            {
                                "id_notificacao": id_notificacao,
                                "chave_idempotencia": chave_idempotencia,
                            },
                        )

                    if resultado.rowcount == 0:
                        raise RuntimeError(
                            "O SMTP confirmou o envio, mas a notificação não pôde ser marcada como enviada."
                        )

                    enviadas += 1
                    logger.info(
                        "Notificação enviada: id=%s, tipo=%s, reserva=%s.",
                        id_notificacao,
                        notificacao.get("TipoEvendo"),
                        notificacao.get("IDReferencia"),
                    )
                except Exception as erro:
                    logger.exception(
                        "Falha ao enviar a notificação id=%s, tipo=%s, reserva=%s.",
                        id_notificacao,
                        notificacao.get("TipoEvendo"),
                        notificacao.get("IDReferencia"),
                    )
                    falhas.append(
                        {
                            "id_notificacao": id_notificacao,
                            "tipo_evento": notificacao.get("TipoEvendo"),
                            "id_reserva": notificacao.get("IDReferencia"),
                            "erro": str(erro)[:500],
                        }
                    )

            resumo = {
                "pendentes": len(pendentes),
                "enviadas": enviadas,
                "falhas": len(falhas),
                "detalhes_falhas": falhas,
            }
            logger.info("Envio das notificações concluído: %s", resumo)

            if falhas:
                raise AirflowFailException(
                    f"{len(falhas)} notificação(ões) não foram enviadas. "
                    "Os registros continuam com BitEnviado = 0 para nova tentativa. "
                    f"IDs: {[item['id_notificacao'] for item in falhas]}"
                )

            return resumo
        finally:
            engine.dispose()

    enviar_notificacoes_pendentes(preparar_notificacoes())


pipeline_notificacoes_midia()
