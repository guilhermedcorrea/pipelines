"""
DAG: pipeline_backup_databases - SQL Server Backups manuais por cadeia

Objetivo
--------
Orquestrar backups do SQL Server em Linux usando Apache Airflow, salvando em:

    /var/opt/mssql/backups/manual

Estratégia
----------
1. Databases em Recovery Model FULL:
   - Integracao
   - DataMart
   - Kanban
   - InteligenciaMercado

   Rotina:
   - FULL toda sexta-feira às 20:00.
   - FULL inicial automático em 2026-07-01, uma única vez por database, caso ainda não exista FULL válido no dia.
   - DIFFERENTIAL todos os dias às 08:00, 12:30, 16:00 e 18:00.
   - LOG a cada 30 minutos.

   Cadeia completa:
   - 1 FULL válido.
   - Pelo menos 1 DIFF válido posterior ao FULL.
   - Pelo menos 1 LOG válido posterior ao FULL.

2. Databases em Recovery Model SIMPLE:
   - DataMining
   - ReceitaFederal

   Rotina:
   - FULL toda sexta-feira às 20:00.
   - FULL inicial automático em 2026-07-01, uma única vez por database, caso ainda não exista FULL válido no dia.
   - DIFFERENTIAL todos os dias às 08:00, 12:30, 16:00 e 18:00.
   - Sem backup de LOG, porque SIMPLE não aceita BACKUP LOG.

   Cadeia completa:
   - 1 FULL válido.
   - Pelo menos 1 DIFF válido posterior ao FULL.

Retenção
--------
A retenção não apaga arquivos isolados por idade.
Ela apaga cadeias completas antigas.

Regra:
    Para cada database, manter as 2 cadeias completas mais recentes.
    Quando existir uma terceira cadeia completa, apagar a cadeia completa mais antiga.

Observação importante:
    Depois de gerar um FULL novo, a cadeia anterior ainda não é apagada.
    A limpeza só acontece quando o FULL novo já tiver seu primeiro DIFF válido
    e, nas bases FULL Recovery Model, seu primeiro LOG válido.

Requisitos
----------
- O arquivo SqlServer.py precisa estar importável pelo Airflow.
- O HookSqlServer precisa usar uma Connection do Airflow com permissão para:
    - ALTER DATABASE ... SET RECOVERY
    - BACKUP DATABASE
    - BACKUP LOG
    - RESTORE VERIFYONLY
    - Criar/atualizar tabela em msdb.dbo.AirflowBackupControle
- O caminho /var/opt/mssql/backups/manual precisa existir no host/container onde o SQL Server grava arquivos.
- O usuário do serviço SQL Server no Linux, normalmente mssql, precisa ter permissão de escrita na pasta.
"""

from __future__ import annotations

import os
import shutil
import sys
import uuid
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable

import pendulum

try:
    # Airflow 3+
    from airflow.sdk import DAG, get_current_context, task
except Exception:  # pragma: no cover
    # Airflow 2.x
    from airflow import DAG
    from airflow.decorators import task
    from airflow.operators.python import get_current_context


# Permite importar SqlServer.py quando ele estiver na mesma pasta do DAG.
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
if CURRENT_DIR not in sys.path:
    sys.path.insert(0, CURRENT_DIR)

from SqlServer import HookSqlServer  # noqa: E402


# =============================================================================
# CONFIGURAÇÕES PRINCIPAIS
# =============================================================================

DAG_ID = "pipeline_backup_databases"

CONN_ID = "mssql_integracao"

TZ = "America/Sao_Paulo"

BASE_BACKUP_DIR = "/var/opt/mssql/backups/manual"

CONTROLE_DB = "msdb"
CONTROLE_SCHEMA = "dbo"
CONTROLE_TABLE = "AirflowBackupControle"
CONTROLE_TABLE_FULL = f"[{CONTROLE_DB}].[{CONTROLE_SCHEMA}].[{CONTROLE_TABLE}]"
CONTROLE_TABLE_MSDB = f"[{CONTROLE_SCHEMA}].[{CONTROLE_TABLE}]"
_CONTROLE_TABELA_GARANTIDA = False

DATABASES_FULL_RECOVERY = [
    "Integracao",
    "DataMart",
    "Kanban",
    "InteligenciaMercado",
]

DATABASES_SIMPLE_RECOVERY = [
    "DataMining",
    "ReceitaFederal",
]

ALL_DATABASES = DATABASES_FULL_RECOVERY + DATABASES_SIMPLE_RECOVERY

DIFF_TIMES = {
    (8, 0),
    (12, 30),
    (16, 0),
    (18, 0),
}

FULL_WEEKDAY = 4  # segunda=0, sexta=4
FULL_TIME = (20, 0)

# Em 2026-07-01 será feito 1 FULL inicial por database,
# no primeiro ciclo do DAG em que ainda não exista FULL válido no dia.
INITIAL_FULL_DATE = pendulum.date(2026, 7, 1)

# Configurações de BACKUP.
USAR_COMPRESSION = True
USAR_CHECKSUM = True
STATS_PERCENTUAL = 10

# Se True, move arquivo que falhou no VERIFYONLY para a subpasta INVALID.
# Se a pasta não estiver montada no container do Airflow, o move pode falhar;
# nesse caso o DAG registra o erro e mantém a trilha na tabela de controle.
MOVER_INVALIDOS = True

# Tags pedidas.
TAGS = [
    "SQL Server",
    "Backups",
    "Diario",
    "Database",
    "Empresas",
]


# =============================================================================
# ESTRUTURAS AUXILIARES
# =============================================================================

@dataclass(frozen=True)
class DatabaseConfig:
    name: str
    recovery_model: str  # FULL ou SIMPLE

    @property
    def usa_log(self) -> bool:
        return self.recovery_model.upper() == "FULL"


@dataclass(frozen=True)
class PlanoExecucao:
    run_ref_iso: str
    run_ref_local: str
    executar_full_programado: bool
    executar_diff: bool
    executar_log: bool
    executar_full_inicial_20260701: bool


def get_db_configs() -> list[DatabaseConfig]:
    configs: list[DatabaseConfig] = []

    for db in DATABASES_FULL_RECOVERY:
        configs.append(DatabaseConfig(name=db, recovery_model="FULL"))

    for db in DATABASES_SIMPLE_RECOVERY:
        configs.append(DatabaseConfig(name=db, recovery_model="SIMPLE"))

    return configs


