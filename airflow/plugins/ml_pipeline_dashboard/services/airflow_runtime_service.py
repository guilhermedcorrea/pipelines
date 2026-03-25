
from __future__ import annotations

import importlib
import json
import logging
import re
from dataclasses import asdict, is_dataclass
from datetime import date, datetime, time
from decimal import Decimal
from pathlib import Path
from typing import Any

from airflow.models import DagRun, TaskInstance
from airflow.models.dagbag import DagBag
try:
    from airflow.models.xcom import XComModel
except Exception:  # pragma: no cover
    from airflow.models.xcom import XCom as XComModel
from airflow.utils.session import create_session


logger = logging.getLogger(__name__)

CHAVES_XCOM_AUDITORIA = (
    "ml_pipeline_dashboard_auditoria",
    "dag_monitoring_auditoria",
    "auditoria_resumo_execucao",
    "auditoria_execucao",
    "task_auditoria",
    "task_audit",
    "metadata_auditoria",
)

PADRAO_OBJETO_SQL = re.compile(
    r"(?i)\b(?:from|join|into|update|merge\s+into|truncate\s+table|delete\s+from|insert\s+into)\s+"
    r"((?:\[[^\]]+\]|\w+)(?:\.(?:\[[^\]]+\]|\w+)){0,2})"
)
PADRAO_PROCEDURE_SQL = re.compile(
    r"(?i)\bexec(?:ute)?\s+((?:\[[^\]]+\]|\w+)(?:\.(?:\[[^\]]+\]|\w+)){0,2})"
)
PADRAO_CAMINHO_ARQUIVO = re.compile(
    r"((?:/|[A-Za-z]:\\\\)[^\s\"']+\.(?:csv|xlsx|xls|parquet|json|txt|sql|html|htm|md|log|pkl|joblib|pdf|yaml|yml|zip|gz))",
    re.IGNORECASE,
)
PADRAO_IDENTIFICADOR_SIMPLES = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
PADRAO_REFERENCIA_QUALIFICADA = re.compile(
    r"(?<![\w])((?:\[[^\]]+\]|\w+)(?:\.(?:\[[^\]]+\]|\w+)){1,2})(?![\w])"
)


def _importar_hook_sqlserver() -> Any:
    """Eu tento localizar o hook de SQL Server em múltiplos caminhos possíveis do projeto."""
    caminhos = (
        "hooks.BancodeDados.SqlServer",
        "hooks.SqlServer",
        "SqlServer",
        "plugins.ml_pipeline_dashboard.SqlServer",
    )

    for nome_modulo in caminhos:
        try:
            modulo = importlib.import_module(nome_modulo)
        except Exception:
            continue

        hook = getattr(modulo, "HookSqlServer", None)
        if hook is not None:
            return hook

    return None


HookSqlServer = _importar_hook_sqlserver()


class TaskSintetica:
    """Eu represento uma task mínima quando a DAG não está carregada no DagBag."""

    def __init__(
        self,
        task_id: str,
        nome_exibicao: str | None = None,
        tipo_task: str = "TaskInstance",
    ) -> None:
        self.task_id = str(task_id)
        self.task_display_name = str(nome_exibicao or task_id)
        self.doc_md = None
        self.upstream_task_ids: set[str] = set()
        self.downstream_task_ids: set[str] = set()
        self._tipo_task = str(tipo_task or "TaskInstance")


def _humanizar_texto(texto: str | None) -> str:
    """Eu transformo um identificador técnico em texto mais amigável."""
    if not texto:
        return "-"

    partes = [parte for parte in re.split(r"[_\-.]+", str(texto)) if parte]
    if not partes:
        return str(texto)

    return " ".join(parte.capitalize() for parte in partes)


def _serializar_datetime(valor: Any) -> str | None:
    """Eu padronizo datetime para string segura no front."""
    if valor is None:
        return None

    if isinstance(valor, datetime):
        return valor.strftime("%Y-%m-%d %H:%M:%S")

    if isinstance(valor, date):
        return valor.strftime("%Y-%m-%d")

    if isinstance(valor, time):
        return valor.isoformat()

    return str(valor)


def _serializar_valor(valor: Any) -> Any:
    """Eu torno qualquer valor o mais JSON-safe possível."""
    if valor is None:
        return None

    if isinstance(valor, (str, int, float, bool)):
        return valor

    if isinstance(valor, Decimal):
        return float(valor)

    if isinstance(valor, Path):
        return str(valor)

    if isinstance(valor, (datetime, date, time)):
        return _serializar_datetime(valor)

    if isinstance(valor, bytes):
        try:
            return valor.decode("utf-8", errors="replace")
        except Exception:
            return str(valor)

    if isinstance(valor, dict):
        return {str(chave): _serializar_valor(item) for chave, item in valor.items()}

    if isinstance(valor, (list, tuple, set)):
        return [_serializar_valor(item) for item in valor]

    if is_dataclass(valor):
        try:
            return _serializar_valor(asdict(valor))
        except Exception:
            return str(valor)

    if hasattr(valor, "model_dump"):
        try:
            return _serializar_valor(valor.model_dump())
        except Exception:
            return str(valor)

    if hasattr(valor, "dict"):
        try:
            return _serializar_valor(valor.dict())
        except Exception:
            return str(valor)

    if hasattr(valor, "__dict__"):
        try:
            return _serializar_valor({
                chave: item
                for chave, item in vars(valor).items()
                if not str(chave).startswith("_")
            })
        except Exception:
            return str(valor)

    return str(valor)


def _valor_preenchido(valor: Any) -> bool:
    """Eu verifico se o valor realmente contém informação útil."""
    if valor is None:
        return False

    if isinstance(valor, str):
        return bool(valor.strip())

    if isinstance(valor, (list, tuple, set, dict)):
        return len(valor) > 0

    return True


def _primeiro_preenchido(*valores: Any) -> Any:
    """Eu retorno o primeiro valor útil entre vários candidatos."""
    for valor in valores:
        if _valor_preenchido(valor):
            return valor
    return None


def _garantir_lista(valor: Any) -> list[Any]:
    """Eu normalizo qualquer valor para lista."""
    if valor is None:
        return []

    if isinstance(valor, list):
        return valor

    if isinstance(valor, tuple):
        return list(valor)

    if isinstance(valor, set):
        return list(valor)

    return [valor]


def _normalizar_texto_status(valor: Any) -> str:
    """Eu normalizo status em caixa baixa, preservando vazio como unknown."""
    texto = str(valor or "").strip().lower()
    return texto or "unknown"


def _normalizar_tag(valor: Any) -> str:
    """Eu removo diferenças superficiais das tags para comparação."""
    texto = str(valor or "").strip().lower()
    return texto.replace("-", "").replace("_", "").replace(" ", "")


