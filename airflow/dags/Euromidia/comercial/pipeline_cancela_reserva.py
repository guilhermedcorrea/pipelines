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


SQL_CONDICAO_RESERVA_MAIS_DE_48H = """
    UPPER(LTRIM(RTRIM(ISNULL(reserva.Origem, N'')))) = N'RESERVA'
    AND reserva.CriadoEm IS NOT NULL
    AND reserva.CriadoEm <= DATEADD(HOUR, -48, SYSDATETIME())
    AND reserva.CanceladoEm IS NULL
    AND ISNULL(UPPER(LTRIM(RTRIM(reserva.Status))), N'') <> N'CANCELADO'
"""


SQL_CONTAR_RESERVAS_MAIS_DE_48H = f"""
SELECT
    COUNT(1) AS TotalElegivel
FROM [Integracao].[Silver].[FatoOcupacaoPaineisEuromidia] AS reserva WITH (NOLOCK)
WHERE
{SQL_CONDICAO_RESERVA_MAIS_DE_48H};
"""


SQL_LISTAR_AMOSTRA_RESERVAS_MAIS_DE_48H = f"""
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
       reserva.DataAtualizacao,
       CAST(DATEDIFF(MINUTE, reserva.CriadoEm, SYSDATETIME()) / 60.0 AS DECIMAL(18, 2)) AS HorasDesdeCriacao
FROM [Integracao].[Silver].[FatoOcupacaoPaineisEuromidia] AS reserva WITH (NOLOCK)
WHERE
{SQL_CONDICAO_RESERVA_MAIS_DE_48H}
ORDER BY
    reserva.CriadoEm ASC,
    reserva.IDFatoOcupacaoPaineisEuromidia ASC;
"""


SQL_CANCELAR_RESERVAS_MAIS_DE_48H = f"""
UPDATE reserva
SET
    reserva.CanceladoEm = SYSDATETIME(),
    reserva.CanceladoPorIDUsuario = :id_usuario_integracao,
    reserva.Status = N'CANCELADO',
    reserva.Observacao = N'Reserva cancelada automaticamente após mais de 48 horas aberta.',
    reserva.DataAtualizacao = SYSDATETIME()
FROM [Integracao].[Silver].[FatoOcupacaoPaineisEuromidia] AS reserva WITH (UPDLOCK, READPAST, ROWLOCK)
WHERE
{SQL_CONDICAO_RESERVA_MAIS_DE_48H};
"""


SQL_OBTER_DATA_HORA_SQL_SERVER = """
SELECT
    SYSDATETIME() AS DataExecucaoSqlServer,
    DATEADD(HOUR, -48, SYSDATETIME()) AS LimiteCriacaoReserva;
"""


DOC_MD = """
# pipeline_cancela_reserva

## Objetivo

Cancelar automaticamente reservas antigas da tabela:

`Integracao.Silver.FatoOcupacaoPaineisEuromidia`

## Regra correta de cancelamento

O DAG cancela somente registros com:

- `Origem = 'RESERVA'`
- `CriadoEm IS NOT NULL`
- `CriadoEm <= DATEADD(HOUR, -48, SYSDATETIME())`
- `CanceladoEm IS NULL`
- `Status` diferente de `CANCELADO`

## Importante

A regra não usa mais `ExpiraEm <= SYSDATETIME()` como gatilho principal.

Motivo:

Se `ExpiraEm` for gravado errado, no passado, com fuso incorreto ou igual à data atual,
uma reserva recém-criada pode ser cancelada indevidamente.

Agora a regra usa `CriadoEm`, ou seja:

Reserva criada há mais de 48 horas = pode cancelar.

Reserva criada agora = não cancela.

## Atualizações realizadas

Quando uma reserva com mais de 48 horas é encontrada, o DAG atualiza:

- `CanceladoEm` = data/hora atual do SQL Server
- `Status` = `CANCELADO`
- `CanceladoPorIDUsuario` = `IDDimUsuarios` do usuário ativo chamado `Integração`
- `Observacao` = `Reserva cancelada automaticamente após mais de 48 horas aberta.`
- `DataAtualizacao` = data/hora atual do SQL Server

## Segurança / idempotência

O DAG não recancela registros já cancelados porque a atualização exige:

- `CanceladoEm IS NULL`
- `Status <> 'CANCELADO'`

Também usa `UPDLOCK`, `READPAST` e `ROWLOCK` no `UPDATE` para reduzir risco de concorrência.

## Agendamento

Executa todos os dias a cada 8 minutos:

`*/8 * * * *`
"""


@dag(
    dag_id=NOME_DAG,
    description="Cancela automaticamente reservas com mais de 48 horas de criação.",
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
    @task(task_id="cancelar_reservas_com_mais_de_48h")
    def cancelar_reservas_com_mais_de_48h() -> dict[str, Any]:
        """
        Cancelo reservas somente depois de 48 horas da criação.

        Regra:
        - olho somente Origem = RESERVA;
        - uso CriadoEm como base da idade da reserva;
        - cancelo apenas se CriadoEm <= agora - 48 horas;
        - não uso ExpiraEm como gatilho principal;
        - só atualizo se CanceladoEm estiver vazio;
        - evito mexer em registro já marcado como CANCELADO.
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
            limite_criacao_reserva = datas_execucao["LimiteCriacaoReserva"]

            total_elegivel_antes = int(
                conexao.execute(text(SQL_CONTAR_RESERVAS_MAIS_DE_48H)).scalar_one() or 0
            )

            amostra_elegiveis = [
                dict(linha)
                for linha in conexao.execute(
                    text(SQL_LISTAR_AMOSTRA_RESERVAS_MAIS_DE_48H)
                ).mappings().all()
            ]

            logger.info(
                "Reservas elegíveis para cancelamento por mais de %s horas: total=%s, amostra=%s",
                HORAS_MINIMAS_RESERVA_ABERTA,
                total_elegivel_antes,
                amostra_elegiveis,
            )

            resultado_update = conexao.execute(
                text(SQL_CANCELAR_RESERVAS_MAIS_DE_48H),
                {"id_usuario_integracao": id_usuario_integracao},
            )

            total_cancelado = resultado_update.rowcount

            if total_cancelado is None or total_cancelado < 0:
                total_cancelado = total_elegivel_antes

        resumo = {
            "dag": NOME_DAG,
            "regra": "Cancelar somente reservas criadas há mais de 48 horas.",
            "usuario_integracao": nome_usuario_encontrado,
            "id_usuario_integracao": id_usuario_integracao,
            "horas_minimas_reserva_aberta": HORAS_MINIMAS_RESERVA_ABERTA,
            "total_elegivel_antes": total_elegivel_antes,
            "total_cancelado": int(total_cancelado or 0),
            "data_execucao_sql_server": (
                data_execucao_sql_server.isoformat()
                if hasattr(data_execucao_sql_server, "isoformat")
                else str(data_execucao_sql_server)
            ),
            "limite_criacao_reserva": (
                limite_criacao_reserva.isoformat()
                if hasattr(limite_criacao_reserva, "isoformat")
                else str(limite_criacao_reserva)
            ),
            "amostra_elegiveis": [
                {
                    chave: (
                        valor.isoformat()
                        if hasattr(valor, "isoformat")
                        else valor
                    )
                    for chave, valor in linha.items()
                }
                for linha in amostra_elegiveis
            ],
        }

        logger.info("Resumo do cancelamento automático de reservas: %s", resumo)

        return resumo

    cancelar_reservas_com_mais_de_48h()


pipeline_cancela_reserva()