def sql_identifier(nome: str) -> str:
    """
    Escapa identificador SQL Server usando colchetes.

    Exemplo:
        Integracao -> [Integracao]
        Nome]Ruim  -> [Nome]]Ruim]
    """
    return "[" + nome.replace("]", "]]") + "]"


def sql_string_literal(valor: str) -> str:
    """
    Monta literal Unicode seguro para T-SQL.

    Uso principal aqui: BACKUP/RESTORE.
    Esses comandos administrativos ficam mais seguros como um único statement,
    sem DECLARE anterior no mesmo batch.
    """
    return "N'" + str(valor).replace("'", "''") + "'"


def normalizar_nome_arquivo(nome: str) -> str:
    """
    Normaliza nome para arquivo/pasta.
    Mantém somente caracteres simples para evitar problemas no Linux.
    """
    permitido = []
    for ch in nome:
        if ch.isalnum() or ch in ("_", "-", "."):
            permitido.append(ch)
        else:
            permitido.append("_")
    return "".join(permitido)


def obter_hook() -> HookSqlServer:
    return HookSqlServer(conn_id=CONN_ID)


def configurar_autocommit(
    conn: Any,
    *,
    autocommit: bool = True,
    obrigatorio: bool = False,
    log_prefix: str = "",
) -> None:
    """
    Força o modo autocommit da conexão DBAPI.

    O SQL Server não permite ALTER DATABASE, BACKUP DATABASE, BACKUP LOG
    nem RESTORE VERIFYONLY dentro de transação explícita/implícita.

    O erro original era:
        ALTER DATABASE statement not allowed within multi-statement transaction.

    Por isso, para comandos administrativos, esta rotina:
        1. tenta desfazer qualquer transação pendurada na conexão reaproveitada;
        2. liga autocommit=True;
        3. quando obrigatório, valida se o driver aceitou o autocommit.
    """
    if autocommit:
        try:
            conn.rollback()
        except Exception:
            # Em conexão nova/autocommit, rollback pode não existir ou não haver transação.
            pass

    try:
        conn.autocommit = autocommit
    except Exception as exc:
        mensagem = f"Não consegui definir conn.autocommit={autocommit}: {exc}"
        if obrigatorio:
            raise RuntimeError(mensagem) from exc
        if log_prefix:
            print(f"{log_prefix} [WARN] {mensagem}")
        else:
            print(f"[SQL][WARN] {mensagem}")

    if obrigatorio:
        valor_atual = getattr(conn, "autocommit", None)
        if valor_atual is not True:
            raise RuntimeError(
                "A conexão DBAPI não confirmou autocommit=True. "
                "Comandos ALTER DATABASE/BACKUP/RESTORE não podem rodar com transação aberta."
            )


def consumir_resultsets(cursor: Any) -> None:
    """Consome resultsets/mensagens intermediárias do SQL Server."""
    while True:
        try:
            if not cursor.nextset():
                break
        except Exception:
            break


def abrir_conexao_dbapi(*, autocommit: bool = True, obrigatorio: bool = False, log_prefix: str = ""):
    """
    Usa o HookSqlServer enviado pelo usuário, mas abre DBAPI/autocommit.

    Motivo:
        ALTER DATABASE, BACKUP DATABASE, BACKUP LOG e RESTORE VERIFYONLY
        não devem depender de transação aberta por engine.begin(), pooling,
        implicit transaction ou cursor anterior.
    """
    hook = obter_hook()
    conn = hook.obter_conexao_dbapi()
    configurar_autocommit(conn, autocommit=autocommit, obrigatorio=obrigatorio, log_prefix=log_prefix)
    return conn


def _extra_get(extra: dict[str, Any], *keys: str, default: Any = None) -> Any:
    """Busca chave em extras do Airflow ignorando variações comuns de nome."""
    if not extra:
        return default

    lower_map = {str(k).lower(): v for k, v in extra.items()}
    for key in keys:
        if key in extra:
            return extra[key]
        key_lower = key.lower()
        if key_lower in lower_map:
            return lower_map[key_lower]
    return default


def _odbc_escape(value: Any) -> str:
    """
    Escapa valor para connection string ODBC.

    Senhas podem conter ponto e vírgula, espaço ou chave. Usar chaves evita
    quebrar a connection string. Chave de fechamento é escapada duplicando.
    """
    if value is None:
        return ""
    value_str = str(value)
    return "{" + value_str.replace("}", "}}") + "}"


def _escolher_driver_pyodbc(driver_preferido: str) -> str:
    """Escolhe driver ODBC instalado, mantendo fallback para o informado."""
    try:
        import pyodbc  # type: ignore

        drivers = list(pyodbc.drivers())
        if driver_preferido in drivers:
            return driver_preferido

        for candidato in (
            "ODBC Driver 18 for SQL Server",
            "ODBC Driver 17 for SQL Server",
            "ODBC Driver 13 for SQL Server",
            "FreeTDS",
        ):
            if candidato in drivers:
                return candidato
    except Exception:
        pass

    return driver_preferido


def abrir_conexao_pyodbc_direta(
    *,
    database: str = "master",
    log_prefix: str = "",
):
    """
    Abre conexão pyodbc diretamente a partir da Connection do Airflow.

    Esta função NÃO usa HookSqlServer/SQLAlchemy. Ela existe porque alguns hooks
    reaproveitam sessão com transação aberta ou usam engine/connection com begin
    automático. Para ALTER DATABASE, BACKUP e RESTORE VERIFYONLY isso é perigoso.
    """
    try:
        import pyodbc  # type: ignore
        try:
            # Airflow 3+
            from airflow.sdk.bases.hook import BaseHook
        except Exception:  # pragma: no cover
            # Airflow 2.x
            from airflow.hooks.base import BaseHook
    except Exception as exc:  # pragma: no cover
        raise RuntimeError(
            "Não consegui importar pyodbc/BaseHook para abrir conexão administrativa direta."
        ) from exc

    airflow_conn = BaseHook.get_connection(CONN_ID)
    extra = airflow_conn.extra_dejson or {}

    driver_preferido = str(
        _extra_get(
            extra,
            "driver",
            "odbc_driver",
            "ODBC Driver",
            "extra__mssql__odbc_driver",
            default="ODBC Driver 18 for SQL Server",
        )
    )
    driver = _escolher_driver_pyodbc(driver_preferido)

    host = airflow_conn.host or "localhost"
    server = f"{host},{airflow_conn.port}" if airflow_conn.port else host
    database_final = database or airflow_conn.schema or "master"

    encrypt = str(_extra_get(extra, "Encrypt", "encrypt", default="yes"))
    trust_cert = str(
        _extra_get(
            extra,
            "TrustServerCertificate",
            "trustservercertificate",
            "Trust Server Certificate",
            default="yes",
        )
    )
    timeout = str(_extra_get(extra, "Connection Timeout", "timeout", default="30"))

    partes = [
        f"DRIVER={{{driver}}}",
        f"SERVER={_odbc_escape(server)}",
        f"DATABASE={_odbc_escape(database_final)}",
        f"Encrypt={encrypt}",
        f"TrustServerCertificate={trust_cert}",
        f"Connection Timeout={timeout}",
        "Application Name={Airflow pipeline_backup_databases}",
    ]

    if airflow_conn.login:
        partes.append(f"UID={_odbc_escape(airflow_conn.login)}")
        partes.append(f"PWD={_odbc_escape(airflow_conn.password or '')}")
    else:
        trusted = str(_extra_get(extra, "Trusted_Connection", "trusted_connection", default="yes"))
        partes.append(f"Trusted_Connection={trusted}")

    conn_str = ";".join(partes) + ";"

    if log_prefix:
        print(
            f"{log_prefix} Abrindo conexão administrativa direta "
            f"em database={database_final}, driver={driver}."
        )

    conn = pyodbc.connect(conn_str, autocommit=True)
    conn.autocommit = True
    return conn


