import logging
import os
import re
from typing import Any

import pendulum

try:
    from airflow.sdk import dag, task, get_current_context
except ImportError:
    from airflow.decorators import dag, task
    from airflow.operators.python import get_current_context

try:
    from hooks.BancodeDados.SqlServer import HookSqlServer
except ImportError:
    from plugins.hooks.BancodeDados.SqlServer import HookSqlServer


NOME_DAG = "pipeline_prioridade_reservas"
CONN_ID_SQL_SERVER = "mssql_integracao"
FUSO_HORARIO = "America/Sao_Paulo"

ORIGEM_RESERVA = "RESERVA"
STATUS_RESERVADO = "RESERVADO"
STATUS_CANCELADO = "CANCELADO"

TIPO_VINCULO_PREFERENCIA_RENOVACAO = "PREFERENCIA RENOVAÇÃO CONTRATO"

USUARIO_SISTEMA_PADRAO = 1
MESES_MINIMOS_PREFERENCIA_RENOVACAO = 6


def _env_bool(nome_variavel: str, padrao: str = "1") -> bool:
    """Converto variáveis de ambiente de liga/desliga para booleano."""
    valor = (os.getenv(nome_variavel, padrao) or "").strip().lower()
    return valor in ("1", "true", "sim", "s", "yes", "y", "on")


CRIAR_RESERVAS_NA_EXECUCAO_AGENDADA = _env_bool(
    "PIPELINE_PRIORIDADE_RESERVAS_CRIAR_NA_AGENDA",
    "1",
)


DOCUMENTACAO_DAG = """
# pipeline_prioridade_reservas

## Objetivo

Esta DAG controla a criação automática de reservas de preferência de renovação e a prioridade das reservas de ocupação dos painéis da Euromídia.

Ela possui duas funções principais:

1. Criar automaticamente uma reserva de preferência de renovação quando uma ocupação contratual tiver duração comercial de 6 meses ou mais.
2. Recalcular continuamente o campo `ReservaOrdemPrioridade` das reservas ativas, respeitando a ordem de criação dentro do mesmo `CodPonto`, `CodFace` e período cruzado.

---

## Regra de criação da reserva de preferência

A DAG cria reserva de preferência em dois cenários:

1. **Aprovação de contrato pela tela administrativa**  
   Quando recebe `id_contrato` no `dag_run.conf`, processa somente as ocupações daquele contrato.

2. **Varredura pós-upsert de ocupação / execução agendada**  
   Quando roda sem `id_contrato`, pode procurar ocupações elegíveis na tabela `Integracao.Silver.FatoOcupacaoPaineisEuromidia` e criar somente as reservas que ainda não existem.

A varredura agendada fica habilitada por padrão. Para desligar, defina:

```text
PIPELINE_PRIORIDADE_RESERVAS_CRIAR_NA_AGENDA=0
```

---

## Exemplo de configuração enviada por aprovação de contrato

```json
{
  "origem": "flask_admin_aprovacao_contrato",
  "id_contrato": 123,
  "id_usuario_logado": 1
}
```

---

## Exemplo de configuração enviada pelo ETL após o upsert da ocupação

```json
{
  "origem": "pipeline_controle_contratos_euromidia",
  "modo_processamento": "varredura_pos_upsert_ocupacao",
  "processar_todos_elegiveis": true,
  "id_usuario": 1
}
```

---

## Regra de elegibilidade

A reserva automática de preferência só é criada quando a ocupação origem do contrato tiver duração comercial de 6 meses ou mais.

A regra usada é:

```sql
DataFim >= DATEADD(DAY, -1, DATEADD(MONTH, 6, DataInicio))
```

Exemplo:

- DataInicio: 2026-01-01
- 6 meses depois: 2026-07-01
- um dia antes: 2026-06-30

Logo, uma ocupação de 2026-01-01 até 2026-06-30 tem direito à preferência.

---

## Como a reserva futura é criada

A reserva futura começa no dia seguinte ao fim da ocupação origem.

Exemplo:

- Ocupação origem: 2026-01-01 até 2026-06-30
- Reserva de preferência: 2026-07-01 até 2026-12-31

A reserva criada mantém:

- Origem = RESERVA
- Status = RESERVADO
- TipoVinculoOrigem = PREFERENCIA RENOVAÇÃO CONTRATO

Além disso, ela grava:

- IDFatoOcupacaoOrigem
- IDFatoControleContratosItemOrigem
- TipoVinculoOrigem

Isso permite saber exatamente qual ocupação vigente originou a reserva de preferência.

---

## Regra de não duplicidade

A DAG tem três travas contra duplicidade:

1. Gera uma `Referencia` determinística usando hash.
2. Antes de inserir, verifica se já existe registro com a mesma `Referencia`.
3. Antes de inserir, verifica se já existe reserva com o mesmo `IDFatoOcupacaoOrigem` e o mesmo `TipoVinculoOrigem`.

Isso protege contra duplicidade quando:

- o contrato é aprovado pela tela;
- o ETL atualiza a tabela de ocupação depois;
- a DAG roda pelo agendamento de 8 em 8 minutos;
- o mesmo contrato aparece novamente no Excel.

---

## Regra de prioridade das reservas

A prioridade é recalculada para todas as reservas ativas.

Entram na fila apenas registros com:

- Origem = RESERVA
- Status = RESERVADO
- CanceladoEm IS NULL
- DataInicio IS NOT NULL
- DataFim IS NOT NULL

A lógica de cruzamento é:

```sql
reserva_anterior.DataInicio <= reserva_atual.DataFim
AND reserva_anterior.DataFim >= reserva_atual.DataInicio
```

A ordem é definida por:

1. CriadoEm mais antigo
2. IDFatoOcupacaoPaineisEuromidia menor em caso de empate

Assim:

- primeira reserva criada = ReservaOrdemPrioridade 1
- segunda reserva criada = ReservaOrdemPrioridade 2
- terceira reserva criada = ReservaOrdemPrioridade 3

---

## Agendamento

A DAG executa todos os dias, a cada 8 minutos:

```cron
*/8 * * * *
```

---

## Tags

- Euromidia
- Reservas
- SQL Server
"""


