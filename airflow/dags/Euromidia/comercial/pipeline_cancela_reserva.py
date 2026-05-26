from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

import pendulum
from airflow.exceptions import AirflowFailException
from sqlalchemy import text

try:
    from airflow.sdk import dag, task
except ImportError:
    from airflow.decorators import dag, task

from hooks.BancodeDados.SqlServer import HookSqlServer


logger = logging.getLogger(__name__)

NOME_DAG = "pipeline_cancela_reserva"
CONN_ID_SQL_SERVER = "mssql_integracao"
TIMEZONE_SAO_PAULO = pendulum.timezone("America/Sao_Paulo")

NOME_USUARIO_INTEGRACAO = "INTEGRACAO"
HORAS_MINIMAS_RESERVA_ABERTA = 48


SQL_BUSCAR_USUARIO_INTEGRACAO = """
SELECT TOP (1)
       usr.IDDimUsuarios,
       usr.NomeUsuario,
       usr.Email
FROM [Integracao].[Silver].[DimUsuarios] AS usr WITH (NOLOCK)
WHERE
    usr.BitAtivo = 1
    AND UPPER(LTRIM(RTRIM(usr.NomeUsuario))) COLLATE Latin1_General_CI_AI = :nome_usuario_integracao
ORDER BY
    usr.IDDimUsuarios;
"""


SQL_CONDICAO_RESERVA_ABERTA = """
    UPPER(LTRIM(RTRIM(ISNULL(reserva.Origem, N'')))) COLLATE Latin1_General_CI_AI = N'RESERVA'
    AND reserva.CanceladoEm IS NULL
    AND ISNULL(UPPER(LTRIM(RTRIM(reserva.Status))), N'') COLLATE Latin1_General_CI_AI <> N'CANCELADO'
"""


SQL_CONDICAO_ITEM_CORRESPONDENTE = """
        (
            item_pref.IDFatoControleContratosItensEuromidia = reserva.IDFatoControleContratosItemOrigem
            OR (
                reserva.IDFatoControleContratosItemOrigem IS NULL
                AND reserva.IDFatoControleContratos IS NOT NULL
                AND item_pref.IDFatoControleContratoEuromidia = reserva.IDFatoControleContratos
                AND UPPER(LTRIM(RTRIM(ISNULL(item_pref.CodPonto, N'')))) COLLATE Latin1_General_CI_AI =
                    UPPER(LTRIM(RTRIM(ISNULL(reserva.CodPonto, N'')))) COLLATE Latin1_General_CI_AI
                AND UPPER(LTRIM(RTRIM(ISNULL(item_pref.CodFace, N'')))) COLLATE Latin1_General_CI_AI =
                    UPPER(LTRIM(RTRIM(ISNULL(reserva.CodFace, N'')))) COLLATE Latin1_General_CI_AI
                AND (
                    reserva.DataInicio IS NULL
                    OR reserva.DataFim IS NULL
                    OR (
                        item_pref.DataInicioPrevisto <= reserva.DataFim
                        AND item_pref.DataTerminoPrevisto >= reserva.DataInicio
                    )
                )
            )
        )
"""


SQL_EXISTE_ITEM_PREFERENCIA_NAO_VENCIDO = f"""
EXISTS (
    SELECT 1
    FROM [Integracao].[Silver].[FatoControleContratosItensEuromidia] AS item_pref WITH (NOLOCK)
    WHERE
        {SQL_CONDICAO_ITEM_CORRESPONDENTE}
        AND ISNULL(item_pref.BitPreferencia, 0) = 1
        AND ISNULL(item_pref.BitAtivo, 1) = 1
        AND item_pref.DataTerminoPrevisto IS NOT NULL
        AND CAST(item_pref.DataTerminoPrevisto AS date) >= CAST(SYSDATETIME() AS date)
)
"""