def limpar_estado_transacional_sqlserver(cursor: Any, *, log_prefix: str = "") -> None:
    """
    Garante @@TRANCOUNT = 0 antes de comando administrativo.

    Mesmo com autocommit=True, pooling/driver/hook podem devolver uma sessão com
    transação pendurada. SQL Server bloqueia ALTER DATABASE/BACKUP/RESTORE nesse
    estado. Por isso fazemos rollback preventivo em qualquer transação aberta.
    """
    if log_prefix:
        print(f"{log_prefix} Limpando estado transacional da sessão SQL Server.")

    cursor.execute(
        """
        SET XACT_ABORT OFF;
        WHILE @@TRANCOUNT > 0
            ROLLBACK TRANSACTION;
        SET IMPLICIT_TRANSACTIONS OFF;
        """
    )
    consumir_resultsets(cursor)

    cursor.execute("SELECT @@TRANCOUNT AS TranCount;")
    row = cursor.fetchone()
    tran_count = int(row[0]) if row else 0
    consumir_resultsets(cursor)

    if tran_count != 0:
        raise RuntimeError(
            f"Sessão SQL Server ainda está com @@TRANCOUNT={tran_count}. "
            "Comando administrativo não será executado dentro de transação."
        )


def executar_sql_administrativo(
    sql: str,
    params: Iterable[Any] | None = None,
    *,
    log_prefix: str = "",
    database_conexao: str = "master",
) -> None:
    """
    Executa comando administrativo fora de transação, usando pyodbc direto.

    Use para:
        - ALTER DATABASE ... SET RECOVERY
        - BACKUP DATABASE
        - BACKUP LOG
        - RESTORE VERIFYONLY

    Importante:
        Não chama executar_sql() e não usa HookSqlServer, porque o erro relatado
        ocorre justamente quando o caminho comum entrega uma sessão transacional.
    """
    params = list(params or [])

    conn = abrir_conexao_pyodbc_direta(database=database_conexao, log_prefix=log_prefix)
    try:
        try:
            conn.rollback()
        except Exception:
            pass

        conn.autocommit = True
        cursor = conn.cursor()
        try:
            limpar_estado_transacional_sqlserver(cursor, log_prefix=log_prefix)

            if log_prefix:
                print(f"{log_prefix} Executando SQL administrativo sem transação.")

            if params:
                cursor.execute(sql, params)
            else:
                cursor.execute(sql)

            consumir_resultsets(cursor)
        finally:
            cursor.close()
    finally:
        conn.close()


def executar_sql(
    sql: str,
    params: Iterable[Any] | None = None,
    *,
    log_prefix: str = "",
    sem_transacao: bool = False,
) -> None:
    params = list(params or [])

    conn = abrir_conexao_dbapi(
        autocommit=True,
        obrigatorio=sem_transacao,
        log_prefix=log_prefix,
    )
    try:
        cursor = conn.cursor()
        try:
            if log_prefix:
                print(f"{log_prefix} Executando SQL.")

            if sem_transacao:
                # Precisa ser um batch separado. Não junte isso com ALTER/BACKUP/RESTORE.
                cursor.execute("SET IMPLICIT_TRANSACTIONS OFF;")
                consumir_resultsets(cursor)

            cursor.execute(sql, params)
            consumir_resultsets(cursor)
        finally:
            cursor.close()
    finally:
        conn.close()


def executar_sql_sem_transacao(
    sql: str,
    params: Iterable[Any] | None = None,
    *,
    log_prefix: str = "",
) -> None:
    """
    Executa comandos administrativos do SQL Server obrigatoriamente fora de transação.

    Diferente da versão anterior, esta função NÃO chama executar_sql(). Ela usa
    pyodbc direto, abre em master, limpa @@TRANCOUNT e só então executa o comando.
    """
    executar_sql_administrativo(
        sql,
        params,
        log_prefix=log_prefix,
        database_conexao="master",
    )


def consultar_sql(
    sql: str,
    params: Iterable[Any] | None = None,
    *,
    log_prefix: str = "",
) -> list[dict[str, Any]]:
    params = list(params or [])

    conn = abrir_conexao_dbapi(autocommit=True, obrigatorio=False, log_prefix=log_prefix)
    try:
        cursor = conn.cursor()
        try:
            if log_prefix:
                print(f"{log_prefix} Consultando SQL.")
            cursor.execute(sql, params)
            if cursor.description is None:
                return []

            colunas = [col[0] for col in cursor.description]
            linhas = cursor.fetchall()
            return [dict(zip(colunas, linha)) for linha in linhas]
        finally:
            cursor.close()
    finally:
        conn.close()


def obter_data_referencia_airflow() -> pendulum.DateTime:
    """
    Usa data_interval_end para bater com o horário real do agendamento.

    Em DAGs cron no Airflow, logical_date pode representar o começo do intervalo.
    Para uma rotina que precisa disparar às 08:00, 12:30, etc.,
    data_interval_end é a referência mais segura.
    """
    context = get_current_context()
    ref = context.get("data_interval_end") or context.get("logical_date")

    if ref is None:
        ref = pendulum.now(TZ)

    return pendulum.instance(ref).in_timezone(TZ)


def timestamp_arquivo(ref: pendulum.DateTime) -> str:
    return ref.format("YYYYMMDD_HHmmss")


