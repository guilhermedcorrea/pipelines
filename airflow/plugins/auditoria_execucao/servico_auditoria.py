from __future__ import annotations

import json
import re
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import text

from auditoria_execucao.schemas import EventoDagPersistencia, EventoTaskPersistencia
from hooks.BancodeDados.SqlServer import HookSqlServer


_lock_estrutura = threading.Lock()
_estrutura_garantida = False


class ServicoAuditoriaExecucao:
    """Serviço central para persistência e leitura dos dados de auditoria."""

    conn_id_sql = "mssql_integracao"

    _regex_nome_objeto_sql = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

    _regex_tabela_sem_colchetes = re.compile(
        r"^\s*(?:(?P<banco>[A-Za-z_][A-Za-z0-9_]*)\.)?(?P<schema>[A-Za-z_][A-Za-z0-9_]*)\.(?P<tabela>[A-Za-z_][A-Za-z0-9_]*)\s*$"
    )

    _regex_tabela_com_colchetes = re.compile(
        r"^\s*(?:\[(?P<banco>[^\[\]]+)\]\.)?\[(?P<schema>[^\[\]]+)\]\.\[(?P<tabela>[^\[\]]+)\]\s*$"
    )

    _extensoes_download_permitidas = {
        ".xlsx",
        ".xls",
        ".csv",
        ".parquet",
        ".json",
        ".txt",
        ".zip",
    }

    _separadores_multiplos_recursos = [
        " + ",
        " | ",
        " ; ",
        " , ",
    ]

    @classmethod
    def obter_engine(cls):
        """Eu obtenho a engine SQLAlchemy reutilizando o hook padrão do projeto."""
        hook_sql_server = HookSqlServer(conn_id=cls.conn_id_sql)
        return hook_sql_server.obter_engine()

    @classmethod
    def garantir_estrutura(cls) -> None:
        """Eu crio as tabelas necessárias uma única vez por processo."""
        global _estrutura_garantida

        if _estrutura_garantida:
            return

        with _lock_estrutura:
            if _estrutura_garantida:
                return

            sql = """
            IF OBJECT_ID('dbo.airflow_auditoria_dag_run', 'U') IS NULL
            BEGIN
                CREATE TABLE dbo.airflow_auditoria_dag_run (
                    id bigint IDENTITY(1,1) PRIMARY KEY,
                    dag_id nvarchar(300) NOT NULL,
                    run_id nvarchar(300) NOT NULL,
                    status nvarchar(50) NOT NULL,
                    run_type nvarchar(100) NULL,
                    queued_at datetime2 NULL,
                    start_date datetime2 NULL,
                    end_date datetime2 NULL,
                    duracao_segundos decimal(18,3) NULL,
                    mensagem_resumo nvarchar(max) NULL,
                    criado_em datetime2 NOT NULL CONSTRAINT DF_airflow_auditoria_dag_criado_em DEFAULT SYSUTCDATETIME(),
                    atualizado_em datetime2 NOT NULL CONSTRAINT DF_airflow_auditoria_dag_atualizado_em DEFAULT SYSUTCDATETIME(),
                    CONSTRAINT UQ_airflow_auditoria_dag UNIQUE (dag_id, run_id)
                );

                CREATE INDEX IX_airflow_auditoria_dag_01
                ON dbo.airflow_auditoria_dag_run (dag_id, criado_em DESC);
            END;

            IF OBJECT_ID('dbo.airflow_auditoria_task_run', 'U') IS NULL
            BEGIN
                CREATE TABLE dbo.airflow_auditoria_task_run (
                    id bigint IDENTITY(1,1) PRIMARY KEY,
                    dag_id nvarchar(300) NOT NULL,
                    run_id nvarchar(300) NOT NULL,
                    task_id nvarchar(300) NOT NULL,
                    try_number int NOT NULL,
                    status nvarchar(50) NOT NULL,
                    operator nvarchar(200) NULL,
                    start_date datetime2 NULL,
                    end_date datetime2 NULL,
                    duracao_segundos decimal(18,3) NULL,
                    nome_amigavel nvarchar(300) NULL,
                    descricao_etapa nvarchar(max) NULL,
                    origem_dados nvarchar(1000) NULL,
                    destino_dados nvarchar(1000) NULL,
                    linhas_lidas bigint NULL,
                    linhas_inseridas bigint NULL,
                    linhas_atualizadas bigint NULL,
                    linhas_descartadas bigint NULL,
                    validacoes_json nvarchar(max) NULL,
                    amostra_json nvarchar(max) NULL,
                    metricas_json nvarchar(max) NULL,
                    observacoes_json nvarchar(max) NULL,
                    erro_tecnico nvarchar(max) NULL,
                    erro_traduzido nvarchar(max) NULL,
                    causa_provavel nvarchar(max) NULL,
                    acao_sugerida nvarchar(max) NULL,
                    host_execucao nvarchar(255) NULL,
                    criado_em datetime2 NOT NULL CONSTRAINT DF_airflow_auditoria_task_criado_em DEFAULT SYSUTCDATETIME(),
                    atualizado_em datetime2 NOT NULL CONSTRAINT DF_airflow_auditoria_task_atualizado_em DEFAULT SYSUTCDATETIME(),
                    CONSTRAINT UQ_airflow_auditoria_task UNIQUE (dag_id, run_id, task_id, try_number)
                );

                CREATE INDEX IX_airflow_auditoria_task_01
                ON dbo.airflow_auditoria_task_run (dag_id, run_id, status, criado_em DESC);

                CREATE INDEX IX_airflow_auditoria_task_02
                ON dbo.airflow_auditoria_task_run (task_id, criado_em DESC);
            END;
            """

            engine = cls.obter_engine()

            try:
                with engine.begin() as conexao:
                    conexao.execute(text(sql))
            finally:
                engine.dispose()

            _estrutura_garantida = True

    @staticmethod
    def _para_datetime(valor: str | None):
        """Eu converto valor ISO para datetime quando possível."""
        if not valor:
            return None

        try:
            return datetime.fromisoformat(valor.replace("Z", "+00:00")).replace(tzinfo=None)
        except Exception:
            return None

    @classmethod
    def _validar_identificador_sql(cls, valor: str | None) -> str | None:
        """
        Eu valido identificadores sem colchetes quando eles seguem o padrão simples
        letra/underscore + letras/números/underscore.
        """
        if valor is None:
            return None

        valor_limpo = valor.strip()
        if not valor_limpo:
            return None

        if not cls._regex_nome_objeto_sql.fullmatch(valor_limpo):
            return None

        return valor_limpo

    @classmethod
    def _extrair_referencia_tabela(
        cls,
        valor: str | None,
    ) -> tuple[str | None, str, str, str] | None:
        """
        Eu interpreto referências de tabela nestes formatos:
        - schema.tabela
        - banco.schema.tabela
        - [schema].[tabela]
        - [banco].[schema].[tabela]
        """
        if not valor or not isinstance(valor, str):
            return None

        texto = valor.strip()
        if not texto:
            return None

        correspondencia_sem_colchetes = cls._regex_tabela_sem_colchetes.match(texto)
        if correspondencia_sem_colchetes:
            banco = correspondencia_sem_colchetes.group("banco")
            schema = correspondencia_sem_colchetes.group("schema")
            tabela = correspondencia_sem_colchetes.group("tabela")

            banco_validado = cls._validar_identificador_sql(banco) if banco else None
            schema_validado = cls._validar_identificador_sql(schema)
            tabela_validada = cls._validar_identificador_sql(tabela)

            if not schema_validado or not tabela_validada:
                return None

            if banco and not banco_validado:
                return None

            if banco_validado:
                texto_normalizado = f"{banco_validado}.{schema_validado}.{tabela_validada}"
            else:
                texto_normalizado = f"{schema_validado}.{tabela_validada}"

            return banco_validado, schema_validado, tabela_validada, texto_normalizado

        correspondencia_com_colchetes = cls._regex_tabela_com_colchetes.match(texto)
        if correspondencia_com_colchetes:
            banco = correspondencia_com_colchetes.group("banco")
            schema = correspondencia_com_colchetes.group("schema")
            tabela = correspondencia_com_colchetes.group("tabela")

            banco_limpo = banco.strip() if banco else None
            schema_limpo = schema.strip() if schema else ""
            tabela_limpa = tabela.strip() if tabela else ""

            if not schema_limpo or not tabela_limpa:
                return None

            if banco_limpo:
                texto_normalizado = f"[{banco_limpo}].[{schema_limpo}].[{tabela_limpa}]"
            else:
                texto_normalizado = f"[{schema_limpo}].[{tabela_limpa}]"

            return banco_limpo, schema_limpo, tabela_limpa, texto_normalizado

        return None

    @classmethod
    def _montar_referencia_tabela(cls, valor: str | None) -> dict[str, Any] | None:
        """
        Eu transformo o texto em metadado estruturado quando ele representa uma tabela SQL.
        """
        referencia = cls._extrair_referencia_tabela(valor)
        if not referencia:
            return None

        banco, schema, tabela, texto = referencia

        return {
            "texto": texto,
            "tipo": "tabela_sql",
            "conexao_id": cls.conn_id_sql,
            "banco": banco,
            "schema": schema,
            "tabela": tabela,
            "preview_habilitado": True,
        }

    @classmethod
    def _montar_referencia_arquivo(cls, valor: str | None) -> dict[str, Any] | None:
        """
        Eu detecto caminhos de arquivo para permitir download.
        """
        if not valor or not isinstance(valor, str):
            return None

        texto = valor.strip()
        if not texto:
            return None

        caminho = Path(texto)
        sufixo = caminho.suffix.lower()

        if sufixo not in cls._extensoes_download_permitidas:
            return None

        return {
            "texto": texto,
            "tipo": "arquivo",
            "caminho": texto,
            "nome_arquivo": caminho.name,
            "download_habilitado": True,
            "extensao": sufixo,
        }

    @classmethod
    def _montar_recurso_textual(cls, valor: str | None) -> dict[str, Any] | None:
        """
        Eu represento texto simples quando ele não for nem tabela nem arquivo.
        """
        if not valor or not isinstance(valor, str):
            return None

        texto = valor.strip()
        if not texto:
            return None

        return {
            "texto": texto,
            "tipo": "texto",
        }

    @classmethod
    def _separar_multiplos_recursos(cls, valor: str | None) -> list[str]:
        """
        Eu separo múltiplos recursos de um mesmo campo.

        Estratégia:
        - começo com o texto inteiro
        - aplico separadores conhecidos como ' + ', ' | ', ' ; ', ' , '
        - preservo apenas partes não vazias
        """
        if not valor or not isinstance(valor, str):
            return []

        partes = [valor.strip()]

        for separador in cls._separadores_multiplos_recursos:
            novas_partes: list[str] = []

            for parte in partes:
                if separador in parte:
                    quebradas = [item.strip() for item in parte.split(separador)]
                    novas_partes.extend(item for item in quebradas if item)
                else:
                    novas_partes.append(parte)

            partes = novas_partes

        return [parte for parte in partes if parte]

    @classmethod
    def _montar_recurso_unico(cls, valor: str | None) -> dict[str, Any] | None:
        """
        Eu tento interpretar um único pedaço de texto como:
        1) tabela SQL
        2) arquivo
        3) texto simples
        """
        recurso_tabela = cls._montar_referencia_tabela(valor)
        if recurso_tabela:
            return recurso_tabela

        recurso_arquivo = cls._montar_referencia_arquivo(valor)
        if recurso_arquivo:
            return recurso_arquivo

        return cls._montar_recurso_textual(valor)

    @classmethod
    def _montar_multiplos_recursos(cls, valor: str | None) -> list[dict[str, Any]]:
        """
        Eu converto o texto do campo em uma lista de recursos.
        """
        partes = cls._separar_multiplos_recursos(valor)
        recursos: list[dict[str, Any]] = []

        for parte in partes:
            recurso = cls._montar_recurso_unico(parte)
            if recurso:
                recursos.append(recurso)

        if not recursos and valor:
            recurso_textual = cls._montar_recurso_textual(valor)
            if recurso_textual:
                recursos.append(recurso_textual)

        return recursos

    @staticmethod
    def _carregar_json_seguro(valor: Any, valor_padrao: Any):
        """Eu converto JSON textual para objeto Python sem quebrar a página."""
        if valor in (None, "", b""):
            return valor_padrao

        if isinstance(valor, (list, dict)):
            return valor

        try:
            return json.loads(valor)
        except Exception:
            return valor_padrao

    @staticmethod
    def _primeiro_recurso_por_tipo(
        recursos: list[dict[str, Any]],
        tipo: str,
    ) -> dict[str, Any] | None:
        """
        Eu devolvo o primeiro recurso de um determinado tipo.
        """
        for recurso in recursos:
            if recurso.get("tipo") == tipo:
                return recurso
        return None

    @classmethod
    def registrar_dag_run(cls, evento: EventoDagPersistencia) -> None:
        """Eu insiro ou atualizo o status de uma execução de DAG."""
        cls.garantir_estrutura()

        sql = text(
            """
            MERGE dbo.airflow_auditoria_dag_run AS destino
            USING (
                SELECT
                    :dag_id AS dag_id,
                    :run_id AS run_id,
                    :status AS status,
                    :run_type AS run_type,
                    :queued_at AS queued_at,
                    :start_date AS start_date,
                    :end_date AS end_date,
                    :duracao_segundos AS duracao_segundos,
                    :mensagem_resumo AS mensagem_resumo
            ) AS origem
            ON destino.dag_id = origem.dag_id
               AND destino.run_id = origem.run_id
            WHEN MATCHED THEN
                UPDATE SET
                    status = origem.status,
                    run_type = origem.run_type,
                    queued_at = origem.queued_at,
                    start_date = origem.start_date,
                    end_date = origem.end_date,
                    duracao_segundos = origem.duracao_segundos,
                    mensagem_resumo = origem.mensagem_resumo,
                    atualizado_em = SYSUTCDATETIME()
            WHEN NOT MATCHED THEN
                INSERT (
                    dag_id, run_id, status, run_type, queued_at, start_date, end_date,
                    duracao_segundos, mensagem_resumo
                )
                VALUES (
                    origem.dag_id, origem.run_id, origem.status, origem.run_type, origem.queued_at,
                    origem.start_date, origem.end_date, origem.duracao_segundos, origem.mensagem_resumo
                );
            """
        )

        parametros = {
            "dag_id": evento.dag_id,
            "run_id": evento.run_id,
            "status": evento.status,
            "run_type": evento.run_type,
            "queued_at": cls._para_datetime(evento.queued_at),
            "start_date": cls._para_datetime(evento.start_date),
            "end_date": cls._para_datetime(evento.end_date),
            "duracao_segundos": evento.duracao_segundos,
            "mensagem_resumo": evento.mensagem_resumo,
        }

        engine = cls.obter_engine()

        try:
            with engine.begin() as conexao:
                conexao.execute(sql, parametros)
        finally:
            engine.dispose()

    @classmethod
    def registrar_task_run(cls, evento: EventoTaskPersistencia) -> None:
        """Eu insiro ou atualizo o status de uma execução de task."""
        cls.garantir_estrutura()

        sql = text(
            """
            MERGE dbo.airflow_auditoria_task_run AS destino
            USING (
                SELECT
                    :dag_id AS dag_id,
                    :run_id AS run_id,
                    :task_id AS task_id,
                    :try_number AS try_number,
                    :status AS status,
                    :operator AS operator,
                    :start_date AS start_date,
                    :end_date AS end_date,
                    :duracao_segundos AS duracao_segundos,
                    :nome_amigavel AS nome_amigavel,
                    :descricao_etapa AS descricao_etapa,
                    :origem_dados AS origem_dados,
                    :destino_dados AS destino_dados,
                    :linhas_lidas AS linhas_lidas,
                    :linhas_inseridas AS linhas_inseridas,
                    :linhas_atualizadas AS linhas_atualizadas,
                    :linhas_descartadas AS linhas_descartadas,
                    :validacoes_json AS validacoes_json,
                    :amostra_json AS amostra_json,
                    :metricas_json AS metricas_json,
                    :observacoes_json AS observacoes_json,
                    :erro_tecnico AS erro_tecnico,
                    :erro_traduzido AS erro_traduzido,
                    :causa_provavel AS causa_provavel,
                    :acao_sugerida AS acao_sugerida,
                    :host_execucao AS host_execucao
            ) AS origem
            ON destino.dag_id = origem.dag_id
               AND destino.run_id = origem.run_id
               AND destino.task_id = origem.task_id
               AND destino.try_number = origem.try_number
            WHEN MATCHED THEN
                UPDATE SET
                    status = origem.status,
                    operator = origem.operator,
                    start_date = origem.start_date,
                    end_date = origem.end_date,
                    duracao_segundos = origem.duracao_segundos,
                    nome_amigavel = origem.nome_amigavel,
                    descricao_etapa = origem.descricao_etapa,
                    origem_dados = origem.origem_dados,
                    destino_dados = origem.destino_dados,
                    linhas_lidas = origem.linhas_lidas,
                    linhas_inseridas = origem.linhas_inseridas,
                    linhas_atualizadas = origem.linhas_atualizadas,
                    linhas_descartadas = origem.linhas_descartadas,
                    validacoes_json = origem.validacoes_json,
                    amostra_json = origem.amostra_json,
                    metricas_json = origem.metricas_json,
                    observacoes_json = origem.observacoes_json,
                    erro_tecnico = origem.erro_tecnico,
                    erro_traduzido = origem.erro_traduzido,
                    causa_provavel = origem.causa_provavel,
                    acao_sugerida = origem.acao_sugerida,
                    host_execucao = origem.host_execucao,
                    atualizado_em = SYSUTCDATETIME()
            WHEN NOT MATCHED THEN
                INSERT (
                    dag_id, run_id, task_id, try_number, status, operator, start_date, end_date,
                    duracao_segundos, nome_amigavel, descricao_etapa, origem_dados, destino_dados,
                    linhas_lidas, linhas_inseridas, linhas_atualizadas, linhas_descartadas,
                    validacoes_json, amostra_json, metricas_json, observacoes_json,
                    erro_tecnico, erro_traduzido, causa_provavel, acao_sugerida, host_execucao
                )
                VALUES (
                    origem.dag_id, origem.run_id, origem.task_id, origem.try_number, origem.status,
                    origem.operator, origem.start_date, origem.end_date, origem.duracao_segundos,
                    origem.nome_amigavel, origem.descricao_etapa, origem.origem_dados, origem.destino_dados,
                    origem.linhas_lidas, origem.linhas_inseridas, origem.linhas_atualizadas,
                    origem.linhas_descartadas, origem.validacoes_json, origem.amostra_json,
                    origem.metricas_json, origem.observacoes_json, origem.erro_tecnico,
                    origem.erro_traduzido, origem.causa_provavel, origem.acao_sugerida, origem.host_execucao
                );
            """
        )

        parametros = {
            "dag_id": evento.dag_id,
            "run_id": evento.run_id,
            "task_id": evento.task_id,
            "try_number": evento.try_number or 1,
            "status": evento.status,
            "operator": evento.operator,
            "start_date": cls._para_datetime(evento.start_date),
            "end_date": cls._para_datetime(evento.end_date),
            "duracao_segundos": evento.duracao_segundos,
            "nome_amigavel": evento.nome_amigavel,
            "descricao_etapa": evento.descricao_etapa,
            "origem_dados": evento.origem_dados,
            "destino_dados": evento.destino_dados,
            "linhas_lidas": evento.linhas_lidas,
            "linhas_inseridas": evento.linhas_inseridas,
            "linhas_atualizadas": evento.linhas_atualizadas,
            "linhas_descartadas": evento.linhas_descartadas,
            "validacoes_json": evento.validacoes_json,
            "amostra_json": evento.amostra_json,
            "metricas_json": evento.metricas_json,
            "observacoes_json": evento.observacoes_json,
            "erro_tecnico": evento.erro_tecnico,
            "erro_traduzido": evento.erro_traduzido,
            "causa_provavel": evento.causa_provavel,
            "acao_sugerida": evento.acao_sugerida,
            "host_execucao": evento.host_execucao,
        }

        engine = cls.obter_engine()

        try:
            with engine.begin() as conexao:
                conexao.execute(sql, parametros)
        finally:
            engine.dispose()

    @classmethod
    def listar_execucoes_recentes(
        cls,
        dag_id: str | None = None,
        status: str | None = None,
        limite: int = 50,
    ) -> list[dict[str, Any]]:
        """Eu listo execuções recentes das DAGs."""
        cls.garantir_estrutura()

        filtros = []
        parametros: dict[str, Any] = {"limite": int(limite)}

        if dag_id:
            filtros.append("dag_id = :dag_id")
            parametros["dag_id"] = dag_id

        if status:
            filtros.append("status = :status")
            parametros["status"] = status

        where_sql = ""
        if filtros:
            where_sql = "WHERE " + " AND ".join(filtros)

        sql = text(
            f"""
            SELECT TOP ({int(limite)})
                dag_id,
                run_id,
                status,
                run_type,
                queued_at,
                start_date,
                end_date,
                duracao_segundos,
                mensagem_resumo,
                criado_em,
                atualizado_em
            FROM dbo.airflow_auditoria_dag_run
            {where_sql}
            ORDER BY COALESCE(start_date, criado_em) DESC
            """
        )

        engine = cls.obter_engine()

        try:
            with engine.connect() as conexao:
                resultado = conexao.execute(sql, parametros)
                linhas = resultado.mappings().all()
                return [dict(linha) for linha in linhas]
        finally:
            engine.dispose()

    @classmethod
    def obter_detalhe_run(cls, dag_id: str, run_id: str) -> dict[str, Any]:
        """Eu obtenho o detalhe consolidado de uma execução de DAG e suas tasks."""
        cls.garantir_estrutura()

        sql_dag = text(
            """
            SELECT TOP 1
                dag_id,
                run_id,
                status,
                run_type,
                queued_at,
                start_date,
                end_date,
                duracao_segundos,
                mensagem_resumo,
                criado_em,
                atualizado_em
            FROM dbo.airflow_auditoria_dag_run
            WHERE dag_id = :dag_id
              AND run_id = :run_id
            """
        )

        sql_tasks = text(
            """
            SELECT
                dag_id,
                run_id,
                task_id,
                try_number,
                status,
                operator,
                start_date,
                end_date,
                duracao_segundos,
                nome_amigavel,
                descricao_etapa,
                origem_dados,
                destino_dados,
                linhas_lidas,
                linhas_inseridas,
                linhas_atualizadas,
                linhas_descartadas,
                validacoes_json,
                amostra_json,
                metricas_json,
                observacoes_json,
                erro_tecnico,
                erro_traduzido,
                causa_provavel,
                acao_sugerida,
                host_execucao,
                criado_em,
                atualizado_em
            FROM dbo.airflow_auditoria_task_run
            WHERE dag_id = :dag_id
              AND run_id = :run_id
            ORDER BY start_date, criado_em, task_id
            """
        )

        parametros = {
            "dag_id": dag_id,
            "run_id": run_id,
        }

        engine = cls.obter_engine()

        try:
            with engine.connect() as conexao:
                resultado_dag = conexao.execute(sql_dag, parametros)
                dag = resultado_dag.mappings().first()

                resultado_tasks = conexao.execute(sql_tasks, parametros)
                tasks = [dict(linha) for linha in resultado_tasks.mappings().all()]
        finally:
            engine.dispose()

        dag_dict = dict(dag) if dag else None

        for task in tasks:
            task["validacoes_json"] = cls._carregar_json_seguro(task.get("validacoes_json"), [])
            task["amostra_json"] = cls._carregar_json_seguro(task.get("amostra_json"), [])
            task["metricas_json"] = cls._carregar_json_seguro(task.get("metricas_json"), [])
            task["observacoes_json"] = cls._carregar_json_seguro(task.get("observacoes_json"), [])

            origem_recursos = cls._montar_multiplos_recursos(task.get("origem_dados"))
            destino_recursos = cls._montar_multiplos_recursos(task.get("destino_dados"))

            task["origem_recursos"] = origem_recursos
            task["destino_recursos"] = destino_recursos

            task["origem_tabela"] = cls._primeiro_recurso_por_tipo(origem_recursos, "tabela_sql")
            task["destino_tabela"] = cls._primeiro_recurso_por_tipo(destino_recursos, "tabela_sql")

            task["origem_arquivo"] = cls._primeiro_recurso_por_tipo(origem_recursos, "arquivo")
            task["destino_arquivo"] = cls._primeiro_recurso_por_tipo(destino_recursos, "arquivo")

        return {
            "dag": dag_dict,
            "tasks": tasks,
        }