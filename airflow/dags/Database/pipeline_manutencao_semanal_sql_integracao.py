from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from math import ceil
from typing import Any

import pendulum
from airflow.decorators import dag, task
from airflow.exceptions import AirflowFailException

try:
    from hooks.BancodeDados.SqlServer import HookSqlServer
except ModuleNotFoundError:
    from plugins.hooks.BancodeDados.SqlServer import HookSqlServer


TIMEZONE_SAO_PAULO = pendulum.timezone("America/Sao_Paulo")

ID_CONEXAO_SQL_SERVER = "mssql_integracao"

BANCOS_ORDEM_PROCESSAMENTO = [
    "Integracao",
    "Kanban",
    "DataMining",
    "Shempo",
    "DataMart",
]

PASTA_BACKUP_FALLBACK = (
    r"C:\Program Files\Microsoft SQL Server\MSSQL17.SQLEXPRESS\MSSQL\Backup"
)

FILEGROWTH_MB = 256

ALVO_MINIMO_LOG_MB = 4096
ALVO_MINIMO_DADOS_MB = 24576

FATOR_FOLGA_DADOS = 1.35
FOLGA_FIXA_DADOS_MB = 4096

GATILHO_VOLUME_LIVRE_GB = 80.0

GATILHO_LOG_LIVRE_MB = 2048.0
GATILHO_LOG_LIVRE_PCT = 70.0

GATILHO_DADOS_LIVRE_MB = 10240.0
GATILHO_DADOS_LIVRE_PCT = 35.0

REDUCAO_MINIMA_LOG_MB = 512.0
REDUCAO_MINIMA_DADOS_MB = 1024.0

EXECUTAR_SHRINK_DADOS_AUTOMATICO = False


def normalizar_valor(valor: Any) -> Any:
    """Eu converto Decimal para float e preservo o restante."""
    if isinstance(valor, Decimal):
        return float(valor)
    return valor


def percentual(parte: float, total: float) -> float:
    """Eu calculo percentual com proteção para divisão por zero."""
    if total <= 0:
        return 0.0
    return (parte / total) * 100.0


def arredondar_para_bloco_superior(valor_mb: float, bloco_mb: int) -> int:
    """Eu arredondo para cima no múltiplo do bloco para evitar alvo quebrado."""
    return int(ceil(valor_mb / bloco_mb) * bloco_mb)


def escapar_literal_sql(texto: str) -> str:
    """Eu escapo aspas simples para montar literal SQL com segurança básica."""
    return texto.replace("'", "''")


def executar_select_pequeno(hook: HookSqlServer, sql: str) -> list[dict[str, Any]]:
    """Eu executo SELECT pequeno e normalizo os tipos para não carregar peso desnecessário."""
    linhas = hook.executar_select(sql)
    return [
        {chave: normalizar_valor(valor) for chave, valor in linha.items()}
        for linha in linhas
    ]


def executar_batch_autocommit(hook: HookSqlServer, comandos: list[str]) -> None:
    """Eu executo comandos pesados em autocommit para evitar problemas com BACKUP e DBCC."""
    conexao = hook.obter_conexao_dbapi()
    cursor = None

    try:
        try:
            conexao.autocommit = True
        except Exception:
            pass

        cursor = conexao.cursor()

        for comando in comandos:
            cursor.execute(comando)

            try:
                while cursor.nextset():
                    pass
            except Exception:
                pass

        try:
            conexao.commit()
        except Exception:
            pass
    finally:
        if cursor is not None:
            try:
                cursor.close()
            except Exception:
                pass

        try:
            conexao.close()
        except Exception:
            pass