def caminho_backup(database: str, backup_type: str, ref: pendulum.DateTime) -> str:
    db_file = normalizar_nome_arquivo(database)
    tipo = backup_type.upper()
    extensao = "trn" if tipo == "LOG" else "bak"

    return str(
        Path(BASE_BACKUP_DIR)
        / db_file
        / tipo
        / f"{db_file}_{tipo}_{timestamp_arquivo(ref)}.{extensao}"
    )


def garantir_pastas() -> None:
    print("[PASTAS] Garantindo estrutura de diretórios.")

    for config in get_db_configs():
        db_dir = Path(BASE_BACKUP_DIR) / normalizar_nome_arquivo(config.name)

        subpastas = ["FULL", "DIFF", "INVALID"]
        if config.usa_log:
            subpastas.append("LOG")

        for sub in subpastas:
            path = db_dir / sub
            path.mkdir(parents=True, exist_ok=True)
            print(f"[PASTAS] OK: {path}")

    print("[PASTAS] Estrutura de diretórios conferida.")


def obter_tamanho_mb(path: str) -> Decimal | None:
    try:
        if not os.path.exists(path):
            return None
        size_mb = os.path.getsize(path) / 1024 / 1024
        return Decimal(str(round(size_mb, 2)))
    except Exception:
        return None


def mover_para_invalid(path: str, database: str) -> str | None:
    if not MOVER_INVALIDOS:
        return None

    try:
        if not os.path.exists(path):
            print(f"[INVALID] Arquivo não encontrado para mover: {path}")
            return None

        invalid_dir = Path(BASE_BACKUP_DIR) / normalizar_nome_arquivo(database) / "INVALID"
        invalid_dir.mkdir(parents=True, exist_ok=True)

        destino = invalid_dir / f"INVALID_{Path(path).name}"
        shutil.move(path, destino)

        print(f"[INVALID] Arquivo movido para: {destino}")
        return str(destino)
    except Exception as exc:
        print(f"[INVALID][ERRO] Não consegui mover arquivo inválido {path}: {exc}")
        return None


# =============================================================================
# TABELA DE CONTROLE
# =============================================================================

def verificar_tabela_controle_existe(*, log_prefix: str = "") -> bool:
    """
    Verifica a existência da tabela de controle diretamente no msdb.

    Não usa consultar_sql() aqui de propósito, porque consultar_sql() é usado por
    várias funções de controle. Se a tabela não existir, precisamos conseguir
    detectar isso sem gerar erro 208 nem recursão.
    """
    conn = abrir_conexao_pyodbc_direta(database=CONTROLE_DB, log_prefix=log_prefix)
    try:
        cursor = conn.cursor()
        try:
            cursor.execute(
                """
                SELECT 1 AS Existe
                  FROM msdb.sys.tables t
                  INNER JOIN msdb.sys.schemas s
                          ON s.schema_id = t.schema_id
                 WHERE s.name = ?
                   AND t.name = ?;
                """,
                CONTROLE_SCHEMA,
                CONTROLE_TABLE,
            )
            row = cursor.fetchone()
            consumir_resultsets(cursor)
            return row is not None
        finally:
            cursor.close()
    finally:
        conn.close()


def criar_tabela_controle() -> None:
    """
    Cria/conferere msdb.dbo.AirflowBackupControle com conexão direta no msdb.

    Correção importante:
        A versão anterior imprimia "Tabela de controle OK", mas a criação podia
        não ser efetivada pelo caminho comum do Hook/SQLAlchemy. Depois, as
        tarefas downstream falhavam com:

            Invalid object name 'msdb.dbo.AirflowBackupControle'.

        Agora o DDL roda via pyodbc direto em database=msdb, autocommit=True, e
        a existência da tabela é validada depois da execução. Se a tabela não
        existir, a DAG falha imediatamente no ponto correto, em vez de falhar
        depois no SELECT.
    """
    global _CONTROLE_TABELA_GARANTIDA

    print("[CONTROLE] Criando/conferindo tabela de controle em msdb.")

    sql = f"""
    USE [{CONTROLE_DB}];

    IF OBJECT_ID(N'[{CONTROLE_SCHEMA}].[{CONTROLE_TABLE}]', N'U') IS NULL
    BEGIN
        CREATE TABLE {CONTROLE_TABLE_MSDB} (
            IDBackupControle BIGINT IDENTITY(1,1) NOT NULL CONSTRAINT PK_AirflowBackupControle PRIMARY KEY,
            DatabaseName SYSNAME NOT NULL,
            RecoveryModel VARCHAR(20) NOT NULL,
            BackupType VARCHAR(10) NOT NULL,
            ChainId UNIQUEIDENTIFIER NOT NULL,
            FullBackupPath NVARCHAR(1000) NULL,
            BackupPath NVARCHAR(1000) NOT NULL,
            BackupStartDate DATETIME2(0) NOT NULL,
            BackupEndDate DATETIME2(0) NULL,
            Status VARCHAR(30) NOT NULL,
            VerifyStatus VARCHAR(30) NULL,
            FileSizeMB DECIMAL(18,2) NULL,
            ErrorMessage NVARCHAR(MAX) NULL,
            AirflowDagId NVARCHAR(250) NULL,
            AirflowRunRef NVARCHAR(50) NULL,
            DeletedAt DATETIME2(0) NULL,
            CreatedAt DATETIME2(0) NOT NULL CONSTRAINT DF_AirflowBackupControle_CreatedAt DEFAULT SYSDATETIME()
        );
    END;

    IF NOT EXISTS (
        SELECT 1
          FROM sys.indexes
         WHERE object_id = OBJECT_ID(N'[{CONTROLE_SCHEMA}].[{CONTROLE_TABLE}]')
           AND name = N'IX_AirflowBackupControle_DB_Type_Date'
    )
    BEGIN
        CREATE INDEX IX_AirflowBackupControle_DB_Type_Date
            ON {CONTROLE_TABLE_MSDB} (DatabaseName, BackupType, BackupStartDate DESC)
            INCLUDE (Status, VerifyStatus, ChainId, DeletedAt);
    END;

    IF NOT EXISTS (
        SELECT 1
          FROM sys.indexes
         WHERE object_id = OBJECT_ID(N'[{CONTROLE_SCHEMA}].[{CONTROLE_TABLE}]')
           AND name = N'IX_AirflowBackupControle_Chain'
    )
    BEGIN
        CREATE INDEX IX_AirflowBackupControle_Chain
            ON {CONTROLE_TABLE_MSDB} (DatabaseName, ChainId)
            INCLUDE (BackupType, BackupPath, Status, VerifyStatus, BackupStartDate, DeletedAt);
    END;
    """

    executar_sql_administrativo(
        sql,
        log_prefix="[CONTROLE]",
        database_conexao=CONTROLE_DB,
    )

    if not verificar_tabela_controle_existe(log_prefix="[CONTROLE]"):
        raise RuntimeError(
            f"A tabela de controle {CONTROLE_TABLE_FULL} não existe após a tentativa de criação. "
            "Verifique permissão de CREATE TABLE no msdb e se a conexão do Airflow aponta para o SQL Server correto."
        )

    _CONTROLE_TABELA_GARANTIDA = True
    print("[CONTROLE] Tabela de controle OK e validada em msdb.")


