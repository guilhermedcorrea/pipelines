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

try:
    from airflow.providers.standard.operators.trigger_dagrun import (
        TriggerDagRunOperator,
    )
except ImportError:
    from airflow.operators.trigger_dagrun import TriggerDagRunOperator

from hooks.BancodeDados.SqlServer import HookSqlServer


logger = logging.getLogger(__name__)

NOME_DAG = "pipeline_cancela_reserva"
NOME_DAG_NOTIFICACOES = "pipeline_notificacoes_euromidia"
CONN_ID_SQL_SERVER = "mssql_integracao"
TIMEZONE_SAO_PAULO = pendulum.timezone("America/Sao_Paulo")

NOME_USUARIO_INTEGRACAO = "INTEGRACAO"
HORAS_MINIMAS_RESERVA_ABERTA = 48
DIAS_TOLERANCIA_RENOVACAO_APOS_FIM_OCUPACAO_ORIGEM = 1


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


SQL_CONDICAO_TIPO_VINCULO_PREFERENCIA_RENOVACAO = """
    UPPER(LTRIM(RTRIM(ISNULL(reserva.TipoVinculoOrigem, N'')))) COLLATE Latin1_General_CI_AI
        LIKE N'%PREFERENCIA%RENOVACAO%CONTRATO%'
"""


SQL_EXISTE_ITEM_VINCULADO_BITPREFERENCIA_ATIVO = """
EXISTS (
    SELECT 1
    FROM [Integracao].[Silver].[FatoControleContratosItensEuromidia] AS item_pref WITH (NOLOCK)
    WHERE
        item_pref.IDFatoControleContratosItensEuromidia = reserva.IDFatoControleContratosItemOrigem
        AND ISNULL(item_pref.BitPreferencia, 0) = 1
        AND ISNULL(item_pref.BitAtivo, 1) = 1
)
"""


SQL_CONDICAO_RESERVA_PREFERENCIA = f"""
(
    {SQL_CONDICAO_TIPO_VINCULO_PREFERENCIA_RENOVACAO}
    OR {SQL_EXISTE_ITEM_VINCULADO_BITPREFERENCIA_ATIVO}
)
"""


SQL_CONDICAO_RESERVA_PREFERENCIA_VENCIDA = f"""
(
    {SQL_CONDICAO_RESERVA_PREFERENCIA}
    AND reserva.DataFim IS NOT NULL
    AND CAST(reserva.DataFim AS date) < CAST(SYSDATETIME() AS date)
)
"""


SQL_CONDICAO_RESERVA_PREFERENCIA_ORIGEM_NAO_RENOVADA = f"""
(
    {SQL_CONDICAO_RESERVA_PREFERENCIA}
    AND reserva.IDFatoOcupacaoOrigem IS NOT NULL
    AND reserva.IDFatoControleContratosItemOrigem IS NOT NULL
    AND EXISTS (
        SELECT 1
        FROM [Integracao].[Silver].[FatoOcupacaoPaineisEuromidia] AS ocupacao_origem WITH (NOLOCK)
        WHERE
            ocupacao_origem.IDFatoOcupacaoPaineisEuromidia = reserva.IDFatoOcupacaoOrigem
            AND ocupacao_origem.IDFatoControleContratosItemOrigem = reserva.IDFatoControleContratosItemOrigem
            AND ocupacao_origem.DataFim IS NOT NULL
            AND DATEADD(
                    DAY,
                    {DIAS_TOLERANCIA_RENOVACAO_APOS_FIM_OCUPACAO_ORIGEM},
                    CAST(ocupacao_origem.DataFim AS date)
                ) < CAST(SYSDATETIME() AS date)
    )
)
"""


SQL_CONDICAO_RESERVA_PREFERENCIA_PROTEGIDA = f"""
(
    {SQL_CONDICAO_RESERVA_PREFERENCIA}
    AND NOT {SQL_CONDICAO_RESERVA_PREFERENCIA_VENCIDA}
    AND NOT {SQL_CONDICAO_RESERVA_PREFERENCIA_ORIGEM_NAO_RENOVADA}
)
"""


SQL_CONDICAO_RESERVA_PREFERENCIA_SEM_DATA_FIM = f"""
(
    {SQL_CONDICAO_RESERVA_PREFERENCIA}
    AND reserva.DataFim IS NULL
)
"""