SQL_EXISTE_ITEM_PREFERENCIA_VENCIDO = f"""
EXISTS (
    SELECT 1
    FROM [Integracao].[Silver].[FatoControleContratosItensEuromidia] AS item_pref WITH (NOLOCK)
    WHERE
        {SQL_CONDICAO_ITEM_CORRESPONDENTE}
        AND ISNULL(item_pref.BitPreferencia, 0) = 1
        AND ISNULL(item_pref.BitAtivo, 1) = 1
        AND item_pref.DataTerminoPrevisto IS NOT NULL
        AND CAST(item_pref.DataTerminoPrevisto AS date) < CAST(SYSDATETIME() AS date)
)
"""


SQL_CONDICAO_RESERVA_ELEGIVEL_CANCELAMENTO = f"""
{SQL_CONDICAO_RESERVA_ABERTA}
AND (
    (
        reserva.CriadoEm IS NOT NULL
        AND reserva.CriadoEm <= DATEADD(HOUR, -{HORAS_MINIMAS_RESERVA_ABERTA}, SYSDATETIME())
        AND NOT {SQL_EXISTE_ITEM_PREFERENCIA_NAO_VENCIDO}
    )
    OR (
        {SQL_EXISTE_ITEM_PREFERENCIA_VENCIDO}
        AND NOT {SQL_EXISTE_ITEM_PREFERENCIA_NAO_VENCIDO}
    )
)
"""


SQL_MOTIVO_CANCELAMENTO = f"""
CASE
    WHEN {SQL_EXISTE_ITEM_PREFERENCIA_VENCIDO}
         AND NOT {SQL_EXISTE_ITEM_PREFERENCIA_NAO_VENCIDO}
        THEN N'PREFERENCIA_RENOVACAO_VENCIDA'
    ELSE N'RESERVA_COMUM_MAIS_DE_48H'
END
"""


SQL_OBSERVACAO_CANCELAMENTO = f"""
CASE
    WHEN {SQL_EXISTE_ITEM_PREFERENCIA_VENCIDO}
         AND NOT {SQL_EXISTE_ITEM_PREFERENCIA_NAO_VENCIDO}
        THEN N'Reserva de preferência de renovação cancelada automaticamente após vencimento do item de contrato vinculado.'
    ELSE N'Reserva cancelada automaticamente após mais de 48 horas aberta.'
END
"""


SQL_CONTAR_RESERVAS_ELEGIVEIS_CANCELAMENTO = f"""
SELECT
    COUNT(1) AS TotalElegivel
FROM [Integracao].[Silver].[FatoOcupacaoPaineisEuromidia] AS reserva WITH (NOLOCK)
WHERE
{SQL_CONDICAO_RESERVA_ELEGIVEL_CANCELAMENTO};
"""


SQL_CONTAR_RESERVAS_PREFERENCIA_PROTEGIDAS = f"""
SELECT
    COUNT(1) AS TotalProtegido
FROM [Integracao].[Silver].[FatoOcupacaoPaineisEuromidia] AS reserva WITH (NOLOCK)
WHERE
{SQL_CONDICAO_RESERVA_ABERTA}
AND {SQL_EXISTE_ITEM_PREFERENCIA_NAO_VENCIDO};
"""


SQL_CONTAR_RESERVAS_PREFERENCIA_VENCIDAS = f"""
SELECT
    COUNT(1) AS TotalPreferenciaVencida
FROM [Integracao].[Silver].[FatoOcupacaoPaineisEuromidia] AS reserva WITH (NOLOCK)
WHERE
{SQL_CONDICAO_RESERVA_ABERTA}
AND {SQL_EXISTE_ITEM_PREFERENCIA_VENCIDO}
AND NOT {SQL_EXISTE_ITEM_PREFERENCIA_NAO_VENCIDO};
"""