def garantir_tabela_controle_existente() -> None:
    """
    Garante a existência da tabela antes de qualquer SELECT/INSERT/UPDATE nela.

    Isso também resolve execuções parciais/retries do Airflow: se uma task
    downstream for reexecutada sem passar novamente pela task preparar_ambiente,
    ela recria/valida a tabela antes de consultar.
    """
    global _CONTROLE_TABELA_GARANTIDA

    if _CONTROLE_TABELA_GARANTIDA:
        return

    if verificar_tabela_controle_existe(log_prefix="[CONTROLE]"):
        _CONTROLE_TABELA_GARANTIDA = True
        return

    print(f"[CONTROLE] {CONTROLE_TABLE_FULL} não existe. Criando agora.")
    criar_tabela_controle()


def registrar_inicio_backup(
    *,
    database: str,
    recovery_model: str,
    backup_type: str,
    chain_id: str,
    full_backup_path: str | None,
    backup_path: str,
    run_ref: pendulum.DateTime,
) -> int:
    garantir_tabela_controle_existente()

    sql = f"""
    INSERT INTO {CONTROLE_TABLE_FULL} (
        DatabaseName,
        RecoveryModel,
        BackupType,
        ChainId,
        FullBackupPath,
        BackupPath,
        BackupStartDate,
        Status,
        VerifyStatus,
        AirflowDagId,
        AirflowRunRef
    )
    OUTPUT INSERTED.IDBackupControle
    VALUES (?, ?, ?, CONVERT(uniqueidentifier, ?), ?, ?, SYSDATETIME(), 'RUNNING', NULL, ?, ?);
    """

    rows = consultar_sql(
        sql,
        [
            database,
            recovery_model,
            backup_type,
            chain_id,
            full_backup_path,
            backup_path,
            DAG_ID,
            run_ref.to_iso8601_string(),
        ],
        log_prefix=f"[CONTROLE][{database}][{backup_type}]",
    )

    if not rows:
        raise RuntimeError(f"Não consegui registrar início do backup {backup_type} de {database}.")

    backup_id = int(rows[0]["IDBackupControle"])
    print(f"[CONTROLE][{database}][{backup_type}] IDBackupControle={backup_id}")
    return backup_id


def atualizar_backup(
    *,
    backup_id: int,
    status: str,
    verify_status: str | None = None,
    file_size_mb: Decimal | None = None,
    error_message: str | None = None,
    backup_path: str | None = None,
) -> None:
    garantir_tabela_controle_existente()

    sql = f"""
    UPDATE {CONTROLE_TABLE_FULL}
       SET BackupEndDate = COALESCE(BackupEndDate, SYSDATETIME()),
           Status = ?,
           VerifyStatus = COALESCE(?, VerifyStatus),
           FileSizeMB = COALESCE(?, FileSizeMB),
           ErrorMessage = ?,
           BackupPath = COALESCE(?, BackupPath)
     WHERE IDBackupControle = ?;
    """

    executar_sql(
        sql,
        [status, verify_status, file_size_mb, error_message, backup_path, backup_id],
        log_prefix=f"[CONTROLE][ID={backup_id}]",
    )


def marcar_deletado(backup_ids: list[int]) -> None:
    garantir_tabela_controle_existente()

    if not backup_ids:
        return

    placeholders = ",".join(["?"] * len(backup_ids))
    sql = f"""
    UPDATE {CONTROLE_TABLE_FULL}
       SET Status = 'DELETED',
           DeletedAt = SYSDATETIME()
     WHERE IDBackupControle IN ({placeholders});
    """

    executar_sql(sql, backup_ids, log_prefix="[RETENCAO]")


def backup_valido_ja_existe(database: str, backup_type: str, backup_path: str) -> bool:
    garantir_tabela_controle_existente()

    sql = f"""
    SELECT TOP (1) 1 AS Existe
      FROM {CONTROLE_TABLE_FULL}
     WHERE DatabaseName = ?
       AND BackupType = ?
       AND BackupPath = ?
       AND Status = 'SUCCESS'
       AND VerifyStatus = 'SUCCESS'
       AND DeletedAt IS NULL;
    """

    rows = consultar_sql(sql, [database, backup_type, backup_path])
    return bool(rows)


def existe_full_valido_na_data(database: str, data_ref: pendulum.Date) -> bool:
    garantir_tabela_controle_existente()

    inicio = pendulum.datetime(data_ref.year, data_ref.month, data_ref.day, 0, 0, 0, tz=TZ)
    fim = inicio.add(days=1)

    sql = f"""
    SELECT TOP (1) 1 AS Existe
      FROM {CONTROLE_TABLE_FULL}
     WHERE DatabaseName = ?
       AND BackupType = 'FULL'
       AND Status = 'SUCCESS'
       AND VerifyStatus = 'SUCCESS'
       AND DeletedAt IS NULL
       AND BackupStartDate >= ?
       AND BackupStartDate < ?;
    """

    rows = consultar_sql(
        sql,
        [
            database,
            inicio.naive().strftime("%Y-%m-%d %H:%M:%S"),
            fim.naive().strftime("%Y-%m-%d %H:%M:%S"),
        ],
    )
    return bool(rows)


def obter_ultima_cadeia_full_valida(database: str) -> dict[str, Any] | None:
    garantir_tabela_controle_existente()

    sql = f"""
    SELECT TOP (1)
           ChainId,
           BackupPath AS FullBackupPath,
           BackupStartDate
      FROM {CONTROLE_TABLE_FULL}
     WHERE DatabaseName = ?
       AND BackupType = 'FULL'
       AND Status = 'SUCCESS'
       AND VerifyStatus = 'SUCCESS'
       AND DeletedAt IS NULL
     ORDER BY BackupStartDate DESC, IDBackupControle DESC;
    """

    rows = consultar_sql(sql, [database])
    return rows[0] if rows else None


