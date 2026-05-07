from __future__ import annotations

import logging
import os
from datetime import timedelta

import pendulum

try:
    from airflow.sdk import dag, task
except ImportError:
    from airflow.decorators import dag, task

from hooks.BancodeDados.SqlServer import HookSqlServer


DAG_ID = "euromidia_mensageria_campanhas"

#
# IMPORTANTE:
# O HookSqlServer do projeto usa BaseHook.get_connection(conn_id).
# Então precisa existir uma Connection no Airflow com esse conn_id.
#
# Padrão usado pelo HookSqlServer do projeto:
#   mssql_integracao
#
# Se no seu Airflow a conexão tiver outro nome, você pode definir a variável
# de ambiente abaixo no docker-compose, sem precisar editar a DAG:
#   EUROMIDIA_SQLSERVER_CONN_ID=nome_da_sua_connection
#
SQLSERVER_CONN_ID = os.getenv("EUROMIDIA_SQLSERVER_CONN_ID", "mssql_integracao").strip()

# Fallback opcional. Exemplo no docker-compose:
# EUROMIDIA_SQLSERVER_CONN_ID_FALLBACKS=mssql_integracao,sqlserver_default
SQLSERVER_CONN_ID_FALLBACKS = tuple(
    conn_id.strip()
    for conn_id in os.getenv("EUROMIDIA_SQLSERVER_CONN_ID_FALLBACKS", "sqlserver_integracao").split(",")
    if conn_id.strip()
)

SQLSERVER_CONN_IDS_CANDIDATOS = tuple(
    dict.fromkeys((SQLSERVER_CONN_ID, *SQLSERVER_CONN_ID_FALLBACKS))
)

TZ = pendulum.timezone("America/Sao_Paulo")

logger = logging.getLogger(__name__)


DOC_MD = """
# DAG: euromidia_mensageria_campanhas

## Objetivo

Atualiza o status das campanhas comerciais da Euromídia e gera mensagens automáticas para os usuários responsáveis.

O DAG trabalha sobre a tabela:

- Integracao.Silver.FatoVencimentoCampanhaEuromidia

E grava mensagens na tabela:

- Integracao.Silver.FatoMensagemUsuario

## Frequência

Executa a cada 10 minutos.

Cron:

*/10 * * * *

## Principais responsabilidades

1. Atualizar `DiasParaVencer`.
2. Atualizar `IDDimStatusCampanha`.
3. Gerar mensagem de campanha quase acabando.
4. Gerar mensagem de campanha terminada.
5. Consolidar várias faces do mesmo contrato em uma única mensagem.
6. Evitar duplicidade de mensagens ativas.

## Regras de status

| Condição | Status |
|---|---|
| BitAtivo = 0 | CANCELADA |
| DataTerminoPrevisto é nula | SEM DATA TERMINO |
| DataInicioCampanha > hoje | CAMPANHA FUTURA |
| DataTerminoPrevisto < hoje | CAMPANHA VENCIDA |
| DiasParaVencer entre 0 e 45 | CAMPANHA VENCENDO |
| Demais campanhas dentro do período | CAMPANHA ATIVA |

## Regras de mensagem

### CAMPANHA QUASE ACABANDO

Criada quando faltarem até 45 dias para o término da campanha.

### CAMPANHA TERMINADA

Criada quando a campanha já passou da data de término.

## Regra importante

Este DAG não gera mensagem de início de campanha.

A mensagem `INICIO CAMPANHA` deve ficar fora deste processo, porque este DAG roda periodicamente e trabalha com vencimento de campanha.

## Regra de agrupamento

As mensagens são agrupadas por contrato, período da campanha, vendedor, empresa e marca.

Se um contrato possuir várias faces, o DAG cria uma única mensagem consolidada informando todas as faces.

## Regra de idempotência

O DAG usa `NOT EXISTS` antes de inserir mensagens, evitando duplicidade de mensagens ativas mesmo rodando a cada 10 minutos.

## Relação vendedor → usuário

A mensagem é enviada ao usuário vinculado ao vendedor:

Integracao.dbo.Vendedores.IDDimUsuarios
"""