SQL_LISTAR_AMOSTRA_RESERVAS_ELEGIVEIS_CANCELAMENTO = f"""
SELECT TOP (30)
       reserva.IDFatoOcupacaoPaineisEuromidia,
       reserva.Referencia,
       reserva.CodPonto,
       reserva.CodFace,
       reserva.MarcaExibida,
       reserva.Status,
       reserva.Origem,
       reserva.CriadoEm,
       reserva.ExpiraEm,
       reserva.DataInicio,
       reserva.DataFim,
       reserva.IDFatoControleContratos,
       reserva.IDFatoControleContratosItemOrigem,
       item_amostra.IDFatoControleContratosItensEuromidia AS IDItemContratoPreferencia,
       item_amostra.BitPreferencia,
       item_amostra.BitAtivo AS BitAtivoItemContrato,
       item_amostra.DataInicioPrevisto AS DataInicioPrevistoItemContrato,
       item_amostra.DataTerminoPrevisto AS DataTerminoPrevistoItemContrato,
       {SQL_MOTIVO_CANCELAMENTO} AS MotivoCancelamento,
       CAST(DATEDIFF(MINUTE, reserva.CriadoEm, SYSDATETIME()) / 60.0 AS DECIMAL(18, 2)) AS HorasDesdeCriacao
FROM [Integracao].[Silver].[FatoOcupacaoPaineisEuromidia] AS reserva WITH (NOLOCK)
OUTER APPLY (
    SELECT TOP (1)
           item_pref.IDFatoControleContratosItensEuromidia,
           item_pref.BitPreferencia,
           item_pref.BitAtivo,
           item_pref.DataInicioPrevisto,
           item_pref.DataTerminoPrevisto
    FROM [Integracao].[Silver].[FatoControleContratosItensEuromidia] AS item_pref WITH (NOLOCK)
    WHERE
        {SQL_CONDICAO_ITEM_CORRESPONDENTE}
        AND ISNULL(item_pref.BitPreferencia, 0) = 1
    ORDER BY
        CASE
            WHEN item_pref.IDFatoControleContratosItensEuromidia = reserva.IDFatoControleContratosItemOrigem THEN 0
            ELSE 1
        END,
        item_pref.DataTerminoPrevisto DESC,
        item_pref.IDFatoControleContratosItensEuromidia DESC
) AS item_amostra
WHERE
{SQL_CONDICAO_RESERVA_ELEGIVEL_CANCELAMENTO}
ORDER BY
    CASE
        WHEN {SQL_EXISTE_ITEM_PREFERENCIA_VENCIDO}
             AND NOT {SQL_EXISTE_ITEM_PREFERENCIA_NAO_VENCIDO}
            THEN 0
        ELSE 1
    END,
    reserva.CriadoEm ASC,
    reserva.IDFatoOcupacaoPaineisEuromidia ASC;
"""