SQL_CONDICAO_RESERVA_COMUM_MAIS_DE_48H = f"""
(
    NOT {SQL_CONDICAO_RESERVA_PREFERENCIA}
    AND reserva.CriadoEm IS NOT NULL
    AND reserva.CriadoEm <= DATEADD(HOUR, -{HORAS_MINIMAS_RESERVA_ABERTA}, SYSDATETIME())
)
"""


SQL_CONDICAO_RESERVA_ELEGIVEL_CANCELAMENTO = f"""
{SQL_CONDICAO_RESERVA_ABERTA}
AND (
    {SQL_CONDICAO_RESERVA_PREFERENCIA_VENCIDA}
    OR {SQL_CONDICAO_RESERVA_PREFERENCIA_ORIGEM_NAO_RENOVADA}
    OR {SQL_CONDICAO_RESERVA_COMUM_MAIS_DE_48H}
)
"""


SQL_MOTIVO_CANCELAMENTO = f"""
CASE
    WHEN {SQL_CONDICAO_RESERVA_PREFERENCIA_VENCIDA}
        THEN N'PREFERENCIA_RENOVACAO_VENCIDA'
    WHEN {SQL_CONDICAO_RESERVA_PREFERENCIA_ORIGEM_NAO_RENOVADA}
        THEN N'PREFERENCIA_RENOVACAO_ORIGEM_NAO_RENOVADA'
    ELSE N'RESERVA_COMUM_MAIS_DE_48H'
END
"""


SQL_OBSERVACAO_CANCELAMENTO = f"""
CASE
    WHEN {SQL_CONDICAO_RESERVA_PREFERENCIA_VENCIDA}
        THEN N'Reserva de preferência de renovação cancelada automaticamente no primeiro dia após o fim da DataFim da própria reserva.'
    WHEN {SQL_CONDICAO_RESERVA_PREFERENCIA_ORIGEM_NAO_RENOVADA}
        THEN N'Reserva de preferência de renovação cancelada automaticamente porque a ocupação origem não foi renovada após o prazo de 1 dia do fim da ocupação origem.'
    ELSE N'Reserva comum cancelada automaticamente após mais de 48 horas aberta.'
END
"""


SQL_CONTAR_RESERVAS_ELEGIVEIS_CANCELAMENTO = f"""
SELECT
    COUNT(1) AS TotalElegivel
FROM [Integracao].[Silver].[FatoOcupacaoPaineisEuromidia] AS reserva WITH (NOLOCK)
WHERE
{SQL_CONDICAO_RESERVA_ELEGIVEL_CANCELAMENTO};
"""


SQL_CONTAR_RESERVAS_COMUNS_ELEGIVEIS = f"""
SELECT
    COUNT(1) AS TotalReservaComumElegivel
FROM [Integracao].[Silver].[FatoOcupacaoPaineisEuromidia] AS reserva WITH (NOLOCK)
WHERE
{SQL_CONDICAO_RESERVA_ABERTA}
AND {SQL_CONDICAO_RESERVA_COMUM_MAIS_DE_48H};
"""


SQL_CONTAR_RESERVAS_PREFERENCIA_PROTEGIDAS = f"""
SELECT
    COUNT(1) AS TotalProtegido
FROM [Integracao].[Silver].[FatoOcupacaoPaineisEuromidia] AS reserva WITH (NOLOCK)
WHERE
{SQL_CONDICAO_RESERVA_ABERTA}
AND {SQL_CONDICAO_RESERVA_PREFERENCIA_PROTEGIDA};
"""


SQL_CONTAR_RESERVAS_PREFERENCIA_VENCIDAS = f"""
SELECT
    COUNT(1) AS TotalPreferenciaVencida
FROM [Integracao].[Silver].[FatoOcupacaoPaineisEuromidia] AS reserva WITH (NOLOCK)
WHERE
{SQL_CONDICAO_RESERVA_ABERTA}
AND {SQL_CONDICAO_RESERVA_PREFERENCIA_VENCIDA};
"""