def _obter_execution_date_ou_logical_date(dagrun: DagRun | None) -> Any:
    """Eu tento obter a data lógica da execução respeitando múltiplas versões do Airflow."""
    if dagrun is None:
        return None

    if hasattr(dagrun, "logical_date") and getattr(dagrun, "logical_date") is not None:
        return dagrun.logical_date

    if hasattr(dagrun, "execution_date") and getattr(dagrun, "execution_date") is not None:
        return dagrun.execution_date

    return None


def _obter_schedule_dag(dag) -> str | None:
    """Eu tento ler o schedule da DAG usando os nomes de atributo mais comuns."""
    if dag is None:
        return None

    for nome_campo in ("schedule_interval", "schedule", "timetable_description"):
        valor = getattr(dag, nome_campo, None)
        if _valor_preenchido(valor):
            return str(valor)

    return None


def _obter_owner_dag(dag) -> str | None:
    """Eu tento ler o owner da DAG."""
    if dag is None:
        return None

    owner = getattr(dag, "owner", None)
    if _valor_preenchido(owner):
        return str(owner)

    owners = getattr(dag, "owners", None)
    if _valor_preenchido(owners):
        try:
            return ", ".join(sorted(str(item) for item in owners if item))
        except Exception:
            return str(owners)

    return None


def _obter_tags_dag(dag) -> list[str]:
    """Eu normalizo as tags da DAG em texto simples."""
    if dag is None:
        return []

    tags = getattr(dag, "tags", None) or []
    retorno: list[str] = []
    for tag in tags:
        nome = getattr(tag, "name", None)
        retorno.append(str(nome if nome is not None else tag))

    return [item for item in retorno if _valor_preenchido(item)]


def _obter_descricao_dag(dag, dag_id: str | None = None) -> str:
    """Eu monto a melhor descrição disponível da DAG."""
    if dag is None:
        if dag_id:
            return (
                "A DAG não foi carregada no DagBag deste processo. Mesmo assim, o painel usa o DagRun, "
                "as Task Instances e os XComs persistidos para montar o dashboard."
            )
        return "Sem descrição cadastrada."

    descricao = getattr(dag, "description", None)
    if _valor_preenchido(descricao):
        return str(descricao)

    doc_md = getattr(dag, "doc_md", None)
    if _valor_preenchido(doc_md):
        return str(doc_md)

    return "Sem descrição cadastrada."


def _obter_nome_dag(dag, dag_id: str | None = None) -> str:
    """Eu monto um nome amigável da DAG."""
    if dag is None:
        return _humanizar_texto(dag_id) if dag_id else "-"

    descricao = getattr(dag, "description", None)
    if _valor_preenchido(descricao):
        return str(descricao)

    dag_id_real = getattr(dag, "dag_id", None)
    if _valor_preenchido(dag_id_real):
        return _humanizar_texto(str(dag_id_real))

    return _humanizar_texto(dag_id) if dag_id else "-"


def _obter_conn_id_sqlserver_padrao() -> str | None:
    """Eu tento descobrir o conn_id padrão do hook oficial do projeto."""
    if HookSqlServer is not None:
        try:
            hook = HookSqlServer()
            conn_id = str(getattr(hook, "conn_id", "") or "").strip()
            if conn_id:
                return conn_id
        except Exception:
            logger.exception("Falha ao obter conn_id padrão a partir do HookSqlServer.")

    return "mssql_integracao"


def _obter_dagbag() -> DagBag | None:
    """Eu tento abrir o DagBag e devolvo None se a carga falhar."""
    try:
        return DagBag(include_examples=False)
    except Exception:
        logger.exception("Falha ao carregar DagBag.")
        return None


def _obter_dag_real(dag_id: str):
    """Eu tento localizar a DAG real no DagBag."""
    dagbag = _obter_dagbag()
    if dagbag is None:
        return None

    try:
        dag = dagbag.get_dag(dag_id)
        if dag is None:
            import_errors = getattr(dagbag, "import_errors", {}) or {}
            if import_errors:
                logger.warning(
                    "DAG '%s' não encontrada no DagBag. Existem %s erro(s) de importação.",
                    dag_id,
                    len(import_errors),
                )
        return dag
    except Exception:
        logger.exception("Falha ao obter DAG '%s' do DagBag.", dag_id)
        return None


def _obter_dagrun_real(dag_id: str, run_id: str | None):
    """Eu busco o DagRun solicitado; se run_id vier vazio, uso o mais recente."""
    with create_session() as session:
        query = session.query(DagRun).filter(DagRun.dag_id == dag_id)

        if run_id:
            query = query.filter(DagRun.run_id == run_id)

        campo_ordenacao = getattr(DagRun, "logical_date", None)
        if campo_ordenacao is None:
            campo_ordenacao = getattr(DagRun, "execution_date", None)

        if campo_ordenacao is not None:
            return query.order_by(campo_ordenacao.desc()).first()

        return query.first()


def _obter_task_instances(dag_id: str, run_id: str) -> list[TaskInstance]:
    """Eu busco as Task Instances da execução real."""
    with create_session() as session:
        task_instances = (
            session.query(TaskInstance)
            .filter(TaskInstance.dag_id == dag_id, TaskInstance.run_id == run_id)
            .all()
        )

    return sorted(
        task_instances,
        key=lambda item: (
            _serializar_datetime(getattr(item, "start_date", None)) or "9999-12-31 23:59:59",
            getattr(item, "task_id", "") or "",
        ),
    )


def _obter_task_instances_por_task_id(task_instances: list[TaskInstance]) -> dict[str, TaskInstance]:
    """Eu indexo as Task Instances por task_id."""
    return {str(ti.task_id): ti for ti in task_instances if _valor_preenchido(getattr(ti, "task_id", None))}


def _obter_operador_ti(ti: TaskInstance | None) -> str:
    """Eu tento descobrir o tipo do operador a partir da TaskInstance."""
    if ti is None:
        return "TaskInstance"

    for campo in ("operator", "operator_name", "task_type"):
        valor = getattr(ti, campo, None)
        if _valor_preenchido(valor):
            return str(valor)

    return "TaskInstance"


def _obter_tasks_sinteticas(task_instances: list[TaskInstance]) -> list[TaskSintetica]:
    """Eu crio tasks sintéticas quando a DAG não veio do DagBag."""
    tarefas: list[TaskSintetica] = []
    vistos: set[str] = set()

    for ti in task_instances:
        task_id = str(getattr(ti, "task_id", "") or "").strip()
        if not task_id or task_id in vistos:
            continue

        vistos.add(task_id)
        tarefas.append(
            TaskSintetica(
                task_id=task_id,
                nome_exibicao=task_id,
                tipo_task=_obter_operador_ti(ti),
            )
        )

    return tarefas