SQL_LISTAR_AMOSTRA_RESERVAS_PREFERENCIA_PROTEGIDAS = f"""
SELECT TOP (30)
       reserva.IDFatoOcupacaoPaineisEuromidia,
       reserva.Referencia,
       reserva.CodPonto,
       reserva.CodFace,
       reserva.MarcaExibida,
       reserva.Status,
       reserva.Origem,
       reserva.CriadoEm,
       reserva.DataInicio,
       reserva.DataFim,
       reserva.IDFatoControleContratos,
       reserva.IDFatoControleContratosItemOrigem,
       item_amostra.IDFatoControleContratosItensEuromidia AS IDItemContratoPreferencia,
       item_amostra.DataInicioPrevisto AS DataInicioPrevistoItemContrato,
       item_amostra.DataTerminoPrevisto AS DataTerminoPrevistoItemContrato,
       CAST(DATEDIFF(MINUTE, reserva.CriadoEm, SYSDATETIME()) / 60.0 AS DECIMAL(18, 2)) AS HorasDesdeCriacao
FROM [Integracao].[Silver].[FatoOcupacaoPaineisEuromidia] AS reserva WITH (NOLOCK)
OUTER APPLY (
    SELECT TOP (1)
           item_pref.IDFatoControleContratosItensEuromidia,
           item_pref.DataInicioPrevisto,
           item_pref.DataTerminoPrevisto
    FROM [Integracao].[Silver].[FatoControleContratosItensEuromidia] AS item_pref WITH (NOLOCK)
    WHERE
        {SQL_CONDICAO_ITEM_CORRESPONDENTE}
        AND ISNULL(item_pref.BitPreferencia, 0) = 1
        AND ISNULL(item_pref.BitAtivo, 1) = 1
        AND item_pref.DataTerminoPrevisto IS NOT NULL
        AND CAST(item_pref.DataTerminoPrevisto AS date) >= CAST(SYSDATETIME() AS date)
    ORDER BY
        CASE
            WHEN item_pref.IDFatoControleContratosItensEuromidia = reserva.IDFatoControleContratosItemOrigem THEN 0
            ELSE 1
        END,
        item_pref.DataTerminoPrevisto DESC,
        item_pref.IDFatoControleContratosItensEuromidia DESC
) AS item_amostra
WHERE
{SQL_CONDICAO_RESERVA_ABERTA}
AND {SQL_EXISTE_ITEM_PREFERENCIA_NAO_VENCIDO}
ORDER BY
    item_amostra.DataTerminoPrevisto ASC,
    reserva.CriadoEm ASC,
    reserva.IDFatoOcupacaoPaineisEuromidia ASC;
"""


SQL_CANCELAR_RESERVAS_ELEGIVEIS = f"""
UPDATE reserva
SET
    reserva.CanceladoEm = SYSDATETIME(),
    reserva.CanceladoPorIDUsuario = :id_usuario_integracao,
    reserva.Status = N'CANCELADO',
    reserva.Observacao = {SQL_OBSERVACAO_CANCELAMENTO},
    reserva.DataAtualizacao = SYSDATETIME()
FROM [Integracao].[Silver].[FatoOcupacaoPaineisEuromidia] AS reserva WITH (UPDLOCK, READPAST, ROWLOCK)
WHERE
{SQL_CONDICAO_RESERVA_ELEGIVEL_CANCELAMENTO};
"""


SQL_OBTER_DATA_HORA_SQL_SERVER = f"""
SELECT
    SYSDATETIME() AS DataExecucaoSqlServer,
    DATEADD(HOUR, -{HORAS_MINIMAS_RESERVA_ABERTA}, SYSDATETIME()) AS LimiteCriacaoReservaComum,
    CAST(SYSDATETIME() AS date) AS DataReferenciaVencimentoPreferencia;
"""


DOC_MD = """
# pipeline_cancela_reserva

## Objetivo

Cancelar automaticamente reservas antigas da tabela:

`Integracao.Silver.FatoOcupacaoPaineisEuromidia`

## Regra correta de cancelamento

O DAG trabalha com duas regras:

### 1. Reserva comum

Cancela quando:

- `Origem = 'RESERVA'`
- `CriadoEm IS NOT NULL`
- `CriadoEm <= DATEADD(HOUR, -48, SYSDATETIME())`
- `CanceladoEm IS NULL`
- `Status` diferente de `CANCELADO`
- não existe item de contrato vinculado com `BitPreferencia = 1` ainda não vencido.

### 2. Reserva de preferência de renovação

A reserva de preferência de renovação é identificada pelo item correspondente em:

`Integracao.Silver.FatoControleContratosItensEuromidia`

com:

- `BitPreferencia = 1`
- `BitAtivo = 1`
- `DataTerminoPrevisto IS NOT NULL`
- item correspondente pelo `IDFatoControleContratosItemOrigem`.

Se o vínculo direto não estiver preenchido, o DAG tenta localizar o item correspondente pelo contrato,
`CodPonto`, `CodFace` e sobreposição de datas.

Essa reserva não é cancelada após 48 horas.

Ela só é cancelada quando:

- continuar como `Origem = 'RESERVA'`
- ainda não tiver `CanceladoEm`
- `Status` ainda não for `CANCELADO`
- o `DataTerminoPrevisto` do item de contrato vinculado já tiver vencido.

A comparação de vencimento usa data, não hora:

`CAST(DataTerminoPrevisto AS date) < CAST(SYSDATETIME() AS date)`

Assim, se o item vence hoje, a reserva não é cancelada no começo do dia.
Ela só fica elegível no dia seguinte ao vencimento.

## Segurança / idempotência

O DAG não recancela registros já cancelados porque a atualização exige:

- `CanceladoEm IS NULL`
- `Status <> 'CANCELADO'`

Também usa `UPDLOCK`, `READPAST` e `ROWLOCK` no `UPDATE` para reduzir risco de concorrência.

## Agendamento

Executa todos os dias a cada 8 minutos:

`*/8 * * * *`
"""