def _erro_conn_id_nao_definido(erro: Exception) -> bool:
    """Identifica erro de Connection inexistente no Airflow."""

    mensagem = str(erro).lower()

    return (
        "conn_id" in mensagem
        and (
            "isn't defined" in mensagem
            or "is not defined" in mensagem
            or "isnt defined" in mensagem
            or "não está definido" in mensagem
            or "nao esta definido" in mensagem
            or "not found" in mensagem
        )
    )


def obter_conexao_dbapi_sqlserver():
    """
    Abre conexão DBAPI usando o HookSqlServer do projeto.

    A DAG não monta conexão manualmente.
    Ela usa o hook oficial do projeto:

        hooks.BancodeDados.SqlServer.HookSqlServer

    O ponto crítico é que o hook precisa encontrar uma Connection
    cadastrada no Airflow com o conn_id informado.
    """

    erros_conn_id = []
    ultimo_erro = None

    for conn_id in SQLSERVER_CONN_IDS_CANDIDATOS:
        try:
            logger.info("Tentando abrir conexão SQL Server com Airflow conn_id=%s", conn_id)
            hook = HookSqlServer(conn_id=conn_id)
            conn = hook.obter_conexao_dbapi()
            logger.info("Conexão SQL Server aberta com sucesso usando conn_id=%s", conn_id)
            return conn, conn_id

        except Exception as exc:
            ultimo_erro = exc

            if _erro_conn_id_nao_definido(exc):
                erros_conn_id.append(f"{conn_id}: {exc}")
                logger.warning("Airflow Connection não encontrada para conn_id=%s", conn_id)
                continue

            logger.exception("Falha ao abrir conexão SQL Server usando conn_id=%s", conn_id)
            raise

    detalhes = " | ".join(erros_conn_id) if erros_conn_id else str(ultimo_erro)

    raise RuntimeError(
        "Nenhuma Connection válida do SQL Server foi encontrada no Airflow. "
        f"Conn_ids testados: {SQLSERVER_CONN_IDS_CANDIDATOS}. "
        "Crie uma Connection no Airflow com conn_id='mssql_integracao' "
        "ou defina a variável de ambiente EUROMIDIA_SQLSERVER_CONN_ID com o nome correto. "
        f"Detalhes: {detalhes}"
    ) from ultimo_erro


def executar_sql(sql: str) -> None:
    """
    Executa um bloco SQL no SQL Server usando conexão DBAPI do HookSqlServer.

    Importante:
    - O HookSqlServer do projeto não possui get_conn().
    - O método correto disponível no hook é obter_conexao_dbapi().
    - O controle de commit/rollback fica aqui para garantir transação segura no DAG.
    """

    conn = None
    cursor = None
    conn_id_usado = None

    try:
        conn, conn_id_usado = obter_conexao_dbapi_sqlserver()
        cursor = conn.cursor()
        cursor.execute(sql)
        conn.commit()
        logger.info("SQL executado e commit realizado com sucesso usando conn_id=%s", conn_id_usado)

    except Exception:
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                logger.exception("Falha ao executar rollback no DAG de mensageria de campanhas.")

        logger.exception("Erro ao executar SQL no DAG de mensageria de campanhas.")
        raise

    finally:
        if cursor is not None:
            try:
                cursor.close()
            except Exception:
                logger.exception("Falha ao fechar cursor no DAG de mensageria de campanhas.")

        if conn is not None:
            try:
                conn.close()
            except Exception:
                logger.exception("Falha ao fechar conexão no DAG de mensageria de campanhas.")