def obter_pasta_backup(hook: HookSqlServer) -> str:
    """Eu descubro a pasta padrão de backup da instância e uso fallback se vier nula."""
    sql = """
    DECLARE @pasta_backup NVARCHAR(4000);

    SET @pasta_backup = CAST(SERVERPROPERTY('InstanceDefaultBackupPath') AS NVARCHAR(4000));

    IF @pasta_backup IS NULL
    BEGIN
        EXEC master.dbo.xp_instance_regread
            N'HKEY_LOCAL_MACHINE',
            N'SOFTWARE\\Microsoft\\MSSQLServer\\MSSQLServer',
            N'BackupDirectory',
            @pasta_backup OUTPUT;
    END

    SELECT @pasta_backup AS PastaBackup;
    """

    linhas = executar_select_pequeno(hook, sql)

    if linhas and linhas[0].get("PastaBackup"):
        pasta_backup = str(linhas[0]["PastaBackup"]).strip()
        if pasta_backup:
            return pasta_backup.rstrip("\\/")

    return PASTA_BACKUP_FALLBACK.rstrip("\\/")


def montar_caminho_backup(pasta_backup: str, nome_banco: str) -> str:
    """Eu monto um .bak fixo por banco para não acumular arquivos e lotar o SSD."""
    return f"{pasta_backup}\\{nome_banco}_antes_manutencao_semanal.bak"


