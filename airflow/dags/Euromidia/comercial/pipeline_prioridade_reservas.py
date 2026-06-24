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


# Por padrão, esta DAG NÃO roda por agenda.
# Ela deve ser acionada pelo Kanban/Admin depois que a ocupação foi criada/efetivada.
# Só habilite varredura agendada se realmente quiser reprocessar ocupações antigas sem trigger.
CRIAR_RESERVAS_NA_EXECUCAO_AGENDADA = _env_bool(
    "PIPELINE_PRIORIDADE_RESERVAS_CRIAR_NA_AGENDA",
    "0",
)

HABILITAR_AGENDAMENTO_AUTOMATICO = _env_bool(
    "PIPELINE_PRIORIDADE_RESERVAS_HABILITAR_AGENDAMENTO",
    "0",
)

DIAS_MAXIMOS_PROCURAR_ENCAIXE_TETRIS = _env_int(
    "PIPELINE_PRIORIDADE_RESERVAS_DIAS_MAXIMOS_PROCURAR_ENCAIXE",
    365,
    minimo=0,
    maximo=3650,
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

# Regra combinada: a DAG é acionada pelo Kanban/Admin, não por cron.
# Se precisar ligar varredura automática depois, defina
# PIPELINE_PRIORIDADE_RESERVAS_HABILITAR_AGENDAMENTO=1.
SCHEDULE_DAG = CRON_AGENDAMENTO_DAG if HABILITAR_AGENDAMENTO_AUTOMATICO else None


DOCUMENTACAO_DAG = """
# pipeline_prioridade_reservas

## Objetivo

Esta DAG controla a criação automática de reservas de preferência de renovação e a prioridade das reservas de ocupação dos painéis da Euromídia.

Ela possui duas funções principais:

1. Criar automaticamente uma reserva de preferência de renovação quando uma ocupação contratual tiver duração comercial de 6 meses ou mais.
2. Marcar `FatoControleContratosItensEuromidia.BitPreferencia = 1` no item de contrato que gerou a reserva.
3. Recalcular continuamente o campo `ReservaOrdemPrioridade` das reservas ativas, respeitando a ordem de criação dentro do mesmo `CodPonto`, `CodFace` e período cruzado.

---

## Regra de criação da reserva de preferência

A DAG cria reserva de preferência a partir das ocupações já gravadas/efetivadas com Origem = CONTRATO:

1. **Aprovação de contrato pela tela administrativa**  
   Quando recebe `id_contrato` no `dag_run.conf`, processa somente as ocupações daquele contrato que já nasceram/foram efetivadas com `Origem = CONTRATO`.

2. **Varredura pós-upsert de ocupação / execução agendada**  
   Quando roda sem `id_contrato`, pode procurar ocupações elegíveis na tabela `Integracao.Silver.FatoOcupacaoPaineisEuromidia`, sempre filtrando `Origem = CONTRATO`, e criar somente as reservas que ainda não existem.

A varredura agendada fica desligada por padrão. A regra combinada é: o Kanban cria/efetiva a ocupação, confirma o commit e aciona esta DAG com o ID da ocupação ou do contrato. A reserva futura é criada somente aqui.

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

A reserva futura usa o dia seguinte ao fim da ocupação origem como data mínima. Se não houver encaixe livre nessa data, a DAG procura para frente o primeiro período livre possível, respeitando a grade tipo Tetris.

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

Antes de inserir, a DAG aplica a regra de encaixe tipo Tetris: calcula as faces/slots disponíveis a partir de DimPaineisEuromidia + DimFacesPaineis, valida que o CodFace pertence ao IDDimPaineisEuromidia, considera contratos, ocupações e reservas ativas no período futuro e só grava a reserva se existir um bloco livre suficiente para a cota.

A quantidade de posições da grade vem de `Integracao.Silver.DimPaineisEuromidia.QuantidadeFaces`. A DAG não inventa capacidade fixa de 16 faces: se o painel 1137 está com `QuantidadeFaces = 16`, usa 16; se outro painel estiver com outra quantidade, usa a quantidade cadastrada; se a quantidade estiver ausente/inválida, a ocupação não é elegível até o cadastro ser corrigido.

A resolução do painel da ocupação/reserva não é feita apenas por `CodPonto`. A DAG valida o `CodFace` pela `Integracao.Silver.DimFacesPaineis`, usando `DimFacesPaineis.IDDimPaineisEuromidia` como chave para a `DimPaineisEuromidia`. Assim o sistema só cria reserva em face cadastrada naquele painel e só considera conflito dentro do mesmo painel/slot.

Quando a ocupação é elegível, a DAG também marca o item original em `FatoControleContratosItensEuromidia.BitPreferencia = 1`, para não depender de outro DAG rodar depois.

---

## Regra de não duplicidade

A DAG tem três travas contra duplicidade:

1. Gera uma `Referencia` determinística usando hash.
2. Antes de inserir, verifica se já existe registro com a mesma `Referencia`.
3. Antes de inserir, verifica se já existe reserva com o mesmo `IDFatoOcupacaoOrigem` ou com o mesmo `IDFatoControleContratosItemOrigem` e o mesmo `TipoVinculoOrigem`.

A trava por item é importante porque uma mesma ocupação comercial pode aparecer em mais de uma linha física na grade, especialmente em painel digital/1080. Isso protege contra duplicidade quando:

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

Por padrão, a DAG não executa sozinha por cron. Ela é acionada pelo Kanban depois da criação/efetivação da ocupação.

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
                AND c.name = 'TipoReserva'
        )
        BEGIN
            ALTER TABLE Integracao.Silver.FatoOcupacaoPaineisEuromidia
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
                   MarcaContrato.IDFatoOcupacaoPaineisEuromidia,
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
        FROM Integracao.Silver.FatoOcupacaoPaineisEuromidia AS R
        OUTER APPLY
        (
            SELECT TOP (1)
                OC.IDFatoOcupacaoPaineisEuromidia,
                COALESCE(
                    OC.IDFatoControleContratosItemOrigem,
                    I.IDFatoControleContratosItensEuromidia
                ) AS IDFatoControleContratosItemOrigem,
                COALESCE(
                    NULLIF(LTRIM(RTRIM(I.MarcaExibida)), ''),
                    NULLIF(LTRIM(RTRIM(OC.MarcaExibida)), '')
                ) AS MarcaExibida
            FROM Integracao.Silver.FatoOcupacaoPaineisEuromidia AS OC WITH (NOLOCK)
            OUTER APPLY
            (
                SELECT TOP (1)
                    I2.IDFatoControleContratosItensEuromidia,
                    I2.MarcaExibida
                FROM Integracao.Silver.FatoControleContratosItensEuromidia AS I2 WITH (NOLOCK)
                WHERE
                    (
                        OC.IDFatoControleContratosItemOrigem IS NOT NULL
                        AND I2.IDFatoControleContratosItensEuromidia = OC.IDFatoControleContratosItemOrigem
                    )
                    OR
                    (
                        OC.IDFatoControleContratos IS NOT NULL
                        AND I2.IDFatoControleContratoEuromidia = OC.IDFatoControleContratos
                        AND I2.CodPonto = OC.CodPonto
                        AND I2.CodFace = OC.CodFace
                    )
                ORDER BY
                    CASE
                        WHEN OC.IDFatoControleContratosItemOrigem IS NOT NULL
                             AND I2.IDFatoControleContratosItensEuromidia = OC.IDFatoControleContratosItemOrigem
                        THEN 0
                        ELSE 1
                    END,
                    I2.IDFatoControleContratosItensEuromidia DESC
            ) AS I
            WHERE
                OC.CanceladoEm IS NULL
                AND UPPER(LTRIM(RTRIM(ISNULL(OC.Status, '')))) <> UPPER(LTRIM(RTRIM(:status_cancelado)))
                AND UPPER(LTRIM(RTRIM(ISNULL(OC.Origem, '')))) = UPPER(LTRIM(RTRIM(:origem_contrato)))
                AND COALESCE(NULLIF(LTRIM(RTRIM(I.MarcaExibida)), ''), NULLIF(LTRIM(RTRIM(OC.MarcaExibida)), '')) IS NOT NULL
                AND (
                    (
                        R.IDFatoControleContratosItemOrigem IS NOT NULL
                        AND COALESCE(OC.IDFatoControleContratosItemOrigem, I.IDFatoControleContratosItensEuromidia) = R.IDFatoControleContratosItemOrigem
                    )
                    OR
                    (
                        R.IDFatoControleContratos IS NOT NULL
                        AND OC.IDFatoControleContratos = R.IDFatoControleContratos
                        AND OC.CodPonto = R.CodPonto
                        AND OC.IDPainelEuromidia = R.IDPainelEuromidia
                    )
                    OR
                    (
                        R.IDFatoOcupacaoOrigem IS NOT NULL
                        AND OC.IDFatoOcupacaoPaineisEuromidia = R.IDFatoOcupacaoOrigem
                    )
                )
            ORDER BY
                CASE
                    WHEN R.IDFatoControleContratosItemOrigem IS NOT NULL
                         AND COALESCE(OC.IDFatoControleContratosItemOrigem, I.IDFatoControleContratosItensEuromidia) = R.IDFatoControleContratosItemOrigem
                    THEN 0
                    WHEN R.IDFatoOcupacaoOrigem IS NOT NULL
                         AND OC.IDFatoOcupacaoPaineisEuromidia = R.IDFatoOcupacaoOrigem
                    THEN 1
                    ELSE 2
                END,
                OC.IDFatoOcupacaoPaineisEuromidia DESC
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
                    MarcaContrato.IDFatoOcupacaoPaineisEuromidia IS NOT NULL
                    AND ISNULL(R.IDFatoOcupacaoOrigem, 0) <> MarcaContrato.IDFatoOcupacaoPaineisEuromidia
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
            or conf.get("IDFatoControleContratosEuromidia")
        )

        id_usuario = _normalizar_int_ou_none(
            conf.get("id_usuario")
            or conf.get("id_usuario_logado")
            or conf.get("IDUsuario")
        ) or USUARIO_SISTEMA_PADRAO

        modo_processamento = str(conf.get("modo_processamento") or "").strip().lower()
        processar_todos_elegiveis = _normalizar_bool_conf(conf.get("processar_todos_elegiveis"))
        ids_ocupacao_origem = _normalizar_lista_ids_int_conf(
            conf.get("ids_ocupacao_origem")
            or conf.get("ids_ocupacao")
            or conf.get("ids_ocupacoes")
            or conf.get("ids_ocupacao_origem_csv")
            or conf.get("id_ocupacao_origem")
            or conf.get("id_fato_ocupacao")
            or conf.get("IDFatoOcupacaoPaineisEuromidia")
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
            "dias_maximos_procurar_encaixe": DIAS_MAXIMOS_PROCURAR_ENCAIXE_TETRIS,
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

        sql_diagnostico = """
        SET NOCOUNT ON;

        ;WITH NumerosBase AS
        (
            SELECT
                ROW_NUMBER() OVER (ORDER BY A.object_id, B.object_id) - 1 AS DiaOffset
            FROM Integracao.sys.all_objects AS A WITH (NOLOCK)
            CROSS JOIN Integracao.sys.all_objects AS B WITH (NOLOCK)
        ),
        Numeros AS
        (
            SELECT DiaOffset
            FROM NumerosBase
            WHERE DiaOffset <= :dias_maximos_procurar_encaixe
        ),
        FaceOrdenada AS
        (
            SELECT
                F.IDDimPaineisEuromidia,
                F.CodPonto,
                F.CodFace,
                ROW_NUMBER() OVER
                (
                    PARTITION BY F.IDDimPaineisEuromidia
                    ORDER BY
                        COALESCE(TRY_CONVERT(INT, F.Face), 2147483647),
                        F.Face,
                        F.CodFace
                ) AS FaceOrdem
            FROM Integracao.Silver.DimFacesPaineis AS F WITH (NOLOCK)
            WHERE
                F.IDDimPaineisEuromidia IS NOT NULL
                AND F.CodFace IS NOT NULL
        ),
        Paineis AS
        (
            SELECT
                P.IDDimPaineisEuromidia,
                P.CodPonto,
                P.Tipo,
                CASE
                    WHEN UPPER(LTRIM(RTRIM(ISNULL(P.Tipo, '')))) LIKE '%DIGITAL%' THEN 1
                    ELSE 0
                END AS BitDigital,
                CASE
                    WHEN TRY_CONVERT(INT, P.QuantidadeFaces) IS NOT NULL
                         AND TRY_CONVERT(INT, P.QuantidadeFaces) > 0
                    THEN TRY_CONVERT(INT, P.QuantidadeFaces)
                    ELSE NULL
                END AS QuantidadeFacesCalculada
            FROM Integracao.Silver.DimPaineisEuromidia AS P WITH (NOLOCK)
            OUTER APPLY
            (
                SELECT COUNT(1) AS QtdeFacesDim
                FROM Integracao.Silver.DimFacesPaineis AS F WITH (NOLOCK)
                WHERE F.IDDimPaineisEuromidia = P.IDDimPaineisEuromidia
            ) AS FC
        ),
        OcupacoesOrigem AS
        (
            SELECT
                O.IDFatoOcupacaoPaineisEuromidia AS IDFatoOcupacaoOrigem,
                COALESCE(O.IDFatoControleContratos, I.IDFatoControleContratoEuromidia) AS IDFatoControleContratos,
                O.CodPonto,
                O.CodFace,
                PF.IDDimPaineisEuromidia AS IDPainelEuromidia,
                PF.FaceOrdem AS FaceOrdemOrigem,
                CAST(COALESCE(I.DataInicioPrevisto, O.DataInicio) AS DATE) AS DataInicio,
                CAST(COALESCE(I.DataTerminoPrevisto, I.DataFimEfetiva, I.DataCancelamento, O.DataFim) AS DATE) AS DataFim,
                O.SpanQtd,
                O.Cota,
                COALESCE(
                    O.IDFatoControleContratosItemOrigem,
                    I.IDFatoControleContratosItensEuromidia
                ) AS IDFatoControleContratosItemOrigem,
                DATEDIFF(
                    MONTH,
                    CAST(COALESCE(I.DataInicioPrevisto, O.DataInicio) AS DATE),
                    DATEADD(DAY, 1, CAST(COALESCE(I.DataTerminoPrevisto, I.DataFimEfetiva, I.DataCancelamento, O.DataFim) AS DATE))
                ) AS QuantidadeMesesContrato,
                DATEADD(DAY, 1, CAST(COALESCE(I.DataTerminoPrevisto, I.DataFimEfetiva, I.DataCancelamento, O.DataFim) AS DATE)) AS DataMinimaReserva
            FROM Integracao.Silver.FatoOcupacaoPaineisEuromidia AS O WITH (NOLOCK)
            OUTER APPLY
            (
                SELECT TOP (1)
                    I2.IDFatoControleContratosItensEuromidia,
                    I2.IDFatoControleContratoEuromidia,
                    I2.DataInicioPrevisto,
                    I2.DataTerminoPrevisto,
                    I2.DataFimEfetiva,
                    I2.DataCancelamento,
                    I2.MarcaExibida
                FROM Integracao.Silver.FatoControleContratosItensEuromidia AS I2 WITH (NOLOCK)
                WHERE
                    I2.CodPonto = O.CodPonto
                    AND I2.CodFace = O.CodFace
                    AND (:id_contrato IS NULL OR I2.IDFatoControleContratoEuromidia = :id_contrato)
                    AND (
                        O.IDFatoControleContratos IS NULL
                        OR I2.IDFatoControleContratoEuromidia = O.IDFatoControleContratos
                    )
                    AND CAST(I2.DataInicioPrevisto AS DATE) <= CAST(O.DataFim AS DATE)
                    AND CAST(COALESCE(I2.DataTerminoPrevisto, I2.DataFimEfetiva, I2.DataCancelamento) AS DATE) >= CAST(O.DataInicio AS DATE)
                ORDER BY
                    I2.IDFatoControleContratosItensEuromidia DESC
            ) AS I
            OUTER APPLY
            (
                /*
                   Regra exata, sem fallback:
                   a ocupação só é válida para preferência se o IDPainelEuromidia gravado
                   existir e o CodFace pertencer exatamente a esse mesmo painel na DimFacesPaineis.
                   Se não bater, a linha fica sem painel/face válido e não cria reserva.
                */
                SELECT TOP (1)
                    FO.IDDimPaineisEuromidia,
                    FO.FaceOrdem
                FROM FaceOrdenada AS FO
                WHERE
                    O.IDPainelEuromidia IS NOT NULL
                    AND FO.IDDimPaineisEuromidia = O.IDPainelEuromidia
                    AND FO.CodPonto = O.CodPonto
                    AND FO.CodFace = O.CodFace
                ORDER BY
                    FO.FaceOrdem
            ) AS PF
            WHERE
                (:id_contrato IS NULL OR COALESCE(O.IDFatoControleContratos, I.IDFatoControleContratoEuromidia) = :id_contrato)
                AND (
                    :ids_ocupacao_origem_csv IS NULL
                    OR EXISTS
                    (
                        SELECT 1
                        FROM STRING_SPLIT(:ids_ocupacao_origem_csv, ',') AS ids_occ
                        WHERE TRY_CONVERT(int, ids_occ.value) = O.IDFatoOcupacaoPaineisEuromidia
                    )
                )
                AND COALESCE(O.IDFatoControleContratos, I.IDFatoControleContratoEuromidia) IS NOT NULL
                AND O.CodPonto IS NOT NULL
                AND O.CodFace IS NOT NULL
                AND O.DataInicio IS NOT NULL
                AND O.DataFim IS NOT NULL
                AND O.CanceladoEm IS NULL
                AND UPPER(LTRIM(RTRIM(ISNULL(O.Status, '')))) <> UPPER(LTRIM(RTRIM(:status_cancelado)))
                AND (
                    UPPER(LTRIM(RTRIM(ISNULL(O.Origem, '')))) = UPPER(LTRIM(RTRIM(:origem_contrato)))
                    OR
                    (
                        /*
                           Segurança operacional:
                           se a aprovação já vinculou contrato/item, mas a linha ainda está como OCUPACAO
                           por legado/ordem de commit, a DAG pode usar a linha como origem para criar RESERVA.
                           A DAG NÃO cria ocupação; apenas evita perder a preferência por causa desse atraso de origem.
                        */
                        UPPER(LTRIM(RTRIM(ISNULL(O.Origem, '')))) = 'OCUPACAO'
                        AND ISNULL(O.TipoReserva, 0) = 0
                        AND COALESCE(O.IDFatoControleContratos, I.IDFatoControleContratoEuromidia) IS NOT NULL
                    )
                )
        ),
        Elegiveis AS
        (
            SELECT
                O.*,
                PA.Tipo AS TipoPainel,
                PA.BitDigital,
                PA.QuantidadeFacesCalculada,
                CASE
                    WHEN PA.BitDigital = 1 THEN
                        CASE
                            WHEN ISNULL(O.SpanQtd, 0) > 0 THEN O.SpanQtd
                            WHEN TRY_CONVERT(INT, O.Cota) = 1080 THEN 2
                            ELSE 1
                        END
                    ELSE 1
                END AS SpanQtdCalculado
            FROM OcupacoesOrigem AS O
            LEFT JOIN Paineis AS PA
                ON PA.IDDimPaineisEuromidia = O.IDPainelEuromidia
            WHERE
                O.DataFim >= DATEADD(DAY, -1, DATEADD(MONTH, :meses_minimos, O.DataInicio))
                AND O.QuantidadeMesesContrato >= :meses_minimos
        ),
        ElegiveisValidos AS
        (
            SELECT *
            FROM Elegiveis
            WHERE
                IDPainelEuromidia IS NOT NULL
                AND ISNULL(QuantidadeFacesCalculada, 0) > 0
                AND ISNULL(SpanQtdCalculado, 0) > 0
                AND SpanQtdCalculado <= QuantidadeFacesCalculada
        ),
        FacesCandidatas AS
        (
            /*
               Correção importante:
               DimFacesPaineis valida que o CodFace pertence ao painel, mas ela NÃO é
               a grade de slots digitais. Em painel digital, os slots 1..QuantidadeFaces
               vêm de DimPaineisEuromidia.QuantidadeFaces e são gravados em LoopInicio/LoopFim.

               Antes o código exigia uma linha física na DimFacesPaineis para cada slot
               virtual. Se o painel 118AD tem QuantidadeFaces=16, mas só uma linha de face
               cadastrada, a DAG não encontrava FO_FINAL para 1080 e não criava a reserva.

               Regra correta:
               - CodFace continua sendo o CodFace da ocupação/painel validado;
               - para digital, testa os slots virtuais 1..QuantidadeFaces;
               - para não digital, usa um único slot lógico.
            */
            SELECT
                E.IDFatoOcupacaoOrigem,
                E.CodFace AS CodFaceCandidata,
                CASE
                    WHEN E.BitDigital = 1 THEN S.SlotOrdem
                    ELSE ISNULL(E.FaceOrdemOrigem, 1)
                END AS FaceInicioOrdem
            FROM ElegiveisValidos AS E
            INNER JOIN
            (
                SELECT DiaOffset + 1 AS SlotOrdem
                FROM NumerosBase
                WHERE DiaOffset BETWEEN 0 AND 3650
            ) AS S
                ON S.SlotOrdem <=
                   CASE
                       WHEN E.BitDigital = 1 THEN E.QuantidadeFacesCalculada - E.SpanQtdCalculado + 1
                       ELSE 1
                   END
            WHERE
                E.CodFace IS NOT NULL
                AND E.FaceOrdemOrigem IS NOT NULL
        ),
        CandidatosDataFace AS
        (
            SELECT
                E.*,
                FC.CodFaceCandidata,
                FC.FaceInicioOrdem,
                DATEADD(DAY, N.DiaOffset, E.DataMinimaReserva) AS DataInicioReservaEncaixada,
                DATEADD(
                    DAY,
                    -1,
                    DATEADD(MONTH, E.QuantidadeMesesContrato, DATEADD(DAY, N.DiaOffset, E.DataMinimaReserva))
                ) AS DataFimReservaEncaixada
            FROM ElegiveisValidos AS E
            INNER JOIN FacesCandidatas AS FC
                ON FC.IDFatoOcupacaoOrigem = E.IDFatoOcupacaoOrigem
            INNER JOIN Numeros AS N
                ON 1 = 1
        ),
        CandidatosSemConflito AS
        (
            SELECT C.*
            FROM CandidatosDataFace AS C
            WHERE NOT EXISTS
            (
                SELECT 1
                FROM Integracao.Silver.FatoOcupacaoPaineisEuromidia AS OC WITH (NOLOCK)
                OUTER APPLY
                (
                    /*
                       Regra exata, sem fallback:
                       a ocupação/reserva existente só entra como conflito se o IDPainelEuromidia
                       dela bater exatamente com a DimFacesPaineis e com o mesmo painel candidato.
                       Se a ocupação existente estiver sem painel/face válido, ela não é usada
                       para inventar correspondência por CodPonto.
                    */
                    SELECT TOP (1)
                        FO.IDDimPaineisEuromidia,
                        FO.FaceOrdem
                    FROM FaceOrdenada AS FO
                    WHERE
                        OC.IDPainelEuromidia IS NOT NULL
                        AND FO.IDDimPaineisEuromidia = OC.IDPainelEuromidia
                        AND FO.CodPonto = OC.CodPonto
                        AND FO.CodFace = OC.CodFace
                    ORDER BY
                        FO.FaceOrdem
                ) AS FOC
                CROSS APPLY
                (
                    SELECT
                        CASE
                            WHEN C.BitDigital = 1
                                 AND TRY_CONVERT(INT, OC.LoopInicio) IS NOT NULL
                                 AND TRY_CONVERT(INT, OC.LoopInicio) > 0
                            THEN TRY_CONVERT(INT, OC.LoopInicio)
                            ELSE FOC.FaceOrdem
                        END AS FaceInicioOcupada,
                        CASE
                            WHEN C.BitDigital = 1 THEN
                                CASE
                                    WHEN TRY_CONVERT(INT, OC.LoopInicio) IS NOT NULL
                                         AND TRY_CONVERT(INT, OC.LoopFim) IS NOT NULL
                                         AND TRY_CONVERT(INT, OC.LoopInicio) > 0
                                         AND TRY_CONVERT(INT, OC.LoopFim) >= TRY_CONVERT(INT, OC.LoopInicio)
                                    THEN TRY_CONVERT(INT, OC.LoopFim) - TRY_CONVERT(INT, OC.LoopInicio) + 1
                                    WHEN ISNULL(OC.SpanQtd, 0) > 0 THEN OC.SpanQtd
                                    WHEN TRY_CONVERT(INT, OC.Cota) = 1080 THEN 2
                                    ELSE 1
                                END
                            ELSE 1
                        END AS SpanQtdOcupada
                ) AS SO
                WHERE
                    OC.IDFatoOcupacaoPaineisEuromidia <> C.IDFatoOcupacaoOrigem
                    AND FOC.IDDimPaineisEuromidia = C.IDPainelEuromidia
                    AND OC.CodFace IS NOT NULL
                    AND OC.DataInicio IS NOT NULL
                    AND OC.DataFim IS NOT NULL
                    AND OC.CanceladoEm IS NULL
                    AND UPPER(LTRIM(RTRIM(ISNULL(OC.Status, '')))) <> UPPER(LTRIM(RTRIM(:status_cancelado)))
                    AND CAST(OC.DataInicio AS DATE) <= C.DataFimReservaEncaixada
                    AND CAST(OC.DataFim AS DATE) >= C.DataInicioReservaEncaixada
                    AND SO.FaceInicioOcupada <= C.FaceInicioOrdem + C.SpanQtdCalculado - 1
                    AND SO.FaceInicioOcupada + SO.SpanQtdOcupada - 1 >= C.FaceInicioOrdem
            )
        ),
        PrimeiroEncaixe AS
        (
            SELECT
                C.*,
                ROW_NUMBER() OVER
                (
                    PARTITION BY C.IDFatoOcupacaoOrigem
                    ORDER BY
                        C.DataInicioReservaEncaixada,
                        C.FaceInicioOrdem,
                        C.CodFaceCandidata
                ) AS OrdemEncaixe
            FROM CandidatosSemConflito AS C
        ),
        ElegiveisComStatusReserva AS
        (
            SELECT
                E.IDFatoOcupacaoOrigem,
                E.IDPainelEuromidia,
                E.QuantidadeFacesCalculada,
                E.SpanQtdCalculado,
                CASE
                    WHEN R.IDFatoOcupacaoPaineisEuromidia IS NULL THEN 0
                    ELSE 1
                END AS JaExisteReserva,
                CASE
                    WHEN PE.IDFatoOcupacaoOrigem IS NULL THEN 0
                    ELSE 1
                END AS TemEncaixeTetris
            FROM Elegiveis AS E
            OUTER APPLY
            (
                SELECT TOP (1)
                    R2.IDFatoOcupacaoPaineisEuromidia
                FROM Integracao.Silver.FatoOcupacaoPaineisEuromidia AS R2 WITH (NOLOCK)
                WHERE
                    (
                        R2.IDFatoOcupacaoOrigem = E.IDFatoOcupacaoOrigem
                        OR
                        (
                            E.IDFatoControleContratosItemOrigem IS NOT NULL
                            AND R2.IDFatoControleContratosItemOrigem = E.IDFatoControleContratosItemOrigem
                        )
                    )
                    AND UPPER(LTRIM(RTRIM(ISNULL(R2.TipoVinculoOrigem, '')))) = UPPER(LTRIM(RTRIM(:tipo_vinculo)))
                    AND UPPER(LTRIM(RTRIM(ISNULL(R2.Origem, '')))) = UPPER(LTRIM(RTRIM(:origem_reserva)))
                ORDER BY
                    R2.IDFatoOcupacaoPaineisEuromidia DESC
            ) AS R
            OUTER APPLY
            (
                SELECT TOP (1)
                    P1.IDFatoOcupacaoOrigem
                FROM PrimeiroEncaixe AS P1
                WHERE
                    P1.IDFatoOcupacaoOrigem = E.IDFatoOcupacaoOrigem
                    AND P1.OrdemEncaixe = 1
            ) AS PE
        )
        SELECT
            COUNT(1) AS ocupacoes_elegiveis,
            COALESCE(SUM(JaExisteReserva), 0) AS reservas_ja_existentes,
            COALESCE(SUM(CASE WHEN JaExisteReserva = 0 THEN 1 ELSE 0 END), 0) AS reservas_pendentes,
            COALESCE(SUM(CASE WHEN IDPainelEuromidia IS NULL THEN 1 ELSE 0 END), 0) AS ocupacoes_sem_painel_face_valido,
            COALESCE(SUM(CASE WHEN IDPainelEuromidia IS NOT NULL AND ISNULL(QuantidadeFacesCalculada, 0) <= 0 THEN 1 ELSE 0 END), 0) AS ocupacoes_sem_quantidade_faces_valida,
            COALESCE(SUM(CASE WHEN IDPainelEuromidia IS NOT NULL AND ISNULL(QuantidadeFacesCalculada, 0) > 0 AND ISNULL(SpanQtdCalculado, 0) <= 0 THEN 1 ELSE 0 END), 0) AS ocupacoes_sem_span_valido,
            COALESCE(SUM(CASE WHEN JaExisteReserva = 0 AND TemEncaixeTetris = 1 THEN 1 ELSE 0 END), 0) AS reservas_pendentes_com_encaixe_tetris,
            COALESCE(SUM(CASE WHEN JaExisteReserva = 0 AND TemEncaixeTetris = 0 THEN 1 ELSE 0 END), 0) AS reservas_pendentes_sem_encaixe_tetris
        FROM ElegiveisComStatusReserva;
        """


        sql_insert = """
        SET NOCOUNT ON;

        ;WITH NumerosBase AS
        (
            SELECT
                ROW_NUMBER() OVER (ORDER BY A.object_id, B.object_id) - 1 AS DiaOffset
            FROM Integracao.sys.all_objects AS A WITH (NOLOCK)
            CROSS JOIN Integracao.sys.all_objects AS B WITH (NOLOCK)
        ),
        Numeros AS
        (
            SELECT DiaOffset
            FROM NumerosBase
            WHERE DiaOffset <= :dias_maximos_procurar_encaixe
        ),
        FaceOrdenada AS
        (
            SELECT
                F.IDDimPaineisEuromidia,
                F.CodPonto,
                F.CodFace,
                ROW_NUMBER() OVER
                (
                    PARTITION BY F.IDDimPaineisEuromidia
                    ORDER BY
                        COALESCE(TRY_CONVERT(INT, F.Face), 2147483647),
                        F.Face,
                        F.CodFace
                ) AS FaceOrdem
            FROM Integracao.Silver.DimFacesPaineis AS F WITH (NOLOCK)
            WHERE
                F.IDDimPaineisEuromidia IS NOT NULL
                AND F.CodFace IS NOT NULL
        ),
        Paineis AS
        (
            SELECT
                P.IDDimPaineisEuromidia,
                P.CodPonto,
                P.Tipo,
                CASE
                    WHEN UPPER(LTRIM(RTRIM(ISNULL(P.Tipo, '')))) LIKE '%DIGITAL%' THEN 1
                    ELSE 0
                END AS BitDigital,
                CASE
                    WHEN TRY_CONVERT(INT, P.QuantidadeFaces) IS NOT NULL
                         AND TRY_CONVERT(INT, P.QuantidadeFaces) > 0
                    THEN TRY_CONVERT(INT, P.QuantidadeFaces)
                    ELSE NULL
                END AS QuantidadeFacesCalculada
            FROM Integracao.Silver.DimPaineisEuromidia AS P WITH (NOLOCK)
            OUTER APPLY
            (
                SELECT COUNT(1) AS QtdeFacesDim
                FROM Integracao.Silver.DimFacesPaineis AS F WITH (NOLOCK)
                WHERE F.IDDimPaineisEuromidia = P.IDDimPaineisEuromidia
            ) AS FC
        ),
        OcupacoesOrigem AS
        (
            SELECT
                O.IDFatoOcupacaoPaineisEuromidia AS IDFatoOcupacaoOrigem,
                COALESCE(O.IDFatoControleContratos, I.IDFatoControleContratoEuromidia) AS IDFatoControleContratos,
                O.CodPonto,
                O.CodFace,
                PF.IDDimPaineisEuromidia AS IDPainelEuromidia,
                PF.FaceOrdem AS FaceOrdemOrigem,
                CAST(COALESCE(I.DataInicioPrevisto, O.DataInicio) AS DATE) AS DataInicio,
                CAST(COALESCE(I.DataTerminoPrevisto, I.DataFimEfetiva, I.DataCancelamento, O.DataFim) AS DATE) AS DataFim,
                O.LoopInicio,
                O.LoopFim,
                O.SpanQtd,
                O.Cota,
                COALESCE(
                    NULLIF(LTRIM(RTRIM(I.MarcaExibida)), ''),
                    NULLIF(LTRIM(RTRIM(O.MarcaExibida)), '')
                ) AS MarcaExibida,
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
                    CAST(COALESCE(I.DataInicioPrevisto, O.DataInicio) AS DATE),
                    DATEADD(DAY, 1, CAST(COALESCE(I.DataTerminoPrevisto, I.DataFimEfetiva, I.DataCancelamento, O.DataFim) AS DATE))
                ) AS QuantidadeMesesContrato,
                DATEADD(DAY, 1, CAST(COALESCE(I.DataTerminoPrevisto, I.DataFimEfetiva, I.DataCancelamento, O.DataFim) AS DATE)) AS DataMinimaReserva
            FROM Integracao.Silver.FatoOcupacaoPaineisEuromidia AS O WITH (UPDLOCK, HOLDLOCK)
            OUTER APPLY
            (
                SELECT TOP (1)
                    I2.IDFatoControleContratosItensEuromidia,
                    I2.IDFatoControleContratoEuromidia,
                    I2.DataInicioPrevisto,
                    I2.DataTerminoPrevisto,
                    I2.DataFimEfetiva,
                    I2.DataCancelamento,
                    I2.MarcaExibida
                FROM Integracao.Silver.FatoControleContratosItensEuromidia AS I2 WITH (NOLOCK)
                WHERE
                    I2.CodPonto = O.CodPonto
                    AND I2.CodFace = O.CodFace
                    AND (:id_contrato IS NULL OR I2.IDFatoControleContratoEuromidia = :id_contrato)
                    AND (
                        O.IDFatoControleContratos IS NULL
                        OR I2.IDFatoControleContratoEuromidia = O.IDFatoControleContratos
                    )
                    AND CAST(I2.DataInicioPrevisto AS DATE) <= CAST(O.DataFim AS DATE)
                    AND CAST(COALESCE(I2.DataTerminoPrevisto, I2.DataFimEfetiva, I2.DataCancelamento) AS DATE) >= CAST(O.DataInicio AS DATE)
                ORDER BY
                    I2.IDFatoControleContratosItensEuromidia DESC
            ) AS I
            OUTER APPLY
            (
                /*
                   Regra exata, sem fallback:
                   a ocupação só é válida para preferência se o IDPainelEuromidia gravado
                   existir e o CodFace pertencer exatamente a esse mesmo painel na DimFacesPaineis.
                   Se não bater, a linha fica sem painel/face válido e não cria reserva.
                */
                SELECT TOP (1)
                    FO.IDDimPaineisEuromidia,
                    FO.FaceOrdem
                FROM FaceOrdenada AS FO
                WHERE
                    O.IDPainelEuromidia IS NOT NULL
                    AND FO.IDDimPaineisEuromidia = O.IDPainelEuromidia
                    AND FO.CodPonto = O.CodPonto
                    AND FO.CodFace = O.CodFace
                ORDER BY
                    FO.FaceOrdem
            ) AS PF
            WHERE
                (:id_contrato IS NULL OR COALESCE(O.IDFatoControleContratos, I.IDFatoControleContratoEuromidia) = :id_contrato)
                AND (
                    :ids_ocupacao_origem_csv IS NULL
                    OR EXISTS
                    (
                        SELECT 1
                        FROM STRING_SPLIT(:ids_ocupacao_origem_csv, ',') AS ids_occ
                        WHERE TRY_CONVERT(int, ids_occ.value) = O.IDFatoOcupacaoPaineisEuromidia
                    )
                )
                AND COALESCE(O.IDFatoControleContratos, I.IDFatoControleContratoEuromidia) IS NOT NULL
                AND O.CodPonto IS NOT NULL
                AND O.CodFace IS NOT NULL
                AND O.DataInicio IS NOT NULL
                AND O.DataFim IS NOT NULL
                AND O.CanceladoEm IS NULL
                AND UPPER(LTRIM(RTRIM(ISNULL(O.Status, '')))) <> UPPER(LTRIM(RTRIM(:status_cancelado)))
                AND (
                    UPPER(LTRIM(RTRIM(ISNULL(O.Origem, '')))) = UPPER(LTRIM(RTRIM(:origem_contrato)))
                    OR
                    (
                        /*
                           Segurança operacional:
                           se a aprovação já vinculou contrato/item, mas a linha ainda está como OCUPACAO
                           por legado/ordem de commit, a DAG pode usar a linha como origem para criar RESERVA.
                           A DAG NÃO cria ocupação; apenas evita perder a preferência por causa desse atraso de origem.
                        */
                        UPPER(LTRIM(RTRIM(ISNULL(O.Origem, '')))) = 'OCUPACAO'
                        AND ISNULL(O.TipoReserva, 0) = 0
                        AND COALESCE(O.IDFatoControleContratos, I.IDFatoControleContratoEuromidia) IS NOT NULL
                    )
                )
                AND CAST(COALESCE(I.DataTerminoPrevisto, I.DataFimEfetiva, I.DataCancelamento, O.DataFim) AS DATE) >= DATEADD(
                    DAY,
                    -1,
                    DATEADD(MONTH, :meses_minimos, CAST(COALESCE(I.DataInicioPrevisto, O.DataInicio) AS DATE))
                )
        ),
        Elegiveis AS
        (
            SELECT
                O.*,
                PA.Tipo AS TipoPainel,
                PA.BitDigital,
                PA.QuantidadeFacesCalculada,
                CASE
                    WHEN PA.BitDigital = 1 THEN
                        CASE
                            WHEN ISNULL(O.SpanQtd, 0) > 0 THEN O.SpanQtd
                            WHEN TRY_CONVERT(INT, O.Cota) = 1080 THEN 2
                            ELSE 1
                        END
                    ELSE 1
                END AS SpanQtdCalculado
            FROM OcupacoesOrigem AS O
            LEFT JOIN Paineis AS PA
                ON PA.IDDimPaineisEuromidia = O.IDPainelEuromidia
            WHERE
                O.QuantidadeMesesContrato >= :meses_minimos
        ),
        ElegiveisValidos AS
        (
            SELECT *
            FROM Elegiveis
            WHERE
                IDPainelEuromidia IS NOT NULL
                AND ISNULL(QuantidadeFacesCalculada, 0) > 0
                AND ISNULL(SpanQtdCalculado, 0) > 0
                AND SpanQtdCalculado <= QuantidadeFacesCalculada
        ),
        FacesCandidatas AS
        (
            /*
               Correção importante:
               DimFacesPaineis valida que o CodFace pertence ao painel, mas ela NÃO é
               a grade de slots digitais. Em painel digital, os slots 1..QuantidadeFaces
               vêm de DimPaineisEuromidia.QuantidadeFaces e são gravados em LoopInicio/LoopFim.

               Antes o código exigia uma linha física na DimFacesPaineis para cada slot
               virtual. Se o painel 118AD tem QuantidadeFaces=16, mas só uma linha de face
               cadastrada, a DAG não encontrava FO_FINAL para 1080 e não criava a reserva.

               Regra correta:
               - CodFace continua sendo o CodFace da ocupação/painel validado;
               - para digital, testa os slots virtuais 1..QuantidadeFaces;
               - para não digital, usa um único slot lógico.
            */
            SELECT
                E.IDFatoOcupacaoOrigem,
                E.CodFace AS CodFaceCandidata,
                CASE
                    WHEN E.BitDigital = 1 THEN S.SlotOrdem
                    ELSE ISNULL(E.FaceOrdemOrigem, 1)
                END AS FaceInicioOrdem
            FROM ElegiveisValidos AS E
            INNER JOIN
            (
                SELECT DiaOffset + 1 AS SlotOrdem
                FROM NumerosBase
                WHERE DiaOffset BETWEEN 0 AND 3650
            ) AS S
                ON S.SlotOrdem <=
                   CASE
                       WHEN E.BitDigital = 1 THEN E.QuantidadeFacesCalculada - E.SpanQtdCalculado + 1
                       ELSE 1
                   END
            WHERE
                E.CodFace IS NOT NULL
                AND E.FaceOrdemOrigem IS NOT NULL
        ),
        CandidatosDataFace AS
        (
            SELECT
                E.*,
                FC.CodFaceCandidata,
                FC.FaceInicioOrdem,
                DATEADD(DAY, N.DiaOffset, E.DataMinimaReserva) AS DataInicioReservaEncaixada,
                DATEADD(
                    DAY,
                    -1,
                    DATEADD(MONTH, E.QuantidadeMesesContrato, DATEADD(DAY, N.DiaOffset, E.DataMinimaReserva))
                ) AS DataFimReservaEncaixada
            FROM ElegiveisValidos AS E
            INNER JOIN FacesCandidatas AS FC
                ON FC.IDFatoOcupacaoOrigem = E.IDFatoOcupacaoOrigem
            INNER JOIN Numeros AS N
                ON 1 = 1
        ),
        CandidatosSemConflito AS
        (
            SELECT C.*
            FROM CandidatosDataFace AS C
            WHERE NOT EXISTS
            (
                SELECT 1
                FROM Integracao.Silver.FatoOcupacaoPaineisEuromidia AS OC WITH (UPDLOCK, HOLDLOCK)
                OUTER APPLY
                (
                    /*
                       Regra exata, sem fallback:
                       a ocupação/reserva existente só entra como conflito se o IDPainelEuromidia
                       dela bater exatamente com a DimFacesPaineis e com o mesmo painel candidato.
                       Se a ocupação existente estiver sem painel/face válido, ela não é usada
                       para inventar correspondência por CodPonto.
                    */
                    SELECT TOP (1)
                        FO.IDDimPaineisEuromidia,
                        FO.FaceOrdem
                    FROM FaceOrdenada AS FO
                    WHERE
                        OC.IDPainelEuromidia IS NOT NULL
                        AND FO.IDDimPaineisEuromidia = OC.IDPainelEuromidia
                        AND FO.CodPonto = OC.CodPonto
                        AND FO.CodFace = OC.CodFace
                    ORDER BY
                        FO.FaceOrdem
                ) AS FOC
                CROSS APPLY
                (
                    SELECT
                        CASE
                            WHEN C.BitDigital = 1
                                 AND TRY_CONVERT(INT, OC.LoopInicio) IS NOT NULL
                                 AND TRY_CONVERT(INT, OC.LoopInicio) > 0
                            THEN TRY_CONVERT(INT, OC.LoopInicio)
                            ELSE FOC.FaceOrdem
                        END AS FaceInicioOcupada,
                        CASE
                            WHEN C.BitDigital = 1 THEN
                                CASE
                                    WHEN TRY_CONVERT(INT, OC.LoopInicio) IS NOT NULL
                                         AND TRY_CONVERT(INT, OC.LoopFim) IS NOT NULL
                                         AND TRY_CONVERT(INT, OC.LoopInicio) > 0
                                         AND TRY_CONVERT(INT, OC.LoopFim) >= TRY_CONVERT(INT, OC.LoopInicio)
                                    THEN TRY_CONVERT(INT, OC.LoopFim) - TRY_CONVERT(INT, OC.LoopInicio) + 1
                                    WHEN ISNULL(OC.SpanQtd, 0) > 0 THEN OC.SpanQtd
                                    WHEN TRY_CONVERT(INT, OC.Cota) = 1080 THEN 2
                                    ELSE 1
                                END
                            ELSE 1
                        END AS SpanQtdOcupada
                ) AS SO
                WHERE
                    OC.IDFatoOcupacaoPaineisEuromidia <> C.IDFatoOcupacaoOrigem
                    AND FOC.IDDimPaineisEuromidia = C.IDPainelEuromidia
                    AND OC.CodFace IS NOT NULL
                    AND OC.DataInicio IS NOT NULL
                    AND OC.DataFim IS NOT NULL
                    AND OC.CanceladoEm IS NULL
                    AND UPPER(LTRIM(RTRIM(ISNULL(OC.Status, '')))) <> UPPER(LTRIM(RTRIM(:status_cancelado)))
                    AND CAST(OC.DataInicio AS DATE) <= C.DataFimReservaEncaixada
                    AND CAST(OC.DataFim AS DATE) >= C.DataInicioReservaEncaixada
                    AND SO.FaceInicioOcupada <= C.FaceInicioOrdem + C.SpanQtdCalculado - 1
                    AND SO.FaceInicioOcupada + SO.SpanQtdOcupada - 1 >= C.FaceInicioOrdem
            )
        ),
        PrimeiroEncaixe AS
        (
            SELECT
                C.*,
                ROW_NUMBER() OVER
                (
                    PARTITION BY C.IDFatoOcupacaoOrigem
                    ORDER BY
                        C.DataInicioReservaEncaixada,
                        C.FaceInicioOrdem,
                        C.CodFaceCandidata
                ) AS OrdemEncaixe
            FROM CandidatosSemConflito AS C
        ),
        ReservasCalculadas AS
        (
            SELECT
                PE.IDFatoOcupacaoOrigem,
                PE.IDFatoControleContratos,
                PE.IDFatoControleContratosItemOrigem,
                PE.CodPonto,
                PE.CodFace AS CodFaceOrigem,
                PE.CodFaceCandidata AS CodFaceEncaixada,
                PE.FaceInicioOrdem,
                PE.IDPainelEuromidia,
                PE.LoopInicio,
                PE.LoopFim,
                PE.SpanQtdCalculado,
                PE.Cota,
                PE.MarcaExibida,
                PE.Vendedor,
                PE.IDVendedor,
                PE.IDCliente,
                PE.NumeroContrato,
                PE.NumeroPrevia,
                PE.DataInicio AS DataInicioOrigem,
                PE.DataFim AS DataFimOrigem,
                PE.DataMinimaReserva,
                PE.DataInicioReservaEncaixada,
                PE.DataFimReservaEncaixada,
                CONCAT(
                    'PREFRENOV-',
                    LEFT(
                        CONVERT(
                            VARCHAR(64),
                            HASHBYTES(
                                'SHA2_256',
                                CONCAT(
                                    :tipo_vinculo, '|',
                                    CAST(PE.IDFatoOcupacaoOrigem AS VARCHAR(30)), '|',
                                    CAST(ISNULL(PE.IDFatoControleContratosItemOrigem, 0) AS VARCHAR(30)), '|',
                                    CAST(PE.IDFatoControleContratos AS VARCHAR(30)), '|',
                                    CAST(PE.CodPonto AS VARCHAR(30)), '|',
                                    CAST(PE.CodFaceCandidata AS VARCHAR(100)), '|',
                                    CAST(PE.FaceInicioOrdem AS VARCHAR(30)), '|',
                                    CAST(PE.FaceInicioOrdem + PE.SpanQtdCalculado - 1 AS VARCHAR(30)), '|',
                                    CONVERT(VARCHAR(10), PE.DataInicioReservaEncaixada, 120), '|',
                                    CONVERT(VARCHAR(10), PE.DataFimReservaEncaixada, 120)
                                )
                            ),
                            2
                        ),
                        44
                    )
                ) AS ReferenciaPreferencia
            FROM PrimeiroEncaixe AS PE
            WHERE PE.OrdemEncaixe = 1
        ),
        ReservasCalculadasDeduplicadas AS
        (
            SELECT *
            FROM
            (
                SELECT
                    R.*,
                    ROW_NUMBER() OVER
                    (
                        PARTITION BY
                            COALESCE(
                                CAST(R.IDFatoControleContratosItemOrigem AS VARCHAR(30)),
                                CONCAT('OCUPACAO:', CAST(R.IDFatoOcupacaoOrigem AS VARCHAR(30)))
                            )
                        ORDER BY
                            R.DataInicioReservaEncaixada,
                            R.CodFaceEncaixada,
                            R.IDFatoOcupacaoOrigem
                    ) AS OrdemChaveLogicaReserva
                FROM ReservasCalculadas AS R
            ) AS D
            WHERE D.OrdemChaveLogicaReserva = 1
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
            TipoVinculoOrigem,
            TipoReserva
        )
        SELECT
            SYSDATETIME() AS DataAtualizacao,
            R.ReferenciaPreferencia AS Referencia,
            R.CodPonto,
            R.CodFaceEncaixada AS CodFace,
            R.IDPainelEuromidia,
            N'RESERVA' AS Origem,
            N'RESERVADO' AS Status,
            R.DataInicioReservaEncaixada AS DataInicio,
            R.DataFimReservaEncaixada AS DataFim,
            R.FaceInicioOrdem AS LoopInicio,
            R.FaceInicioOrdem + R.SpanQtdCalculado - 1 AS LoopFim,
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
                'Reserva automática criada por preferência de renovação de contrato com encaixe Tetris. ',
                'Ocupação origem: ',
                CAST(R.IDFatoOcupacaoOrigem AS VARCHAR(30)),
                '. Período origem: ',
                CONVERT(VARCHAR(10), R.DataInicioOrigem, 103),
                ' até ',
                CONVERT(VARCHAR(10), R.DataFimOrigem, 103),
                '. Data mínima da reserva: ',
                CONVERT(VARCHAR(10), R.DataMinimaReserva, 103),
                '. Data encaixada: ',
                CONVERT(VARCHAR(10), R.DataInicioReservaEncaixada, 103),
                ' até ',
                CONVERT(VARCHAR(10), R.DataFimReservaEncaixada, 103),
                '. Face origem: ',
                ISNULL(CAST(R.CodFaceOrigem AS VARCHAR(100)), ''),
                '. Face encaixada: ',
                ISNULL(CAST(R.CodFaceEncaixada AS VARCHAR(100)), ''),
                '. Slot encaixado: ',
                CAST(R.FaceInicioOrdem AS VARCHAR(30)),
                ' até ',
                CAST(R.FaceInicioOrdem + R.SpanQtdCalculado - 1 AS VARCHAR(30)),
                '.'
            ), 1000) AS TextoOriginal,
            SYSDATETIME() AS CriadoEm,
            :id_usuario AS CriadoPorIDUsuario,
            NULL AS ExpiraEm,
            NULL AS CanceladoEm,
            NULL AS CanceladoPorIDUsuario,
            LEFT(CONCAT(
                'Preferência de renovação gerada automaticamente com regra Tetris. ',
                'TipoVinculoOrigem=',
                :tipo_vinculo,
                '. TipoReserva=',
                CAST(:tipo_reserva_preferencia AS VARCHAR(10)),
                '. ',
                :marcador_execucao
            ), 500) AS Observacao,
            DATEDIFF(DAY, R.DataInicioReservaEncaixada, R.DataFimReservaEncaixada) + 1 AS Dias,
            NULL AS ReservaOrdemPrioridade,
            R.IDFatoOcupacaoOrigem,
            R.IDFatoControleContratosItemOrigem,
            :tipo_vinculo AS TipoVinculoOrigem,
            :tipo_reserva_preferencia AS TipoReserva
        FROM ReservasCalculadasDeduplicadas AS R
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
                (
                    EXISTENTE.IDFatoOcupacaoOrigem = R.IDFatoOcupacaoOrigem
                    OR
                    (
                        R.IDFatoControleContratosItemOrigem IS NOT NULL
                        AND EXISTENTE.IDFatoControleContratosItemOrigem = R.IDFatoControleContratosItemOrigem
                    )
                )
                AND UPPER(LTRIM(RTRIM(ISNULL(EXISTENTE.TipoVinculoOrigem, '')))) = UPPER(LTRIM(RTRIM(:tipo_vinculo)))
                AND UPPER(LTRIM(RTRIM(ISNULL(EXISTENTE.Origem, '')))) = UPPER(LTRIM(RTRIM(:origem_reserva)))
        );
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
        FROM Integracao.Silver.FatoOcupacaoPaineisEuromidia AS R
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

        ;WITH OcupacoesOrigem AS
        (
            SELECT
                O.IDFatoOcupacaoPaineisEuromidia AS IDFatoOcupacaoOrigem,
                COALESCE(O.IDFatoControleContratos, I.IDFatoControleContratoEuromidia) AS IDFatoControleContratos,
                CAST(COALESCE(I.DataInicioPrevisto, O.DataInicio) AS DATE) AS DataInicio,
                CAST(COALESCE(I.DataTerminoPrevisto, I.DataFimEfetiva, I.DataCancelamento, O.DataFim) AS DATE) AS DataFim,
                COALESCE(
                    O.IDFatoControleContratosItemOrigem,
                    I.IDFatoControleContratosItensEuromidia
                ) AS IDFatoControleContratosItemOrigem,
                DATEDIFF(
                    MONTH,
                    CAST(COALESCE(I.DataInicioPrevisto, O.DataInicio) AS DATE),
                    DATEADD(DAY, 1, CAST(COALESCE(I.DataTerminoPrevisto, I.DataFimEfetiva, I.DataCancelamento, O.DataFim) AS DATE))
                ) AS QuantidadeMesesContrato
            FROM Integracao.Silver.FatoOcupacaoPaineisEuromidia AS O WITH (NOLOCK)
            OUTER APPLY
            (
                SELECT TOP (1)
                    I2.IDFatoControleContratosItensEuromidia,
                    I2.IDFatoControleContratoEuromidia,
                    I2.DataInicioPrevisto,
                    I2.DataTerminoPrevisto,
                    I2.DataFimEfetiva,
                    I2.DataCancelamento,
                    I2.MarcaExibida
                FROM Integracao.Silver.FatoControleContratosItensEuromidia AS I2 WITH (NOLOCK)
                WHERE
                    I2.CodPonto = O.CodPonto
                    AND I2.CodFace = O.CodFace
                    AND (:id_contrato IS NULL OR I2.IDFatoControleContratoEuromidia = :id_contrato)
                    AND (
                        O.IDFatoControleContratos IS NULL
                        OR I2.IDFatoControleContratoEuromidia = O.IDFatoControleContratos
                    )
                    AND CAST(I2.DataInicioPrevisto AS DATE) <= CAST(O.DataFim AS DATE)
                    AND CAST(COALESCE(I2.DataTerminoPrevisto, I2.DataFimEfetiva, I2.DataCancelamento) AS DATE) >= CAST(O.DataInicio AS DATE)
                ORDER BY
                    I2.IDFatoControleContratosItensEuromidia DESC
            ) AS I
            WHERE
                (:id_contrato IS NULL OR COALESCE(O.IDFatoControleContratos, I.IDFatoControleContratoEuromidia) = :id_contrato)
                AND (
                    :ids_ocupacao_origem_csv IS NULL
                    OR EXISTS
                    (
                        SELECT 1
                        FROM STRING_SPLIT(:ids_ocupacao_origem_csv, ',') AS ids_occ
                        WHERE TRY_CONVERT(int, ids_occ.value) = O.IDFatoOcupacaoPaineisEuromidia
                    )
                )
                AND COALESCE(O.IDFatoControleContratos, I.IDFatoControleContratoEuromidia) IS NOT NULL
                AND COALESCE(O.IDFatoControleContratosItemOrigem, I.IDFatoControleContratosItensEuromidia) IS NOT NULL
                AND O.CodPonto IS NOT NULL
                AND O.CodFace IS NOT NULL
                AND O.DataInicio IS NOT NULL
                AND O.DataFim IS NOT NULL
                AND O.CanceladoEm IS NULL
                AND UPPER(LTRIM(RTRIM(ISNULL(O.Status, '')))) <> UPPER(LTRIM(RTRIM(:status_cancelado)))
                AND (
                    UPPER(LTRIM(RTRIM(ISNULL(O.Origem, '')))) = UPPER(LTRIM(RTRIM(:origem_contrato)))
                    OR
                    (
                        /*
                           Segurança operacional:
                           se a aprovação já vinculou contrato/item, mas a linha ainda está como OCUPACAO
                           por legado/ordem de commit, a DAG pode usar a linha como origem para criar RESERVA.
                           A DAG NÃO cria ocupação; apenas evita perder a preferência por causa desse atraso de origem.
                        */
                        UPPER(LTRIM(RTRIM(ISNULL(O.Origem, '')))) = 'OCUPACAO'
                        AND ISNULL(O.TipoReserva, 0) = 0
                        AND COALESCE(O.IDFatoControleContratos, I.IDFatoControleContratoEuromidia) IS NOT NULL
                    )
                )
                AND CAST(COALESCE(I.DataTerminoPrevisto, I.DataFimEfetiva, I.DataCancelamento, O.DataFim) AS DATE) >= DATEADD(
                    DAY,
                    -1,
                    DATEADD(MONTH, :meses_minimos, CAST(COALESCE(I.DataInicioPrevisto, O.DataInicio) AS DATE))
                )
        ),
        ItensElegiveis AS
        (
            SELECT DISTINCT
                O.IDFatoControleContratosItemOrigem
            FROM OcupacoesOrigem AS O
            WHERE
                O.QuantidadeMesesContrato >= :meses_minimos
        )
        UPDATE item
           SET item.BitPreferencia = 1,
               item.DataAtualizacao = SYSDATETIME()
        FROM Integracao.Silver.FatoControleContratosItensEuromidia AS item
        INNER JOIN ItensElegiveis AS elegivel
            ON elegivel.IDFatoControleContratosItemOrigem = item.IDFatoControleContratosItensEuromidia
        WHERE
            ISNULL(item.BitAtivo, 1) = 1
            AND ISNULL(item.BitPreferencia, 0) <> 1;
        """

        sql_contar_itens_bit_preferencia_1 = """
        SET NOCOUNT ON;

        ;WITH ItensComReservaPreferencia AS
        (
            SELECT DISTINCT
                R.IDFatoControleContratosItemOrigem
            FROM Integracao.Silver.FatoOcupacaoPaineisEuromidia AS R WITH (NOLOCK)
            WHERE
                R.IDFatoControleContratosItemOrigem IS NOT NULL
                AND R.Origem = :origem_reserva
                AND R.Status = :status_reservado
                AND R.TipoVinculoOrigem = :tipo_vinculo
                AND ISNULL(R.TipoReserva, 0) = :tipo_reserva_preferencia
                AND R.CanceladoEm IS NULL
                AND (:id_contrato IS NULL OR R.IDFatoControleContratos = :id_contrato)
        )
        SELECT
            COUNT(1) AS itens_preferencia_marcados
        FROM Integracao.Silver.FatoControleContratosItensEuromidia AS item WITH (NOLOCK)
        INNER JOIN ItensComReservaPreferencia AS reserva
            ON reserva.IDFatoControleContratosItemOrigem = item.IDFatoControleContratosItensEuromidia
        WHERE
            ISNULL(item.BitPreferencia, 0) = 1;
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
            AND ISNULL(O.TipoReserva, 0) = :tipo_reserva_preferencia
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
                "Origem igual a CONTRATO, Status diferente de CANCELADO, datas válidas, face/painel válidos e reserva já existente. "
                "diagnostico=%s",
                diagnostico_antes,
            )

        if int((diagnostico_antes or {}).get("reservas_pendentes_sem_encaixe_tetris") or 0):
            logging.warning(
                "Existem reservas pendentes sem encaixe Tetris no horizonte configurado de %s dia(s). "
                "Nenhuma delas será criada sobre ocupação/reserva existente. diagnostico=%s",
                DIAS_MAXIMOS_PROCURAR_ENCAIXE_TETRIS,
                diagnostico_antes,
            )

        _executar_comando_parametrizado(hook, sql_insert, parametros)
        _executar_comando_parametrizado(
            hook,
            sql_garantir_origem_status_reservas_preferencia,
            parametros,
        )
        _executar_comando_parametrizado(
            hook,
            sql_marcar_bit_preferencia_itens_elegiveis,
            parametros,
        )

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
                AND ISNULL(O.TipoReserva, 0) <> 0
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