SQL_ATUALIZAR_STATUS_CAMPANHA = """
USE [Integracao];

SET NOCOUNT ON;

DECLARE @Hoje DATE = CAST(SYSDATETIME() AS DATE);

;WITH StatusCalculado AS
(
    SELECT
        f.IDFatoVencimentoCampanhaEuromidia,

        DiasParaVencerCalculado =
            CASE
                WHEN f.DataTerminoPrevisto IS NULL THEN NULL
                ELSE DATEDIFF(DAY, @Hoje, f.DataTerminoPrevisto)
            END,

        IDDimStatusCampanhaCalculado =
            CASE
                WHEN ISNULL(f.BitAtivo, 1) = 0 THEN
                    (
                        SELECT TOP (1) s.IDDimStatusCampanha
                        FROM Silver.DimStatusCampanha s
                        WHERE s.NomeStatus = N'CANCELADA'
                    )

                WHEN f.DataTerminoPrevisto IS NULL THEN
                    (
                        SELECT TOP (1) s.IDDimStatusCampanha
                        FROM Silver.DimStatusCampanha s
                        WHERE s.NomeStatus = N'SEM DATA TERMINO'
                    )

                WHEN f.DataInicioCampanha IS NOT NULL
                     AND f.DataInicioCampanha > @Hoje THEN
                    (
                        SELECT TOP (1) s.IDDimStatusCampanha
                        FROM Silver.DimStatusCampanha s
                        WHERE s.NomeStatus = N'CAMPANHA FUTURA'
                    )

                WHEN f.DataTerminoPrevisto < @Hoje THEN
                    (
                        SELECT TOP (1) s.IDDimStatusCampanha
                        FROM Silver.DimStatusCampanha s
                        WHERE s.NomeStatus = N'CAMPANHA VENCIDA'
                    )

                WHEN DATEDIFF(DAY, @Hoje, f.DataTerminoPrevisto) BETWEEN 0 AND 45 THEN
                    (
                        SELECT TOP (1) s.IDDimStatusCampanha
                        FROM Silver.DimStatusCampanha s
                        WHERE s.NomeStatus = N'CAMPANHA VENCENDO'
                    )

                ELSE
                    (
                        SELECT TOP (1) s.IDDimStatusCampanha
                        FROM Silver.DimStatusCampanha s
                        WHERE s.NomeStatus = N'CAMPANHA ATIVA'
                    )
            END
    FROM Silver.FatoVencimentoCampanhaEuromidia f
)
UPDATE f
SET
    f.DiasParaVencer = sc.DiasParaVencerCalculado,
    f.IDDimStatusCampanha = sc.IDDimStatusCampanhaCalculado,
    f.DataAtualizacao = SYSDATETIME()
FROM Silver.FatoVencimentoCampanhaEuromidia f
INNER JOIN StatusCalculado sc
    ON sc.IDFatoVencimentoCampanhaEuromidia = f.IDFatoVencimentoCampanhaEuromidia
WHERE
    ISNULL(f.DiasParaVencer, -999999) <> ISNULL(sc.DiasParaVencerCalculado, -999999)
    OR ISNULL(f.IDDimStatusCampanha, -1) <> ISNULL(sc.IDDimStatusCampanhaCalculado, -1);
"""