# =============================================================================
# SQL SERVER: RECOVERY MODEL, BACKUP E VERIFY
# =============================================================================

def validar_database_existe(database: str) -> None:
    rows = consultar_sql(
        "SELECT name FROM sys.databases WHERE name = ?;",
        [database],
        log_prefix=f"[DATABASE][{database}]",
    )

    if not rows:
        raise RuntimeError(f"Database não encontrada no SQL Server: {database}")


def ajustar_recovery_model(database: str, recovery_model: str) -> None:
    validar_database_existe(database)

    rows = consultar_sql(
        "SELECT recovery_model_desc FROM sys.databases WHERE name = ?;",
        [database],
    )
    atual = rows[0]["recovery_model_desc"] if rows else None

    if atual and str(atual).upper() == recovery_model.upper():
        print(f"[RECOVERY][{database}] Já está em {recovery_model}.")
        return

    print(f"[RECOVERY][{database}] Alterando de {atual} para {recovery_model}.")

    sql = f"""
    ALTER DATABASE {sql_identifier(database)}
    SET RECOVERY {recovery_model.upper()} WITH NO_WAIT;
    """

    executar_sql_sem_transacao(sql, log_prefix=f"[RECOVERY][{database}]")
    print(f"[RECOVERY][{database}] Recovery Model ajustado para {recovery_model}.")


def montar_opcoes_backup() -> str:
    opcoes = ["INIT"]

    if USAR_COMPRESSION:
        opcoes.append("COMPRESSION")

    if USAR_CHECKSUM:
        opcoes.append("CHECKSUM")

    opcoes.append(f"STATS = {int(STATS_PERCENTUAL)}")

    return ", ".join(opcoes)


def executar_backup_sqlserver(
    *,
    database: str,
    backup_type: str,
    backup_path: str,
) -> None:
    tipo = backup_type.upper()
    opcoes = montar_opcoes_backup()
    backup_path_sql = sql_string_literal(backup_path)

    # Não uso DECLARE @backup_path aqui de propósito.
    # ALTER/BACKUP/RESTORE devem rodar fora de transação e, na prática,
    # é mais seguro mandar o comando administrativo como um único statement.
    if tipo == "FULL":
        sql = f"""
        BACKUP DATABASE {sql_identifier(database)}
        TO DISK = {backup_path_sql}
        WITH {opcoes};
        """
    elif tipo == "DIFF":
        sql = f"""
        BACKUP DATABASE {sql_identifier(database)}
        TO DISK = {backup_path_sql}
        WITH DIFFERENTIAL, {opcoes};
        """
    elif tipo == "LOG":
        sql = f"""
        BACKUP LOG {sql_identifier(database)}
        TO DISK = {backup_path_sql}
        WITH {opcoes};
        """
    else:
        raise ValueError(f"Tipo de backup inválido: {backup_type}")

    executar_sql_sem_transacao(sql, log_prefix=f"[BACKUP][{database}][{tipo}]")


def executar_verifyonly(backup_path: str) -> None:
    checksum = "WITH CHECKSUM" if USAR_CHECKSUM else ""
    backup_path_sql = sql_string_literal(backup_path)

    sql = f"""
    RESTORE VERIFYONLY
    FROM DISK = {backup_path_sql}
    {checksum};
    """

    executar_sql_sem_transacao(sql, log_prefix="[VERIFYONLY]")


def executar_backup_controlado(
    *,
    database: str,
    recovery_model: str,
    backup_type: str,
    backup_path: str,
    chain_id: str,
    full_backup_path: str | None,
    run_ref: pendulum.DateTime,
) -> bool:
    tipo = backup_type.upper()

    print("=" * 100)
    print(f"[INICIO][{database}][{tipo}] Arquivo: {backup_path}")
    print(f"[INICIO][{database}][{tipo}] ChainId: {chain_id}")
    print("=" * 100)

    if backup_valido_ja_existe(database, tipo, backup_path):
        print(f"[SKIP][{database}][{tipo}] Backup válido já existe para este arquivo.")
        return True

    backup_id = registrar_inicio_backup(
        database=database,
        recovery_model=recovery_model,
        backup_type=tipo,
        chain_id=chain_id,
        full_backup_path=full_backup_path,
        backup_path=backup_path,
        run_ref=run_ref,
    )

    try:
        # Se sobrou arquivo parcial de tentativa anterior no mesmo caminho,
        # tenta remover antes de gerar o backup.
        try:
            if os.path.exists(backup_path):
                print(f"[LIMPEZA][{database}][{tipo}] Removendo arquivo parcial anterior: {backup_path}")
                os.remove(backup_path)
        except Exception as exc:
            print(f"[LIMPEZA][{database}][{tipo}][WARN] Não consegui remover parcial local: {exc}")

        executar_backup_sqlserver(
            database=database,
            backup_type=tipo,
            backup_path=backup_path,
        )

        print(f"[VERIFYONLY][{database}][{tipo}] Validando arquivo.")
        executar_verifyonly(backup_path)

        tamanho_mb = obter_tamanho_mb(backup_path)

        atualizar_backup(
            backup_id=backup_id,
            status="SUCCESS",
            verify_status="SUCCESS",
            file_size_mb=tamanho_mb,
            error_message=None,
        )

        print(f"[SUCESSO][{database}][{tipo}] Backup validado com sucesso. TamanhoMB={tamanho_mb}")
        return True

    except Exception as exc:
        erro = str(exc)
        print(f"[ERRO][{database}][{tipo}] {erro}")

        novo_path = None
        if tipo in {"FULL", "DIFF", "LOG"}:
            novo_path = mover_para_invalid(backup_path, database)

        atualizar_backup(
            backup_id=backup_id,
            status="FAILED",
            verify_status="FAILED",
            file_size_mb=obter_tamanho_mb(novo_path or backup_path),
            error_message=erro,
            backup_path=novo_path,
        )

        return False


# =============================================================================
# PLANEJAMENTO DE EXECUÇÃO
# =============================================================================