def _obter_ordem_real_tasks(dag) -> list[Any]:
    """Eu tento obter as tasks em ordem topológica real."""
    if dag is None:
        return []

    try:
        if hasattr(dag, "topological_sort"):
            tasks = list(dag.topological_sort())
            if tasks:
                return tasks
    except Exception:
        logger.exception("Falha ao usar topological_sort da DAG %s.", getattr(dag, "dag_id", None))

    try:
        return list(dag.tasks)
    except Exception:
        return []


def _obter_tasks_para_montagem(dag, task_instances: list[TaskInstance]) -> list[Any]:
    """Eu devolvo tasks reais da DAG; se não houver, monto fallback sintético."""
    tasks_reais = _obter_ordem_real_tasks(dag)
    if tasks_reais:
        return tasks_reais
    return _obter_tasks_sinteticas(task_instances)


def _obter_tipo_task(task, ti: TaskInstance | None = None) -> str:
    """Eu identifico o tipo lógico da task."""
    for valor in (
        getattr(task, "_tipo_task", None),
        getattr(task, "task_type", None),
        getattr(task, "operator_name", None),
        getattr(task, "operator", None),
        _obter_operador_ti(ti),
    ):
        if _valor_preenchido(valor):
            return str(valor)

    return "Task"


def _calcular_tempo_execucao_ti(ti: TaskInstance | None) -> str | None:
    """Eu calculo o tempo da task a partir de start_date e end_date."""
    if ti is None:
        return None

    inicio = getattr(ti, "start_date", None)
    fim = getattr(ti, "end_date", None)
    if inicio is None or fim is None:
        return None

    try:
        diferenca = fim - inicio
        total_segundos = int(diferenca.total_seconds())
        return f"{total_segundos}s"
    except Exception:
        return None


def _calcular_duracao_segundos_dagrun(dagrun: DagRun | None) -> float | None:
    """Eu calculo a duração total do DagRun em segundos."""
    if dagrun is None:
        return None

    inicio = getattr(dagrun, "start_date", None)
    fim = getattr(dagrun, "end_date", None)
    if inicio is None or fim is None:
        return None

    try:
        return float((fim - inicio).total_seconds())
    except Exception:
        return None


def _montar_health(task_instances: list[TaskInstance], dagrun: DagRun | None) -> dict[str, Any]:
    """Eu monto um resumo de saúde operacional da execução."""
    contadores = {
        "success": 0,
        "failed": 0,
        "running": 0,
        "queued": 0,
        "up_for_retry": 0,
        "skipped": 0,
        "other": 0,
    }

    for ti in task_instances:
        estado = _normalizar_texto_status(getattr(ti, "state", None))
        if estado in contadores:
            contadores[estado] += 1
        else:
            contadores["other"] += 1

    total_tasks = len(task_instances)
    total_ok = contadores["success"] + contadores["skipped"]
    total_erro = contadores["failed"] + contadores["up_for_retry"]
    percentual_ok = round((total_ok / total_tasks) * 100, 2) if total_tasks else 0.0

    return {
        "status_dag": _normalizar_texto_status(getattr(dagrun, "state", None) if dagrun else None),
        "duracao_segundos": _calcular_duracao_segundos_dagrun(dagrun),
        "tasks_total": total_tasks,
        "tasks_sucesso": contadores["success"],
        "tasks_falha": contadores["failed"],
        "tasks_executando": contadores["running"],
        "tasks_em_fila": contadores["queued"],
        "tasks_retry": contadores["up_for_retry"],
        "tasks_puladas": contadores["skipped"],
        "tasks_outros": contadores["other"],
        "percentual_ok": percentual_ok,
        "broker": None,
        "workers_online": None,
        "scheduler_heartbeat": None,
        "triggerer_heartbeat": None,
    }


def _obter_valor_xcom_bruto_registro(registro: XComModel) -> Any:
    """Eu leio o valor do XCom suportando diferentes nomes de atributo internos."""
    for campo in ("value", "_value"):
        if hasattr(registro, campo):
            try:
                return getattr(registro, campo)
            except Exception:
                continue

    try:
        if hasattr(registro, "deserialize_value"):
            return registro.deserialize_value()
    except Exception:
        logger.exception("Falha ao desserializar XCom por deserialize_value().")

    return None


def _obter_registros_xcom_task(dag_id: str, run_id: str, task_id: str) -> list[XComModel]:
    """Eu busco todos os XComs daquela task naquela execução."""
    with create_session() as session:
        query = session.query(XComModel).filter(
            XComModel.dag_id == dag_id,
            XComModel.run_id == run_id,
            XComModel.task_id == task_id,
        )

        campo_ts = getattr(XComModel, "timestamp", None)
        if campo_ts is not None:
            query = query.order_by(campo_ts.desc())

        return query.all()


def _normalizar_payload_xcom(valor: Any) -> dict[str, Any]:
    """Eu converto o valor bruto do XCom em dicionário seguro."""
    if valor is None:
        return {}

    if isinstance(valor, bytes):
        try:
            valor = valor.decode("utf-8")
        except Exception:
            return {}

    if isinstance(valor, str):
        texto = valor.strip()
        if not texto:
            return {}

        try:
            convertido = json.loads(texto)
            return _normalizar_payload_xcom(convertido)
        except Exception:
            return {"valor": texto}

    if isinstance(valor, dict):
        return _serializar_valor(valor)

    if isinstance(valor, list):
        return {"amostra": _serializar_valor(valor)}

    if is_dataclass(valor):
        try:
            return _serializar_valor(asdict(valor))
        except Exception:
            return {"valor": str(valor)}

    if hasattr(valor, "model_dump"):
        try:
            retorno = valor.model_dump()
            if isinstance(retorno, dict):
                return _serializar_valor(retorno)
        except Exception:
            pass

    if hasattr(valor, "dict"):
        try:
            retorno = valor.dict()
            if isinstance(retorno, dict):
                return _serializar_valor(retorno)
        except Exception:
            pass

    if hasattr(valor, "__dict__"):
        try:
            retorno = {
                chave: item
                for chave, item in vars(valor).items()
                if not str(chave).startswith("_")
            }
            if isinstance(retorno, dict):
                return _serializar_valor(retorno)
        except Exception:
            pass

    return {"valor": _serializar_valor(valor)}


def _eh_chave_auditoria(chave: str | None) -> bool:
    """Eu verifico se a chave do XCom parece ser de auditoria estruturada."""
    chave_limpa = str(chave or "").strip()
    if not chave_limpa:
        return False

    return chave_limpa in CHAVES_XCOM_AUDITORIA


def _mesclar_dicionarios_priorizando_base(base: dict[str, Any], complemento: dict[str, Any]) -> dict[str, Any]:
    """Eu mesclo dois dicionários preservando o que já veio preenchido na base."""
    resultado = dict(base)

    for chave, valor_complemento in (complemento or {}).items():
        valor_base = resultado.get(chave)

        if isinstance(valor_base, dict) and isinstance(valor_complemento, dict):
            resultado[chave] = _mesclar_dicionarios_priorizando_base(valor_base, valor_complemento)
            continue

        if not _valor_preenchido(valor_base) and _valor_preenchido(valor_complemento):
            resultado[chave] = valor_complemento

    return resultado