def coletar_metricas_banco(hook: HookSqlServer, nome_banco: str) -> dict[str, Any]:
    """Eu leio arquivos, espaço reservado, espaço usado e recovery model da base atual."""
    sql_existe_banco = f"""
    SELECT
        name AS Banco
    FROM sys.databases
    WHERE name = N'{escapar_literal_sql(nome_banco)}';
    """

    banco_existe = executar_select_pequeno(hook, sql_existe_banco)

    if not banco_existe:
        raise AirflowFailException(f"A base {nome_banco} não existe na instância.")

    sql_arquivos_master = f"""
    USE [master];

    SELECT
        mf.file_id AS FileId,
        mf.name AS NomeArquivo,
        mf.type_desc AS TipoArquivo,
        mf.physical_name AS CaminhoFisico,
        CAST(mf.size * 8.0 / 1024 AS FLOAT) AS TamanhoArquivoMB,
        CASE
            WHEN mf.max_size = -1 THEN NULL
            ELSE CAST(mf.max_size * 8.0 / 1024 AS FLOAT)
        END AS TamanhoMaximoMB,
        CASE
            WHEN mf.is_percent_growth = 1 THEN NULL
            ELSE CAST(mf.growth * 8.0 / 1024 AS FLOAT)
        END AS CrescimentoAutomaticoMB,
        mf.is_percent_growth AS CrescimentoEhPercentual,
        CAST(vs.total_bytes / 1024.0 / 1024 / 1024 AS FLOAT) AS VolumeTotalGB,
        CAST(vs.available_bytes / 1024.0 / 1024 / 1024 AS FLOAT) AS VolumeLivreGB
    FROM sys.master_files mf
    CROSS APPLY sys.dm_os_volume_stats(mf.database_id, mf.file_id) vs
    WHERE DB_NAME(mf.database_id) = N'{escapar_literal_sql(nome_banco)}'
    ORDER BY mf.file_id;
    """

    sql_arquivos_banco = f"""
    USE [{nome_banco}];

    SELECT
        file_id AS FileId,
        name AS NomeArquivo,
        type_desc AS TipoArquivo,
        physical_name AS CaminhoFisico,
        CAST(size * 8.0 / 1024 AS FLOAT) AS TamanhoTotalMB,
        CAST(FILEPROPERTY(name, 'SpaceUsed') * 8.0 / 1024 AS FLOAT) AS EspacoUsadoMB,
        CAST((size - FILEPROPERTY(name, 'SpaceUsed')) * 8.0 / 1024 AS FLOAT) AS EspacoLivreMB
    FROM sys.database_files
    ORDER BY file_id;
    """

    sql_recuperacao = f"""
    SELECT
        name AS Banco,
        recovery_model_desc AS ModeloRecuperacao,
        log_reuse_wait_desc AS MotivoEsperaLog
    FROM sys.databases
    WHERE name = N'{escapar_literal_sql(nome_banco)}';
    """

    linhas_master = executar_select_pequeno(hook, sql_arquivos_master)
    linhas_banco = executar_select_pequeno(hook, sql_arquivos_banco)
    linhas_recuperacao = executar_select_pequeno(hook, sql_recuperacao)

    if not linhas_master:
        raise AirflowFailException(f"Não encontrei arquivos no master para a base {nome_banco}.")

    if not linhas_banco:
        raise AirflowFailException(f"Não encontrei arquivos em sys.database_files para a base {nome_banco}.")

    if not linhas_recuperacao:
        raise AirflowFailException(f"Não encontrei recovery model para a base {nome_banco}.")

    arquivos_master_por_id = {linha["FileId"]: linha for linha in linhas_master}

    arquivos_mesclados: list[dict[str, Any]] = []
    for linha_banco in linhas_banco:
        file_id = linha_banco["FileId"]
        linha_master = arquivos_master_por_id.get(file_id)

        if linha_master is None:
            continue

        arquivo_mesclado = {
            **linha_banco,
            "VolumeTotalGB": linha_master["VolumeTotalGB"],
            "VolumeLivreGB": linha_master["VolumeLivreGB"],
            "TamanhoArquivoMasterMB": linha_master["TamanhoArquivoMB"],
            "TamanhoMaximoMB": linha_master["TamanhoMaximoMB"],
            "CrescimentoAutomaticoMB": linha_master["CrescimentoAutomaticoMB"],
            "CrescimentoEhPercentual": linha_master["CrescimentoEhPercentual"],
        }
        arquivos_mesclados.append(arquivo_mesclado)

    if not arquivos_mesclados:
        raise AirflowFailException(f"Falha ao mesclar arquivos da base {nome_banco}.")

    arquivos_dados = [a for a in arquivos_mesclados if a["TipoArquivo"] == "ROWS"]
    arquivos_log = [a for a in arquivos_mesclados if a["TipoArquivo"] == "LOG"]

    if not arquivos_dados:
        raise AirflowFailException(f"A base {nome_banco} não possui arquivo ROWS.")
    if not arquivos_log:
        raise AirflowFailException(f"A base {nome_banco} não possui arquivo LOG.")

    arquivo_dados_principal = sorted(arquivos_dados, key=lambda x: x["FileId"])[0]
    arquivo_log_principal = sorted(arquivos_log, key=lambda x: x["FileId"])[0]

    total_dados_mb = sum(float(a["TamanhoTotalMB"]) for a in arquivos_dados)
    usado_dados_mb = sum(float(a["EspacoUsadoMB"]) for a in arquivos_dados)
    livre_dados_mb = sum(float(a["EspacoLivreMB"]) for a in arquivos_dados)

    total_log_mb = sum(float(a["TamanhoTotalMB"]) for a in arquivos_log)
    usado_log_mb = sum(float(a["EspacoUsadoMB"]) for a in arquivos_log)
    livre_log_mb = sum(float(a["EspacoLivreMB"]) for a in arquivos_log)

    volume_livre_critico_gb = min(float(a["VolumeLivreGB"]) for a in arquivos_mesclados)

    return {
        "banco": nome_banco,
        "recuperacao": linhas_recuperacao[0],
        "arquivos": arquivos_mesclados,
        "arquivos_dados": arquivos_dados,
        "arquivos_log": arquivos_log,
        "arquivo_dados_principal": arquivo_dados_principal,
        "arquivo_log_principal": arquivo_log_principal,
        "resumo": {
            "total_dados_mb": total_dados_mb,
            "usado_dados_mb": usado_dados_mb,
            "livre_dados_mb": livre_dados_mb,
            "livre_dados_pct": round(percentual(livre_dados_mb, total_dados_mb), 2),
            "total_log_mb": total_log_mb,
            "usado_log_mb": usado_log_mb,
            "livre_log_mb": livre_log_mb,
            "livre_log_pct": round(percentual(livre_log_mb, total_log_mb), 2),
            "volume_livre_critico_gb": round(volume_livre_critico_gb, 2),
            "quantidade_arquivos_dados": len(arquivos_dados),
            "quantidade_arquivos_log": len(arquivos_log),
        },
    }