def montar_plano() -> PlanoExecucao:
    ref = obter_data_referencia_airflow()

    hora_minuto = (ref.hour, ref.minute)
    eh_sexta_20 = ref.weekday() == FULL_WEEKDAY and hora_minuto == FULL_TIME
    eh_horario_diff = hora_minuto in DIFF_TIMES
    eh_dia_full_inicial = ref.date() == INITIAL_FULL_DATE

    plano = PlanoExecucao(
        run_ref_iso=ref.to_iso8601_string(),
        run_ref_local=ref.format("YYYY-MM-DD HH:mm:ss ZZ"),
        executar_full_programado=eh_sexta_20,
        executar_diff=eh_horario_diff,
        executar_log=True,  # A DAG roda a cada 30 min. LOG será filtrado por database FULL.
        executar_full_inicial_20260701=eh_dia_full_inicial,
    )

    print("[PLANO] Referência local:", plano.run_ref_local)
    print("[PLANO] executar_full_programado:", plano.executar_full_programado)
    print("[PLANO] executar_full_inicial_20260701:", plano.executar_full_inicial_20260701)
    print("[PLANO] executar_diff:", plano.executar_diff)
    print("[PLANO] executar_log:", plano.executar_log)

    return plano


# =============================================================================
# RETENÇÃO POR CADEIA
# =============================================================================

def listar_cadeias_completas(database: str, recovery_model: str) -> list[dict[str, Any]]:
    garantir_tabela_controle_existente()

    """
    Retorna cadeias completas e não deletadas.

    FULL:
        full válido + diff válido + log válido.
    SIMPLE:
        full válido + diff válido.
    """
    usa_log = recovery_model.upper() == "FULL"

    condicao_log = "AND QtdLogOk >= 1" if usa_log else ""

    sql = f"""
    WITH Itens AS (
        SELECT
            DatabaseName,
            ChainId,
            BackupType,
            BackupPath,
            BackupStartDate,
            Status,
            VerifyStatus,
            DeletedAt
        FROM {CONTROLE_TABLE_FULL}
        WHERE DatabaseName = ?
          AND DeletedAt IS NULL
    ),
    Cadeias AS (
        SELECT
            DatabaseName,
            ChainId,
            MIN(CASE WHEN BackupType = 'FULL' THEN BackupStartDate END) AS FullDate,
            SUM(CASE WHEN BackupType = 'FULL'
                      AND Status = 'SUCCESS'
                      AND VerifyStatus = 'SUCCESS'
                      AND DeletedAt IS NULL
                     THEN 1 ELSE 0 END) AS QtdFullOk,
            SUM(CASE WHEN BackupType = 'DIFF'
                      AND Status = 'SUCCESS'
                      AND VerifyStatus = 'SUCCESS'
                      AND DeletedAt IS NULL
                     THEN 1 ELSE 0 END) AS QtdDiffOk,
            SUM(CASE WHEN BackupType = 'LOG'
                      AND Status = 'SUCCESS'
                      AND VerifyStatus = 'SUCCESS'
                      AND DeletedAt IS NULL
                     THEN 1 ELSE 0 END) AS QtdLogOk
        FROM Itens
        GROUP BY DatabaseName, ChainId
    )
    SELECT
        DatabaseName,
        CONVERT(varchar(36), ChainId) AS ChainId,
        FullDate,
        QtdFullOk,
        QtdDiffOk,
        QtdLogOk
    FROM Cadeias
    WHERE QtdFullOk >= 1
      AND QtdDiffOk >= 1
      {condicao_log}
    ORDER BY FullDate DESC;
    """

    rows = consultar_sql(sql, [database], log_prefix=f"[RETENCAO][{database}]")
    return rows


def listar_arquivos_da_cadeia(database: str, chain_id: str) -> list[dict[str, Any]]:
    garantir_tabela_controle_existente()

    sql = f"""
    SELECT
        IDBackupControle,
        BackupType,
        BackupPath
    FROM {CONTROLE_TABLE_FULL}
    WHERE DatabaseName = ?
      AND ChainId = CONVERT(uniqueidentifier, ?)
      AND DeletedAt IS NULL
      AND Status IN ('SUCCESS', 'FAILED')
    ORDER BY
        CASE BackupType
            WHEN 'LOG' THEN 1
            WHEN 'DIFF' THEN 2
            WHEN 'FULL' THEN 3
            ELSE 9
        END,
        BackupStartDate ASC;
    """

    return consultar_sql(sql, [database, chain_id], log_prefix=f"[RETENCAO][{database}][{chain_id}]")


def apagar_arquivo(path: str) -> bool:
    try:
        if os.path.exists(path):
            os.remove(path)
            print(f"[RETENCAO] Arquivo apagado: {path}")
        else:
            print(f"[RETENCAO] Arquivo não existe localmente, marcando mesmo assim: {path}")
        return True
    except Exception as exc:
        print(f"[RETENCAO][ERRO] Falha ao apagar {path}: {exc}")
        return False


def aplicar_retencao_database(database: str, recovery_model: str) -> None:
    print("=" * 100)
    print(f"[RETENCAO][{database}] Avaliando cadeias completas.")
    print("=" * 100)

    cadeias = listar_cadeias_completas(database, recovery_model)

    if len(cadeias) <= 2:
        print(f"[RETENCAO][{database}] Cadeias completas={len(cadeias)}. Nada para apagar.")
        return

    manter = cadeias[:2]
    apagar = cadeias[2:]

    print(f"[RETENCAO][{database}] Mantendo cadeias:")
    for c in manter:
        print(f"  - ChainId={c['ChainId']} FullDate={c['FullDate']}")

    print(f"[RETENCAO][{database}] Apagando cadeias antigas:")
    for c in apagar:
        print(f"  - ChainId={c['ChainId']} FullDate={c['FullDate']}")

        arquivos = listar_arquivos_da_cadeia(database, c["ChainId"])
        ids_deletados: list[int] = []

        for item in arquivos:
            path = str(item["BackupPath"])
            backup_id = int(item["IDBackupControle"])

            if apagar_arquivo(path):
                ids_deletados.append(backup_id)

        marcar_deletado(ids_deletados)

    print(f"[RETENCAO][{database}] Retenção finalizada.")


# =============================================================================
# TAREFAS AIRFLOW
# =============================================================================

@task
def preparar_ambiente() -> dict[str, Any]:
    print("=" * 100)
    print("[PREPARAR] Iniciando preparação do ambiente.")
    print("=" * 100)

    garantir_pastas()
    criar_tabela_controle()

    for config in get_db_configs():
        ajustar_recovery_model(config.name, config.recovery_model)

    print("[PREPARAR] Ambiente preparado.")

    return {
        "base_backup_dir": BASE_BACKUP_DIR,
        "controle_table": CONTROLE_TABLE_FULL,
        "databases": [config.name for config in get_db_configs()],
    }