def obter_hook_sql_server() -> HookSqlServer:
    """Retorno o hook padrão de conexão com SQL Server."""
    return HookSqlServer(conn_id=CONN_ID_SQL_SERVER)


def _normalizar_bool_conf(valor: Any) -> bool:
    """Normalizo valores booleanos vindos do dag_run.conf."""
    if isinstance(valor, bool):
        return valor

    if valor is None:
        return False

    texto = str(valor).strip().lower()
    return texto in ("1", "true", "sim", "s", "yes", "y", "on")


def _normalizar_int_ou_none(valor: Any) -> int | None:
    """Converto valores do dag_run.conf para inteiro quando possível."""
    if valor in (None, "", 0, "0"):
        return None

    try:
        return int(valor)
    except Exception:
        return None


_PADRAO_PARAMETRO_SQL = re.compile(r"(?<!:):(\w+)")


def _sql_literal(valor: Any) -> str:
    """Converto um valor Python para literal SQL Server seguro para este DAG.

    Eu uso isso somente para comandos DML executados por `executar_comando`,
    porque alguns Hooks aceitam parâmetros apenas em SELECT e não em INSERT/UPDATE.
    """
    if valor is None:
        return "NULL"

    if isinstance(valor, bool):
        return "1" if valor else "0"

    if isinstance(valor, int):
        return str(valor)

    texto = str(valor).replace("'", "''")
    return f"N'{texto}'"


def _substituir_parametros_sql(sql: str, parametros: dict[str, Any]) -> str:
    """Substituo :parametro por literal SQL para rodar em HookSqlServer.executar_comando."""
    def trocar(match: re.Match) -> str:
        nome = match.group(1)
        if nome not in parametros:
            return match.group(0)
        return _sql_literal(parametros[nome])

    return _PADRAO_PARAMETRO_SQL.sub(trocar, sql)


def _executar_comando_parametrizado(hook: HookSqlServer, sql: str, parametros: dict[str, Any]) -> None:
    """Executo comando de escrita com parâmetros convertidos para literais SQL.

    Isso evita o problema de usar `executar_select` para INSERT e evita depender
    da assinatura exata de `executar_comando` no HookSqlServer.
    """
    sql_final = _substituir_parametros_sql(sql, parametros)
    hook.executar_comando(sql_final)