def _normalizar_linhas_para_log(linhas: list[dict[str, Any]]) -> list[dict[str, Any]]:
    linhas_normalizadas: list[dict[str, Any]] = []

    for linha in linhas:
        linha_normalizada: dict[str, Any] = {}

        for chave, valor in linha.items():
            if hasattr(valor, "isoformat"):
                linha_normalizada[chave] = valor.isoformat()
            else:
                linha_normalizada[chave] = valor

        linhas_normalizadas.append(linha_normalizada)

    return linhas_normalizadas


@dag(
    dag_id=NOME_DAG,
    description="Cancela reservas comuns em 48h e preserva reservas de preferência até o vencimento do item.",
    schedule="*/8 * * * *",
    start_date=pendulum.datetime(2026, 5, 13, 0, 0, tz=TIMEZONE_SAO_PAULO),
    catchup=False,
    max_active_runs=1,
    tags=[
        "euromidia",
        "ocupacao",
        "reserva",
        "preferencia-renovacao",
        "sql-server",
        "integracao",
        "cancelamento-automatico",
    ],
    doc_md=DOC_MD,
    default_args={
        "owner": "integracao",
        "retries": 2,
        "retry_delay": timedelta(minutes=1),
    },
)
def pipeline_cancela_reserva():
    @task(task_id="cancelar_reservas_elegiveis")
    def cancelar_reservas_elegiveis() -> dict[str, Any]:
        """
        Cancela reservas conforme a regra de negócio.

        Regra final:
        - reserva comum: cancela após 48 horas de criação;
        - reserva vinculada a item BitPreferencia = 1 ainda não vencido: não cancela por 48 horas;
        - reserva vinculada a item BitPreferencia = 1 vencido: cancela após o vencimento do DataTerminoPrevisto;
        - item que vence hoje ainda não é considerado vencido, porque a comparação é feita por data.
        """
        hook_sql_server = HookSqlServer(conn_id=CONN_ID_SQL_SERVER)
        engine = hook_sql_server.obter_engine()

        with engine.begin() as conexao:
            usuario_integracao = conexao.execute(
                text(SQL_BUSCAR_USUARIO_INTEGRACAO),
                {"nome_usuario_integracao": NOME_USUARIO_INTEGRACAO},
            ).mappings().first()

            if not usuario_integracao:
                raise AirflowFailException(
                    "Usuário ativo 'Integração' não encontrado em "
                    "[Integracao].[Silver].[DimUsuarios]. "
                    "Verifique se existe um registro ativo com NomeUsuario = 'Integração'."
                )

            id_usuario_integracao = int(usuario_integracao["IDDimUsuarios"])
            nome_usuario_encontrado = str(usuario_integracao["NomeUsuario"])

            datas_execucao = conexao.execute(
                text(SQL_OBTER_DATA_HORA_SQL_SERVER)
            ).mappings().first()

            data_execucao_sql_server = datas_execucao["DataExecucaoSqlServer"]
            limite_criacao_reserva_comum = datas_execucao["LimiteCriacaoReservaComum"]
            data_referencia_vencimento_preferencia = datas_execucao[
                "DataReferenciaVencimentoPreferencia"
            ]

            total_elegivel_antes = int(
                conexao.execute(
                    text(SQL_CONTAR_RESERVAS_ELEGIVEIS_CANCELAMENTO)
                ).scalar_one()
                or 0
            )

            total_preferencia_protegida = int(
                conexao.execute(
                    text(SQL_CONTAR_RESERVAS_PREFERENCIA_PROTEGIDAS)
                ).scalar_one()
                or 0
            )

            total_preferencia_vencida_elegivel = int(
                conexao.execute(
                    text(SQL_CONTAR_RESERVAS_PREFERENCIA_VENCIDAS)
                ).scalar_one()
                or 0
            )

            amostra_elegiveis = [
                dict(linha)
                for linha in conexao.execute(
                    text(SQL_LISTAR_AMOSTRA_RESERVAS_ELEGIVEIS_CANCELAMENTO)
                ).mappings().all()
            ]

            amostra_preferencia_protegida = [
                dict(linha)
                for linha in conexao.execute(
                    text(SQL_LISTAR_AMOSTRA_RESERVAS_PREFERENCIA_PROTEGIDAS)
                ).mappings().all()
            ]

            logger.info(
                "Reservas elegíveis para cancelamento: total=%s | "
                "preferencia_vencida_elegivel=%s | preferencia_protegida_nao_vencida=%s | "
                "amostra_elegiveis=%s | amostra_preferencia_protegida=%s",
                total_elegivel_antes,
                total_preferencia_vencida_elegivel,
                total_preferencia_protegida,
                _normalizar_linhas_para_log(amostra_elegiveis),
                _normalizar_linhas_para_log(amostra_preferencia_protegida),
            )

            resultado_update = conexao.execute(
                text(SQL_CANCELAR_RESERVAS_ELEGIVEIS),
                {"id_usuario_integracao": id_usuario_integracao},
            )

            total_cancelado = resultado_update.rowcount

            if total_cancelado is None or total_cancelado < 0:
                total_cancelado = total_elegivel_antes

        resumo = {
            "dag": NOME_DAG,
            "regra": (
                "Reserva comum cancela após 48h. "
                "Reserva com BitPreferencia=1 não vencida fica protegida. "
                "Reserva com BitPreferencia=1 só cancela após o DataTerminoPrevisto do item vencer."
            ),
            "usuario_integracao": nome_usuario_encontrado,
            "id_usuario_integracao": id_usuario_integracao,
            "horas_minimas_reserva_aberta": HORAS_MINIMAS_RESERVA_ABERTA,
            "total_elegivel_antes": total_elegivel_antes,
            "total_preferencia_protegida_nao_vencida": total_preferencia_protegida,
            "total_preferencia_vencida_elegivel": total_preferencia_vencida_elegivel,
            "total_cancelado": int(total_cancelado or 0),
            "data_execucao_sql_server": (
                data_execucao_sql_server.isoformat()
                if hasattr(data_execucao_sql_server, "isoformat")
                else str(data_execucao_sql_server)
            ),
            "limite_criacao_reserva_comum": (
                limite_criacao_reserva_comum.isoformat()
                if hasattr(limite_criacao_reserva_comum, "isoformat")
                else str(limite_criacao_reserva_comum)
            ),
            "data_referencia_vencimento_preferencia": (
                data_referencia_vencimento_preferencia.isoformat()
                if hasattr(data_referencia_vencimento_preferencia, "isoformat")
                else str(data_referencia_vencimento_preferencia)
            ),
            "amostra_elegiveis": _normalizar_linhas_para_log(amostra_elegiveis),
            "amostra_preferencia_protegida": _normalizar_linhas_para_log(
                amostra_preferencia_protegida
            ),
        }

        logger.info("Resumo do cancelamento automático de reservas: %s", resumo)

        return resumo

    cancelar_reservas_elegiveis()


pipeline_cancela_reserva()
