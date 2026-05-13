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


SQL_CONTAR_RESERVAS_EXPIRADAS = """
SELECT
    COUNT(1) AS TotalElegivel
FROM [Integracao].[Silver].[FatoOcupacaoPaineisEuromidia] AS reserva WITH (NOLOCK)
WHERE
    UPPER(LTRIM(RTRIM(reserva.Origem))) = N'RESERVA'
    AND reserva.ExpiraEm IS NOT NULL
    AND reserva.ExpiraEm <= SYSDATETIME()
    AND reserva.CanceladoEm IS NULL
    AND ISNULL(UPPER(LTRIM(RTRIM(reserva.Status))), N'') <> N'CANCELADO';
"""


SQL_CANCELAR_RESERVAS_EXPIRADAS = """
UPDATE reserva
SET
    reserva.CanceladoEm = SYSDATETIME(),
    reserva.CanceladoPorIDUsuario = :id_usuario_integracao,
    reserva.Status = N'CANCELADO',
    reserva.Observacao = N'Reserva Expirada',
    reserva.DataAtualizacao = SYSDATETIME()
FROM [Integracao].[Silver].[FatoOcupacaoPaineisEuromidia] AS reserva WITH (UPDLOCK, READPAST, ROWLOCK)
WHERE
    UPPER(LTRIM(RTRIM(reserva.Origem))) = N'RESERVA'
    AND reserva.ExpiraEm IS NOT NULL
    AND reserva.ExpiraEm <= SYSDATETIME()
    AND reserva.CanceladoEm IS NULL
    AND ISNULL(UPPER(LTRIM(RTRIM(reserva.Status))), N'') <> N'CANCELADO';
"""


SQL_OBTER_DATA_HORA_SQL_SERVER = """
SELECT SYSDATETIME() AS DataExecucaoSqlServer;
"""


DOC_MD = """
# pipeline_cancela_reserva

## Objetivo

Cancelar automaticamente reservas expiradas da tabela:

`Integracao.Silver.FatoOcupacaoPaineisEuromidia`

## Regra de elegibilidade

O DAG cancela somente registros com:

- `Origem = 'RESERVA'`
- `ExpiraEm IS NOT NULL`
- `ExpiraEm <= SYSDATETIME()`
- `CanceladoEm IS NULL`
- `Status` diferente de `CANCELADO`

## Atualizações realizadas

Quando uma reserva expirada é encontrada, o DAG atualiza:

- `CanceladoEm` = data/hora atual do SQL Server
- `Status` = `CANCELADO`
- `CanceladoPorIDUsuario` = `IDDimUsuarios` do usuário ativo chamado `Integração`
- `Observacao` = `Reserva Expirada`
- `DataAtualizacao` = data/hora atual do SQL Server

## Segurança / idempotência

O DAG não recancela registros já cancelados porque a atualização exige:

- `CanceladoEm IS NULL`
- `Status <> 'CANCELADO'`

Também usa `UPDLOCK`, `READPAST` e `ROWLOCK` no `UPDATE` para reduzir risco de concorrência.

## Correção técnica importante

O `UPDATE` é executado sozinho, sem tentar ler linhas do mesmo resultado.
Isso evita o erro:

`This result object does not return rows. It has been closed automatically.`

## Agendamento

Executa todos os dias a cada 8 minutos:

`*/8 * * * *`
"""


@dag(
    dag_id=NOME_DAG,
    description="Cancela automaticamente reservas expiradas de ocupação de painéis Euromídia.",
    schedule="*/8 * * * *",
    start_date=pendulum.datetime(2026, 5, 13, 0, 0, tz=TIMEZONE_SAO_PAULO),
    catchup=False,
    max_active_runs=1,
    tags=[
        "euromidia",
        "ocupacao",
        "reserva",
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
    @task(task_id="cancelar_reservas_expiradas")
    def cancelar_reservas_expiradas() -> dict[str, Any]:
        """
        Cancelo reservas expiradas.

        Regra:
        - olho somente Origem = RESERVA;
        - considero expirada quando ExpiraEm <= SYSDATETIME();
        - só atualizo se CanceladoEm estiver vazio;
        - evito mexer em registro já marcado como CANCELADO.

        Importante:
        - não tento ler linhas de um UPDATE;
        - SELECT fica separado de UPDATE para evitar ResourceClosedError.
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

            total_elegivel_antes = int(
                conexao.execute(text(SQL_CONTAR_RESERVAS_EXPIRADAS)).scalar_one() or 0
            )

            resultado_update = conexao.execute(
                text(SQL_CANCELAR_RESERVAS_EXPIRADAS),
                {"id_usuario_integracao": id_usuario_integracao},
            )

            total_cancelado = resultado_update.rowcount

            if total_cancelado is None or total_cancelado < 0:
                total_cancelado = total_elegivel_antes

            data_execucao_sql_server = conexao.execute(
                text(SQL_OBTER_DATA_HORA_SQL_SERVER)
            ).scalar_one()

        resumo = {
            "dag": NOME_DAG,
            "usuario_integracao": nome_usuario_encontrado,
            "id_usuario_integracao": id_usuario_integracao,
            "total_elegivel_antes": total_elegivel_antes,
            "total_cancelado": int(total_cancelado or 0),
            "data_execucao_sql_server": (
                data_execucao_sql_server.isoformat()
                if hasattr(data_execucao_sql_server, "isoformat")
                else str(data_execucao_sql_server)
            ),
        }

        logger.info("Resumo do cancelamento automático de reservas: %s", resumo)

        return resumo

    cancelar_reservas_expiradas()


pipeline_cancela_reserva()