@task
def planejar_execucao(_ambiente: dict[str, Any]) -> dict[str, Any]:
    plano = montar_plano()
    return {
        "run_ref_iso": plano.run_ref_iso,
        "run_ref_local": plano.run_ref_local,
        "executar_full_programado": plano.executar_full_programado,
        "executar_diff": plano.executar_diff,
        "executar_log": plano.executar_log,
        "executar_full_inicial_20260701": plano.executar_full_inicial_20260701,
    }


@task
def executar_backups_full(plano_dict: dict[str, Any]) -> dict[str, Any]:
    ref = pendulum.parse(plano_dict["run_ref_iso"]).in_timezone(TZ)

    executar_full_programado = bool(plano_dict["executar_full_programado"])
    executar_full_inicial = bool(plano_dict["executar_full_inicial_20260701"])

    if not executar_full_programado and not executar_full_inicial:
        print("[FULL] Este horário não é de FULL. Pulando tarefa.")
        return {"executou": False, "sucessos": [], "falhas": []}

    sucessos: list[str] = []
    falhas: list[str] = []

    for config in get_db_configs():
        deve_executar = executar_full_programado

        if executar_full_inicial:
            # FULL inicial de 2026-07-01 só roda se ainda não existir FULL válido nesse dia.
            if existe_full_valido_na_data(config.name, INITIAL_FULL_DATE):
                print(f"[FULL][{config.name}] Já existe FULL válido em {INITIAL_FULL_DATE}. Pulando inicial.")
            else:
                deve_executar = True

        if not deve_executar:
            continue

        chain_id = str(uuid.uuid4())
        path = caminho_backup(config.name, "FULL", ref)

        ok = executar_backup_controlado(
            database=config.name,
            recovery_model=config.recovery_model,
            backup_type="FULL",
            backup_path=path,
            chain_id=chain_id,
            full_backup_path=path,
            run_ref=ref,
        )

        if ok:
            sucessos.append(config.name)
        else:
            falhas.append(config.name)

    print(f"[FULL] Sucessos={sucessos}")
    print(f"[FULL] Falhas={falhas}")

    return {
        "executou": bool(sucessos or falhas),
        "sucessos": sucessos,
        "falhas": falhas,
    }


@task
def executar_backups_log(plano_dict: dict[str, Any], _resultado_full: dict[str, Any]) -> dict[str, Any]:
    ref = pendulum.parse(plano_dict["run_ref_iso"]).in_timezone(TZ)

    if not bool(plano_dict["executar_log"]):
        print("[LOG] Plano não habilitou LOG. Pulando.")
        return {"executou": False, "sucessos": [], "falhas": [], "pulados": []}

    sucessos: list[str] = []
    falhas: list[str] = []
    pulados: list[str] = []

    for config in get_db_configs():
        if not config.usa_log:
            print(f"[LOG][{config.name}] Recovery SIMPLE. BACKUP LOG não se aplica.")
            pulados.append(config.name)
            continue

        cadeia = obter_ultima_cadeia_full_valida(config.name)
        if not cadeia:
            print(f"[LOG][{config.name}] Sem FULL válido. LOG será pulado.")
            pulados.append(config.name)
            continue

        chain_id = str(cadeia["ChainId"])
        full_path = str(cadeia["FullBackupPath"])
        path = caminho_backup(config.name, "LOG", ref)

        ok = executar_backup_controlado(
            database=config.name,
            recovery_model=config.recovery_model,
            backup_type="LOG",
            backup_path=path,
            chain_id=chain_id,
            full_backup_path=full_path,
            run_ref=ref,
        )

        if ok:
            sucessos.append(config.name)
        else:
            falhas.append(config.name)

    print(f"[LOG] Sucessos={sucessos}")
    print(f"[LOG] Falhas={falhas}")
    print(f"[LOG] Pulados={pulados}")

    return {
        "executou": bool(sucessos or falhas),
        "sucessos": sucessos,
        "falhas": falhas,
        "pulados": pulados,
    }


@task
def executar_backups_differential(plano_dict: dict[str, Any], _resultado_log: dict[str, Any]) -> dict[str, Any]:
    ref = pendulum.parse(plano_dict["run_ref_iso"]).in_timezone(TZ)

    if not bool(plano_dict["executar_diff"]):
        print("[DIFF] Este horário não é de DIFF. Pulando tarefa.")
        return {"executou": False, "sucessos": [], "falhas": [], "pulados": []}

    sucessos: list[str] = []
    falhas: list[str] = []
    pulados: list[str] = []

    for config in get_db_configs():
        cadeia = obter_ultima_cadeia_full_valida(config.name)
        if not cadeia:
            print(f"[DIFF][{config.name}] Sem FULL válido. DIFF será pulado.")
            pulados.append(config.name)
            continue

        chain_id = str(cadeia["ChainId"])
        full_path = str(cadeia["FullBackupPath"])
        path = caminho_backup(config.name, "DIFF", ref)

        ok = executar_backup_controlado(
            database=config.name,
            recovery_model=config.recovery_model,
            backup_type="DIFF",
            backup_path=path,
            chain_id=chain_id,
            full_backup_path=full_path,
            run_ref=ref,
        )

        if ok:
            sucessos.append(config.name)
        else:
            falhas.append(config.name)

    print(f"[DIFF] Sucessos={sucessos}")
    print(f"[DIFF] Falhas={falhas}")
    print(f"[DIFF] Pulados={pulados}")

    return {
        "executou": bool(sucessos or falhas),
        "sucessos": sucessos,
        "falhas": falhas,
        "pulados": pulados,
    }


@task
def executar_retencao(_resultado_diff: dict[str, Any]) -> dict[str, Any]:
    print("=" * 100)
    print("[RETENCAO] Iniciando retenção por cadeia.")
    print("=" * 100)

    for config in get_db_configs():
        aplicar_retencao_database(config.name, config.recovery_model)

    print("[RETENCAO] Finalizada.")

    return {"status": "ok"}



with DAG(
    dag_id=DAG_ID,
    description=(
        "Backups SQL Server em cadeia: FULL, DIFF e LOG, com retenção das 2 "
        "cadeias completas mais recentes por database."
    ),
    start_date=pendulum.datetime(2026, 7, 1, 0, 0, tz=TZ),
    schedule="*/30 * * * *",
    catchup=False,
    max_active_runs=1,
    tags=TAGS,
    doc_md=__doc__,
) as dag:
    ambiente = preparar_ambiente()
    plano = planejar_execucao(ambiente)

    resultado_full = executar_backups_full(plano)
    resultado_log = executar_backups_log(plano, resultado_full)
    resultado_diff = executar_backups_differential(plano, resultado_log)

    executar_retencao(resultado_diff)