SQL_CONTAR_RESERVAS_PREFERENCIA_ORIGEM_NAO_RENOVADA = f"""
SELECT
    COUNT(1) AS TotalPreferenciaOrigemNaoRenovada
FROM [Integracao].[Silver].[FatoOcupacaoPaineisEuromidia] AS reserva WITH (NOLOCK)
WHERE
{SQL_CONDICAO_RESERVA_ABERTA}
AND {SQL_CONDICAO_RESERVA_PREFERENCIA_ORIGEM_NAO_RENOVADA};
"""


SQL_CONTAR_RESERVAS_PREFERENCIA_SEM_DATA_FIM = f"""
SELECT
    COUNT(1) AS TotalPreferenciaSemDataFim
FROM [Integracao].[Silver].[FatoOcupacaoPaineisEuromidia] AS reserva WITH (NOLOCK)
WHERE
{SQL_CONDICAO_RESERVA_ABERTA}
AND {SQL_CONDICAO_RESERVA_PREFERENCIA_SEM_DATA_FIM};
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
       reserva.TipoVinculoOrigem,
       reserva.CriadoEm,
       reserva.ExpiraEm,
       reserva.DataInicio,
       reserva.DataFim,
       reserva.IDFatoControleContratos,
       reserva.IDFatoControleContratosItemOrigem,
       reserva.IDFatoOcupacaoOrigem,
       item_amostra.IDFatoControleContratosItensEuromidia AS IDItemContratoVinculado,
       item_amostra.BitPreferencia,
       item_amostra.BitAtivo AS BitAtivoItemContrato,
       item_amostra.DataInicioPrevisto AS DataInicioPrevistoItemContrato,
       item_amostra.DataTerminoPrevisto AS DataTerminoPrevistoItemContrato,
       item_amostra.DataFimEfetiva AS DataFimEfetivaItemContrato,
       ocupacao_origem_amostra.IDFatoOcupacaoPaineisEuromidia AS IDOcupacaoOrigemRegraCancelamento,
       ocupacao_origem_amostra.DataInicio AS DataInicioOcupacaoOrigem,
       ocupacao_origem_amostra.DataFim AS DataFimOcupacaoOrigem,
       DATEADD(
           DAY,
           {DIAS_TOLERANCIA_RENOVACAO_APOS_FIM_OCUPACAO_ORIGEM},
           CAST(ocupacao_origem_amostra.DataFim AS date)
       ) AS DataLimiteRenovacaoOcupacaoOrigem,
       reserva.DataFim AS DataFimRegraCancelamento,
       {SQL_MOTIVO_CANCELAMENTO} AS MotivoCancelamento,
       CAST(DATEDIFF(MINUTE, reserva.CriadoEm, SYSDATETIME()) / 60.0 AS DECIMAL(18, 2)) AS HorasDesdeCriacao
FROM [Integracao].[Silver].[FatoOcupacaoPaineisEuromidia] AS reserva WITH (NOLOCK)
OUTER APPLY (
    SELECT TOP (1)
           item_pref.IDFatoControleContratosItensEuromidia,
           item_pref.BitPreferencia,
           item_pref.BitAtivo,
           item_pref.DataInicioPrevisto,
           item_pref.DataTerminoPrevisto,
           item_pref.DataFimEfetiva
    FROM [Integracao].[Silver].[FatoControleContratosItensEuromidia] AS item_pref WITH (NOLOCK)
    WHERE
        item_pref.IDFatoControleContratosItensEuromidia = reserva.IDFatoControleContratosItemOrigem
) AS item_amostra
OUTER APPLY (
    SELECT TOP (1)
           origem.IDFatoOcupacaoPaineisEuromidia,
           origem.Origem,
           origem.Status,
           origem.DataInicio,
           origem.DataFim,
           origem.CanceladoEm
    FROM [Integracao].[Silver].[FatoOcupacaoPaineisEuromidia] AS origem WITH (NOLOCK)
    WHERE
        origem.IDFatoOcupacaoPaineisEuromidia = reserva.IDFatoOcupacaoOrigem
) AS ocupacao_origem_amostra
WHERE
{SQL_CONDICAO_RESERVA_ELEGIVEL_CANCELAMENTO}
ORDER BY
    CASE
        WHEN {SQL_CONDICAO_RESERVA_PREFERENCIA_VENCIDA}
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
       reserva.TipoVinculoOrigem,
       reserva.CriadoEm,
       reserva.DataInicio,
       reserva.DataFim,
       reserva.IDFatoControleContratos,
       reserva.IDFatoControleContratosItemOrigem,
       reserva.IDFatoOcupacaoOrigem,
       item_amostra.IDFatoControleContratosItensEuromidia AS IDItemContratoVinculado,
       item_amostra.BitPreferencia,
       item_amostra.BitAtivo AS BitAtivoItemContrato,
       item_amostra.DataInicioPrevisto AS DataInicioPrevistoItemContrato,
       item_amostra.DataTerminoPrevisto AS DataTerminoPrevistoItemContrato,
       item_amostra.DataFimEfetiva AS DataFimEfetivaItemContrato,
       ocupacao_origem_amostra.IDFatoOcupacaoPaineisEuromidia AS IDOcupacaoOrigemRegraCancelamento,
       ocupacao_origem_amostra.DataInicio AS DataInicioOcupacaoOrigem,
       ocupacao_origem_amostra.DataFim AS DataFimOcupacaoOrigem,
       DATEADD(
           DAY,
           {DIAS_TOLERANCIA_RENOVACAO_APOS_FIM_OCUPACAO_ORIGEM},
           CAST(ocupacao_origem_amostra.DataFim AS date)
       ) AS DataLimiteRenovacaoOcupacaoOrigem,
       reserva.DataFim AS DataFimRegraCancelamento,
       CAST(DATEDIFF(MINUTE, reserva.CriadoEm, SYSDATETIME()) / 60.0 AS DECIMAL(18, 2)) AS HorasDesdeCriacao
FROM [Integracao].[Silver].[FatoOcupacaoPaineisEuromidia] AS reserva WITH (NOLOCK)
OUTER APPLY (
    SELECT TOP (1)
           item_pref.IDFatoControleContratosItensEuromidia,
           item_pref.BitPreferencia,
           item_pref.BitAtivo,
           item_pref.DataInicioPrevisto,
           item_pref.DataTerminoPrevisto,
           item_pref.DataFimEfetiva
    FROM [Integracao].[Silver].[FatoControleContratosItensEuromidia] AS item_pref WITH (NOLOCK)
    WHERE
        item_pref.IDFatoControleContratosItensEuromidia = reserva.IDFatoControleContratosItemOrigem
) AS item_amostra
OUTER APPLY (
    SELECT TOP (1)
           origem.IDFatoOcupacaoPaineisEuromidia,
           origem.Origem,
           origem.Status,
           origem.DataInicio,
           origem.DataFim,
           origem.CanceladoEm
    FROM [Integracao].[Silver].[FatoOcupacaoPaineisEuromidia] AS origem WITH (NOLOCK)
    WHERE
        origem.IDFatoOcupacaoPaineisEuromidia = reserva.IDFatoOcupacaoOrigem
) AS ocupacao_origem_amostra
WHERE
{SQL_CONDICAO_RESERVA_ABERTA}
AND {SQL_CONDICAO_RESERVA_PREFERENCIA_PROTEGIDA}
ORDER BY
    reserva.DataFim ASC,
    reserva.CriadoEm ASC,
    reserva.IDFatoOcupacaoPaineisEuromidia ASC;
"""


