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

ORIGEM_CONTRATO = "CONTRATO"
ORIGEM_RESERVA = "RESERVA"
STATUS_RESERVADO = "RESERVADO"
STATUS_CANCELADO = "CANCELADO"

TIPO_VINCULO_PREFERENCIA_RENOVACAO = "PREFERENCIA RENOVAÇÃO CONTRATO"
TIPO_RESERVA_PREFERENCIA_RENOVACAO = 2

USUARIO_SISTEMA_PADRAO = 1
MESES_MINIMOS_PREFERENCIA_RENOVACAO = 6


def _env_bool(nome_variavel: str, padrao: str = "1") -> bool:
    """Converto variáveis de ambiente de liga/desliga para booleano."""
    valor = (os.getenv(nome_variavel, padrao) or "").strip().lower()
    return valor in ("1", "true", "sim", "s", "yes", "y", "on")


def _env_int(nome_variavel: str, padrao: int, minimo: int | None = None, maximo: int | None = None) -> int:
    """Converto variável de ambiente para inteiro com limites opcionais."""
    try:
        valor = int(str(os.getenv(nome_variavel, str(padrao)) or padrao).strip())
    except Exception:
        valor = int(padrao)

    if minimo is not None:
        valor = max(int(minimo), valor)
    if maximo is not None:
        valor = min(int(maximo), valor)

    return valor


# A criação por trigger continua sendo o caminho mais rápido, mas a varredura
# agendada fica habilitada por padrão como garantia de consistência. Assim uma
# falha/atraso no disparo do Kanban/Admin não deixa uma ocupação elegível apenas
# com BitPreferencia=1 e sem a reserva física correspondente.
CRIAR_RESERVAS_NA_EXECUCAO_AGENDADA = _env_bool(
    "PIPELINE_PRIORIDADE_RESERVAS_CRIAR_NA_AGENDA",
    "1",
)

HABILITAR_AGENDAMENTO_AUTOMATICO = _env_bool(
    "PIPELINE_PRIORIDADE_RESERVAS_HABILITAR_AGENDAMENTO",
    "1",
)

# O cron anterior era */8, ou seja, a cada 8 minutos.
# Reduzindo aproximadamente 70% do intervalo: 8 min * 30% = 2,4 min.
# Como cron trabalha em minuto inteiro, uso 2 minutos para ficar mais rápido
# e manter uma folga de segurança com max_active_runs=1.
INTERVALO_MINUTOS_AGENDAMENTO = _env_int(
    "PIPELINE_PRIORIDADE_RESERVAS_INTERVALO_MINUTOS",
    2,
    minimo=1,
    maximo=60,
)

CRON_AGENDAMENTO_DAG = (
    "* * * * *"
    if INTERVALO_MINUTOS_AGENDAMENTO <= 1
    else f"*/{INTERVALO_MINUTOS_AGENDAMENTO} * * * *"
)

# Regra combinada: o Kanban/Admin pode acionar a DAG imediatamente e o cron
# reconcilia ocupações elegíveis que eventualmente ficaram sem reserva.
# Os NOT EXISTS do INSERT mantêm a operação idempotente.
SCHEDULE_DAG = CRON_AGENDAMENTO_DAG if HABILITAR_AGENDAMENTO_AUTOMATICO else None