@dag(
    dag_id=NOME_DAG,
    description=(
        "Cria reservas automáticas de preferência de renovação na aprovação, "
        "na varredura pós-upsert de ocupação e recalcula prioridade por face/período."
    ),
    schedule="*/8 * * * *",
    start_date=pendulum.datetime(2026, 1, 1, tz=FUSO_HORARIO),
    catchup=False,
    max_active_runs=1,
    tags=["Euromidia", "Reservas", "SQL Server"],
    doc_md=DOCUMENTACAO_DAG,
)
def pipeline_prioridade_reservas():

    @task(task_id="garantir_colunas_vinculo_reserva")
    def garantir_colunas_vinculo_reserva() -> dict[str, Any]:
        """
        Garanto que a tabela de ocupação possui as colunas necessárias
        para relacionar a reserva futura com a ocupação vigente que gerou
        o direito de preferência.
        """

        sql = """
        SET NOCOUNT ON;

        IF NOT EXISTS (
            SELECT 1
            FROM Integracao.sys.columns c
            INNER JOIN Integracao.sys.objects o
                ON o.object_id = c.object_id
            INNER JOIN Integracao.sys.schemas s
                ON s.schema_id = o.schema_id
            WHERE
                s.name = 'Silver'
                AND o.name = 'FatoOcupacaoPaineisEuromidia'
                AND c.name = 'IDFatoOcupacaoOrigem'
        )
        BEGIN
            ALTER TABLE Integracao.Silver.FatoOcupacaoPaineisEuromidia
            ADD IDFatoOcupacaoOrigem INT NULL;
        END;

        IF NOT EXISTS (
            SELECT 1
            FROM Integracao.sys.columns c
            INNER JOIN Integracao.sys.objects o
                ON o.object_id = c.object_id
            INNER JOIN Integracao.sys.schemas s
                ON s.schema_id = o.schema_id
            WHERE
                s.name = 'Silver'
                AND o.name = 'FatoOcupacaoPaineisEuromidia'
                AND c.name = 'IDFatoControleContratosItemOrigem'
        )
        BEGIN
            ALTER TABLE Integracao.Silver.FatoOcupacaoPaineisEuromidia
            ADD IDFatoControleContratosItemOrigem INT NULL;
        END;

        IF NOT EXISTS (
            SELECT 1
            FROM Integracao.sys.columns c
            INNER JOIN Integracao.sys.objects o
                ON o.object_id = c.object_id
            INNER JOIN Integracao.sys.schemas s
                ON s.schema_id = o.schema_id
            WHERE
                s.name = 'Silver'
                AND o.name = 'FatoOcupacaoPaineisEuromidia'
                AND c.name = 'TipoVinculoOrigem'
        )
        BEGIN
            ALTER TABLE Integracao.Silver.FatoOcupacaoPaineisEuromidia
            ADD TipoVinculoOrigem NVARCHAR(80) NULL;
        END;
        """

        hook = obter_hook_sql_server()
        hook.executar_comando(sql)

        logging.info("Colunas de vínculo verificadas/criadas com sucesso.")

        return {
            "status": "ok",
            "mensagem": "Estrutura de vínculo da reserva verificada.",
        }

    @task(task_id="criar_reservas_preferencia_renovacao")
    def criar_reservas_preferencia_renovacao() -> dict[str, Any]:
        """
        Crio reservas automáticas de preferência de renovação a partir das ocupações CONTRATO.

        Regra operacional:
        1. O admin_views.py aprova o contrato e grava a ocupação com Origem = CONTRATO.
        2. Depois do commit, o admin_views.py dispara esta DAG com id_contrato.
        3. Esta task procura ocupações elegíveis daquele contrato e cria reservas com Origem = RESERVA.

        Importante:
        - uso executar_comando para o INSERT, porque executar_select pode não confirmar DML
          dependendo da implementação do HookSqlServer;
        - faço SELECTs separados antes/depois para diagnóstico e retorno.
        """

        contexto = get_current_context()
        dag_run = contexto.get("dag_run")
        conf = dag_run.conf if dag_run and dag_run.conf else {}
        run_id = str(getattr(dag_run, "run_id", "") or "manual_sem_run_id")

        id_contrato = _normalizar_int_ou_none(
            conf.get("id_contrato")
            or conf.get("id_fato_controle_contratos")
            or conf.get("IDFatoControleContratos")
            or conf.get("IDFatoControleContratosEuromidia")
        )

        id_usuario = _normalizar_int_ou_none(
            conf.get("id_usuario")
            or conf.get("id_usuario_logado")
            or conf.get("IDUsuario")
        ) or USUARIO_SISTEMA_PADRAO

        modo_processamento = str(conf.get("modo_processamento") or "").strip().lower()
        processar_todos_elegiveis = _normalizar_bool_conf(conf.get("processar_todos_elegiveis"))

        veio_de_varredura_pos_upsert = modo_processamento in (
            "varredura_pos_upsert_ocupacao",
            "pos_upsert_ocupacao",
            "upsert_ocupacao",
            "etl_ocupacao",
        )

        processar_escopo_total = (
            id_contrato is None
            and (
                processar_todos_elegiveis
                or veio_de_varredura_pos_upsert
                or CRIAR_RESERVAS_NA_EXECUCAO_AGENDADA
            )
        )

        if id_contrato is None and not processar_escopo_total:
            logging.info(
                "Execução sem id_contrato e varredura agendada desabilitada. "
                "Nenhuma reserva de preferência será criada nesta etapa. conf=%s",
                conf,
            )

            return {
                "status": "ignorado",
                "motivo": "Sem id_contrato e sem permissão para varredura total.",
                "reservas_criadas": 0,
            }

        escopo = "contrato" if id_contrato is not None else "todos_elegiveis"
        marcador_execucao = f"DAG_RUN_PRIORIDADE_RESERVAS={run_id}"[:450]

        parametros = {
            "id_contrato": id_contrato,
            "id_usuario": int(id_usuario),
            "origem_reserva": ORIGEM_RESERVA,
            "status_reservado": STATUS_RESERVADO,
            "status_cancelado": STATUS_CANCELADO,
            "tipo_vinculo": TIPO_VINCULO_PREFERENCIA_RENOVACAO,
            "meses_minimos": MESES_MINIMOS_PREFERENCIA_RENOVACAO,
            "marcador_execucao": marcador_execucao,
        }

        logging.info(
            "Iniciando criação de reserva de preferência. escopo=%s | id_contrato=%s | modo=%s | conf=%s",
            escopo,
            id_contrato,
            modo_processamento,
            conf,
        )

        sql_diagnostico = """
        SET NOCOUNT ON;

        ;WITH OcupacoesOrigem AS
        (
            SELECT
                O.IDFatoOcupacaoPaineisEuromidia AS IDFatoOcupacaoOrigem,
                COALESCE(O.IDFatoControleContratos, I.IDFatoControleContratoEuromidia) AS IDFatoControleContratos,
                O.CodPonto,
                O.CodFace,
                CAST(O.DataInicio AS DATE) AS DataInicio,
                CAST(O.DataFim AS DATE) AS DataFim,
                COALESCE(
                    O.IDFatoControleContratosItemOrigem,
                    I.IDFatoControleContratosItensEuromidia
                ) AS IDFatoControleContratosItemOrigem,
                DATEDIFF(
                    MONTH,
                    CAST(O.DataInicio AS DATE),
                    DATEADD(DAY, 1, CAST(O.DataFim AS DATE))
                ) AS QuantidadeMesesContrato,
                DATEADD(DAY, 1, CAST(O.DataFim AS DATE)) AS DataInicioReserva
            FROM Integracao.Silver.FatoOcupacaoPaineisEuromidia AS O WITH (NOLOCK)
            OUTER APPLY
            (
                SELECT TOP (1)
                    I2.IDFatoControleContratosItensEuromidia,
                    I2.IDFatoControleContratoEuromidia
                FROM Integracao.Silver.FatoControleContratosItensEuromidia AS I2 WITH (NOLOCK)
                WHERE
                    I2.CodPonto = O.CodPonto
                    AND I2.CodFace = O.CodFace
                    AND (:id_contrato IS NULL OR I2.IDFatoControleContratoEuromidia = :id_contrato)
                    AND (
                        O.IDFatoControleContratos IS NULL
                        OR I2.IDFatoControleContratoEuromidia = O.IDFatoControleContratos
                    )
                    AND CAST(I2.DataInicioPrevisto AS DATE) = CAST(O.DataInicio AS DATE)
                    AND CAST(COALESCE(I2.DataCancelamento, I2.DataTerminoPrevisto) AS DATE) = CAST(O.DataFim AS DATE)
                ORDER BY
                    I2.IDFatoControleContratosItensEuromidia DESC
            ) AS I
            WHERE
                (:id_contrato IS NULL OR COALESCE(O.IDFatoControleContratos, I.IDFatoControleContratoEuromidia) = :id_contrato)
                AND COALESCE(O.IDFatoControleContratos, I.IDFatoControleContratoEuromidia) IS NOT NULL
                AND O.CodPonto IS NOT NULL
                AND O.CodFace IS NOT NULL
                AND O.DataInicio IS NOT NULL
                AND O.DataFim IS NOT NULL
                AND O.CanceladoEm IS NULL
                AND UPPER(LTRIM(RTRIM(ISNULL(O.Status, '')))) <> UPPER(LTRIM(RTRIM(:status_cancelado)))
                AND UPPER(LTRIM(RTRIM(ISNULL(O.Origem, '')))) <> UPPER(LTRIM(RTRIM(:origem_reserva)))
        ),
        Elegiveis AS
        (
            SELECT
                O.*
            FROM OcupacoesOrigem AS O
            WHERE
                O.DataFim >= DATEADD(DAY, -1, DATEADD(MONTH, :meses_minimos, O.DataInicio))
                AND O.QuantidadeMesesContrato >= :meses_minimos
        ),
        ElegiveisComStatusReserva AS
        (
            SELECT
                E.IDFatoOcupacaoOrigem,
                CASE
                    WHEN R.IDFatoOcupacaoPaineisEuromidia IS NULL THEN 0
                    ELSE 1
                END AS JaExisteReserva
            FROM Elegiveis AS E
            OUTER APPLY
            (
                SELECT TOP (1)
                    R2.IDFatoOcupacaoPaineisEuromidia
                FROM Integracao.Silver.FatoOcupacaoPaineisEuromidia AS R2 WITH (NOLOCK)
                WHERE
                    R2.IDFatoOcupacaoOrigem = E.IDFatoOcupacaoOrigem
                    AND UPPER(LTRIM(RTRIM(ISNULL(R2.TipoVinculoOrigem, '')))) = UPPER(LTRIM(RTRIM(:tipo_vinculo)))
                    AND UPPER(LTRIM(RTRIM(ISNULL(R2.Origem, '')))) = UPPER(LTRIM(RTRIM(:origem_reserva)))
                ORDER BY
                    R2.IDFatoOcupacaoPaineisEuromidia DESC
            ) AS R
        )
        SELECT
            COUNT(1) AS ocupacoes_elegiveis,
            COALESCE(SUM(JaExisteReserva), 0) AS reservas_ja_existentes,
            COALESCE(SUM(CASE WHEN JaExisteReserva = 0 THEN 1 ELSE 0 END), 0) AS reservas_pendentes
        FROM ElegiveisComStatusReserva;
        """


        sql_insert = """
        SET NOCOUNT ON;

        ;WITH OcupacoesOrigem AS
        (
            SELECT
                O.IDFatoOcupacaoPaineisEuromidia AS IDFatoOcupacaoOrigem,
                COALESCE(O.IDFatoControleContratos, I.IDFatoControleContratoEuromidia) AS IDFatoControleContratos,
                O.CodPonto,
                O.CodFace,
                O.IDPainelEuromidia,
                CAST(O.DataInicio AS DATE) AS DataInicio,
                CAST(O.DataFim AS DATE) AS DataFim,
                O.LoopInicio,
                O.LoopFim,
                O.SpanQtd,
                O.Cota,
                O.MarcaExibida,
                O.Vendedor,
                O.IDVendedor,
                O.IDCliente,
                O.NumeroContrato,
                O.NumeroPrevia,
                COALESCE(
                    O.IDFatoControleContratosItemOrigem,
                    I.IDFatoControleContratosItensEuromidia
                ) AS IDFatoControleContratosItemOrigem,
                DATEDIFF(
                    MONTH,
                    CAST(O.DataInicio AS DATE),
                    DATEADD(DAY, 1, CAST(O.DataFim AS DATE))
                ) AS QuantidadeMesesContrato,
                DATEADD(DAY, 1, CAST(O.DataFim AS DATE)) AS DataInicioReserva
            FROM Integracao.Silver.FatoOcupacaoPaineisEuromidia AS O WITH (UPDLOCK, HOLDLOCK)
            OUTER APPLY
            (
                SELECT TOP (1)
                    I2.IDFatoControleContratosItensEuromidia,
                    I2.IDFatoControleContratoEuromidia
                FROM Integracao.Silver.FatoControleContratosItensEuromidia AS I2 WITH (NOLOCK)
                WHERE
                    I2.CodPonto = O.CodPonto
                    AND I2.CodFace = O.CodFace
                    AND (:id_contrato IS NULL OR I2.IDFatoControleContratoEuromidia = :id_contrato)
                    AND (
                        O.IDFatoControleContratos IS NULL
                        OR I2.IDFatoControleContratoEuromidia = O.IDFatoControleContratos
                    )
                    AND CAST(I2.DataInicioPrevisto AS DATE) = CAST(O.DataInicio AS DATE)
                    AND CAST(COALESCE(I2.DataCancelamento, I2.DataTerminoPrevisto) AS DATE) = CAST(O.DataFim AS DATE)
                ORDER BY
                    I2.IDFatoControleContratosItensEuromidia DESC
            ) AS I
            WHERE
                (:id_contrato IS NULL OR COALESCE(O.IDFatoControleContratos, I.IDFatoControleContratoEuromidia) = :id_contrato)
                AND COALESCE(O.IDFatoControleContratos, I.IDFatoControleContratoEuromidia) IS NOT NULL
                AND O.CodPonto IS NOT NULL
                AND O.CodFace IS NOT NULL
                AND O.DataInicio IS NOT NULL
                AND O.DataFim IS NOT NULL
                AND O.CanceladoEm IS NULL
                AND UPPER(LTRIM(RTRIM(ISNULL(O.Status, '')))) <> UPPER(LTRIM(RTRIM(:status_cancelado)))
                AND UPPER(LTRIM(RTRIM(ISNULL(O.Origem, '')))) <> UPPER(LTRIM(RTRIM(:origem_reserva)))
                AND CAST(O.DataFim AS DATE) >= DATEADD(
                    DAY,
                    -1,
                    DATEADD(MONTH, :meses_minimos, CAST(O.DataInicio AS DATE))
                )
        ),
        ReservasCalculadas AS
        (
            SELECT
                O.IDFatoOcupacaoOrigem,
                O.IDFatoControleContratos,
                O.IDFatoControleContratosItemOrigem,
                O.CodPonto,
                O.CodFace,
                O.IDPainelEuromidia,
                O.LoopInicio,
                O.LoopFim,
                CASE
                    WHEN O.SpanQtd IS NOT NULL THEN O.SpanQtd
                    WHEN O.Cota = 1080 THEN 2
                    ELSE 1
                END AS SpanQtdCalculado,
                O.Cota,
                O.MarcaExibida,
                O.Vendedor,
                O.IDVendedor,
                O.IDCliente,
                O.NumeroContrato,
                O.NumeroPrevia,
                O.DataInicio AS DataInicioOrigem,
                O.DataFim AS DataFimOrigem,
                O.DataInicioReserva,
                DATEADD(
                    DAY,
                    -1,
                    DATEADD(MONTH, O.QuantidadeMesesContrato, O.DataInicioReserva)
                ) AS DataFimReserva,
                CONCAT(
                    'PREFRENOV-',
                    LEFT(
                        CONVERT(
                            VARCHAR(64),
                            HASHBYTES(
                                'SHA2_256',
                                CONCAT(
                                    :tipo_vinculo, '|',
                                    CAST(O.IDFatoOcupacaoOrigem AS VARCHAR(30)), '|',
                                    CAST(ISNULL(O.IDFatoControleContratosItemOrigem, 0) AS VARCHAR(30)), '|',
                                    CAST(O.IDFatoControleContratos AS VARCHAR(30)), '|',
                                    CAST(O.CodPonto AS VARCHAR(30)), '|',
                                    CAST(O.CodFace AS VARCHAR(100)), '|',
                                    CONVERT(VARCHAR(10), O.DataInicioReserva, 120), '|',
                                    CONVERT(
                                        VARCHAR(10),
                                        DATEADD(
                                            DAY,
                                            -1,
                                            DATEADD(MONTH, O.QuantidadeMesesContrato, O.DataInicioReserva)
                                        ),
                                        120
                                    )
                                )
                            ),
                            2
                        ),
                        44
                    )
                ) AS ReferenciaPreferencia
            FROM OcupacoesOrigem AS O
            WHERE
                O.QuantidadeMesesContrato >= :meses_minimos
        )
        INSERT INTO Integracao.Silver.FatoOcupacaoPaineisEuromidia
        (
            DataAtualizacao,
            Referencia,
            CodPonto,
            CodFace,
            IDPainelEuromidia,
            Origem,
            Status,
            DataInicio,
            DataFim,
            LoopInicio,
            LoopFim,
            SpanQtd,
            Cota,
            MarcaExibida,
            Vendedor,
            IDVendedor,
            IDCliente,
            IDFatoControleContratos,
            NumeroContrato,
            NumeroPrevia,
            TextoOriginal,
            CriadoEm,
            CriadoPorIDUsuario,
            ExpiraEm,
            CanceladoEm,
            CanceladoPorIDUsuario,
            Observacao,
            Dias,
            ReservaOrdemPrioridade,
            IDFatoOcupacaoOrigem,
            IDFatoControleContratosItemOrigem,
            TipoVinculoOrigem
        )
        SELECT
            SYSDATETIME() AS DataAtualizacao,
            R.ReferenciaPreferencia AS Referencia,
            R.CodPonto,
            R.CodFace,
            R.IDPainelEuromidia,
            :origem_reserva AS Origem,
            :status_reservado AS Status,
            R.DataInicioReserva AS DataInicio,
            R.DataFimReserva AS DataFim,
            R.LoopInicio,
            R.LoopFim,
            R.SpanQtdCalculado AS SpanQtd,
            R.Cota,
            R.MarcaExibida,
            R.Vendedor,
            R.IDVendedor,
            R.IDCliente,
            R.IDFatoControleContratos,
            R.NumeroContrato,
            R.NumeroPrevia,
            LEFT(CONCAT(
                'Reserva automática criada por preferência de renovação de contrato. ',
                'Ocupação origem: ',
                CAST(R.IDFatoOcupacaoOrigem AS VARCHAR(30)),
                '. Período origem: ',
                CONVERT(VARCHAR(10), R.DataInicioOrigem, 103),
                ' até ',
                CONVERT(VARCHAR(10), R.DataFimOrigem, 103),
                '.'
            ), 1000) AS TextoOriginal,
            SYSDATETIME() AS CriadoEm,
            :id_usuario AS CriadoPorIDUsuario,
            NULL AS ExpiraEm,
            NULL AS CanceladoEm,
            NULL AS CanceladoPorIDUsuario,
            LEFT(CONCAT(
                'Preferência de renovação gerada automaticamente. ',
                'TipoVinculoOrigem=',
                :tipo_vinculo,
                '. ',
                :marcador_execucao
            ), 500) AS Observacao,
            DATEDIFF(DAY, R.DataInicioReserva, R.DataFimReserva) + 1 AS Dias,
            NULL AS ReservaOrdemPrioridade,
            R.IDFatoOcupacaoOrigem,
            R.IDFatoControleContratosItemOrigem,
            :tipo_vinculo AS TipoVinculoOrigem
        FROM ReservasCalculadas AS R
        WHERE NOT EXISTS
        (
            SELECT 1
            FROM Integracao.Silver.FatoOcupacaoPaineisEuromidia AS EXISTENTE WITH (UPDLOCK, HOLDLOCK)
            WHERE EXISTENTE.Referencia = R.ReferenciaPreferencia
        )
        AND NOT EXISTS
        (
            SELECT 1
            FROM Integracao.Silver.FatoOcupacaoPaineisEuromidia AS EXISTENTE WITH (UPDLOCK, HOLDLOCK)
            WHERE
                EXISTENTE.IDFatoOcupacaoOrigem = R.IDFatoOcupacaoOrigem
                AND UPPER(LTRIM(RTRIM(ISNULL(EXISTENTE.TipoVinculoOrigem, '')))) = UPPER(LTRIM(RTRIM(:tipo_vinculo)))
                AND UPPER(LTRIM(RTRIM(ISNULL(EXISTENTE.Origem, '')))) = UPPER(LTRIM(RTRIM(:origem_reserva)))
        );
        """

        sql_contar_criadas_execucao = """
        SET NOCOUNT ON;

        SELECT
            COUNT(1) AS reservas_criadas
        FROM Integracao.Silver.FatoOcupacaoPaineisEuromidia AS O WITH (NOLOCK)
        WHERE
            O.Origem = :origem_reserva
            AND O.Status = :status_reservado
            AND O.TipoVinculoOrigem = :tipo_vinculo
            AND O.Observacao LIKE '%' + :marcador_execucao + '%';
        """

        hook = obter_hook_sql_server()

        diagnostico_antes_rows = hook.executar_select(sql_diagnostico, parametros) or []
        diagnostico_antes = dict(diagnostico_antes_rows[0]) if diagnostico_antes_rows else {}

        logging.info(
            "Diagnóstico antes do INSERT de reservas. escopo=%s | id_contrato=%s | diagnostico=%s",
            escopo,
            id_contrato,
            diagnostico_antes,
        )

        if not int((diagnostico_antes or {}).get("reservas_pendentes") or 0):
            logging.warning(
                "Nenhuma reserva pendente encontrada antes do INSERT. "
                "Verifique principalmente: IDFatoControleContratos preenchido ou inferível pelo item, "
                "Origem diferente de RESERVA, Status diferente de CANCELADO, datas válidas e reserva já existente. "
                "diagnostico=%s",
                diagnostico_antes,
            )

        _executar_comando_parametrizado(hook, sql_insert, parametros)

        criadas_rows = hook.executar_select(sql_contar_criadas_execucao, parametros) or []
        reservas_criadas = int((criadas_rows[0] or {}).get("reservas_criadas") or 0) if criadas_rows else 0

        logging.info(
            "Criação de reserva de preferência finalizada. "
            "escopo=%s | id_contrato=%s | reservas_criadas=%s | diagnostico_antes=%s | tipo_vinculo=%s",
            escopo,
            id_contrato,
            reservas_criadas,
            diagnostico_antes,
            TIPO_VINCULO_PREFERENCIA_RENOVACAO,
        )

        return {
            "status": "ok",
            "escopo": escopo,
            "id_contrato": id_contrato,
            "tipo_vinculo": TIPO_VINCULO_PREFERENCIA_RENOVACAO,
            "reservas_criadas": reservas_criadas,
            "diagnostico_antes": diagnostico_antes,
        }

    @task(task_id="recalcular_prioridade_reservas")
    def recalcular_prioridade_reservas() -> dict[str, Any]:
        """
        Recalculo a fila de prioridade das reservas ativas.

        Regra:
        Para cada reserva ativa, conto quantas reservas anteriores existem
        no mesmo CodPonto/CodFace e com período cruzado.

        A prioridade é:

        1 + quantidade de reservas anteriores que cruzam o período.
        """

        parametros = {
            "origem_reserva": ORIGEM_RESERVA,
            "status_reservado": STATUS_RESERVADO,
        }

        sql = """
        SET NOCOUNT ON;

        DECLARE @ReservasAtualizadas TABLE
        (
            IDFatoOcupacaoPaineisEuromidia INT NOT NULL,
            PrioridadeAnterior INT NULL,
            NovaPrioridade INT NOT NULL
        );

        ;WITH ReservasAtivas AS
        (
            SELECT
                O.IDFatoOcupacaoPaineisEuromidia,
                O.CodPonto,
                O.CodFace,
                CAST(O.DataInicio AS DATE) AS DataInicio,
                CAST(O.DataFim AS DATE) AS DataFim,
                O.CriadoEm,
                O.ReservaOrdemPrioridade
            FROM Integracao.Silver.FatoOcupacaoPaineisEuromidia AS O
            WHERE
                O.Origem = :origem_reserva
                AND O.Status = :status_reservado
                AND O.CanceladoEm IS NULL
                AND O.CodPonto IS NOT NULL
                AND O.CodFace IS NOT NULL
                AND O.DataInicio IS NOT NULL
                AND O.DataFim IS NOT NULL
        ),
        PrioridadesCalculadas AS
        (
            SELECT
                atual.IDFatoOcupacaoPaineisEuromidia,
                atual.ReservaOrdemPrioridade AS PrioridadeAnterior,
                1 + COUNT(anterior.IDFatoOcupacaoPaineisEuromidia) AS NovaPrioridade
            FROM ReservasAtivas AS atual
            LEFT JOIN ReservasAtivas AS anterior
                ON anterior.CodPonto = atual.CodPonto
                AND anterior.CodFace = atual.CodFace
                AND anterior.DataInicio <= atual.DataFim
                AND anterior.DataFim >= atual.DataInicio
                AND
                (
                    anterior.CriadoEm < atual.CriadoEm
                    OR
                    (
                        anterior.CriadoEm = atual.CriadoEm
                        AND anterior.IDFatoOcupacaoPaineisEuromidia < atual.IDFatoOcupacaoPaineisEuromidia
                    )
                )
            GROUP BY
                atual.IDFatoOcupacaoPaineisEuromidia,
                atual.ReservaOrdemPrioridade
        )
        UPDATE destino
        SET
            destino.ReservaOrdemPrioridade = prioridade.NovaPrioridade,
            destino.DataAtualizacao = SYSDATETIME()
        OUTPUT
            inserted.IDFatoOcupacaoPaineisEuromidia,
            deleted.ReservaOrdemPrioridade,
            inserted.ReservaOrdemPrioridade
        INTO @ReservasAtualizadas
        (
            IDFatoOcupacaoPaineisEuromidia,
            PrioridadeAnterior,
            NovaPrioridade
        )
        FROM Integracao.Silver.FatoOcupacaoPaineisEuromidia AS destino
        INNER JOIN PrioridadesCalculadas AS prioridade
            ON prioridade.IDFatoOcupacaoPaineisEuromidia = destino.IDFatoOcupacaoPaineisEuromidia
        WHERE
            ISNULL(destino.ReservaOrdemPrioridade, -1) <> prioridade.NovaPrioridade;

        SELECT
            COUNT(1) AS reservas_atualizadas
        FROM @ReservasAtualizadas;
        """

        hook = obter_hook_sql_server()
        resultado = hook.executar_select(sql, parametros)

        reservas_atualizadas = 0
        if resultado:
            reservas_atualizadas = int(resultado[0].get("reservas_atualizadas") or 0)

        logging.info(
            "Recalculo de prioridade finalizado. reservas_atualizadas=%s",
            reservas_atualizadas,
        )

        return {
            "status": "ok",
            "reservas_atualizadas": reservas_atualizadas,
        }

    estrutura = garantir_colunas_vinculo_reserva()
    criacao = criar_reservas_preferencia_renovacao()
    prioridade = recalcular_prioridade_reservas()

    estrutura >> criacao >> prioridade


dag_pipeline_prioridade_reservas = pipeline_prioridade_reservas()