def _desembrulhar_dicionario_auditoria(payload: dict[str, Any]) -> dict[str, Any]:
    """Eu desembrulho payloads que vieram encapsulados em um campo interno."""
    if not isinstance(payload, dict):
        return {}

    candidatos = (
        payload.get("resumo"),
        payload.get("auditoria"),
        payload.get("data"),
        payload.get("payload"),
        payload.get("valor"),
        payload.get("metadata"),
    )

    for candidato in candidatos:
        if isinstance(candidato, dict):
            return _mesclar_dicionarios_priorizando_base(candidato, payload)

    return payload


def _montar_metadata_task_xcoms(dag_id: str, run_id: str, task_id: str) -> dict[str, Any]:
    """Eu consolido os XComs da task em um único metadata coerente."""
    registros = _obter_registros_xcom_task(dag_id=dag_id, run_id=run_id, task_id=task_id)

    metadata: dict[str, Any] = {}
    chaves_encontradas: list[str] = []
    retorno_task: Any = None

    for registro in registros:
        chave = str(getattr(registro, "key", "") or "").strip()
        if chave:
            chaves_encontradas.append(chave)

        valor_bruto = _obter_valor_xcom_bruto_registro(registro)
        payload = _normalizar_payload_xcom(valor_bruto)

        if not payload:
            continue

        if chave == "return_value":
            retorno_task = payload

        if _eh_chave_auditoria(chave):
            payload = _desembrulhar_dicionario_auditoria(payload)
            metadata = _mesclar_dicionarios_priorizando_base(metadata, payload)
            continue

        if chave and chave not in {"return_value", "None"}:
            if chave not in metadata:
                metadata[chave] = payload.get("valor", payload)

    if retorno_task and not _valor_preenchido(metadata.get("amostra_dados")):
        if isinstance(retorno_task, dict):
            if any(chave in retorno_task for chave in ("linhas", "colunas", "amostra", "preview", "table_preview")):
                metadata["amostra_dados"] = retorno_task
            elif len(retorno_task) <= 30:
                metadata.setdefault("resultado_task", retorno_task)

    metadata["_xcom_keys_encontradas"] = list(dict.fromkeys(chaves_encontradas))
    return metadata


def _extrair_metricas_extras(metadata_auditoria: dict[str, Any]) -> dict[str, Any]:
    """Eu localizo metricas_extras mesmo quando vieram em campos alternativos."""
    for chave in ("metricas_extras", "extras", "metricas", "metrics"):
        valor = metadata_auditoria.get(chave)
        if isinstance(valor, dict):
            return _serializar_valor(valor)

    return {}


def _limpar_identificador_sql_bruto(valor: str | None) -> list[str]:
    """Eu limpo um identificador SQL qualificado e separo seus segmentos."""
    texto = str(valor or "").strip()
    if not texto:
        return []

    texto = texto.replace("[", "").replace("]", "").replace('"', "").replace("`", "")
    return [parte.strip() for parte in texto.split(".") if parte.strip()]


def _parece_caminho_arquivo(texto: str | None) -> bool:
    """Eu diferencio caminho de arquivo de referência de tabela."""
    valor = str(texto or "").strip()
    if not valor:
        return False

    if "/" in valor or "\\" in valor:
        return True

    nome_final = valor.split("?")[0].split("#")[0].split("/")[-1].split("\\")[-1]
    if "." not in nome_final:
        return False

    extensao = nome_final.rsplit(".", 1)[-1].strip().lower()
    return extensao in {
        "csv", "xlsx", "xls", "parquet", "json", "txt", "sql", "yaml", "yml",
        "md", "pdf", "html", "htm", "log", "pkl", "joblib", "zip", "gz",
    }


def _montar_objeto_referencia(
    *,
    tipo: str,
    nome: str,
    descricao: str,
    conn_id: str | None,
    banco: str | None,
    schema: str | None,
    tabela: str | None,
    caminho_arquivo: str | None,
    formato: str | None,
    direcao: str,
) -> dict[str, Any]:
    """Eu monto o contrato final de um objeto técnico exibido no front."""
    tipo_limpo = str(tipo or "Objeto")
    conn_id_limpo = str(conn_id).strip() if _valor_preenchido(conn_id) else None
    banco_limpo = str(banco).strip() if _valor_preenchido(banco) else None
    schema_limpo = str(schema).strip() if _valor_preenchido(schema) else None
    tabela_limpa = str(tabela).strip() if _valor_preenchido(tabela) else None
    caminho_limpo = str(caminho_arquivo).strip() if _valor_preenchido(caminho_arquivo) else None
    formato_limpo = str(formato).strip() if _valor_preenchido(formato) else None

    eh_arquivo = bool(caminho_limpo)
    eh_tabela = tipo_limpo.lower() == "tabela"

    return {
        "tipo": tipo_limpo,
        "nome": str(nome or "-"),
        "descricao": str(descricao or ""),
        "conn_id": conn_id_limpo,
        "banco": banco_limpo,
        "schema": schema_limpo,
        "tabela": tabela_limpa,
        "caminho_arquivo": caminho_limpo,
        "formato": formato_limpo,
        "direcao": direcao,
        "downloadable": eh_arquivo,
        "visualizavel": bool(eh_tabela and conn_id_limpo and schema_limpo and tabela_limpa),
    }


def _aplicar_conn_id_padrao_em_objeto(objeto: dict[str, Any], conn_id_padrao: str | None) -> dict[str, Any]:
    """Eu completo conn_id padrão em tabelas quando a auditoria não informou explicitamente."""
    resultado = dict(objeto)
    tipo = str(resultado.get("tipo", "") or "").strip().lower()

    if tipo == "tabela" and not resultado.get("conn_id") and conn_id_padrao:
        if resultado.get("schema") and resultado.get("tabela"):
            resultado["conn_id"] = conn_id_padrao

    resultado["visualizavel"] = bool(
        str(resultado.get("tipo", "")).lower() == "tabela"
        and resultado.get("conn_id")
        and resultado.get("schema")
        and resultado.get("tabela")
    )
    resultado["downloadable"] = bool(resultado.get("caminho_arquivo"))
    return resultado