DOCUMENTACAO_DAG = """
# pipeline_prioridade_reservas

## Objetivo

Esta DAG controla a criação automática de reservas de preferência de renovação e a prioridade das reservas de ocupação dos painéis da Euromídia.

Ela possui duas funções principais:

1. Criar automaticamente uma reserva de preferência de renovação quando uma ocupação contratual tiver duração comercial de 6 meses ou mais.
2. Marcar `FatoControleContratosItensMidia.BitPreferencia = 1` no item de contrato que gerou a reserva.
3. Recalcular continuamente o campo `ReservaOrdemPrioridade` das reservas ativas, respeitando a ordem de criação dentro do mesmo `CodPonto`, `CodFace` e período cruzado.

---

## Regra de criação da reserva de preferência

A DAG cria reserva de preferência a partir das ocupações já gravadas/efetivadas com Origem = CONTRATO:

1. **Aprovação de contrato pela tela administrativa**  
   Quando recebe `id_contrato` no `dag_run.conf`, processa somente as ocupações daquele contrato que já nasceram/foram efetivadas com `Origem = CONTRATO`.

2. **Varredura pós-upsert de ocupação / execução agendada**  
   Quando roda sem `id_contrato`, pode procurar ocupações elegíveis na tabela `Integracao.Silver.FatoOcupacaoPaineisMidia`, sempre filtrando `Origem = CONTRATO`, e criar somente as reservas que ainda não existem.

A varredura agendada fica habilitada por padrão como reconciliação. A regra combinada é: o Kanban cria/efetiva a ocupação, confirma o commit e aciona esta DAG com o ID da ocupação ou do contrato; o cron também recupera ocupações elegíveis que eventualmente ficaram sem preferência.

Para habilitar varredura automática por cron, defina explicitamente:

```text
PIPELINE_PRIORIDADE_RESERVAS_HABILITAR_AGENDAMENTO=1
PIPELINE_PRIORIDADE_RESERVAS_CRIAR_NA_AGENDA=1
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
  "origem": "pipeline_controle_contratos_midia",
  "modo_processamento": "varredura_pos_upsert_ocupacao",
  "processar_todos_elegiveis": true,
  "id_usuario": 1
}
```

---

## Regra de elegibilidade

A reserva automática de preferência só é criada quando a ocupação origem do contrato tiver duração comercial de 6 meses ou mais.

Para uma ocupação fatiada, as fatias não são avaliadas separadamente. A DAG
consulta `FatoAgendamentoFaceContrato` e considera o período consolidado entre
a menor `DataInicio` e a maior `DataTermino` vinculadas à mesma ocupação.

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

A reserva futura começa obrigatoriamente no dia seguinte ao fim consolidado da
ocupação de origem. O encaixe tipo Tetris procura outro slot/linha livre no
mesmo período; ele nunca empurra a data para D+2, D+3 ou dias posteriores.

Se nenhum slot comportar todo o período a partir de D+1, a reserva não é criada
em uma data incorreta. A execução seguinte volta a avaliá-la depois que a grade
for liberada.

Exemplo:

- Ocupação origem: 2026-01-01 até 2026-06-30
- Reserva de preferência: 2026-07-01 até 2026-12-31

A reserva criada mantém:

- Origem = RESERVA
- Status = RESERVADO
- MarcaExibida = marca comercial da ocupação contratual, nunca razão social do cliente
- TipoVinculoOrigem = PREFERENCIA RENOVAÇÃO CONTRATO
- TipoReserva = 2

Além disso, ela grava:

- IDFatoOcupacaoOrigem
- IDFatoControleContratosItemOrigem
- TipoVinculoOrigem

Isso permite saber exatamente qual ocupação vigente originou a reserva de preferência.

Antes de inserir, a DAG aplica a regra de encaixe tipo Tetris: calcula as faces/slots disponíveis a partir de DimPaineisMidia + DimFacesPaineis, valida que o CodFace pertence ao IDDimPaineisMidia, considera contratos, ocupações e reservas ativas no período futuro e só grava a reserva se existir um bloco livre suficiente para a cota.

A quantidade de posições da grade vem de `Integracao.Silver.DimPaineisMidia.QuantidadeFaces`. A DAG não inventa capacidade fixa de 16 faces: se o painel 1137 está com `QuantidadeFaces = 16`, usa 16; se outro painel estiver com outra quantidade, usa a quantidade cadastrada; se a quantidade estiver ausente/inválida, a ocupação não é elegível até o cadastro ser corrigido.

A resolução do painel da ocupação/reserva não é feita apenas por `CodPonto`. A DAG valida o `CodFace` pela `Integracao.Silver.DimFacesPaineis`, usando `DimFacesPaineis.IDDimPaineisMidia` como chave para a `DimPaineisMidia`. Assim o sistema só cria reserva em face cadastrada naquele painel e só considera conflito dentro do mesmo painel/slot.

Quando a ocupação é elegível, a DAG também marca o item original em `FatoControleContratosItensMidia.BitPreferencia = 1`, para não depender de outro DAG rodar depois.

---

## Regra de não duplicidade

A DAG tem três travas contra duplicidade:

1. Gera uma `Referencia` determinística usando hash.
2. Antes de inserir, verifica se já existe registro com a mesma `Referencia`.
3. Antes de inserir, verifica se já existe reserva com o mesmo `IDFatoOcupacaoOrigem` ou com o mesmo `IDFatoControleContratosItemOrigem` e o mesmo `TipoVinculoOrigem`.

A trava por item é importante porque uma mesma ocupação comercial pode aparecer em mais de uma linha física na grade, especialmente em painel digital/1080. Isso protege contra duplicidade quando:

- o contrato é aprovado pela tela;
- o ETL atualiza a tabela de ocupação depois;
- a DAG roda novamente pelo agendamento configurado;
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
2. IDFatoOcupacaoPaineisMidia menor em caso de empate

Assim:

- primeira reserva criada = ReservaOrdemPrioridade 1
- segunda reserva criada = ReservaOrdemPrioridade 2
- terceira reserva criada = ReservaOrdemPrioridade 3

---

## Agendamento

Por padrão, a DAG executa pelo cron e também pode ser acionada pelo Kanban depois da criação/efetivação da ocupação.

O cron só será usado se `PIPELINE_PRIORIDADE_RESERVAS_HABILITAR_AGENDAMENTO=1`. Caso contrário, `schedule=None` e o Airflow não cria execuções agendadas.

```cron
*/2 * * * *
```

Para alterar sem mexer no código, use:

```text
PIPELINE_PRIORIDADE_RESERVAS_INTERVALO_MINUTOS=2
```

---

## Tags

- Midia
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


def _normalizar_lista_ids_int_conf(valor: Any) -> list[int]:
    """Converto IDs vindos do dag_run.conf para lista de inteiros únicos."""
    if valor in (None, "", 0, "0"):
        return []

    if isinstance(valor, str):
        candidatos = re.split(r"[,;\s]+", valor.strip())
    elif isinstance(valor, (list, tuple, set)):
        candidatos = list(valor)
    else:
        candidatos = [valor]

    ids: list[int] = []
    vistos: set[int] = set()
    for candidato in candidatos:
        try:
            id_int = int(candidato or 0)
        except Exception:
            continue
        if id_int <= 0 or id_int in vistos:
            continue
        vistos.add(id_int)
        ids.append(id_int)

    return ids


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
    schedule=SCHEDULE_DAG,
    start_date=pendulum.datetime(2026, 1, 1, tz=FUSO_HORARIO),
    catchup=False,
    max_active_runs=1,
    tags=["Midia", "Reservas", "SQL Server"],
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
                AND o.name = 'FatoOcupacaoPaineisMidia'
                AND c.name = 'IDFatoOcupacaoOrigem'
        )
        BEGIN
            ALTER TABLE Integracao.Silver.FatoOcupacaoPaineisMidia
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
                AND o.name = 'FatoOcupacaoPaineisMidia'
                AND c.name = 'IDFatoControleContratosItemOrigem'
        )
        BEGIN
            ALTER TABLE Integracao.Silver.FatoOcupacaoPaineisMidia
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
                AND o.name = 'FatoOcupacaoPaineisMidia'
                AND c.name = 'TipoVinculoOrigem'
        )
        BEGIN
            ALTER TABLE Integracao.Silver.FatoOcupacaoPaineisMidia
            ADD TipoVinculoOrigem NVARCHAR(80) NULL;
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
                AND o.name = 'FatoOcupacaoPaineisMidia'
                AND c.name = 'TipoReserva'
        )
        BEGIN
            ALTER TABLE Integracao.Silver.FatoOcupacaoPaineisMidia
            ADD TipoReserva INT NULL;
        END;
        """

        hook = obter_hook_sql_server()
        hook.executar_comando(sql)

        # Normalizo reservas de preferência já existentes.
        # Regra obrigatória para a grade/listagem enxergar a reserva:
        # Origem=RESERVA, Status=RESERVADO, TipoReserva=2, MarcaExibida com a marca comercial
        # e IDFatoOcupacaoOrigem apontando para a ocupação CONTRATO que originou a preferência.
        sql_normalizar_reservas_preferencia = """
        SET NOCOUNT ON;

        UPDATE R
           SET R.Origem = :origem_reserva,
               R.Status = :status_reservado,
               R.TipoReserva = :tipo_reserva_preferencia,
               R.IDFatoOcupacaoOrigem = COALESCE(
                   MarcaContrato.IDFatoOcupacaoPaineisMidia,
                   R.IDFatoOcupacaoOrigem
               ),
               R.IDFatoControleContratosItemOrigem = COALESCE(
                   R.IDFatoControleContratosItemOrigem,
                   MarcaContrato.IDFatoControleContratosItemOrigem
               ),
               R.MarcaExibida = COALESCE(
                   NULLIF(LTRIM(RTRIM(MarcaContrato.MarcaExibida)), ''),
                   NULLIF(LTRIM(RTRIM(R.MarcaExibida)), ''),
                   R.MarcaExibida
               ),
               R.DataAtualizacao = SYSDATETIME()
        FROM Integracao.Silver.FatoOcupacaoPaineisMidia AS R
        OUTER APPLY
        (
            SELECT TOP (1)
                OC.IDFatoOcupacaoPaineisMidia,
                COALESCE(
                    OC.IDFatoControleContratosItemOrigem,
                    I.IDFatoControleContratosItensMidia
                ) AS IDFatoControleContratosItemOrigem,
                COALESCE(
                    NULLIF(LTRIM(RTRIM(I.MarcaExibida)), ''),
                    NULLIF(LTRIM(RTRIM(OC.MarcaExibida)), '')
                ) AS MarcaExibida
            FROM Integracao.Silver.FatoOcupacaoPaineisMidia AS OC WITH (NOLOCK)
            OUTER APPLY
            (
                SELECT TOP (1)
                    I2.IDFatoControleContratosItensMidia,
                    I2.MarcaExibida
                FROM Integracao.Silver.FatoControleContratosItensMidia AS I2 WITH (NOLOCK)
                WHERE
                    (
                        OC.IDFatoControleContratosItemOrigem IS NOT NULL
                        AND I2.IDFatoControleContratosItensMidia = OC.IDFatoControleContratosItemOrigem
                    )
                    OR
                    (
                        OC.IDFatoControleContratos IS NOT NULL
                        AND I2.IDFatoControleContratoMidia = OC.IDFatoControleContratos
                        AND I2.CodPonto = OC.CodPonto
                        AND I2.CodFace = OC.CodFace
                    )
                ORDER BY
                    CASE
                        WHEN OC.IDFatoControleContratosItemOrigem IS NOT NULL
                             AND I2.IDFatoControleContratosItensMidia = OC.IDFatoControleContratosItemOrigem
                        THEN 0
                        ELSE 1
                    END,
                    I2.IDFatoControleContratosItensMidia DESC
            ) AS I
            WHERE
                OC.CanceladoEm IS NULL
                AND UPPER(LTRIM(RTRIM(ISNULL(OC.Status, '')))) <> UPPER(LTRIM(RTRIM(:status_cancelado)))
                AND UPPER(LTRIM(RTRIM(ISNULL(OC.Origem, '')))) = UPPER(LTRIM(RTRIM(:origem_contrato)))
                AND COALESCE(NULLIF(LTRIM(RTRIM(I.MarcaExibida)), ''), NULLIF(LTRIM(RTRIM(OC.MarcaExibida)), '')) IS NOT NULL
                AND (
                    (
                        R.IDFatoControleContratosItemOrigem IS NOT NULL
                        AND COALESCE(OC.IDFatoControleContratosItemOrigem, I.IDFatoControleContratosItensMidia) = R.IDFatoControleContratosItemOrigem
                    )
                    OR
                    (
                        R.IDFatoControleContratos IS NOT NULL
                        AND OC.IDFatoControleContratos = R.IDFatoControleContratos
                        AND OC.CodPonto = R.CodPonto
                        AND OC.IDPainelMidia = R.IDPainelMidia
                    )
                    OR
                    (
                        R.IDFatoOcupacaoOrigem IS NOT NULL
                        AND OC.IDFatoOcupacaoPaineisMidia = R.IDFatoOcupacaoOrigem
                    )
                )
            ORDER BY
                CASE
                    WHEN R.IDFatoControleContratosItemOrigem IS NOT NULL
                         AND COALESCE(OC.IDFatoControleContratosItemOrigem, I.IDFatoControleContratosItensMidia) = R.IDFatoControleContratosItemOrigem
                    THEN 0
                    WHEN R.IDFatoOcupacaoOrigem IS NOT NULL
                         AND OC.IDFatoOcupacaoPaineisMidia = R.IDFatoOcupacaoOrigem
                    THEN 1
                    ELSE 2
                END,
                OC.IDFatoOcupacaoPaineisMidia DESC
        ) AS MarcaContrato
        WHERE
            (
                UPPER(LTRIM(RTRIM(ISNULL(R.TipoVinculoOrigem, '')))) = UPPER(LTRIM(RTRIM(:tipo_vinculo)))
                OR UPPER(LTRIM(RTRIM(ISNULL(R.Referencia, '')))) LIKE 'PREFRENOV-%'
            )
            AND (
                UPPER(LTRIM(RTRIM(ISNULL(R.Origem, '')))) <> UPPER(LTRIM(RTRIM(:origem_reserva)))
                OR UPPER(LTRIM(RTRIM(ISNULL(R.Status, '')))) <> UPPER(LTRIM(RTRIM(:status_reservado)))
                OR ISNULL(R.TipoReserva, 0) <> :tipo_reserva_preferencia
                OR (
                    MarcaContrato.IDFatoOcupacaoPaineisMidia IS NOT NULL
                    AND ISNULL(R.IDFatoOcupacaoOrigem, 0) <> MarcaContrato.IDFatoOcupacaoPaineisMidia
                )
                OR (
                    R.IDFatoControleContratosItemOrigem IS NULL
                    AND MarcaContrato.IDFatoControleContratosItemOrigem IS NOT NULL
                )
                OR (
                    MarcaContrato.MarcaExibida IS NOT NULL
                    AND ISNULL(NULLIF(LTRIM(RTRIM(R.MarcaExibida)), ''), '') <> NULLIF(LTRIM(RTRIM(MarcaContrato.MarcaExibida)), '')
                )
            );
        """
        _executar_comando_parametrizado(
            hook,
            sql_normalizar_reservas_preferencia,
            {
                "origem_contrato": ORIGEM_CONTRATO,
                "origem_reserva": ORIGEM_RESERVA,
                "status_reservado": STATUS_RESERVADO,
                "status_cancelado": STATUS_CANCELADO,
                "tipo_vinculo": TIPO_VINCULO_PREFERENCIA_RENOVACAO,
                "tipo_reserva_preferencia": TIPO_RESERVA_PREFERENCIA_RENOVACAO,
            },
        )


        # Regra importante:
        # O Kanban NÃO cria reserva futura de preferência. Ele cria/efetiva ocupação.
        # Portanto esta DAG não deve transformar linhas do Kanban em RESERVA por observação,
        # por ExpiraEm ou por TipoReserva preenchido errado em legado.
        # A única linha de reserva de preferência nasce no INSERT da task
        # criar_reservas_preferencia_renovacao, com Origem=RESERVA, Status=RESERVADO e TipoReserva=2.

        logging.info("Colunas de vínculo verificadas/criadas com sucesso.")

        return {
            "status": "ok",
            "mensagem": "Estrutura de vínculo da reserva verificada.",
        }

    @task(task_id="criar_reservas_preferencia_renovacao")
    def criar_reservas_preferencia_renovacao() -> dict[str, Any]:
        """
        Crio reservas automáticas de preferência de renovação a partir das ocupações com Origem = CONTRATO.

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
            or conf.get("IDFatoControleContratosMidia")
        )

        id_usuario = _normalizar_int_ou_none(
            conf.get("id_usuario")
            or conf.get("id_usuario_logado")
            or conf.get("IDUsuario")
        ) or USUARIO_SISTEMA_PADRAO

        modo_processamento = str(
            conf.get("modo_processamento")
            or conf.get("modo_execucao")
            or conf.get("modo")
            or ""
        ).strip().lower()
        processar_todos_elegiveis = _normalizar_bool_conf(conf.get("processar_todos_elegiveis"))
        ids_ocupacao_origem = _normalizar_lista_ids_int_conf(
            conf.get("ids_ocupacao_origem")
            or conf.get("ids_ocupacao")
            or conf.get("ids_ocupacoes")
            or conf.get("ids_ocupacao_origem_csv")
            or conf.get("id_ocupacao_origem")
            or conf.get("id_fato_ocupacao")
            or conf.get("IDFatoOcupacaoPaineisMidia")
        )
        ids_ocupacao_origem_csv = ",".join(str(x) for x in ids_ocupacao_origem) or None

        veio_de_varredura_pos_upsert = modo_processamento in (
            "varredura_pos_upsert_ocupacao",
            "pos_upsert_ocupacao",
            "upsert_ocupacao",
            "etl_ocupacao",
        )

        processar_escopo_total = (
            id_contrato is None
            and (
                bool(ids_ocupacao_origem)
                or processar_todos_elegiveis
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

        if id_contrato is not None:
            escopo = "contrato"
        elif ids_ocupacao_origem:
            escopo = "ocupacoes_informadas"
        else:
            escopo = "todos_elegiveis"

        marcador_execucao = f"DAG_RUN_PRIORIDADE_RESERVAS={run_id}"[:450]

        parametros = {
            "id_contrato": id_contrato,
            "ids_ocupacao_origem_csv": ids_ocupacao_origem_csv,
            "id_usuario": int(id_usuario),
            "origem_contrato": ORIGEM_CONTRATO,
            "origem_reserva": ORIGEM_RESERVA,
            "status_reservado": STATUS_RESERVADO,
            "status_cancelado": STATUS_CANCELADO,
            "tipo_vinculo": TIPO_VINCULO_PREFERENCIA_RENOVACAO,
            "tipo_reserva_preferencia": TIPO_RESERVA_PREFERENCIA_RENOVACAO,
            "meses_minimos": MESES_MINIMOS_PREFERENCIA_RENOVACAO,
            "marcador_execucao": marcador_execucao,
        }

        logging.info(
            "Iniciando criação de reserva de preferência. escopo=%s | id_contrato=%s | modo=%s | schedule=%s | conf=%s",
            escopo,
            id_contrato,
            modo_processamento,
            SCHEDULE_DAG,
            conf,
        )





        sql_insert = """
        SET NOCOUNT ON;
        SET XACT_ABORT ON;
        SET LOCK_TIMEOUT 60000;

        /*
           Implementação incremental do Tetris.

           A versão anterior criava antecipadamente o produto cartesiano:
               todas as ocupações x até 366 dias x todos os slots
           e, para cada candidato, voltava a consultar toda a FatoOcupacao.
           Na varredura total isso podia levar horas e manter HOLDLOCK amplo.

           Nesta versão:
           1. materializo ocupações elegíveis novas e as preferências existentes
              que precisam ser conferidas/realinhadas;
           2. materializo uma vez os bloqueios ativos e empilho registros
              digitais antigos sem LoopInicio/LoopFim pelo mesmo first-fit;
           3. fixa a reserva em D+1 e procura somente o primeiro slot livre;
           4. acrescenta imediatamente cada reserva aos bloqueios temporários.

           Assim duas preferências criadas na mesma execução também não ocupam
           o mesmo slot/período.
        */

        IF OBJECT_ID('tempdb..#FaceOrdenada') IS NOT NULL DROP TABLE #FaceOrdenada;
        IF OBJECT_ID('tempdb..#Paineis') IS NOT NULL DROP TABLE #Paineis;
        IF OBJECT_ID('tempdb..#Elegiveis') IS NOT NULL DROP TABLE #Elegiveis;
        IF OBJECT_ID('tempdb..#Bloqueios') IS NOT NULL DROP TABLE #Bloqueios;

        ;WITH FacesBase AS
        (
            SELECT
                F.IDDimPaineisMidia,
                F.CodPonto,
                F.CodFace,
                ROW_NUMBER() OVER
                (
                    PARTITION BY F.IDDimPaineisMidia
                    ORDER BY
                        COALESCE(TRY_CONVERT(INT, F.Face), 2147483647),
                        F.Face,
                        F.CodFace
                ) AS FaceOrdem
            FROM Integracao.Silver.DimFacesPaineis AS F WITH (NOLOCK)
            WHERE
                F.IDDimPaineisMidia IS NOT NULL
                AND F.CodFace IS NOT NULL
        )
        SELECT
            IDDimPaineisMidia,
            CodPonto,
            CodFace,
            FaceOrdem
        INTO #FaceOrdenada
        FROM FacesBase;

        CREATE INDEX IX_FaceOrdenada_PainelFace
            ON #FaceOrdenada (IDDimPaineisMidia, CodPonto, CodFace);

        SELECT
            P.IDDimPaineisMidia,
            P.CodPonto,
            CASE
                WHEN UPPER(LTRIM(RTRIM(ISNULL(P.Tipo, '')))) LIKE '%DIGITAL%' THEN 1
                ELSE 0
            END AS BitDigital,
            CASE
                WHEN TRY_CONVERT(INT, P.QuantidadeFaces) > 0
                THEN TRY_CONVERT(INT, P.QuantidadeFaces)
                ELSE NULL
            END AS QuantidadeFacesCalculada
        INTO #Paineis
        FROM Integracao.Silver.DimPaineisMidia AS P WITH (NOLOCK);

        CREATE UNIQUE CLUSTERED INDEX IX_Paineis_ID
            ON #Paineis (IDDimPaineisMidia);

        CREATE TABLE #Elegiveis
        (
            IDFatoOcupacaoOrigem INT NOT NULL PRIMARY KEY,
            IDReservaPreferenciaExistente INT NULL,
            IDFatoControleContratos INT NULL,
            IDFatoControleContratosItemOrigem INT NULL,
            CodPonto INT NOT NULL,
            CodFace NVARCHAR(100) NOT NULL,
            IDPainelMidia INT NOT NULL,
            FaceOrdemOrigem INT NOT NULL,
            BitDigital BIT NOT NULL,
            QuantidadeFacesCalculada INT NOT NULL,
            DataInicio DATE NOT NULL,
            DataFim DATE NOT NULL,
            QuantidadeMesesContrato INT NOT NULL,
            DataMinimaReserva DATE NOT NULL,
            SpanQtdCalculado INT NOT NULL,
            Cota INT NULL,
            MarcaExibida NVARCHAR(200) NULL,
            Vendedor NVARCHAR(200) NULL,
            IDVendedor INT NULL,
            IDCliente INT NULL,
            NumeroContrato NVARCHAR(100) NULL,
            NumeroPrevia NVARCHAR(100) NULL
        );

        ;WITH Origens AS
        (
            SELECT
                O.IDFatoOcupacaoPaineisMidia AS IDFatoOcupacaoOrigem,
                PreferenciaExistente.IDFatoOcupacaoPaineisMidia
                    AS IDReservaPreferenciaExistente,
                COALESCE(
                    O.IDFatoControleContratos,
                    V.IDFatoControleContratosMidia,
                    I.IDFatoControleContratoMidia
                ) AS IDFatoControleContratos,
                COALESCE(
                    O.IDFatoControleContratosItemOrigem,
                    V.IDFatoControleContratosItensMidia,
                    I.IDFatoControleContratosItensMidia
                ) AS IDFatoControleContratosItemOrigem,
                O.CodPonto,
                O.CodFace,
                O.IDPainelMidia,
                PF.FaceOrdem AS FaceOrdemOrigem,
                P.BitDigital,
                P.QuantidadeFacesCalculada,
                PeriodoOcupacao.DataInicio AS DataInicio,
                PeriodoOcupacao.DataFim AS DataFim,
                DATEDIFF(
                    MONTH,
                    PeriodoOcupacao.DataInicio,
                    DATEADD(DAY, 1, PeriodoOcupacao.DataFim)
                ) AS QuantidadeMesesContrato,
                DATEADD(DAY, 1, PeriodoOcupacao.DataFim) AS DataMinimaReserva,
                CASE
                    WHEN P.BitDigital = 1 THEN
                        CASE
                            WHEN TRY_CONVERT(INT, O.SpanQtd) > 0
                            THEN TRY_CONVERT(INT, O.SpanQtd)
                            WHEN TRY_CONVERT(INT, O.Cota) = 1080 THEN 2
                            ELSE 1
                        END
                    ELSE 1
                END AS SpanQtdCalculado,
                TRY_CONVERT(INT, O.Cota) AS Cota,
                COALESCE(
                    NULLIF(LTRIM(RTRIM(O.MarcaExibida)), ''),
                    NULLIF(LTRIM(RTRIM(V.Marca)), ''),
                    NULLIF(LTRIM(RTRIM(I.MarcaExibida)), '')
                ) AS MarcaExibida,
                O.Vendedor,
                O.IDVendedor,
                O.IDCliente,
                COALESCE(
                    NULLIF(LTRIM(RTRIM(O.NumeroContrato)), ''),
                    NULLIF(LTRIM(RTRIM(V.ReferenciaContrato)), '')
                ) AS NumeroContrato,
                COALESCE(
                    NULLIF(LTRIM(RTRIM(O.NumeroPrevia)), ''),
                    NULLIF(LTRIM(RTRIM(V.ReferenciaLogycWare)), '')
                ) AS NumeroPrevia
            FROM Integracao.Silver.FatoOcupacaoPaineisMidia AS O WITH (READCOMMITTEDLOCK)
            OUTER APPLY
            (
                /*
                   FatoAgendamentoFaceContrato existe apenas para detalhar as
                   fatias da ocupação na grade. Para preferência, consolido o
                   primeiro início e o último término entre a ocupação e todas
                   as fatias ativas. Assim uma fatia parcial nunca antecipa a
                   preferência; sem agendamento, permanecem as datas de O.
                */
                SELECT
                    MIN(Periodo.DataInicio) AS DataInicio,
                    MAX(Periodo.DataFim) AS DataFim
                FROM
                (
                    SELECT
                        CAST(O.DataInicio AS DATE) AS DataInicio,
                        CAST(O.DataFim AS DATE) AS DataFim

                    UNION ALL

                    SELECT
                        CAST(A.DataInicio AS DATE),
                        CAST(A.DataTermino AS DATE)
                    FROM Integracao.Silver.FatoAgendamentoFaceContrato AS A WITH (NOLOCK)
                    WHERE
                        A.IDFatoOcupacaoPaineisMidia =
                            O.IDFatoOcupacaoPaineisMidia
                        AND A.BitAtivo = 1
                        AND A.DataInicio IS NOT NULL
                        AND A.DataTermino IS NOT NULL
                ) AS Periodo
            ) AS PeriodoOcupacao
            OUTER APPLY
            (
                SELECT TOP (1)
                    V2.IDFatoControleContratosMidia,
                    V2.IDFatoControleContratosItensMidia,
                    V2.ReferenciaContrato,
                    V2.ReferenciaLogycWare,
                    V2.Marca
                FROM Integracao.Silver.FatoVinculaMarcasOcupacao AS V2 WITH (NOLOCK)
                WHERE
                    V2.IDFatoOcupacaoPaineisMidia = O.IDFatoOcupacaoPaineisMidia
                ORDER BY
                    CASE
                        WHEN V2.IDFatoControleContratosItensMidia IS NULL THEN 1
                        ELSE 0
                    END,
                    V2.IDFatoControleContratosItensMidia,
                    V2.IDFatoVinculaMarcasOcupacao
            ) AS V
            OUTER APPLY
            (
                SELECT TOP (1)
                    I2.IDFatoControleContratosItensMidia,
                    I2.IDFatoControleContratoMidia,
                    I2.MarcaExibida
                FROM Integracao.Silver.FatoControleContratosItensMidia AS I2 WITH (NOLOCK)
                WHERE
                    (
                        (
                            COALESCE(
                                O.IDFatoControleContratosItemOrigem,
                                V.IDFatoControleContratosItensMidia
                            ) IS NOT NULL
                            AND I2.IDFatoControleContratosItensMidia = COALESCE(
                                O.IDFatoControleContratosItemOrigem,
                                V.IDFatoControleContratosItensMidia
                            )
                        )
                        OR
                        (
                            COALESCE(
                                O.IDFatoControleContratosItemOrigem,
                                V.IDFatoControleContratosItensMidia
                            ) IS NULL
                            AND I2.CodPonto = O.CodPonto
                            AND I2.CodFace = O.CodFace
                            AND (
                                COALESCE(
                                    O.IDFatoControleContratos,
                                    V.IDFatoControleContratosMidia
                                ) IS NULL
                                OR I2.IDFatoControleContratoMidia = COALESCE(
                                    O.IDFatoControleContratos,
                                    V.IDFatoControleContratosMidia
                                )
                            )
                            AND CAST(I2.DataInicioPrevisto AS DATE) <= PeriodoOcupacao.DataFim
                            AND CAST(COALESCE(
                                I2.DataTerminoPrevisto,
                                I2.DataFimEfetiva,
                                I2.DataCancelamento
                            ) AS DATE) >= PeriodoOcupacao.DataInicio
                        )
                    )
                    AND (
                        :id_contrato IS NULL
                        OR I2.IDFatoControleContratoMidia = :id_contrato
                    )
                ORDER BY
                    I2.IDFatoControleContratosItensMidia DESC
            ) AS I
            OUTER APPLY
            (
                SELECT TOP (1)
                    R.IDFatoOcupacaoPaineisMidia,
                    R.IDFatoOcupacaoOrigem
                FROM Integracao.Silver.FatoOcupacaoPaineisMidia AS R WITH (NOLOCK)
                WHERE
                    (
                        R.IDFatoOcupacaoOrigem =
                            O.IDFatoOcupacaoPaineisMidia
                        OR
                        (
                            COALESCE(
                                O.IDFatoControleContratosItemOrigem,
                                V.IDFatoControleContratosItensMidia,
                                I.IDFatoControleContratosItensMidia
                            ) IS NOT NULL
                            AND R.IDFatoControleContratosItemOrigem = COALESCE(
                                O.IDFatoControleContratosItemOrigem,
                                V.IDFatoControleContratosItensMidia,
                                I.IDFatoControleContratosItensMidia
                            )
                        )
                    )
                    AND UPPER(LTRIM(RTRIM(ISNULL(R.TipoVinculoOrigem, ''))))
                        = UPPER(LTRIM(RTRIM(:tipo_vinculo)))
                    AND UPPER(LTRIM(RTRIM(ISNULL(R.Origem, ''))))
                        = UPPER(LTRIM(RTRIM(:origem_reserva)))
                    AND R.CanceladoEm IS NULL
                    AND UPPER(LTRIM(RTRIM(ISNULL(R.Status, ''))))
                        <> UPPER(LTRIM(RTRIM(:status_cancelado)))
                ORDER BY
                    CASE
                        WHEN R.IDFatoOcupacaoOrigem =
                            O.IDFatoOcupacaoPaineisMidia
                        THEN 0 ELSE 1
                    END,
                    R.IDFatoOcupacaoPaineisMidia
            ) AS PreferenciaExistente
            INNER JOIN #Paineis AS P
                ON P.IDDimPaineisMidia = O.IDPainelMidia
            OUTER APPLY
            (
                SELECT TOP (1)
                    F.FaceOrdem
                FROM #FaceOrdenada AS F
                WHERE
                    F.IDDimPaineisMidia = O.IDPainelMidia
                    AND F.CodPonto = O.CodPonto
                    AND F.CodFace = O.CodFace
                ORDER BY
                    F.FaceOrdem
            ) AS PF
            WHERE
                (
                    :id_contrato IS NULL
                    OR COALESCE(
                        O.IDFatoControleContratos,
                        V.IDFatoControleContratosMidia,
                        I.IDFatoControleContratoMidia
                    ) = :id_contrato
                )
                AND
                (
                    :ids_ocupacao_origem_csv IS NULL
                    OR EXISTS
                    (
                        SELECT 1
                        FROM STRING_SPLIT(:ids_ocupacao_origem_csv, ',') AS IDS
                        WHERE
                            TRY_CONVERT(INT, IDS.value) = O.IDFatoOcupacaoPaineisMidia
                    )
                )
                AND COALESCE(
                    O.IDFatoControleContratos,
                    V.IDFatoControleContratosMidia,
                    I.IDFatoControleContratoMidia
                ) IS NOT NULL
                AND O.CodPonto IS NOT NULL
                AND O.CodFace IS NOT NULL
                AND O.IDPainelMidia IS NOT NULL
                AND PeriodoOcupacao.DataInicio IS NOT NULL
                AND PeriodoOcupacao.DataFim IS NOT NULL
                AND
                (
                    PreferenciaExistente.IDFatoOcupacaoPaineisMidia IS NULL
                    OR PreferenciaExistente.IDFatoOcupacaoOrigem =
                        O.IDFatoOcupacaoPaineisMidia
                )
                AND O.CanceladoEm IS NULL
                AND UPPER(LTRIM(RTRIM(ISNULL(O.Status, ''))))
                    <> UPPER(LTRIM(RTRIM(:status_cancelado)))
                AND
                (
                    UPPER(LTRIM(RTRIM(ISNULL(O.Origem, ''))))
                        = UPPER(LTRIM(RTRIM(:origem_contrato)))
                    OR
                    (
                        UPPER(LTRIM(RTRIM(ISNULL(O.Origem, '')))) = 'OCUPACAO'
                        AND ISNULL(O.TipoReserva, 0) = 0
                    )
                )
        )
        INSERT INTO #Elegiveis
        (
            IDFatoOcupacaoOrigem,
            IDReservaPreferenciaExistente,
            IDFatoControleContratos,
            IDFatoControleContratosItemOrigem,
            CodPonto,
            CodFace,
            IDPainelMidia,
            FaceOrdemOrigem,
            BitDigital,
            QuantidadeFacesCalculada,
            DataInicio,
            DataFim,
            QuantidadeMesesContrato,
            DataMinimaReserva,
            SpanQtdCalculado,
            Cota,
            MarcaExibida,
            Vendedor,
            IDVendedor,
            IDCliente,
            NumeroContrato,
            NumeroPrevia
        )
        SELECT
            O.IDFatoOcupacaoOrigem,
            O.IDReservaPreferenciaExistente,
            O.IDFatoControleContratos,
            O.IDFatoControleContratosItemOrigem,
            O.CodPonto,
            O.CodFace,
            O.IDPainelMidia,
            O.FaceOrdemOrigem,
            O.BitDigital,
            O.QuantidadeFacesCalculada,
            O.DataInicio,
            O.DataFim,
            O.QuantidadeMesesContrato,
            O.DataMinimaReserva,
            O.SpanQtdCalculado,
            O.Cota,
            O.MarcaExibida,
            O.Vendedor,
            O.IDVendedor,
            O.IDCliente,
            O.NumeroContrato,
            O.NumeroPrevia
        FROM Origens AS O
        WHERE
            O.FaceOrdemOrigem IS NOT NULL
            AND O.QuantidadeFacesCalculada > 0
            AND O.SpanQtdCalculado > 0
            AND O.SpanQtdCalculado <= O.QuantidadeFacesCalculada
            AND O.DataFim >= DATEADD(
                DAY,
                -1,
                DATEADD(MONTH, :meses_minimos, O.DataInicio)
            )
            AND O.QuantidadeMesesContrato >= :meses_minimos
        OPTION (RECOMPILE);

        DECLARE
            @JanelaReservaInicio DATE,
            @JanelaReservaFim DATE;

        SELECT
            @JanelaReservaInicio = MIN(E.DataMinimaReserva),
            @JanelaReservaFim = MAX(
                DATEADD(
                    DAY,
                    -1,
                    DATEADD(
                        MONTH,
                        E.QuantidadeMesesContrato,
                        E.DataMinimaReserva
                    )
                )
            )
        FROM #Elegiveis AS E;

        CREATE TABLE #Bloqueios
        (
            IDFatoOcupacaoPaineisMidia INT NOT NULL,
            IDPainelMidia INT NOT NULL,
            DataInicio DATE NOT NULL,
            DataFim DATE NOT NULL,
            FaceInicio INT NOT NULL,
            FaceFim INT NOT NULL
        );

        /*
           Primeiro preservo as posições já explicitamente gravadas. Para
           painel analógico, a posição é a própria face física. Para painel
           digital, LoopInicio/LoopFim representam o slot já fixado.
        */
        INSERT INTO #Bloqueios
        (
            IDFatoOcupacaoPaineisMidia,
            IDPainelMidia,
            DataInicio,
            DataFim,
            FaceInicio,
            FaceFim
        )
        SELECT
            O.IDFatoOcupacaoPaineisMidia,
            O.IDPainelMidia,
            PeriodoBloqueio.DataInicio,
            PeriodoBloqueio.DataFim,
            SlotCalculado.FaceInicio,
            SlotCalculado.FaceInicio + SlotCalculado.SpanQtd - 1
        FROM Integracao.Silver.FatoOcupacaoPaineisMidia AS O WITH (READCOMMITTEDLOCK)
        INNER JOIN #Paineis AS P
            ON P.IDDimPaineisMidia = O.IDPainelMidia
        OUTER APPLY
        (
            SELECT TOP (1)
                F.FaceOrdem
            FROM #FaceOrdenada AS F
            WHERE
                F.IDDimPaineisMidia = O.IDPainelMidia
                AND F.CodPonto = O.CodPonto
                AND F.CodFace = O.CodFace
            ORDER BY
                F.FaceOrdem
        ) AS PF
        CROSS APPLY
        (
            SELECT
                CASE
                    WHEN P.BitDigital = 1
                         AND TRY_CONVERT(INT, O.LoopInicio) > 0
                    THEN TRY_CONVERT(INT, O.LoopInicio)
                    ELSE PF.FaceOrdem
                END AS FaceInicio,
                CASE
                    WHEN P.BitDigital = 0 THEN 1
                    WHEN TRY_CONVERT(INT, O.LoopInicio) > 0
                         AND TRY_CONVERT(INT, O.LoopFim) >= TRY_CONVERT(INT, O.LoopInicio)
                    THEN TRY_CONVERT(INT, O.LoopFim) - TRY_CONVERT(INT, O.LoopInicio) + 1
                    WHEN TRY_CONVERT(INT, O.SpanQtd) > 0
                    THEN TRY_CONVERT(INT, O.SpanQtd)
                    WHEN TRY_CONVERT(INT, O.Cota) = 1080 THEN 2
                    ELSE 1
                END AS SpanQtd
        ) AS SlotCalculado
        OUTER APPLY
        (
            SELECT
                MIN(Periodo.DataInicio) AS DataInicio,
                MAX(Periodo.DataFim) AS DataFim
            FROM
            (
                SELECT
                    CAST(O.DataInicio AS DATE) AS DataInicio,
                    CAST(O.DataFim AS DATE) AS DataFim

                UNION ALL

                SELECT
                    CAST(A.DataInicio AS DATE),
                    CAST(A.DataTermino AS DATE)
                FROM Integracao.Silver.FatoAgendamentoFaceContrato AS A WITH (NOLOCK)
                WHERE
                    A.IDFatoOcupacaoPaineisMidia =
                        O.IDFatoOcupacaoPaineisMidia
                    AND A.BitAtivo = 1
                    AND A.DataInicio IS NOT NULL
                    AND A.DataTermino IS NOT NULL
            ) AS Periodo
        ) AS PeriodoBloqueio
        WHERE
            O.IDPainelMidia IS NOT NULL
            AND O.CodPonto IS NOT NULL
            AND O.CodFace IS NOT NULL
            AND PeriodoBloqueio.DataInicio IS NOT NULL
            AND PeriodoBloqueio.DataFim IS NOT NULL
            AND @JanelaReservaInicio IS NOT NULL
            AND PeriodoBloqueio.DataInicio <= @JanelaReservaFim
            AND PeriodoBloqueio.DataFim >= @JanelaReservaInicio
            AND PF.FaceOrdem IS NOT NULL
            AND SlotCalculado.FaceInicio IS NOT NULL
            AND SlotCalculado.SpanQtd > 0
            AND SlotCalculado.FaceInicio + SlotCalculado.SpanQtd - 1
                <= P.QuantidadeFacesCalculada
            AND
            (
                P.BitDigital = 0
                OR
                (
                    TRY_CONVERT(INT, O.LoopInicio) > 0
                    AND TRY_CONVERT(INT, O.LoopFim)
                        >= TRY_CONVERT(INT, O.LoopInicio)
                )
            )
            AND O.CanceladoEm IS NULL
            AND UPPER(LTRIM(RTRIM(ISNULL(O.Status, ''))))
                <> UPPER(LTRIM(RTRIM(:status_cancelado)));

        /*
           Ocupações digitais antigas normalmente não possuem LoopInicio e
           LoopFim. A regra anterior colocava todas no mesmo slot retornado
           por DimFacesPaineis, enquanto a grade as empilhava em linhas
           diferentes. Aqui reproduzo o first-fit da grade: cada barra sem
           slot explícito ocupa o primeiro bloco contíguo livre durante todo
           o seu período.
        */
        DECLARE
            @BloqueioID INT,
            @BloqueioPainel INT,
            @BloqueioDataInicio DATE,
            @BloqueioDataFim DATE,
            @BloqueioSpan INT,
            @BloqueioQuantidadeFaces INT,
            @BloqueioFaceInicio INT,
            @BloqueioFaceInicioMaxima INT,
            @BloqueioFaceFim INT,
            @BloqueioEncaixado BIT;

        DECLARE cursor_bloqueios_digitais CURSOR LOCAL FAST_FORWARD FOR
            SELECT
                O.IDFatoOcupacaoPaineisMidia,
                O.IDPainelMidia,
                PeriodoBloqueio.DataInicio,
                PeriodoBloqueio.DataFim,
                SlotCalculado.SpanQtd,
                P.QuantidadeFacesCalculada
            FROM Integracao.Silver.FatoOcupacaoPaineisMidia AS O WITH (READCOMMITTEDLOCK)
            INNER JOIN #Paineis AS P
                ON P.IDDimPaineisMidia = O.IDPainelMidia
                AND P.BitDigital = 1
            CROSS APPLY
            (
                SELECT
                    CASE
                        WHEN TRY_CONVERT(INT, O.SpanQtd) > 0
                        THEN TRY_CONVERT(INT, O.SpanQtd)
                        WHEN TRY_CONVERT(INT, O.Cota) = 1080 THEN 2
                        ELSE 1
                    END AS SpanQtd
            ) AS SlotCalculado
            OUTER APPLY
            (
                SELECT
                    MIN(Periodo.DataInicio) AS DataInicio,
                    MAX(Periodo.DataFim) AS DataFim
                FROM
                (
                    SELECT
                        CAST(O.DataInicio AS DATE) AS DataInicio,
                        CAST(O.DataFim AS DATE) AS DataFim

                    UNION ALL

                    SELECT
                        CAST(A.DataInicio AS DATE),
                        CAST(A.DataTermino AS DATE)
                    FROM Integracao.Silver.FatoAgendamentoFaceContrato AS A WITH (NOLOCK)
                    WHERE
                        A.IDFatoOcupacaoPaineisMidia =
                            O.IDFatoOcupacaoPaineisMidia
                        AND A.BitAtivo = 1
                        AND A.DataInicio IS NOT NULL
                        AND A.DataTermino IS NOT NULL
                ) AS Periodo
            ) AS PeriodoBloqueio
            WHERE
                O.IDPainelMidia IS NOT NULL
                AND PeriodoBloqueio.DataInicio IS NOT NULL
                AND PeriodoBloqueio.DataFim IS NOT NULL
                AND @JanelaReservaInicio IS NOT NULL
                AND PeriodoBloqueio.DataInicio <= @JanelaReservaFim
                AND PeriodoBloqueio.DataFim >= @JanelaReservaInicio
                AND SlotCalculado.SpanQtd > 0
                AND SlotCalculado.SpanQtd <= P.QuantidadeFacesCalculada
                AND NOT
                (
                    TRY_CONVERT(INT, O.LoopInicio) > 0
                    AND TRY_CONVERT(INT, O.LoopFim)
                        >= TRY_CONVERT(INT, O.LoopInicio)
                    AND TRY_CONVERT(INT, O.LoopFim)
                        <= P.QuantidadeFacesCalculada
                )
                AND O.CanceladoEm IS NULL
                AND UPPER(LTRIM(RTRIM(ISNULL(O.Status, ''))))
                    <> UPPER(LTRIM(RTRIM(:status_cancelado)))
            ORDER BY
                PeriodoBloqueio.DataInicio,
                PeriodoBloqueio.DataFim,
                O.IDFatoOcupacaoPaineisMidia;

        OPEN cursor_bloqueios_digitais;

        FETCH NEXT FROM cursor_bloqueios_digitais INTO
            @BloqueioID,
            @BloqueioPainel,
            @BloqueioDataInicio,
            @BloqueioDataFim,
            @BloqueioSpan,
            @BloqueioQuantidadeFaces;

        WHILE @@FETCH_STATUS = 0
        BEGIN
            SET @BloqueioEncaixado = 0;
            SET @BloqueioFaceInicio = 1;
            SET @BloqueioFaceInicioMaxima =
                @BloqueioQuantidadeFaces - @BloqueioSpan + 1;

            WHILE
                @BloqueioEncaixado = 0
                AND @BloqueioFaceInicio <= @BloqueioFaceInicioMaxima
            BEGIN
                SET @BloqueioFaceFim =
                    @BloqueioFaceInicio + @BloqueioSpan - 1;

                IF NOT EXISTS
                (
                    SELECT 1
                    FROM #Bloqueios AS B
                    WHERE
                        B.IDPainelMidia = @BloqueioPainel
                        AND B.DataInicio <= @BloqueioDataFim
                        AND B.DataFim >= @BloqueioDataInicio
                        AND B.FaceInicio <= @BloqueioFaceFim
                        AND B.FaceFim >= @BloqueioFaceInicio
                )
                BEGIN
                    INSERT INTO #Bloqueios
                    (
                        IDFatoOcupacaoPaineisMidia,
                        IDPainelMidia,
                        DataInicio,
                        DataFim,
                        FaceInicio,
                        FaceFim
                    )
                    VALUES
                    (
                        @BloqueioID,
                        @BloqueioPainel,
                        @BloqueioDataInicio,
                        @BloqueioDataFim,
                        @BloqueioFaceInicio,
                        @BloqueioFaceFim
                    );

                    SET @BloqueioEncaixado = 1;
                END;

                SET @BloqueioFaceInicio = @BloqueioFaceInicio + 1;
            END;

            /*
               Se a própria base já estiver acima da capacidade, preservo um
               bloqueio explícito. Isso impede uma nova preferência de ser
               criada sobre uma grade que já está em conflito.
            */
            IF @BloqueioEncaixado = 0
            BEGIN
                INSERT INTO #Bloqueios
                (
                    IDFatoOcupacaoPaineisMidia,
                    IDPainelMidia,
                    DataInicio,
                    DataFim,
                    FaceInicio,
                    FaceFim
                )
                VALUES
                (
                    @BloqueioID,
                    @BloqueioPainel,
                    @BloqueioDataInicio,
                    @BloqueioDataFim,
                    1,
                    @BloqueioQuantidadeFaces
                );
            END;

            FETCH NEXT FROM cursor_bloqueios_digitais INTO
                @BloqueioID,
                @BloqueioPainel,
                @BloqueioDataInicio,
                @BloqueioDataFim,
                @BloqueioSpan,
                @BloqueioQuantidadeFaces;
        END;

        CLOSE cursor_bloqueios_digitais;
        DEALLOCATE cursor_bloqueios_digitais;

        CREATE INDEX IX_Bloqueios_PainelPeriodoSlot
            ON #Bloqueios
            (
                IDPainelMidia,
                DataInicio,
                DataFim,
                FaceInicio,
                FaceFim
            );

        DECLARE
            @IDFatoOcupacaoOrigem INT,
            @IDReservaPreferenciaExistente INT,
            @IDFatoControleContratos INT,
            @IDFatoControleContratosItemOrigem INT,
            @CodPonto INT,
            @CodFace NVARCHAR(100),
            @IDPainelMidia INT,
            @FaceOrdemOrigem INT,
            @BitDigital BIT,
            @QuantidadeFacesCalculada INT,
            @DataInicioOrigem DATE,
            @DataFimOrigem DATE,
            @QuantidadeMesesContrato INT,
            @DataMinimaReserva DATE,
            @SpanQtdCalculado INT,
            @Cota INT,
            @MarcaExibida NVARCHAR(200),
            @Vendedor NVARCHAR(200),
            @IDVendedor INT,
            @IDCliente INT,
            @NumeroContrato NVARCHAR(100),
            @NumeroPrevia NVARCHAR(100),
            @FaceInicio INT,
            @FaceFim INT,
            @FaceInicioMaxima INT,
            @DataInicioReserva DATE,
            @DataFimReserva DATE,
            @ReferenciaPreferencia VARCHAR(64),
            @Inseriu BIT,
            @NovoID INT,
            @ResultadoAppLock INT;

        BEGIN TRY
            BEGIN TRANSACTION;

            EXEC @ResultadoAppLock = sys.sp_getapplock
                @Resource = N'pipeline_prioridade_reservas:tetris',
                @LockMode = N'Exclusive',
                @LockOwner = N'Transaction',
                @LockTimeout = 60000;

            IF @ResultadoAppLock < 0
                THROW 51001, 'Não foi possível obter a trava da criação de preferências.', 1;

            DECLARE cursor_elegiveis CURSOR LOCAL FAST_FORWARD FOR
                SELECT
                    E.IDFatoOcupacaoOrigem,
                    E.IDReservaPreferenciaExistente,
                    E.IDFatoControleContratos,
                    E.IDFatoControleContratosItemOrigem,
                    E.CodPonto,
                    E.CodFace,
                    E.IDPainelMidia,
                    E.FaceOrdemOrigem,
                    E.BitDigital,
                    E.QuantidadeFacesCalculada,
                    E.DataInicio,
                    E.DataFim,
                    E.QuantidadeMesesContrato,
                    E.DataMinimaReserva,
                    E.SpanQtdCalculado,
                    E.Cota,
                    E.MarcaExibida,
                    E.Vendedor,
                    E.IDVendedor,
                    E.IDCliente,
                    E.NumeroContrato,
                    E.NumeroPrevia
                FROM #Elegiveis AS E
                ORDER BY
                    E.DataMinimaReserva,
                    E.IDFatoOcupacaoOrigem;

            OPEN cursor_elegiveis;

            FETCH NEXT FROM cursor_elegiveis INTO
                @IDFatoOcupacaoOrigem,
                @IDReservaPreferenciaExistente,
                @IDFatoControleContratos,
                @IDFatoControleContratosItemOrigem,
                @CodPonto,
                @CodFace,
                @IDPainelMidia,
                @FaceOrdemOrigem,
                @BitDigital,
                @QuantidadeFacesCalculada,
                @DataInicioOrigem,
                @DataFimOrigem,
                @QuantidadeMesesContrato,
                @DataMinimaReserva,
                @SpanQtdCalculado,
                @Cota,
                @MarcaExibida,
                @Vendedor,
                @IDVendedor,
                @IDCliente,
                @NumeroContrato,
                @NumeroPrevia;

            WHILE @@FETCH_STATUS = 0
            BEGIN
                SET @Inseriu = 0;
                SET @NovoID = NULL;
                SET @FaceInicioMaxima =
                    CASE
                        WHEN @BitDigital = 1
                        THEN @QuantidadeFacesCalculada - @SpanQtdCalculado + 1
                        ELSE @FaceOrdemOrigem
                    END;

                /* D+1 é uma data fixa; o Tetris pode mudar o slot, não o dia. */
                SET @DataInicioReserva = @DataMinimaReserva;
                SET @DataFimReserva = DATEADD(
                    DAY,
                    -1,
                    DATEADD(
                        MONTH,
                        @QuantidadeMesesContrato,
                        @DataInicioReserva
                    )
                );
                SET @FaceInicio =
                    CASE
                        WHEN @BitDigital = 1 THEN 1
                        ELSE @FaceOrdemOrigem
                    END;

                    WHILE
                        @Inseriu = 0
                        AND @FaceInicio <= @FaceInicioMaxima
                    BEGIN
                        SET @FaceFim = @FaceInicio + @SpanQtdCalculado - 1;

                        IF NOT EXISTS
                        (
                            SELECT 1
                            FROM #Bloqueios AS B
                            WHERE
                                B.IDFatoOcupacaoPaineisMidia
                                    <> @IDFatoOcupacaoOrigem
                                AND
                                (
                                    @IDReservaPreferenciaExistente IS NULL
                                    OR B.IDFatoOcupacaoPaineisMidia
                                        <> @IDReservaPreferenciaExistente
                                )
                                AND B.IDPainelMidia = @IDPainelMidia
                                AND B.DataInicio <= @DataFimReserva
                                AND B.DataFim >= @DataInicioReserva
                                AND B.FaceInicio <= @FaceFim
                                AND B.FaceFim >= @FaceInicio
                        )
                        BEGIN
                            SET @ReferenciaPreferencia = CONCAT(
                                'PREFRENOV-',
                                LEFT(
                                    CONVERT(
                                        VARCHAR(64),
                                        HASHBYTES(
                                            'SHA2_256',
                                            CONCAT(
                                                :tipo_vinculo, '|',
                                                @IDFatoOcupacaoOrigem, '|',
                                                ISNULL(@IDFatoControleContratosItemOrigem, 0), '|',
                                                @IDFatoControleContratos, '|',
                                                @CodPonto, '|',
                                                @CodFace, '|',
                                                @FaceInicio, '|',
                                                @FaceFim, '|',
                                                CONVERT(VARCHAR(10), @DataInicioReserva, 120), '|',
                                                CONVERT(VARCHAR(10), @DataFimReserva, 120)
                                            )
                                        ),
                                        2
                                    ),
                                    44
                                )
                            );

                            /*
                               A execução agendada também corrige preferências
                               já existentes: reposiciona o slot e força o
                               início para D+1 do fim consolidado da origem.
                            */
                            IF @IDReservaPreferenciaExistente IS NOT NULL
                            BEGIN
                                DELETE FROM #Bloqueios
                                WHERE IDFatoOcupacaoPaineisMidia =
                                    @IDReservaPreferenciaExistente;

                                UPDATE R
                                   SET R.DataAtualizacao = SYSDATETIME(),
                                       R.Referencia = @ReferenciaPreferencia,
                                       R.CodPonto = @CodPonto,
                                       R.CodFace = @CodFace,
                                       R.IDPainelMidia = @IDPainelMidia,
                                       R.Origem = :origem_reserva,
                                       R.Status = :status_reservado,
                                       R.DataInicio = @DataInicioReserva,
                                       R.DataFim = @DataFimReserva,
                                       R.LoopInicio = @FaceInicio,
                                       R.LoopFim = @FaceFim,
                                       R.SpanQtd = @SpanQtdCalculado,
                                       R.Cota = @Cota,
                                       R.MarcaExibida = @MarcaExibida,
                                       R.Vendedor = @Vendedor,
                                       R.IDVendedor = @IDVendedor,
                                       R.IDCliente = @IDCliente,
                                       R.IDFatoControleContratos =
                                           @IDFatoControleContratos,
                                       R.NumeroContrato = @NumeroContrato,
                                       R.NumeroPrevia = @NumeroPrevia,
                                       R.TextoOriginal = LEFT(CONCAT(
                                           'Reserva automática realinhada por preferência de renovação. ',
                                           'Ocupação origem: ', @IDFatoOcupacaoOrigem,
                                           '. Período origem: ',
                                           CONVERT(VARCHAR(10), @DataInicioOrigem, 103),
                                           ' até ',
                                           CONVERT(VARCHAR(10), @DataFimOrigem, 103),
                                           '. Reserva obrigatória em D+1: ',
                                           CONVERT(VARCHAR(10), @DataInicioReserva, 103),
                                           ' até ',
                                           CONVERT(VARCHAR(10), @DataFimReserva, 103),
                                           '. Slot: ', @FaceInicio, ' até ', @FaceFim, '.'
                                       ), 1000),
                                       R.Observacao = LEFT(CONCAT(
                                           'Preferência de renovação realinhada automaticamente. ',
                                           'TipoVinculoOrigem=', :tipo_vinculo,
                                           '. TipoReserva=', :tipo_reserva_preferencia,
                                           '. ', :marcador_execucao
                                       ), 500),
                                       R.Dias = DATEDIFF(
                                           DAY,
                                           @DataInicioReserva,
                                           @DataFimReserva
                                       ) + 1,
                                       R.IDFatoOcupacaoOrigem =
                                           @IDFatoOcupacaoOrigem,
                                       R.IDFatoControleContratosItemOrigem =
                                           @IDFatoControleContratosItemOrigem,
                                       R.TipoVinculoOrigem = :tipo_vinculo,
                                       R.TipoReserva = :tipo_reserva_preferencia
                                FROM Integracao.Silver.FatoOcupacaoPaineisMidia AS R
                                WHERE
                                    R.IDFatoOcupacaoPaineisMidia =
                                        @IDReservaPreferenciaExistente
                                    AND R.CanceladoEm IS NULL;

                                SET @NovoID = @IDReservaPreferenciaExistente;

                                INSERT INTO #Bloqueios
                                (
                                    IDFatoOcupacaoPaineisMidia,
                                    IDPainelMidia,
                                    DataInicio,
                                    DataFim,
                                    FaceInicio,
                                    FaceFim
                                )
                                VALUES
                                (
                                    @NovoID,
                                    @IDPainelMidia,
                                    @DataInicioReserva,
                                    @DataFimReserva,
                                    @FaceInicio,
                                    @FaceFim
                                );
                            END;

                            IF @IDReservaPreferenciaExistente IS NULL
                               AND NOT EXISTS
                            (
                                SELECT 1
                                FROM Integracao.Silver.FatoOcupacaoPaineisMidia AS X
                                    WITH (UPDLOCK, HOLDLOCK)
                                WHERE
                                    X.Referencia = @ReferenciaPreferencia
                                    OR
                                    (
                                        (
                                            X.IDFatoOcupacaoOrigem = @IDFatoOcupacaoOrigem
                                            OR
                                            (
                                                @IDFatoControleContratosItemOrigem IS NOT NULL
                                                AND X.IDFatoControleContratosItemOrigem =
                                                    @IDFatoControleContratosItemOrigem
                                            )
                                        )
                                        AND UPPER(LTRIM(RTRIM(ISNULL(X.TipoVinculoOrigem, ''))))
                                            = UPPER(LTRIM(RTRIM(:tipo_vinculo)))
                                        AND UPPER(LTRIM(RTRIM(ISNULL(X.Origem, ''))))
                                            = UPPER(LTRIM(RTRIM(:origem_reserva)))
                                    )
                            )
                            BEGIN
                                INSERT INTO Integracao.Silver.FatoOcupacaoPaineisMidia
                                (
                                    DataAtualizacao,
                                    Referencia,
                                    CodPonto,
                                    CodFace,
                                    IDPainelMidia,
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
                                    TipoVinculoOrigem,
                                    TipoReserva
                                )
                                VALUES
                                (
                                    SYSDATETIME(),
                                    @ReferenciaPreferencia,
                                    @CodPonto,
                                    @CodFace,
                                    @IDPainelMidia,
                                    :origem_reserva,
                                    :status_reservado,
                                    @DataInicioReserva,
                                    @DataFimReserva,
                                    @FaceInicio,
                                    @FaceFim,
                                    @SpanQtdCalculado,
                                    @Cota,
                                    @MarcaExibida,
                                    @Vendedor,
                                    @IDVendedor,
                                    @IDCliente,
                                    @IDFatoControleContratos,
                                    @NumeroContrato,
                                    @NumeroPrevia,
                                    LEFT(CONCAT(
                                        'Reserva automática criada por preferência de renovação. ',
                                        'Ocupação origem: ', @IDFatoOcupacaoOrigem,
                                        '. Período origem: ',
                                        CONVERT(VARCHAR(10), @DataInicioOrigem, 103),
                                        ' até ',
                                        CONVERT(VARCHAR(10), @DataFimOrigem, 103),
                                        '. Data encaixada: ',
                                        CONVERT(VARCHAR(10), @DataInicioReserva, 103),
                                        ' até ',
                                        CONVERT(VARCHAR(10), @DataFimReserva, 103),
                                        '. Slot: ', @FaceInicio, ' até ', @FaceFim, '.'
                                    ), 1000),
                                    SYSDATETIME(),
                                    :id_usuario,
                                    NULL,
                                    NULL,
                                    NULL,
                                    LEFT(CONCAT(
                                        'Preferência de renovação gerada automaticamente. ',
                                        'TipoVinculoOrigem=', :tipo_vinculo,
                                        '. TipoReserva=', :tipo_reserva_preferencia,
                                        '. ', :marcador_execucao
                                    ), 500),
                                    DATEDIFF(
                                        DAY,
                                        @DataInicioReserva,
                                        @DataFimReserva
                                    ) + 1,
                                    NULL,
                                    @IDFatoOcupacaoOrigem,
                                    @IDFatoControleContratosItemOrigem,
                                    :tipo_vinculo,
                                    :tipo_reserva_preferencia
                                );

                                SET @NovoID = CONVERT(INT, SCOPE_IDENTITY());

                                INSERT INTO #Bloqueios
                                (
                                    IDFatoOcupacaoPaineisMidia,
                                    IDPainelMidia,
                                    DataInicio,
                                    DataFim,
                                    FaceInicio,
                                    FaceFim
                                )
                                VALUES
                                (
                                    @NovoID,
                                    @IDPainelMidia,
                                    @DataInicioReserva,
                                    @DataFimReserva,
                                    @FaceInicio,
                                    @FaceFim
                                );
                            END;

                            /*
                               Se outra execução já criou a preferência, também
                               encerro a busca desta origem: idempotência.
                            */
                            SET @Inseriu = 1;
                        END;

                        SET @FaceInicio = @FaceInicio + 1;
                    END;

                FETCH NEXT FROM cursor_elegiveis INTO
                    @IDFatoOcupacaoOrigem,
                    @IDReservaPreferenciaExistente,
                    @IDFatoControleContratos,
                    @IDFatoControleContratosItemOrigem,
                    @CodPonto,
                    @CodFace,
                    @IDPainelMidia,
                    @FaceOrdemOrigem,
                    @BitDigital,
                    @QuantidadeFacesCalculada,
                    @DataInicioOrigem,
                    @DataFimOrigem,
                    @QuantidadeMesesContrato,
                    @DataMinimaReserva,
                    @SpanQtdCalculado,
                    @Cota,
                    @MarcaExibida,
                    @Vendedor,
                    @IDVendedor,
                    @IDCliente,
                    @NumeroContrato,
                    @NumeroPrevia;
            END;

            CLOSE cursor_elegiveis;
            DEALLOCATE cursor_elegiveis;

            COMMIT TRANSACTION;
        END TRY
        BEGIN CATCH
            IF CURSOR_STATUS('local', 'cursor_elegiveis') >= 0
                CLOSE cursor_elegiveis;

            IF CURSOR_STATUS('local', 'cursor_elegiveis') >= -1
                DEALLOCATE cursor_elegiveis;

            IF XACT_STATE() <> 0
                ROLLBACK TRANSACTION;

            THROW;
        END CATCH;
        """

        sql_garantir_origem_status_reservas_preferencia = """
        SET NOCOUNT ON;

        /*
           Trava final obrigatória: qualquer reserva de preferência criada ou reaproveitada
           por esta DAG precisa ficar visível para a grade/listagem como reserva de verdade.

           Não pode ficar como OCUPACAO, KANBAN, CONTRATO ou ATIVO, porque isso faz a grade
           tratar a linha como ocupação normal ou ignorar a reserva.
        */
        UPDATE R
           SET R.Origem = :origem_reserva,
               R.Status = :status_reservado,
               R.TipoReserva = :tipo_reserva_preferencia,
               R.DataAtualizacao = SYSDATETIME()
        FROM Integracao.Silver.FatoOcupacaoPaineisMidia AS R
        WHERE
            R.CanceladoEm IS NULL
            AND (
                UPPER(LTRIM(RTRIM(ISNULL(R.TipoVinculoOrigem, '')))) = UPPER(LTRIM(RTRIM(:tipo_vinculo)))
                OR UPPER(LTRIM(RTRIM(ISNULL(R.Referencia, '')))) LIKE 'PREFRENOV-%'
                OR UPPER(LTRIM(RTRIM(ISNULL(R.Observacao, '')))) LIKE '%' + UPPER(LTRIM(RTRIM(:marcador_execucao))) + '%'
            )
            AND (:id_contrato IS NULL OR R.IDFatoControleContratos = :id_contrato)
            AND (
                :ids_ocupacao_origem_csv IS NULL
                OR EXISTS
                (
                    SELECT 1
                    FROM STRING_SPLIT(:ids_ocupacao_origem_csv, ',') AS ids_occ
                    WHERE TRY_CONVERT(int, ids_occ.value) = R.IDFatoOcupacaoOrigem
                )
            )
            AND (
                UPPER(LTRIM(RTRIM(ISNULL(R.Origem, '')))) <> UPPER(LTRIM(RTRIM(:origem_reserva)))
                OR UPPER(LTRIM(RTRIM(ISNULL(R.Status, '')))) <> UPPER(LTRIM(RTRIM(:status_reservado)))
                OR ISNULL(R.TipoReserva, 0) <> :tipo_reserva_preferencia
            );
        """


        sql_marcar_bit_preferencia_itens_elegiveis = """
        SET NOCOUNT ON;

        /*
           BitPreferencia só pode ser marcado quando a reserva física existe.
           A regra anterior marcava todos os itens teoricamente elegíveis, mesmo
           quando o Tetris não encontrava encaixe ou o INSERT não acontecia.
        */
        ;WITH ReservasPreferencia AS
        (
            SELECT DISTINCT
                R.IDFatoOcupacaoOrigem,
                R.IDFatoControleContratosItemOrigem
            FROM Integracao.Silver.FatoOcupacaoPaineisMidia AS R WITH (NOLOCK)
            WHERE
                R.IDFatoOcupacaoOrigem IS NOT NULL
                AND UPPER(LTRIM(RTRIM(ISNULL(R.Origem, ''))))
                    = UPPER(LTRIM(RTRIM(:origem_reserva)))
                AND UPPER(LTRIM(RTRIM(ISNULL(R.Status, ''))))
                    = UPPER(LTRIM(RTRIM(:status_reservado)))
                AND UPPER(LTRIM(RTRIM(ISNULL(R.TipoVinculoOrigem, ''))))
                    = UPPER(LTRIM(RTRIM(:tipo_vinculo)))
                AND ISNULL(R.TipoReserva, 0) = :tipo_reserva_preferencia
                AND R.CanceladoEm IS NULL
                AND
                (
                    :id_contrato IS NULL
                    OR R.IDFatoControleContratos = :id_contrato
                    OR EXISTS
                    (
                        SELECT 1
                        FROM Integracao.Silver.FatoVinculaMarcasOcupacao AS VC WITH (NOLOCK)
                        WHERE
                            VC.IDFatoOcupacaoPaineisMidia = R.IDFatoOcupacaoOrigem
                            AND VC.IDFatoControleContratosMidia = :id_contrato
                    )
                )
                AND (
                    :ids_ocupacao_origem_csv IS NULL
                    OR EXISTS
                    (
                        SELECT 1
                        FROM STRING_SPLIT(:ids_ocupacao_origem_csv, ',') AS ids_occ
                        WHERE
                            TRY_CONVERT(INT, ids_occ.value) = R.IDFatoOcupacaoOrigem
                    )
                )
        ),
        ItensComPreferencia AS
        (
            SELECT DISTINCT
                R.IDFatoControleContratosItemOrigem
            FROM ReservasPreferencia AS R
            WHERE R.IDFatoControleContratosItemOrigem IS NOT NULL

            UNION

            SELECT DISTINCT
                V.IDFatoControleContratosItensMidia
            FROM ReservasPreferencia AS R
            INNER JOIN Integracao.Silver.FatoVinculaMarcasOcupacao AS V WITH (NOLOCK)
                ON V.IDFatoOcupacaoPaineisMidia = R.IDFatoOcupacaoOrigem
            WHERE V.IDFatoControleContratosItensMidia IS NOT NULL

            UNION

            SELECT DISTINCT
                A.IDFatoControleContratosItensMidia
            FROM ReservasPreferencia AS R
            INNER JOIN Integracao.Silver.FatoAgendamentoFaceContrato AS A WITH (NOLOCK)
                ON A.IDFatoOcupacaoPaineisMidia = R.IDFatoOcupacaoOrigem
            WHERE
                A.IDFatoControleContratosItensMidia IS NOT NULL
                AND A.BitAtivo = 1
        )
        UPDATE item
           SET item.BitPreferencia = 1,
               item.DataAtualizacao = SYSDATETIME()
        FROM Integracao.Silver.FatoControleContratosItensMidia AS item
        INNER JOIN ItensComPreferencia AS preferencia
            ON preferencia.IDFatoControleContratosItemOrigem =
               item.IDFatoControleContratosItensMidia
        WHERE
            ISNULL(item.BitAtivo, 1) = 1
            AND ISNULL(item.BitPreferencia, 0) <> 1;
        """

        sql_diagnostico_rapido = """
        SET NOCOUNT ON;

        ;WITH Escopo AS
        (
            SELECT
                O.IDFatoOcupacaoPaineisMidia,
                PeriodoOcupacao.DataInicio,
                PeriodoOcupacao.DataFim,
                O.IDPainelMidia,
                COALESCE(
                    O.IDFatoControleContratos,
                    V.IDFatoControleContratosMidia,
                    I.IDFatoControleContratoMidia
                ) AS IDFatoControleContratos,
                P.IDDimPaineisMidia AS PainelValido,
                PF.IDDimPaineisMidia AS FaceValida,
                CASE
                    WHEN R.IDFatoOcupacaoPaineisMidia IS NULL THEN 0
                    ELSE 1
                END AS JaTemPreferencia
            FROM Integracao.Silver.FatoOcupacaoPaineisMidia AS O WITH (NOLOCK)
            OUTER APPLY
            (
                SELECT
                    MIN(Periodo.DataInicio) AS DataInicio,
                    MAX(Periodo.DataFim) AS DataFim
                FROM
                (
                    SELECT
                        CAST(O.DataInicio AS DATE) AS DataInicio,
                        CAST(O.DataFim AS DATE) AS DataFim

                    UNION ALL

                    SELECT
                        CAST(A.DataInicio AS DATE),
                        CAST(A.DataTermino AS DATE)
                    FROM Integracao.Silver.FatoAgendamentoFaceContrato AS A WITH (NOLOCK)
                    WHERE
                        A.IDFatoOcupacaoPaineisMidia =
                            O.IDFatoOcupacaoPaineisMidia
                        AND A.BitAtivo = 1
                        AND A.DataInicio IS NOT NULL
                        AND A.DataTermino IS NOT NULL
                ) AS Periodo
            ) AS PeriodoOcupacao
            OUTER APPLY
            (
                SELECT TOP (1)
                    V2.IDFatoControleContratosMidia,
                    V2.IDFatoControleContratosItensMidia
                FROM Integracao.Silver.FatoVinculaMarcasOcupacao AS V2 WITH (NOLOCK)
                WHERE
                    V2.IDFatoOcupacaoPaineisMidia =
                        O.IDFatoOcupacaoPaineisMidia
                ORDER BY
                    V2.IDFatoVinculaMarcasOcupacao
            ) AS V
            LEFT JOIN Integracao.Silver.FatoControleContratosItensMidia AS I WITH (NOLOCK)
                ON I.IDFatoControleContratosItensMidia = COALESCE(
                    O.IDFatoControleContratosItemOrigem,
                    V.IDFatoControleContratosItensMidia
                )
            LEFT JOIN Integracao.Silver.DimPaineisMidia AS P WITH (NOLOCK)
                ON P.IDDimPaineisMidia = O.IDPainelMidia
                AND TRY_CONVERT(INT, P.QuantidadeFaces) > 0
            OUTER APPLY
            (
                SELECT TOP (1)
                    F.IDDimPaineisMidia
                FROM Integracao.Silver.DimFacesPaineis AS F WITH (NOLOCK)
                WHERE
                    F.IDDimPaineisMidia = O.IDPainelMidia
                    AND F.CodPonto = O.CodPonto
                    AND F.CodFace = O.CodFace
            ) AS PF
            OUTER APPLY
            (
                SELECT TOP (1)
                    R2.IDFatoOcupacaoPaineisMidia
                FROM Integracao.Silver.FatoOcupacaoPaineisMidia AS R2 WITH (NOLOCK)
                WHERE
                    R2.IDFatoOcupacaoOrigem =
                        O.IDFatoOcupacaoPaineisMidia
                    AND UPPER(LTRIM(RTRIM(ISNULL(R2.Origem, ''))))
                        = UPPER(LTRIM(RTRIM(:origem_reserva)))
                    AND UPPER(LTRIM(RTRIM(ISNULL(R2.TipoVinculoOrigem, ''))))
                        = UPPER(LTRIM(RTRIM(:tipo_vinculo)))
            ) AS R
            WHERE
                (
                    :id_contrato IS NULL
                    OR COALESCE(
                        O.IDFatoControleContratos,
                        V.IDFatoControleContratosMidia,
                        I.IDFatoControleContratoMidia
                    ) = :id_contrato
                )
                AND
                (
                    :ids_ocupacao_origem_csv IS NULL
                    OR EXISTS
                    (
                        SELECT 1
                        FROM STRING_SPLIT(:ids_ocupacao_origem_csv, ',') AS IDS
                        WHERE
                            TRY_CONVERT(INT, IDS.value) =
                                O.IDFatoOcupacaoPaineisMidia
                    )
                )
                AND O.CanceladoEm IS NULL
                AND PeriodoOcupacao.DataInicio IS NOT NULL
                AND PeriodoOcupacao.DataFim IS NOT NULL
                AND UPPER(LTRIM(RTRIM(ISNULL(O.Status, ''))))
                    <> UPPER(LTRIM(RTRIM(:status_cancelado)))
                AND
                (
                    UPPER(LTRIM(RTRIM(ISNULL(O.Origem, ''))))
                        = UPPER(LTRIM(RTRIM(:origem_contrato)))
                    OR
                    (
                        UPPER(LTRIM(RTRIM(ISNULL(O.Origem, '')))) = 'OCUPACAO'
                        AND ISNULL(O.TipoReserva, 0) = 0
                    )
                )
        )
        SELECT
            COUNT(1) AS origens_no_escopo,
            COALESCE(SUM(
                CASE
                    WHEN CAST(E.DataFim AS DATE) >= DATEADD(
                        DAY,
                        -1,
                        DATEADD(MONTH, :meses_minimos, CAST(E.DataInicio AS DATE))
                    )
                    THEN 1 ELSE 0
                END
            ), 0) AS origens_com_seis_meses,
            COALESCE(SUM(
                CASE WHEN E.IDFatoControleContratos IS NULL THEN 1 ELSE 0 END
            ), 0) AS origens_sem_contrato,
            COALESCE(SUM(
                CASE WHEN E.PainelValido IS NULL THEN 1 ELSE 0 END
            ), 0) AS origens_sem_painel_valido,
            COALESCE(SUM(
                CASE WHEN E.FaceValida IS NULL THEN 1 ELSE 0 END
            ), 0) AS origens_sem_face_valida,
            COALESCE(SUM(E.JaTemPreferencia), 0) AS origens_com_preferencia_existente,
            COALESCE(SUM(
                CASE
                    WHEN CAST(E.DataFim AS DATE) >= DATEADD(
                        DAY,
                        -1,
                        DATEADD(MONTH, :meses_minimos, CAST(E.DataInicio AS DATE))
                    )
                    AND E.IDFatoControleContratos IS NOT NULL
                    AND E.PainelValido IS NOT NULL
                    AND E.FaceValida IS NOT NULL
                    AND E.JaTemPreferencia = 0
                    THEN 1 ELSE 0
                END
            ), 0) AS origens_prontas_para_tetris
        FROM Escopo AS E
        OPTION (RECOMPILE);
        """

        sql_contar_itens_bit_preferencia_1 = """
        SET NOCOUNT ON;

        ;WITH ItensComReservaPreferencia AS
        (
            SELECT DISTINCT
                R.IDFatoControleContratosItemOrigem
            FROM Integracao.Silver.FatoOcupacaoPaineisMidia AS R WITH (NOLOCK)
            WHERE
                R.IDFatoControleContratosItemOrigem IS NOT NULL
                AND R.Origem = :origem_reserva
                AND R.Status = :status_reservado
                AND R.TipoVinculoOrigem = :tipo_vinculo
                AND ISNULL(R.TipoReserva, 0) = :tipo_reserva_preferencia
                AND R.CanceladoEm IS NULL
                AND (:id_contrato IS NULL OR R.IDFatoControleContratos = :id_contrato)

            UNION

            SELECT DISTINCT
                V.IDFatoControleContratosItensMidia
            FROM Integracao.Silver.FatoOcupacaoPaineisMidia AS R WITH (NOLOCK)
            INNER JOIN Integracao.Silver.FatoVinculaMarcasOcupacao AS V WITH (NOLOCK)
                ON V.IDFatoOcupacaoPaineisMidia = R.IDFatoOcupacaoOrigem
            WHERE
                V.IDFatoControleContratosItensMidia IS NOT NULL
                AND R.Origem = :origem_reserva
                AND R.Status = :status_reservado
                AND R.TipoVinculoOrigem = :tipo_vinculo
                AND ISNULL(R.TipoReserva, 0) = :tipo_reserva_preferencia
                AND R.CanceladoEm IS NULL
                AND (
                    :id_contrato IS NULL
                    OR R.IDFatoControleContratos = :id_contrato
                    OR V.IDFatoControleContratosMidia = :id_contrato
                )

            UNION

            SELECT DISTINCT
                A.IDFatoControleContratosItensMidia
            FROM Integracao.Silver.FatoOcupacaoPaineisMidia AS R WITH (NOLOCK)
            INNER JOIN Integracao.Silver.FatoAgendamentoFaceContrato AS A WITH (NOLOCK)
                ON A.IDFatoOcupacaoPaineisMidia = R.IDFatoOcupacaoOrigem
            WHERE
                A.IDFatoControleContratosItensMidia IS NOT NULL
                AND A.BitAtivo = 1
                AND R.Origem = :origem_reserva
                AND R.Status = :status_reservado
                AND R.TipoVinculoOrigem = :tipo_vinculo
                AND ISNULL(R.TipoReserva, 0) = :tipo_reserva_preferencia
                AND R.CanceladoEm IS NULL
                AND
                (
                    :id_contrato IS NULL
                    OR R.IDFatoControleContratos = :id_contrato
                    OR A.IDFatoControleContratosMidia = :id_contrato
                )
        )
        SELECT
            COUNT(1) AS itens_preferencia_marcados
        FROM Integracao.Silver.FatoControleContratosItensMidia AS item WITH (NOLOCK)
        INNER JOIN ItensComReservaPreferencia AS reserva
            ON reserva.IDFatoControleContratosItemOrigem = item.IDFatoControleContratosItensMidia
        WHERE
            ISNULL(item.BitPreferencia, 0) = 1;
        """

        sql_contar_criadas_execucao = """
        SET NOCOUNT ON;

        SELECT
            COUNT(1) AS reservas_criadas
        FROM Integracao.Silver.FatoOcupacaoPaineisMidia AS O WITH (NOLOCK)
        WHERE
            O.Origem = :origem_reserva
            AND O.Status = :status_reservado
            AND O.TipoVinculoOrigem = :tipo_vinculo
            AND ISNULL(O.TipoReserva, 0) = :tipo_reserva_preferencia
            AND O.Observacao LIKE '%' + :marcador_execucao + '%';
        """

        logging.info(
            "Abrindo conexão SQL Server para processar preferências. escopo=%s",
            escopo,
        )
        hook = obter_hook_sql_server()
        logging.info("Conexão SQL Server pronta. Executando diagnóstico leve.")

        diagnostico_rows = hook.executar_select(
            sql_diagnostico_rapido,
            parametros,
        ) or []
        diagnostico_antes = (
            dict(diagnostico_rows[0] or {})
            if diagnostico_rows
            else {
                "origens_no_escopo": 0,
                "origens_com_seis_meses": 0,
                "origens_prontas_para_tetris": 0,
            }
        )
        logging.info(
            "Diagnóstico de preferência concluído. escopo=%s | id_contrato=%s | dados=%s",
            escopo,
            id_contrato,
            diagnostico_antes,
        )

        logging.info(
            "Executando criação incremental das reservas de preferência. "
            "escopo=%s | inicio_reserva=D+1_fixo | encaixe=tetris_por_slot",
            escopo,
        )
        _executar_comando_parametrizado(hook, sql_insert, parametros)
        logging.info("INSERT incremental de preferências concluído.")

        _executar_comando_parametrizado(
            hook,
            sql_garantir_origem_status_reservas_preferencia,
            parametros,
        )
        logging.info("Normalização final de Origem/Status/TipoReserva concluída.")

        _executar_comando_parametrizado(
            hook,
            sql_marcar_bit_preferencia_itens_elegiveis,
            parametros,
        )
        logging.info("BitPreferencia atualizado somente para itens com reserva existente.")

        criadas_rows = hook.executar_select(sql_contar_criadas_execucao, parametros) or []
        reservas_criadas = int((criadas_rows[0] or {}).get("reservas_criadas") or 0) if criadas_rows else 0

        itens_rows = hook.executar_select(sql_contar_itens_bit_preferencia_1, parametros) or []
        itens_preferencia_marcados = (
            int((itens_rows[0] or {}).get("itens_preferencia_marcados") or 0)
            if itens_rows
            else 0
        )

        logging.info(
            "Criação de reserva de preferência finalizada. "
            "escopo=%s | id_contrato=%s | reservas_criadas=%s | "
            "itens_preferencia_marcados=%s | diagnostico_antes=%s | tipo_vinculo=%s",
            escopo,
            id_contrato,
            reservas_criadas,
            itens_preferencia_marcados,
            diagnostico_antes,
            TIPO_VINCULO_PREFERENCIA_RENOVACAO,
        )

        return {
            "status": "ok",
            "escopo": escopo,
            "id_contrato": id_contrato,
            "tipo_vinculo": TIPO_VINCULO_PREFERENCIA_RENOVACAO,
            "reservas_criadas": reservas_criadas,
            "itens_preferencia_marcados": itens_preferencia_marcados,
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
            IDFatoOcupacaoPaineisMidia INT NOT NULL,
            PrioridadeAnterior INT NULL,
            NovaPrioridade INT NOT NULL
        );

        ;WITH ReservasAtivas AS
        (
            SELECT
                O.IDFatoOcupacaoPaineisMidia,
                O.CodPonto,
                O.CodFace,
                CAST(O.DataInicio AS DATE) AS DataInicio,
                CAST(O.DataFim AS DATE) AS DataFim,
                O.CriadoEm,
                O.ReservaOrdemPrioridade
            FROM Integracao.Silver.FatoOcupacaoPaineisMidia AS O
            WHERE
                O.Origem = :origem_reserva
                AND O.Status = :status_reservado
                AND O.CanceladoEm IS NULL
                AND O.CodPonto IS NOT NULL
                AND O.CodFace IS NOT NULL
                AND O.DataInicio IS NOT NULL
                AND O.DataFim IS NOT NULL
                AND ISNULL(O.TipoReserva, 0) <> 0
        ),
        PrioridadesCalculadas AS
        (
            SELECT
                atual.IDFatoOcupacaoPaineisMidia,
                atual.ReservaOrdemPrioridade AS PrioridadeAnterior,
                1 + COUNT(anterior.IDFatoOcupacaoPaineisMidia) AS NovaPrioridade
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
                        AND anterior.IDFatoOcupacaoPaineisMidia < atual.IDFatoOcupacaoPaineisMidia
                    )
                )
            GROUP BY
                atual.IDFatoOcupacaoPaineisMidia,
                atual.ReservaOrdemPrioridade
        )
        UPDATE destino
        SET
            destino.ReservaOrdemPrioridade = prioridade.NovaPrioridade,
            destino.DataAtualizacao = SYSDATETIME()
        OUTPUT
            inserted.IDFatoOcupacaoPaineisMidia,
            deleted.ReservaOrdemPrioridade,
            inserted.ReservaOrdemPrioridade
        INTO @ReservasAtualizadas
        (
            IDFatoOcupacaoPaineisMidia,
            PrioridadeAnterior,
            NovaPrioridade
        )
        FROM Integracao.Silver.FatoOcupacaoPaineisMidia AS destino
        INNER JOIN PrioridadesCalculadas AS prioridade
            ON prioridade.IDFatoOcupacaoPaineisMidia = destino.IDFatoOcupacaoPaineisMidia
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