def decidir_acoes_banco(metricas: dict[str, Any]) -> dict[str, Any]:
    """Eu transformo as métricas em decisão objetiva e conservadora."""
    banco = metricas["banco"]
    recuperacao = metricas["recuperacao"]
    resumo = metricas["resumo"]

    total_dados_mb = float(resumo["total_dados_mb"])
    usado_dados_mb = float(resumo["usado_dados_mb"])
    livre_dados_mb = float(resumo["livre_dados_mb"])
    livre_dados_pct = float(resumo["livre_dados_pct"])

    total_log_mb = float(resumo["total_log_mb"])
    usado_log_mb = float(resumo["usado_log_mb"])
    livre_log_mb = float(resumo["livre_log_mb"])
    livre_log_pct = float(resumo["livre_log_pct"])

    volume_livre_critico_gb = float(resumo["volume_livre_critico_gb"])

    quantidade_arquivos_dados = int(resumo["quantidade_arquivos_dados"])
    quantidade_arquivos_log = int(resumo["quantidade_arquivos_log"])

    alvo_dados_por_fator = usado_dados_mb * FATOR_FOLGA_DADOS
    alvo_dados_por_folga = usado_dados_mb + FOLGA_FIXA_DADOS_MB
    alvo_dados_mb = max(
        ALVO_MINIMO_DADOS_MB,
        alvo_dados_por_fator,
        alvo_dados_por_folga,
    )
    alvo_dados_mb = arredondar_para_bloco_superior(alvo_dados_mb, FILEGROWTH_MB)

    alvo_log_mb = arredondar_para_bloco_superior(ALVO_MINIMO_LOG_MB, FILEGROWTH_MB)

    motivo_bloqueio_log = None
    motivo_bloqueio_dados = None

    if quantidade_arquivos_log != 1:
        motivo_bloqueio_log = (
            f"{banco}: shrink automático de LOG bloqueado porque há "
            f"{quantidade_arquivos_log} arquivos LOG."
        )

    if quantidade_arquivos_dados != 1:
        motivo_bloqueio_dados = (
            f"{banco}: shrink automático de DADOS bloqueado porque há "
            f"{quantidade_arquivos_dados} arquivos ROWS."
        )

    reduzir_log = (
        motivo_bloqueio_log is None
        and recuperacao["ModeloRecuperacao"] == "SIMPLE"
        and recuperacao["MotivoEsperaLog"] == "NOTHING"
        and livre_log_mb >= GATILHO_LOG_LIVRE_MB
        and livre_log_pct >= GATILHO_LOG_LIVRE_PCT
        and (total_log_mb - alvo_log_mb) >= REDUCAO_MINIMA_LOG_MB
    )

    reduzir_dados = (
        EXECUTAR_SHRINK_DADOS_AUTOMATICO
        and motivo_bloqueio_dados is None
        and volume_livre_critico_gb <= GATILHO_VOLUME_LIVRE_GB
        and livre_dados_mb >= GATILHO_DADOS_LIVRE_MB
        and livre_dados_pct >= GATILHO_DADOS_LIVRE_PCT
        and (total_dados_mb - alvo_dados_mb) >= REDUCAO_MINIMA_DADOS_MB
    )

    precisa_backup = reduzir_log or reduzir_dados

    return {
        "banco": banco,
        "precisa_backup": precisa_backup,
        "reduzir_log": reduzir_log,
        "reduzir_dados": reduzir_dados,
        "ajustar_filegrowth": True,
        "alvo_log_mb": int(alvo_log_mb),
        "alvo_dados_mb": int(alvo_dados_mb),
        "motivo_bloqueio_log": motivo_bloqueio_log,
        "motivo_bloqueio_dados": motivo_bloqueio_dados,
        "resumo": {
            "modelo_recuperacao": recuperacao["ModeloRecuperacao"],
            "motivo_espera_log": recuperacao["MotivoEsperaLog"],
            "total_dados_mb": round(total_dados_mb, 2),
            "usado_dados_mb": round(usado_dados_mb, 2),
            "livre_dados_mb": round(livre_dados_mb, 2),
            "livre_dados_pct": round(livre_dados_pct, 2),
            "total_log_mb": round(total_log_mb, 2),
            "usado_log_mb": round(usado_log_mb, 2),
            "livre_log_mb": round(livre_log_mb, 2),
            "livre_log_pct": round(livre_log_pct, 2),
            "volume_livre_critico_gb": round(volume_livre_critico_gb, 2),
            "alvo_log_mb": int(alvo_log_mb),
            "alvo_dados_mb": int(alvo_dados_mb),
        },
    }