def _normalizar_referencia_objeto(item: Any, direcao: str, conn_id_padrao: str | None) -> dict[str, Any] | None:
    """Eu normalizo strings ou dicionários em objetos consumíveis pelo front."""
    if item is None:
        return None

    if isinstance(item, dict):
        tipo = _primeiro_preenchido(item.get("tipo"), item.get("type"), item.get("object_type"), "Tabela")
        nome = _primeiro_preenchido(item.get("nome"), item.get("name"), item.get("label"), item.get("tabela"), item.get("table"), item.get("arquivo"), item.get("file"), "-")
        descricao = _primeiro_preenchido(item.get("descricao"), item.get("description"), "")
        conn_id = _primeiro_preenchido(item.get("conn_id"), item.get("conexao_id"), item.get("connection_id"), conn_id_padrao)
        banco = _primeiro_preenchido(item.get("banco"), item.get("database"))
        schema = _primeiro_preenchido(item.get("schema"), item.get("esquema"))
        tabela = _primeiro_preenchido(item.get("tabela"), item.get("table"))
        caminho = _primeiro_preenchido(item.get("caminho_arquivo"), item.get("arquivo"), item.get("path"), item.get("file"))
        formato = _primeiro_preenchido(item.get("formato"), item.get("format"))

        if not _valor_preenchido(caminho):
            texto_referencia = _primeiro_preenchido(item.get("objeto"), item.get("sql_object"), item.get("referencia"))
            if _parece_caminho_arquivo(texto_referencia):
                caminho = texto_referencia
                tipo = "Arquivo"
            elif _valor_preenchido(texto_referencia) and not (_valor_preenchido(schema) and _valor_preenchido(tabela)):
                partes = _limpar_identificador_sql_bruto(str(texto_referencia))
                if len(partes) == 3:
                    banco, schema, tabela = partes
                    tipo = "Tabela"
                elif len(partes) == 2:
                    schema, tabela = partes
                    tipo = "Tabela"

        objeto = _montar_objeto_referencia(
            tipo=str(tipo),
            nome=str(nome),
            descricao=str(descricao),
            conn_id=str(conn_id).strip() if _valor_preenchido(conn_id) else None,
            banco=str(banco).strip() if _valor_preenchido(banco) else None,
            schema=str(schema).strip() if _valor_preenchido(schema) else None,
            tabela=str(tabela).strip() if _valor_preenchido(tabela) else None,
            caminho_arquivo=str(caminho).strip() if _valor_preenchido(caminho) else None,
            formato=str(formato).strip() if _valor_preenchido(formato) else None,
            direcao=direcao,
        )
        return _aplicar_conn_id_padrao_em_objeto(objeto, conn_id_padrao)

    texto = str(item).strip()
    if not texto:
        return None

    if _parece_caminho_arquivo(texto):
        return _aplicar_conn_id_padrao_em_objeto(
            _montar_objeto_referencia(
                tipo="Arquivo",
                nome=Path(texto).name,
                descricao="Arquivo inferido a partir da auditoria da task.",
                conn_id=None,
                banco=None,
                schema=None,
                tabela=None,
                caminho_arquivo=texto,
                formato=Path(texto).suffix.lstrip(".").lower() or None,
                direcao=direcao,
            ),
            conn_id_padrao,
        )

    partes = _limpar_identificador_sql_bruto(texto)
    if len(partes) == 3:
        banco, schema, tabela = partes
        return _aplicar_conn_id_padrao_em_objeto(
            _montar_objeto_referencia(
                tipo="Tabela",
                nome=f"{banco}.{schema}.{tabela}",
                descricao="Tabela inferida a partir do texto da auditoria.",
                conn_id=conn_id_padrao,
                banco=banco,
                schema=schema,
                tabela=tabela,
                caminho_arquivo=None,
                formato=None,
                direcao=direcao,
            ),
            conn_id_padrao,
        )

    if len(partes) == 2:
        schema, tabela = partes
        return _aplicar_conn_id_padrao_em_objeto(
            _montar_objeto_referencia(
                tipo="Tabela",
                nome=f"{schema}.{tabela}",
                descricao="Tabela inferida a partir do texto da auditoria.",
                conn_id=conn_id_padrao,
                banco=None,
                schema=schema,
                tabela=tabela,
                caminho_arquivo=None,
                formato=None,
                direcao=direcao,
            ),
            conn_id_padrao,
        )

    return _aplicar_conn_id_padrao_em_objeto(
        _montar_objeto_referencia(
            tipo="Objeto",
            nome=texto,
            descricao="Referência textual informada na auditoria da task.",
            conn_id=None,
            banco=None,
            schema=None,
            tabela=None,
            caminho_arquivo=None,
            formato=None,
            direcao=direcao,
        ),
        conn_id_padrao,
    )