SQL_LISTAR_AMOSTRA_RESERVAS_PREFERENCIA_SEM_DATA_FIM = f"""
SELECT TOP (30)
       reserva.IDFatoOcupacaoPaineisEuromidia,
       reserva.Referencia,
       reserva.CodPonto,
       reserva.CodFace,
       reserva.MarcaExibida,
       reserva.Status,
       reserva.Origem,
       reserva.TipoVinculoOrigem,
       reserva.CriadoEm,
       reserva.DataInicio,
       reserva.DataFim,
       reserva.IDFatoControleContratos,
       reserva.IDFatoControleContratosItemOrigem,
       reserva.IDFatoOcupacaoOrigem,
       ocupacao_origem_amostra.DataInicio AS DataInicioOcupacaoOrigem,
       ocupacao_origem_amostra.DataFim AS DataFimOcupacaoOrigem,
       DATEADD(
           DAY,
           {DIAS_TOLERANCIA_RENOVACAO_APOS_FIM_OCUPACAO_ORIGEM},
           CAST(ocupacao_origem_amostra.DataFim AS date)
       ) AS DataLimiteRenovacaoOcupacaoOrigem
FROM [Integracao].[Silver].[FatoOcupacaoPaineisEuromidia] AS reserva WITH (NOLOCK)
OUTER APPLY (
    SELECT TOP (1)
           origem.IDFatoOcupacaoPaineisEuromidia,
           origem.DataInicio,
           origem.DataFim
    FROM [Integracao].[Silver].[FatoOcupacaoPaineisEuromidia] AS origem WITH (NOLOCK)
    WHERE
        origem.IDFatoOcupacaoPaineisEuromidia = reserva.IDFatoOcupacaoOrigem
) AS ocupacao_origem_amostra
WHERE
{SQL_CONDICAO_RESERVA_ABERTA}
AND {SQL_CONDICAO_RESERVA_PREFERENCIA_SEM_DATA_FIM}
ORDER BY
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
OUTPUT
    INSERTED.IDFatoOcupacaoPaineisEuromidia AS IDReserva
FROM [Integracao].[Silver].[FatoOcupacaoPaineisEuromidia] AS reserva WITH (UPDLOCK, READPAST, ROWLOCK)
WHERE
{SQL_CONDICAO_RESERVA_ELEGIVEL_CANCELAMENTO};
"""