SQL_GERAR_MENSAGENS_CAMPANHAS = """
USE [Integracao];

SET NOCOUNT ON;
SET XACT_ABORT ON;

DECLARE @Hoje DATE = CAST(SYSDATETIME() AS DATE);

DECLARE @IDTipoCampanhaQuaseAcabando INT;
DECLARE @IDTipoCampanhaTerminada INT;

SELECT @IDTipoCampanhaQuaseAcabando = IDDimTipoMensagem
FROM Silver.DimTipoMensagem
WHERE NomeTipoMensagem = N'CAMPANHA QUASE ACABANDO';

SELECT @IDTipoCampanhaTerminada = IDDimTipoMensagem
FROM Silver.DimTipoMensagem
WHERE NomeTipoMensagem = N'CAMPANHA TERMINADA';

IF OBJECT_ID(N'tempdb..#CampanhaAgrupada', N'U') IS NOT NULL
BEGIN
    DROP TABLE #CampanhaAgrupada;
END;

/*
    Base consolidada por contrato/período/vendedor.

    Regra principal desta DAG:
    - esta DAG NÃO gera INICIO CAMPANHA;
    - gera somente CAMPANHA QUASE ACABANDO;
    - gera somente CAMPANHA TERMINADA;
    - não gera uma mensagem por face;
    - agrupa todas as faces do mesmo contrato/período/vendedor;
    - mantém um IDFatoVencimentoCampanhaEuromidia de referência;
    - mantém um item de referência;
    - gera texto único e profissional.

    Observação técnica:
    No SQL Server, uma CTE só vale para o próximo comando.
    Como precisamos usar a base em mais de um INSERT, gravamos o agrupamento
    em uma tabela temporária #CampanhaAgrupada.
*/

;WITH BaseCampanha AS
(
    SELECT
        f.IDFatoVencimentoCampanhaEuromidia,
        f.IDFatoControleContratosEuromidia,
        f.IDFatoControleContratosItensEuromidia,
        f.IDVendedor,
        f.IDEmpresa,
        f.MarcaExibida,
        f.DataInicioCampanha,
        f.DataTerminoPrevisto,
        f.BitAtivo,

        v.NomeVendedor,
        v.IDDimUsuarios AS IDDimUsuariosDestinatario,

        NumContrato = CONVERT(NVARCHAR(80), f.IDFatoControleContratosEuromidia),

        e.RazaoSocial,

        CodFaceExibicao =
            COALESCE(
                NULLIF(LTRIM(RTRIM(i.CodFace)), N''),
                NULLIF(LTRIM(RTRIM(CONVERT(NVARCHAR(50), fp.CodFace))), N''),
                NULLIF(LTRIM(RTRIM(CONVERT(NVARCHAR(50), fp.Face))), N''),
                N'FACE NÃO INFORMADA'
            )
    FROM Silver.FatoVencimentoCampanhaEuromidia f
    INNER JOIN dbo.Vendedores v
        ON v.IDVendedor = f.IDVendedor
       AND v.IDDimUsuarios IS NOT NULL
    LEFT JOIN Silver.FatoControleContratosItensEuromidia i
        ON i.IDFatoControleContratosItensEuromidia = f.IDFatoControleContratosItensEuromidia
    LEFT JOIN Silver.DimFacesPaineis fp
        ON fp.IDDimFacesPaineis = i.IDDimFacesPaineis
    LEFT JOIN Silver.DimEmpresas e
        ON e.IDEmpresa = f.IDEmpresa
    WHERE
        ISNULL(f.BitAtivo, 1) = 1
),
CampanhaAgrupada AS
(
    SELECT
        MIN(b.IDFatoVencimentoCampanhaEuromidia) AS IDFatoVencimentoCampanhaEuromidiaReferencia,
        b.IDFatoControleContratosEuromidia,
        MIN(b.IDFatoControleContratosItensEuromidia) AS IDFatoControleContratosItensEuromidiaReferencia,
        b.IDVendedor,
        b.IDDimUsuariosDestinatario,
        b.NomeVendedor,
        b.IDEmpresa,
        b.RazaoSocial,
        b.MarcaExibida,
        b.DataInicioCampanha,
        b.DataTerminoPrevisto,

        DiasParaVencer =
            CASE
                WHEN b.DataTerminoPrevisto IS NULL THEN NULL
                ELSE DATEDIFF(DAY, @Hoje, b.DataTerminoPrevisto)
            END,

        NumContrato = b.NumContrato,

        Faces =
            STRING_AGG(CONVERT(NVARCHAR(MAX), b.CodFaceExibicao), N', ')
                WITHIN GROUP (ORDER BY b.CodFaceExibicao)
    FROM BaseCampanha b
    GROUP BY
        b.IDFatoControleContratosEuromidia,
        b.IDVendedor,
        b.IDDimUsuariosDestinatario,
        b.NomeVendedor,
        b.IDEmpresa,
        b.RazaoSocial,
        b.MarcaExibida,
        b.DataInicioCampanha,
        b.DataTerminoPrevisto,
        b.NumContrato
)
SELECT
    ca.IDFatoVencimentoCampanhaEuromidiaReferencia,
    ca.IDFatoControleContratosEuromidia,
    ca.IDFatoControleContratosItensEuromidiaReferencia,
    ca.IDVendedor,
    ca.IDDimUsuariosDestinatario,
    ca.NomeVendedor,
    ca.IDEmpresa,
    ca.RazaoSocial,
    ca.MarcaExibida,
    ca.DataInicioCampanha,
    ca.DataTerminoPrevisto,
    ca.DiasParaVencer,
    ca.NumContrato,
    ca.Faces
INTO #CampanhaAgrupada
FROM CampanhaAgrupada ca;


/*
    Mensagem 1:
    CAMPANHA QUASE ACABANDO

    Regra:
    - DataTerminoPrevisto não pode ser nula;
    - DiasParaVencer precisa estar entre 0 e 45;
    - Não pode existir mensagem ativa igual para o mesmo contrato/vencimento.
*/

INSERT INTO Silver.FatoMensagemUsuario
(
    IDDimUsuariosDestinatario,
    IDDimTipoMensagem,
    IDFatoVencimentoCampanhaEuromidia,
    IDFatoControleContratosEuromidia,
    IDFatoControleContratosItensEuromidia,
    TituloMensagem,
    TextoMensagem,
    LinkDestino,
    BitLida,
    DataLeitura,
    BitAtivo,
    DataCriacao,
    DataAtualizacao
)
SELECT
    ca.IDDimUsuariosDestinatario,
    @IDTipoCampanhaQuaseAcabando,
    ca.IDFatoVencimentoCampanhaEuromidiaReferencia,
    ca.IDFatoControleContratosEuromidia,
    ca.IDFatoControleContratosItensEuromidiaReferencia,

    TituloMensagem =
        CONCAT(
            N'CAMPANHA QUASE ACABANDO - Contrato ',
            ca.NumContrato,
            N' / AC ',
            ca.NomeVendedor
        ),

    TextoMensagem =
        CONCAT(
            N'A campanha do contrato ',
            ca.NumContrato,
            N', referente à empresa ',
            COALESCE(ca.RazaoSocial, N'empresa não informada'),
            N', marca ',
            COALESCE(ca.MarcaExibida, N'não informada'),
            N', está próxima do término. Restam ',
            CONVERT(NVARCHAR(20), ca.DiasParaVencer),
            N' dia(s) para encerrar. Face(s): ',
            COALESCE(ca.Faces, N'não informada'),
            N'. Início da campanha: ',
            COALESCE(CONVERT(NVARCHAR(10), ca.DataInicioCampanha, 103), N'não informado'),
            N'. Término previsto: ',
            COALESCE(CONVERT(NVARCHAR(10), ca.DataTerminoPrevisto, 103), N'não informado'),
            N'.'
        ),

    LinkDestino =
        CONCAT(N'/contratos/', ca.IDFatoControleContratosEuromidia),

    BitLida = 0,
    DataLeitura = NULL,
    BitAtivo = 1,
    DataCriacao = SYSDATETIME(),
    DataAtualizacao = SYSDATETIME()
FROM #CampanhaAgrupada ca
WHERE
    @IDTipoCampanhaQuaseAcabando IS NOT NULL
    AND ca.DataTerminoPrevisto IS NOT NULL
    AND ca.DiasParaVencer BETWEEN 0 AND 45
    AND NOT EXISTS
    (
        SELECT 1
        FROM Silver.FatoMensagemUsuario m
        WHERE
            m.IDDimTipoMensagem = @IDTipoCampanhaQuaseAcabando
            AND m.IDFatoVencimentoCampanhaEuromidia = ca.IDFatoVencimentoCampanhaEuromidiaReferencia
            AND m.IDFatoControleContratosEuromidia = ca.IDFatoControleContratosEuromidia
            AND ISNULL(m.BitAtivo, 1) = 1
    );


/*
    Mensagem 2:
    CAMPANHA TERMINADA

    Regra:
    - DataTerminoPrevisto não pode ser nula;
    - DataTerminoPrevisto precisa ser menor do que hoje;
    - Não pode existir mensagem ativa igual para o mesmo contrato/vencimento.
*/

INSERT INTO Silver.FatoMensagemUsuario
(
    IDDimUsuariosDestinatario,
    IDDimTipoMensagem,
    IDFatoVencimentoCampanhaEuromidia,
    IDFatoControleContratosEuromidia,
    IDFatoControleContratosItensEuromidia,
    TituloMensagem,
    TextoMensagem,
    LinkDestino,
    BitLida,
    DataLeitura,
    BitAtivo,
    DataCriacao,
    DataAtualizacao
)
SELECT
    ca.IDDimUsuariosDestinatario,
    @IDTipoCampanhaTerminada,
    ca.IDFatoVencimentoCampanhaEuromidiaReferencia,
    ca.IDFatoControleContratosEuromidia,
    ca.IDFatoControleContratosItensEuromidiaReferencia,

    TituloMensagem =
        CONCAT(
            N'CAMPANHA TERMINADA - Contrato ',
            ca.NumContrato,
            N' / AC ',
            ca.NomeVendedor
        ),

    TextoMensagem =
        CONCAT(
            N'A campanha do contrato ',
            ca.NumContrato,
            N', referente à empresa ',
            COALESCE(ca.RazaoSocial, N'empresa não informada'),
            N', marca ',
            COALESCE(ca.MarcaExibida, N'não informada'),
            N', foi encerrada. Face(s): ',
            COALESCE(ca.Faces, N'não informada'),
            N'. Início da campanha: ',
            COALESCE(CONVERT(NVARCHAR(10), ca.DataInicioCampanha, 103), N'não informado'),
            N'. Término da campanha: ',
            COALESCE(CONVERT(NVARCHAR(10), ca.DataTerminoPrevisto, 103), N'não informado'),
            N'.'
        ),

    LinkDestino =
        CONCAT(N'/contratos/', ca.IDFatoControleContratosEuromidia),

    BitLida = 0,
    DataLeitura = NULL,
    BitAtivo = 1,
    DataCriacao = SYSDATETIME(),
    DataAtualizacao = SYSDATETIME()
FROM #CampanhaAgrupada ca
WHERE
    @IDTipoCampanhaTerminada IS NOT NULL
    AND ca.DataTerminoPrevisto IS NOT NULL
    AND ca.DataTerminoPrevisto < @Hoje
    AND NOT EXISTS
    (
        SELECT 1
        FROM Silver.FatoMensagemUsuario m
        WHERE
            m.IDDimTipoMensagem = @IDTipoCampanhaTerminada
            AND m.IDFatoVencimentoCampanhaEuromidia = ca.IDFatoVencimentoCampanhaEuromidiaReferencia
            AND m.IDFatoControleContratosEuromidia = ca.IDFatoControleContratosEuromidia
            AND ISNULL(m.BitAtivo, 1) = 1
    );

DROP TABLE #CampanhaAgrupada;
"""