def executar_backup_banco(hook: HookSqlServer, nome_banco: str, caminho_backup: str) -> str:
    """Eu faço backup COPY_ONLY sem compressão porque a instância é Express."""
    sql = (
        f"BACKUP DATABASE [{nome_banco}] "
        f"TO DISK = N'{escapar_literal_sql(caminho_backup)}' "
        f"WITH COPY_ONLY, INIT, CHECKSUM, STATS = 10;"
    )
    executar_batch_autocommit(hook, [sql])
    return caminho_backup


def executar_shrink_log_banco(
    hook: HookSqlServer,
    nome_banco: str,
    nome_arquivo_log: str,
    alvo_log_mb: int,
) -> None:
    """Eu reduzo o log apenas quando a sobra reservada está realmente alta."""
    executar_batch_autocommit(
        hook,
        [
            f"USE [{nome_banco}];",
            "CHECKPOINT;",
            f"DBCC SHRINKFILE (N'{escapar_literal_sql(nome_arquivo_log)}', {alvo_log_mb});",
        ],
    )


def executar_shrink_dados_banco(
    hook: HookSqlServer,
    nome_banco: str,
    nome_arquivo_dados: str,
    alvo_dados_mb: int,
) -> None:
    """Eu reduzo o arquivo de dados só quando a regra realmente autoriza."""
    executar_batch_autocommit(
        hook,
        [
            f"USE [{nome_banco}];",
            f"DBCC SHRINKFILE (N'{escapar_literal_sql(nome_arquivo_dados)}', {alvo_dados_mb});",
        ],
    )


def ajustar_filegrowth_banco(hook: HookSqlServer, nome_banco: str, arquivos: list[dict[str, Any]]) -> None:
    """Eu padronizo o crescimento em todos os arquivos para evitar autogrowth picado."""
    comandos: list[str] = []

    for arquivo in arquivos:
        nome_arquivo = str(arquivo["NomeArquivo"])
        comandos.append(
            f"ALTER DATABASE [{nome_banco}] "
            f"MODIFY FILE (NAME = N'{escapar_literal_sql(nome_arquivo)}', FILEGROWTH = {FILEGROWTH_MB}MB);"
        )

    if comandos:
        executar_batch_autocommit(hook, comandos)