def _deduplicar_objetos(objetos: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Eu removo duplicidades preservando a ordem original."""
    vistos: set[tuple[str, str, str, str, str, str]] = set()
    resultado: list[dict[str, Any]] = []

    for objeto in objetos:
        chave = (
            str(objeto.get("tipo") or ""),
            str(objeto.get("conn_id") or ""),
            str(objeto.get("banco") or ""),
            str(objeto.get("schema") or ""),
            str(objeto.get("tabela") or ""),
            str(objeto.get("caminho_arquivo") or objeto.get("nome") or ""),
        )
        if chave in vistos:
            continue

        vistos.add(chave)
        resultado.append(objeto)

    return resultado


def _extrair_objetos_sql(sql_real: str | None, conn_id_padrao: str | None = None) -> list[dict[str, Any]]:
    """Eu extraio tabelas, procedures e arquivos citados no SQL ou no texto técnico."""
    texto = str(sql_real or "")
    if not texto.strip():
        return []

    objetos: list[dict[str, Any]] = []

    for match in PADRAO_OBJETO_SQL.finditer(texto):
        referencia = match.group(1)
        objeto = _normalizar_referencia_objeto(referencia, direcao="neutro", conn_id_padrao=conn_id_padrao)
        if objeto:
            objetos.append(objeto)

    for match in PADRAO_PROCEDURE_SQL.finditer(texto):
        referencia = match.group(1)
        partes = _limpar_identificador_sql_bruto(referencia)
        nome = ".".join(partes) if partes else referencia
        objetos.append(
            _montar_objeto_referencia(
                tipo="Procedure",
                nome=nome,
                descricao="Procedure inferida a partir do SQL da task.",
                conn_id=conn_id_padrao,
                banco=partes[0] if len(partes) == 3 else None,
                schema=partes[-2] if len(partes) >= 2 else None,
                tabela=None,
                caminho_arquivo=None,
                formato=None,
                direcao="neutro",
            )
        )

    for match in PADRAO_CAMINHO_ARQUIVO.finditer(texto):
        caminho = match.group(1)
        objetos.append(
            _montar_objeto_referencia(
                tipo="Arquivo",
                nome=Path(caminho).name,
                descricao="Arquivo citado no texto técnico da task.",
                conn_id=None,
                banco=None,
                schema=None,
                tabela=None,
                caminho_arquivo=caminho,
                formato=Path(caminho).suffix.lstrip(".").lower() or None,
                direcao="neutro",
            )
        )

    return _deduplicar_objetos([_aplicar_conn_id_padrao_em_objeto(item, conn_id_padrao) for item in objetos])


def _inferir_conn_id_task(task, metadata_auditoria: dict[str, Any]) -> str | None:
    """Eu tento descobrir o conn_id mais provável da task."""
    metricas_extras = _extrair_metricas_extras(metadata_auditoria)

    for valor in (
        metadata_auditoria.get("conn_id"),
        metadata_auditoria.get("conexao_id"),
        metadata_auditoria.get("connection_id"),
        metricas_extras.get("conn_id"),
        metricas_extras.get("conn_id_sql"),
    ):
        if _valor_preenchido(valor):
            return str(valor).strip()

    for atributo in ("conn_id", "mssql_conn_id", "sql_conn_id"):
        valor = getattr(task, atributo, None)
        if _valor_preenchido(valor):
            return str(valor).strip()

    return _obter_conn_id_sqlserver_padrao()


def _sql_real_da_task(task, metadata_auditoria: dict[str, Any]) -> str | None:
    """Eu procuro o SQL real da task em campos usuais da auditoria ou do próprio operador."""
    metricas_extras = _extrair_metricas_extras(metadata_auditoria)

    candidatos = (
        metadata_auditoria.get("sql"),
        metadata_auditoria.get("query_sql"),
        metadata_auditoria.get("query"),
        metadata_auditoria.get("sql_real"),
        metadata_auditoria.get("sql_preview"),
        metricas_extras.get("sql"),
        metricas_extras.get("query_sql"),
        metricas_extras.get("caminho_query_sql"),
        getattr(task, "sql", None),
        getattr(task, "query", None),
    )

    for valor in candidatos:
        if _valor_preenchido(valor):
            return str(valor)

    return None


def _normalizar_guia_transformacoes(metadata_auditoria: dict[str, Any]) -> list[dict[str, Any]]:
    """Eu normalizo a guia de transformações da task."""
    candidatos = (
        metadata_auditoria.get("guia_transformacoes"),
        metadata_auditoria.get("transformacoes"),
        metadata_auditoria.get("transformation_guide"),
    )

    for valor in candidatos:
        if isinstance(valor, list):
            retorno: list[dict[str, Any]] = []
            for item in valor:
                if isinstance(item, dict):
                    retorno.append(_serializar_valor(item))
                else:
                    retorno.append({"descricao": _serializar_valor(item)})
            return retorno

    return []


def _normalizar_regras_upsert(metadata_auditoria: dict[str, Any]) -> list[dict[str, Any]]:
    """Eu normalizo regras de upsert/merge quando existirem na auditoria."""
    candidatos = (
        metadata_auditoria.get("regras_upsert"),
        metadata_auditoria.get("upsert_rules"),
        metadata_auditoria.get("merge_rules"),
    )

    for valor in candidatos:
        if isinstance(valor, list):
            retorno: list[dict[str, Any]] = []
            for item in valor:
                if isinstance(item, dict):
                    retorno.append(_serializar_valor(item))
                else:
                    retorno.append({"descricao": _serializar_valor(item)})
            return retorno

    return []


def _inferir_fonte_dados(metadata_auditoria: dict[str, Any], objetos: list[dict[str, Any]]) -> str:
    """Eu monto uma descrição curta da fonte principal da task."""
    origem = _primeiro_preenchido(
        metadata_auditoria.get("fonte_dados"),
        metadata_auditoria.get("origem_dados"),
        metadata_auditoria.get("source"),
    )
    if _valor_preenchido(origem):
        return str(origem)

    for objeto in objetos:
        if str(objeto.get("tipo", "")).lower() == "tabela" and objeto.get("schema") and objeto.get("tabela"):
            if objeto.get("banco"):
                return f"{objeto['banco']}.{objeto['schema']}.{objeto['tabela']}"
            return f"{objeto['schema']}.{objeto['tabela']}"

    for objeto in objetos:
        if objeto.get("caminho_arquivo"):
            return str(objeto.get("caminho_arquivo"))

    return "Não registrada"


def _normalizar_amostra_dados(metadata_auditoria: dict[str, Any], objetos: list[dict[str, Any]]) -> dict[str, Any]:
    """Eu transformo qualquer amostra da auditoria em tabela consistente para o front."""
    candidatos = (
        metadata_auditoria.get("amostra_dados"),
        metadata_auditoria.get("amostra"),
        metadata_auditoria.get("sample"),
        metadata_auditoria.get("preview"),
        metadata_auditoria.get("table_preview"),
        metadata_auditoria.get("resultado_task"),
    )

    fonte = _inferir_fonte_dados(metadata_auditoria=metadata_auditoria, objetos=objetos)

    for candidato in candidatos:
        if candidato is None:
            continue

        if isinstance(candidato, dict):
            linhas = candidato.get("linhas")
            colunas = candidato.get("colunas")
            if isinstance(linhas, list):
                linhas_norm = [_serializar_valor(item) for item in linhas if isinstance(item, dict)]
                colunas_norm = list(colunas) if isinstance(colunas, list) else sorted({chave for linha in linhas_norm for chave in linha.keys()})
                return {"fonte": str(fonte), "colunas": colunas_norm, "linhas": linhas_norm}

            amostra = candidato.get("amostra")
            if isinstance(amostra, list):
                linhas_norm = [_serializar_valor(item) for item in amostra if isinstance(item, dict)]
                colunas_norm = sorted({chave for linha in linhas_norm for chave in linha.keys()})
                return {"fonte": str(fonte), "colunas": colunas_norm, "linhas": linhas_norm}

            if candidato and all(not isinstance(valor, (dict, list)) for valor in candidato.values()):
                return {
                    "fonte": str(fonte),
                    "colunas": list(candidato.keys()),
                    "linhas": [_serializar_valor(candidato)],
                }

        if isinstance(candidato, list):
            linhas_norm = [_serializar_valor(item) for item in candidato if isinstance(item, dict)]
            if linhas_norm:
                colunas_norm = sorted({chave for linha in linhas_norm for chave in linha.keys()})
                return {"fonte": str(fonte), "colunas": colunas_norm, "linhas": linhas_norm}

    return {"fonte": str(fonte), "colunas": [], "linhas": []}


def _obter_tags_task(task, metadata_auditoria: dict[str, Any]) -> list[str]:
    """Eu normalizo tags da task usando auditoria e métricas extras."""
    retorno: list[str] = []

    for candidato in (
        metadata_auditoria.get("tags"),
        _extrair_metricas_extras(metadata_auditoria).get("tags"),
    ):
        if isinstance(candidato, str) and candidato.strip():
            retorno.append(candidato.strip())
        elif isinstance(candidato, (list, tuple, set)):
            retorno.extend(str(item).strip() for item in candidato if _valor_preenchido(item))

    return list(dict.fromkeys(item for item in retorno if item))


def _extrair_objetos_task(task, metadata_auditoria: dict[str, Any]) -> list[dict[str, Any]]:
    """Eu extraio objetos estruturados e textuais da auditoria da task."""
    conn_id_padrao = _inferir_conn_id_task(task, metadata_auditoria)
    metricas_extras = _extrair_metricas_extras(metadata_auditoria)
    objetos: list[dict[str, Any]] = []

    candidatos_estruturados = []
    for chave in (
        "objetos",
        "objects",
        "artefatos",
        "artifacts",
        "inputs",
        "outputs",
    ):
        valor = metadata_auditoria.get(chave)
        if isinstance(valor, list):
            candidatos_estruturados.extend(valor)
        elif isinstance(valor, dict):
            candidatos_estruturados.extend(valor.values())

    for chave in (
        "arquivos_gerados",
        "arquivos_usados",
        "caminhos_arquivos",
        "files",
    ):
        valor = metricas_extras.get(chave)
        if isinstance(valor, list):
            candidatos_estruturados.extend(valor)

    for item in candidatos_estruturados:
        objeto = _normalizar_referencia_objeto(item, direcao="neutro", conn_id_padrao=conn_id_padrao)
        if objeto:
            objetos.append(objeto)

    textos_origem = _garantir_lista(_primeiro_preenchido(metadata_auditoria.get("origem_dados"), metadata_auditoria.get("source"), metadata_auditoria.get("origem_tabela")))
    textos_destino = _garantir_lista(_primeiro_preenchido(metadata_auditoria.get("destino_dados"), metadata_auditoria.get("target"), metadata_auditoria.get("destino_tabela")))
    textos_extras = _garantir_lista(metricas_extras.get("caminho_query_sql"))

    for texto in textos_origem:
        objeto = _normalizar_referencia_objeto(texto, direcao="entrada", conn_id_padrao=conn_id_padrao)
        if objeto:
            objetos.append(objeto)

    for texto in textos_destino:
        objeto = _normalizar_referencia_objeto(texto, direcao="saida", conn_id_padrao=conn_id_padrao)
        if objeto:
            objetos.append(objeto)

    for texto in textos_extras:
        objeto = _normalizar_referencia_objeto(texto, direcao="apoio", conn_id_padrao=conn_id_padrao)
        if objeto:
            objetos.append(objeto)

    sql_real = _sql_real_da_task(task=task, metadata_auditoria=metadata_auditoria)
    objetos.extend(_extrair_objetos_sql(sql_real=sql_real, conn_id_padrao=conn_id_padrao))

    return _deduplicar_objetos([_aplicar_conn_id_padrao_em_objeto(item, conn_id_padrao) for item in objetos])


def _buscar_dag_metadata_lista(dag_ids: list[str]) -> dict[str, dict[str, Any]]:
    """Eu carrego metadados leves das DAGs para enriquecer a listagem."""
    dagbag = _obter_dagbag()
    if dagbag is None:
        return {}

    retorno: dict[str, dict[str, Any]] = {}
    for dag_id in dag_ids:
        try:
            dag = dagbag.get_dag(dag_id)
        except Exception:
            dag = None

        retorno[dag_id] = {
            "nome": _obter_nome_dag(dag, dag_id=dag_id),
            "descricao_curta": _obter_descricao_dag(dag, dag_id=dag_id),
            "owner": _obter_owner_dag(dag),
            "dag_tags": _obter_tags_dag(dag),
            "dag_carregada_no_dagbag": dag is not None,
        }

    return retorno


def listar_execucoes_reais(
    dag_id: str | None = None,
    status: str | None = None,
    limite: int = 50,
) -> dict[str, Any]:
    """Eu listo execuções reais do Airflow para alimentar o catálogo do plugin."""
    limite = max(1, min(int(limite), 500))

    with create_session() as session:
        query = session.query(DagRun)

        if dag_id:
            query = query.filter(DagRun.dag_id.ilike(f"%{str(dag_id).strip()}%"))

        if status:
            query = query.filter(DagRun.state == str(status).strip())

        campo_ordenacao = getattr(DagRun, "logical_date", None)
        if campo_ordenacao is None:
            campo_ordenacao = getattr(DagRun, "execution_date", None)

        if campo_ordenacao is not None:
            execucoes = query.order_by(campo_ordenacao.desc()).limit(limite).all()
        else:
            execucoes = query.limit(limite).all()

    mapa_dags = _buscar_dag_metadata_lista([str(item.dag_id) for item in execucoes])

    itens: list[dict[str, Any]] = []
    for dagrun in execucoes:
        dag_id_item = str(dagrun.dag_id)
        metadata_dag = mapa_dags.get(dag_id_item, {})

        itens.append(
            {
                "dag_id": dag_id_item,
                "run_id": dagrun.run_id,
                "status": str(dagrun.state) if dagrun.state else None,
                "run_type": str(dagrun.run_type) if getattr(dagrun, "run_type", None) else None,
                "execution_date": _serializar_datetime(_obter_execution_date_ou_logical_date(dagrun)),
                "start_date": _serializar_datetime(getattr(dagrun, "start_date", None)),
                "end_date": _serializar_datetime(getattr(dagrun, "end_date", None)),
                "duration_seconds": _calcular_duracao_segundos_dagrun(dagrun),
                "nome": metadata_dag.get("nome") or _humanizar_texto(dag_id_item),
                "descricao_curta": metadata_dag.get("descricao_curta"),
                "owner": metadata_dag.get("owner"),
                "dag_tags": metadata_dag.get("dag_tags", []),
                "dag_carregada_no_dagbag": bool(metadata_dag.get("dag_carregada_no_dagbag")),
            }
        )

    return {"total": len(itens), "itens": itens}


def montar_dashboard_real(dag_id: str, run_id: str | None = None) -> dict[str, Any]:
    """Eu monto o dashboard real da DAG usando DagRun, TaskInstance, DAG e XComs persistidos."""
    dagrun = _obter_dagrun_real(dag_id=dag_id, run_id=run_id)
    if dagrun is None:
        raise ValueError(f"Nenhum DagRun encontrado para a DAG '{dag_id}'.")

    dag = _obter_dag_real(dag_id)
    task_instances = _obter_task_instances(dag_id=dag_id, run_id=dagrun.run_id)
    mapa_ti = _obter_task_instances_por_task_id(task_instances)

    tarefas: list[dict[str, Any]] = []

    for task in _obter_tasks_para_montagem(dag=dag, task_instances=task_instances):
        task_id = str(getattr(task, "task_id", "") or "").strip()
        if not task_id:
            continue

        ti = mapa_ti.get(task_id)
        metadata_auditoria = _montar_metadata_task_xcoms(dag_id=dag_id, run_id=dagrun.run_id, task_id=task_id)

        objetivo = _primeiro_preenchido(
            metadata_auditoria.get("objetivo"),
            metadata_auditoria.get("goal"),
            metadata_auditoria.get("resumo"),
            metadata_auditoria.get("descricao_etapa"),
            metadata_auditoria.get("descricao"),
            getattr(task, "doc_md", None),
            "Sem objetivo documentado.",
        )
        descricao = _primeiro_preenchido(
            metadata_auditoria.get("descricao"),
            metadata_auditoria.get("description"),
            metadata_auditoria.get("descricao_etapa"),
            getattr(task, "doc_md", None),
            "Sem descrição documentada.",
        )

        sql_real = _sql_real_da_task(task=task, metadata_auditoria=metadata_auditoria)
        objetos = _extrair_objetos_task(task=task, metadata_auditoria=metadata_auditoria)
        tabela_normalizada = _normalizar_amostra_dados(metadata_auditoria=metadata_auditoria, objetos=objetos)
        guia_transformacoes = _normalizar_guia_transformacoes(metadata_auditoria)
        regras_upsert = _normalizar_regras_upsert(metadata_auditoria)
        metricas_extras = _extrair_metricas_extras(metadata_auditoria)

        task_nome = getattr(task, "task_display_name", None) or task_id
        task_tipo = _obter_tipo_task(task, ti=ti)
        task_status = str(getattr(ti, "state", None) or "none")
        linhas_processadas = _primeiro_preenchido(
            metadata_auditoria.get("linhas_processadas"),
            metadata_auditoria.get("rows_processed"),
            metadata_auditoria.get("quantidade_linhas"),
            metadata_auditoria.get("row_count"),
            metadata_auditoria.get("linhas_lidas"),
            metadata_auditoria.get("linhas_inseridas"),
            metadata_auditoria.get("linhas_atualizadas"),
            metricas_extras.get("quantidade_linhas"),
        )

        if not _valor_preenchido(linhas_processadas):
            linhas_processadas = len(tabela_normalizada.get("linhas", [])) if tabela_normalizada.get("linhas") else "Não registrado"

        tarefas.append(
            {
                "id": task_id,
                "task_id": task_id,
                "nome": str(task_nome),
                "nome_amigavel": str(_primeiro_preenchido(metadata_auditoria.get("nome_amigavel"), task_nome)),
                "task_label": str(task_nome),
                "status": task_status,
                "subtitulo": task_tipo,
                "tipo": task_tipo,
                "operator": task_tipo,
                "etapa": _primeiro_preenchido(
                    metadata_auditoria.get("etapa"),
                    metadata_auditoria.get("stage"),
                    metadata_auditoria.get("descricao_etapa"),
                    task_id,
                ),
                "objetivo": str(objetivo),
                "operacao": _primeiro_preenchido(
                    metadata_auditoria.get("operacao"),
                    metadata_auditoria.get("operation"),
                    metadata_auditoria.get("nome_amigavel"),
                    task_id,
                ),
                "descricao": str(descricao),
                "origem_dados": _primeiro_preenchido(metadata_auditoria.get("origem_dados"), metadata_auditoria.get("source")),
                "destino_dados": _primeiro_preenchido(metadata_auditoria.get("destino_dados"), metadata_auditoria.get("target")),
                "origem_tabela": metadata_auditoria.get("origem_tabela"),
                "destino_tabela": metadata_auditoria.get("destino_tabela"),
                "fonte_dados": tabela_normalizada.get("fonte"),
                "metricas_extras": metricas_extras,
                "validacoes": _garantir_lista(metadata_auditoria.get("validacoes")),
                "observacoes": _garantir_lista(metadata_auditoria.get("observacoes")),
                "metricas": {
                    "linhas_processadas": _serializar_valor(linhas_processadas),
                    "tempo_execucao": _primeiro_preenchido(
                        metadata_auditoria.get("tempo_execucao"),
                        metadata_auditoria.get("execution_time"),
                        _calcular_tempo_execucao_ti(ti),
                        "Não registrado",
                    ),
                    "tentativas": _primeiro_preenchido(
                        metadata_auditoria.get("tentativas"),
                        metadata_auditoria.get("tries"),
                        getattr(ti, "try_number", None) if ti else None,
                        "Não registrado",
                    ),
                    "ultimo_status": _primeiro_preenchido(
                        metadata_auditoria.get("ultimo_status"),
                        metadata_auditoria.get("status"),
                        task_status,
                    ),
                },
                "objetos": objetos,
                "sql": sql_real,
                "sql_preview": sql_real,
                "tabela": tabela_normalizada,
                "table": tabela_normalizada,
                "table_preview": tabela_normalizada,
                "amostra_dados": tabela_normalizada.get("linhas", []),
                "guia_transformacoes": guia_transformacoes,
                "transformation_guide": guia_transformacoes,
                "regras_upsert": regras_upsert,
                "tags": _obter_tags_task(task, metadata_auditoria),
                "upstream_task_ids": sorted(list(getattr(task, "upstream_task_ids", set()) or [])),
                "downstream_task_ids": sorted(list(getattr(task, "downstream_task_ids", set()) or [])),
                "task_doc_md": getattr(task, "doc_md", None),
                "log_url": getattr(ti, "log_url", None) if ti else None,
                "start_date": _serializar_datetime(getattr(ti, "start_date", None)) if ti else None,
                "end_date": _serializar_datetime(getattr(ti, "end_date", None)) if ti else None,
                "xcom_keys_encontradas": metadata_auditoria.get("_xcom_keys_encontradas", []),
                "metadata_auditoria": metadata_auditoria,
            }
        )

    descricao_dag = _obter_descricao_dag(dag, dag_id=dag_id)
    health = _montar_health(task_instances=task_instances, dagrun=dagrun)
    tags_dag = _obter_tags_dag(dag)

    return {
        "dag_id": getattr(dag, "dag_id", dag_id) or dag_id,
        "run_id": dagrun.run_id,
        "nome": _obter_nome_dag(dag, dag_id=dag_id),
        "dag_descricao": descricao_dag,
        "descricao_curta": descricao_dag,
        "descricao": descricao_dag,
        "status": str(dagrun.state) if dagrun.state else "unknown",
        "proxima_execucao": None,
        "ultima_execucao": _serializar_datetime(_obter_execution_date_ou_logical_date(dagrun)),
        "documentacao_dag": getattr(dag, "doc_md", None) if dag else None,
        "agendamento": _obter_schedule_dag(dag),
        "inicio": _serializar_datetime(getattr(dagrun, "start_date", None)),
        "fim": _serializar_datetime(getattr(dagrun, "end_date", None)),
        "owner": _obter_owner_dag(dag),
        "tags": tags_dag,
        "health": health,
        "tasks": tarefas,
        "dag_carregada_no_dagbag": dag is not None,
    }


__all__ = [
    "CHAVES_XCOM_AUDITORIA",
    "listar_execucoes_reais",
    "montar_dashboard_real",
]