SQL_OBTER_DATA_HORA_SQL_SERVER = f"""
SELECT
    SYSDATETIME() AS DataExecucaoSqlServer,
    DATEADD(HOUR, -{HORAS_MINIMAS_RESERVA_ABERTA}, SYSDATETIME()) AS LimiteCriacaoReservaComum,
    CAST(SYSDATETIME() AS date) AS DataReferenciaCancelamentoPreferencia,
    {DIAS_TOLERANCIA_RENOVACAO_APOS_FIM_OCUPACAO_ORIGEM} AS DiasToleranciaRenovacaoAposFimOcupacaoOrigem;
"""


DOC_MD = """
# pipeline_cancela_reserva

## Objetivo

Cancelar automaticamente reservas antigas da tabela:

`Integracao.Silver.FatoOcupacaoPaineisEuromidia`

## Regra correta de cancelamento

O DAG trabalha com três regras.

### 1. Reserva comum

Cancela quando:

- `Origem = 'RESERVA'`
- `CriadoEm IS NOT NULL`
- `CriadoEm <= DATEADD(HOUR, -48, SYSDATETIME())`
- `CanceladoEm IS NULL`
- `Status` diferente de `CANCELADO`
- não é reserva de preferência de renovação.

### 2. Reserva de preferência de renovação

A preferência é identificada principalmente pela própria ocupação/reserva:

- `Origem = 'RESERVA'`
- `TipoVinculoOrigem` contendo `PREFERENCIA RENOVACAO CONTRATO`

Como fallback de compatibilidade, também entra na exceção se o item vinculado diretamente por
`IDFatoControleContratosItemOrigem` tiver `BitPreferencia = 1` e `BitAtivo = 1`.

Essa reserva não é cancelada pela regra de 48 horas.

Ela é cancelada em duas situações:

#### 2.1. Validade/período da própria reserva vencido

`CAST(reserva.DataFim AS date) < CAST(SYSDATETIME() AS date)`

Exemplo:

- `reserva.DataFim = 2026-06-02`
- em `2026-06-02`, não cancela
- em `2026-06-03`, cancela

#### 2.2. Ocupação origem não renovada no prazo

Quando a reserva foi criada por uma ocupação origem, o DAG usa:

`reserva.IDFatoOcupacaoOrigem = ocupacao_origem.IDFatoOcupacaoPaineisEuromidia`

A reserva é cancelada se passou 1 dia depois da `DataFim` da ocupação origem vinculada pelo mesmo `IDFatoControleContratosItemOrigem`.

Exemplo:

- `ocupacao_origem.DataFim = 2026-06-15`
- prazo para renovar: `2026-06-16`
- em `2026-06-17`, cancela a reserva de preferência vinculada ao mesmo item de contrato.

Se `reserva.DataFim` estiver nula em uma preferência e a ocupação origem ainda não tiver vencido o prazo de renovação, o DAG protege a reserva e não cancela automaticamente.

## Segurança / idempotência

O DAG não recancela registros já cancelados porque a atualização exige:

- `CanceladoEm IS NULL`
- `Status <> 'CANCELADO'`

Também usa `UPDLOCK`, `READPAST` e `ROWLOCK` no `UPDATE` para reduzir risco de concorrência.

O próprio `UPDATE` devolve, por meio de `OUTPUT INSERTED`, somente os IDs que
foram realmente cancelados. A transação é confirmada antes de o DAG
`pipeline_notificacoes_euromidia` ser disparado. Quando nenhum registro é
alterado, o DAG de notificações não é chamado.

O disparo é assíncrono: o DAG pai não mantém um worker ocupado aguardando o
DAG de notificações. Isso evita bloqueio quando o executor ou o pool possui
poucos slots disponíveis.

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
    description="Cancela reservas comuns em 48h e cancela preferências vencidas ou sem renovação da ocupação origem após 1 dia.",
    schedule="*/8 * * * *",
    start_date=pendulum.datetime(2026, 5, 13, 0, 0, tz=TIMEZONE_SAO_PAULO),
    catchup=False,
    max_active_runs=1,
    tags=[
        "euromidia",
        "ocupacao",
        "reserva",
        "preferencia-renovacao",
        "ocupacao-origem",
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
        - reserva com TipoVinculoOrigem = PREFERENCIA RENOVAÇÃO CONTRATO: não cancela por 48 horas;
        - reserva de preferência: cancela no primeiro dia após reserva.DataFim;
        - reserva de preferência criada por ocupação origem: cancela se passar 1 dia após a DataFim da ocupação origem e não houver renovação;
        - se vence hoje, ainda não cancela hoje, porque a comparação é feita por data.
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
            data_referencia_cancelamento_preferencia = datas_execucao[
                "DataReferenciaCancelamentoPreferencia"
            ]

            total_elegivel_antes = int(
                conexao.execute(
                    text(SQL_CONTAR_RESERVAS_ELEGIVEIS_CANCELAMENTO)
                ).scalar_one()
                or 0
            )

            total_reserva_comum_elegivel = int(
                conexao.execute(
                    text(SQL_CONTAR_RESERVAS_COMUNS_ELEGIVEIS)
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

            total_preferencia_origem_nao_renovada = int(
                conexao.execute(
                    text(SQL_CONTAR_RESERVAS_PREFERENCIA_ORIGEM_NAO_RENOVADA)
                ).scalar_one()
                or 0
            )

            total_preferencia_sem_data_fim = int(
                conexao.execute(
                    text(SQL_CONTAR_RESERVAS_PREFERENCIA_SEM_DATA_FIM)
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

            amostra_preferencia_sem_data_fim = [
                dict(linha)
                for linha in conexao.execute(
                    text(SQL_LISTAR_AMOSTRA_RESERVAS_PREFERENCIA_SEM_DATA_FIM)
                ).mappings().all()
            ]

            logger.info(
                "Reservas elegíveis para cancelamento: total=%s | "
                "reserva_comum_48h=%s | preferencia_vencida_elegivel=%s | "
                "preferencia_origem_nao_renovada=%s | preferencia_protegida=%s | "
                "preferencia_sem_data_fim=%s | "
                "amostra_elegiveis=%s | amostra_preferencia_protegida=%s | "
                "amostra_preferencia_sem_data_fim=%s",
                total_elegivel_antes,
                total_reserva_comum_elegivel,
                total_preferencia_vencida_elegivel,
                total_preferencia_origem_nao_renovada,
                total_preferencia_protegida,
                total_preferencia_sem_data_fim,
                _normalizar_linhas_para_log(amostra_elegiveis),
                _normalizar_linhas_para_log(amostra_preferencia_protegida),
                _normalizar_linhas_para_log(amostra_preferencia_sem_data_fim),
            )

            resultado_update = conexao.execute(
                text(SQL_CANCELAR_RESERVAS_ELEGIVEIS),
                {"id_usuario_integracao": id_usuario_integracao},
            )

            reservas_canceladas = [
                dict(linha) for linha in resultado_update.mappings().all()
            ]
            ids_reservas_canceladas = [
                int(linha["IDReserva"]) for linha in reservas_canceladas
            ]
            total_cancelado = len(ids_reservas_canceladas)

        # A saída do bloco engine.begin() confirma a transação. Somente depois
        # desse ponto o resumo fica disponível para a tarefa que dispara o DAG
        # de notificações.

        resumo = {
            "dag": NOME_DAG,
            "regra": (
                "Reserva comum cancela após 48h. "
                "Reserva com TipoVinculoOrigem=PREFERENCIA RENOVAÇÃO CONTRATO não cancela por 48h. "
                "Reserva de preferência cancela após reserva.DataFim ou quando a ocupação origem não for renovada "
                "após 1 dia do fim da ocupação origem."
            ),
            "usuario_integracao": nome_usuario_encontrado,
            "id_usuario_integracao": id_usuario_integracao,
            "horas_minimas_reserva_aberta": HORAS_MINIMAS_RESERVA_ABERTA,
            "dias_tolerancia_renovacao_apos_fim_ocupacao_origem": DIAS_TOLERANCIA_RENOVACAO_APOS_FIM_OCUPACAO_ORIGEM,
            "total_elegivel_antes": total_elegivel_antes,
            "total_reserva_comum_elegivel": total_reserva_comum_elegivel,
            "total_preferencia_protegida": total_preferencia_protegida,
            "total_preferencia_vencida_elegivel": total_preferencia_vencida_elegivel,
            "total_preferencia_origem_nao_renovada": total_preferencia_origem_nao_renovada,
            "total_preferencia_sem_data_fim": total_preferencia_sem_data_fim,
            "total_cancelado": int(total_cancelado or 0),
            "ids_reservas_canceladas": ids_reservas_canceladas,
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
            "data_referencia_cancelamento_preferencia": (
                data_referencia_cancelamento_preferencia.isoformat()
                if hasattr(data_referencia_cancelamento_preferencia, "isoformat")
                else str(data_referencia_cancelamento_preferencia)
            ),
            "amostra_elegiveis": _normalizar_linhas_para_log(amostra_elegiveis),
            "amostra_preferencia_protegida": _normalizar_linhas_para_log(
                amostra_preferencia_protegida
            ),
            "amostra_preferencia_sem_data_fim": _normalizar_linhas_para_log(
                amostra_preferencia_sem_data_fim
            ),
        }

        logger.info("Resumo do cancelamento automático de reservas: %s", resumo)

        return resumo

    @task.short_circuit(task_id="verificar_se_houve_cancelamento")
    def verificar_se_houve_cancelamento(resumo: dict[str, Any]) -> bool:
        ids_cancelados = resumo.get("ids_reservas_canceladas") or []
        houve_cancelamento = bool(ids_cancelados)

        if not houve_cancelamento:
            logger.info(
                "Nenhuma reserva foi cancelada nesta execução; o DAG %s não será disparado.",
                NOME_DAG_NOTIFICACOES,
            )

        return houve_cancelamento

    resultado_cancelamento = cancelar_reservas_elegiveis()
    houve_cancelamento = verificar_se_houve_cancelamento(resultado_cancelamento)

    disparar_notificacoes = TriggerDagRunOperator(
        task_id="disparar_pipeline_notificacoes_euromidia",
        trigger_dag_id=NOME_DAG_NOTIFICACOES,
        conf={
            "origem_disparo": NOME_DAG,
            "tipo_evento": "RESERVA CANCELADA AUTOMATICAMENTE",
            "ids_reservas_canceladas": (
                "{{ ti.xcom_pull(task_ids='cancelar_reservas_elegiveis', "
                "key='return_value')['ids_reservas_canceladas'] }}"
            ),
            "data_execucao_cancelamento": (
                "{{ ti.xcom_pull(task_ids='cancelar_reservas_elegiveis', "
                "key='return_value')['data_execucao_sql_server'] }}"
            ),
            "run_id_origem": "{{ run_id }}",
        },
        # O cancelamento já foi confirmado antes deste ponto. O DAG de
        # notificações deve ser disparado sem manter um worker ocupado
        # esperando o DAG filho. Em ambientes com apenas um slot de worker ou
        # de pool, wait_for_completion=True cria um bloqueio: o pai espera e o
        # filho não consegue iniciar.
        wait_for_completion=False,
        retries=0,
    )

    houve_cancelamento >> disparar_notificacoes


pipeline_cancela_reserva()