@dag(
    dag_id="manutencao_semanal_sql_bases",
    schedule="0 13 * * 5",
    start_date=pendulum.datetime(2026, 4, 24, 13, 0, tz="America/Sao_Paulo"),
    catchup=False,
    max_active_runs=1,
    max_active_tasks=1,
    default_args={
        "owner": "guilherme",
        "retries": 0,
    },
    tags=["sqlserver", "backup", "shrink", "manutencao", "semanal"],
)
def criar_dag_manutencao_semanal_sql_bases():
    @task()
    def processar_bancos_sequencialmente() -> dict[str, Any]:
        """Eu processo uma base por vez para reduzir pressão de memória e de I/O."""
        hook = HookSqlServer(conn_id=ID_CONEXAO_SQL_SERVER)
        pasta_backup = obter_pasta_backup(hook)

        print(f"Pasta de backup detectada: {pasta_backup}")
        print(f"Time zone da DAG: {TIMEZONE_SAO_PAULO.name}")
        print(f"Ordem de processamento: {BANCOS_ORDEM_PROCESSAMENTO}")

        total_processados = 0
        total_backup = 0
        total_shrink_log = 0
        total_shrink_dados = 0
        total_erros = 0

        resumo_final: list[dict[str, Any]] = []
        erros: list[str] = []

        for nome_banco in BANCOS_ORDEM_PROCESSAMENTO:
            print("=" * 120)
            print(f"Iniciando manutenção da base: {nome_banco}")

            try:
                metricas_antes = coletar_metricas_banco(hook, nome_banco)
                decisoes = decidir_acoes_banco(metricas_antes)

                print(f"{nome_banco} | Resumo antes: {decisoes['resumo']}")

                if decisoes["motivo_bloqueio_log"]:
                    print(decisoes["motivo_bloqueio_log"])

                if decisoes["motivo_bloqueio_dados"]:
                    print(decisoes["motivo_bloqueio_dados"])

                caminho_backup = montar_caminho_backup(pasta_backup, nome_banco)

                backup_executado = False
                shrink_log_executado = False
                shrink_dados_executado = False

                if decisoes["precisa_backup"]:
                    print(f"{nome_banco} | Executando backup em: {caminho_backup}")
                    executar_backup_banco(hook, nome_banco, caminho_backup)
                    backup_executado = True
                    total_backup += 1
                else:
                    print(f"{nome_banco} | Backup não necessário nesta execução.")

                if decisoes["reduzir_log"]:
                    nome_arquivo_log = str(metricas_antes["arquivo_log_principal"]["NomeArquivo"])
                    alvo_log_mb = int(decisoes["alvo_log_mb"])

                    print(
                        f"{nome_banco} | Executando shrink do log "
                        f"| arquivo={nome_arquivo_log} | alvo_mb={alvo_log_mb}"
                    )
                    executar_shrink_log_banco(hook, nome_banco, nome_arquivo_log, alvo_log_mb)
                    shrink_log_executado = True
                    total_shrink_log += 1
                else:
                    print(f"{nome_banco} | Shrink do log não necessário nesta execução.")

                if decisoes["reduzir_dados"]:
                    nome_arquivo_dados = str(metricas_antes["arquivo_dados_principal"]["NomeArquivo"])
                    alvo_dados_mb = int(decisoes["alvo_dados_mb"])

                    print(
                        f"{nome_banco} | Executando shrink do arquivo de dados "
                        f"| arquivo={nome_arquivo_dados} | alvo_mb={alvo_dados_mb}"
                    )
                    executar_shrink_dados_banco(
                        hook,
                        nome_banco,
                        nome_arquivo_dados,
                        alvo_dados_mb,
                    )
                    shrink_dados_executado = True
                    total_shrink_dados += 1
                else:
                    print(f"{nome_banco} | Shrink do arquivo de dados não necessário nesta execução.")

                print(f"{nome_banco} | Ajustando FILEGROWTH para {FILEGROWTH_MB} MB.")
                ajustar_filegrowth_banco(hook, nome_banco, metricas_antes["arquivos"])

                metricas_depois = coletar_metricas_banco(hook, nome_banco)

                resumo_banco = {
                    "banco": nome_banco,
                    "backup_executado": backup_executado,
                    "shrink_log_executado": shrink_log_executado,
                    "shrink_dados_executado": shrink_dados_executado,
                    "antes": metricas_antes["resumo"],
                    "depois": metricas_depois["resumo"],
                }

                print(f"{nome_banco} | Resumo final: {resumo_banco}")
                resumo_final.append(resumo_banco)
                total_processados += 1

            except Exception as erro:
                total_erros += 1
                mensagem_erro = f"{nome_banco} | Erro: {erro}"
                print(mensagem_erro)
                erros.append(mensagem_erro)

        print("=" * 120)
        print("Resumo consolidado da execução:")
        print(
            {
                "total_processados": total_processados,
                "total_backup": total_backup,
                "total_shrink_log": total_shrink_log,
                "total_shrink_dados": total_shrink_dados,
                "total_erros": total_erros,
            }
        )

        for item in resumo_final:
            print(item)

        if erros:
            print("Erros encontrados na execução:")
            for erro in erros:
                print(erro)

            raise AirflowFailException(
                "A DAG terminou com erro em uma ou mais bases. "
                "Verifique os logs detalhados da task."
            )

        return {
            "total_processados": total_processados,
            "total_backup": total_backup,
            "total_shrink_log": total_shrink_log,
            "total_shrink_dados": total_shrink_dados,
            "total_erros": total_erros,
        }

    processar_bancos_sequencialmente()


dag = criar_dag_manutencao_semanal_sql_bases()