@dag(
    dag_id=DAG_ID,
    description="Atualiza status de campanhas e gera mensagens automáticas para usuários responsáveis por contratos da Euromídia.",
    schedule="*/10 * * * *",
    start_date=pendulum.datetime(2026, 5, 7, tz=TZ),
    catchup=False,
    max_active_runs=1,
    dagrun_timeout=timedelta(minutes=8),
    tags=[
        "euromidia",
        "contratos",
        "campanhas",
        "mensageria",
        "sqlserver",
    ],
    doc_md=DOC_MD,
)
def euromidia_mensageria_campanhas():

    @task(
        task_id="atualizar_status_campanha",
        retries=2,
        retry_delay=timedelta(minutes=1),
    )
    def atualizar_status_campanha():
        """
        Atualiza DiasParaVencer e IDDimStatusCampanha.
        """

        logger.info("Iniciando atualização de status das campanhas.")
        executar_sql(SQL_ATUALIZAR_STATUS_CAMPANHA)
        logger.info("Status das campanhas atualizado com sucesso.")

    @task(
        task_id="gerar_mensagens_campanhas",
        retries=2,
        retry_delay=timedelta(minutes=1),
    )
    def gerar_mensagens_campanhas():
        """
        Gera mensagens consolidadas de início, vencimento próximo e término de campanha.
        """

        logger.info("Iniciando geração de mensagens de campanhas.")
        executar_sql(SQL_GERAR_MENSAGENS_CAMPANHAS)
        logger.info("Mensagens de campanhas geradas com sucesso.")

    status = atualizar_status_campanha()
    mensagens = gerar_mensagens_campanhas()

    status >> mensagens


euromidia_mensageria_campanhas()