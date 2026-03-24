from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict, is_dataclass
from datetime import datetime
from typing import Any

from airflow.models import DagRun, TaskInstance
from airflow.models.dagbag import DagBag
from airflow.models.xcom import XComModel
from airflow.utils.session import create_session

try:
    from hooks.BancodeDados.SqlServer import HookSqlServer
except Exception:  # pragma: no cover
    HookSqlServer = None


logger = logging.getLogger(__name__)

CHAVE_XCOM_AUDITORIA = "dag_monitoring_auditoria"
CHAVES_XCOM_AUDITORIA_COMPATIVEIS = (
    "dag_monitoring_auditoria",
    "auditoria_resumo_execucao",
    "auditoria_execucao",
    "task_auditoria",
    "task_audit",
    "metadata_auditoria",
)

PADRAO_IDENTIFICADOR_SQL = re.compile(
    r"(?i)\b(?:from|join|into|update|merge\s+into|truncate\s+table|delete\s+from|insert\s+into)\s+"
    r"((?:\[[^\]]+\]|\w+)(?:\.(?:\[[^\]]+\]|\w+)){0,2})"
)

PADRAO_PROCEDURE_SQL = re.compile(
    r"(?i)\bexec(?:ute)?\s+((?:\[[^\]]+\]|\w+)(?:\.(?:\[[^\]]+\]|\w+)){0,2})"
)

PADRAO_REFERENCIA_QUALIFICADA_TEXTO = re.compile(
    r"(?<![\w])((?:\[[^\]]+\]|\w+)(?:\.(?:\[[^\]]+\]|\w+)){1,2})(?![\w])"
)

PADRAO_IDENTIFICADOR_SIMPLES = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

EXTENSOES_ARQUIVO_BAIXAVEL = {
    "xlsx",
    "xls",
    "csv",
    "parquet",
    "json",
    "txt",
    "zip",
    "gz",
    "pdf",
    "css",
    "js",
    "sql",
    "yaml",
    "yml",
    "xml",
    "html",
    "htm",
    "md",
    "log",
    "feather",
    "orc",
}


def _humanizar_texto(texto: str | None) -> str:
    """Eu transformo texto técnico em texto amigável."""
    if not texto:
        return "-"

    partes = [parte for parte in re.split(r"[_\-.]+", str(texto)) if parte]
    if not partes:
        return str(texto)

    return " ".join(parte.capitalize() for parte in partes)


def _serializar_datetime(valor: Any) -> str | None:
    """Eu transformo datetime em string segura para o front."""
    if valor is None:
        return None

    if isinstance(valor, datetime):
        return valor.strftime("%Y-%m-%d %H:%M:%S")

    return str(valor)


def _valor_preenchido(valor: Any) -> bool:
    """Eu verifico se um valor está realmente preenchido."""
    if valor is None:
        return False

    if isinstance(valor, str):
        return bool(valor.strip())

    if isinstance(valor, (list, tuple, set, dict)):
        return len(valor) > 0

    return True


def _primeiro_preenchido(*valores: Any) -> Any:
    """Eu retorno o primeiro valor útil."""
    for valor in valores:
        if _valor_preenchido(valor):
            return valor
    return None


def _garantir_lista(valor: Any) -> list[Any]:
    """Eu garanto que o retorno seja uma lista."""
    if valor is None:
        return []

    if isinstance(valor, list):
        return valor

    if isinstance(valor, tuple):
        return list(valor)

    if isinstance(valor, set):
        return list(valor)

    return [valor]


def _converter_objeto_para_dict(valor: Any) -> dict[str, Any]:
    """
    Eu tento converter objetos Python comuns em dicionário.

    Isso é essencial porque o resumo de auditoria pode ter sido gravado
    como dataclass, Pydantic model, objeto simples ou mapping.
    """
    if valor is None:
        return {}

    if isinstance(valor, dict):
        return valor

    if is_dataclass(valor):
        try:
            convertido = asdict(valor)
            return convertido if isinstance(convertido, dict) else {}
        except Exception:
            pass

    if hasattr(valor, "model_dump"):
        try:
            convertido = valor.model_dump()
            return convertido if isinstance(convertido, dict) else {}
        except Exception:
            pass

    if hasattr(valor, "dict"):
        try:
            convertido = valor.dict()
            return convertido if isinstance(convertido, dict) else {}
        except Exception:
            pass

    if hasattr(valor, "items"):
        try:
            convertido = dict(valor)
            return convertido if isinstance(convertido, dict) else {}
        except Exception:
            pass

    if hasattr(valor, "__dict__"):
        try:
            bruto = {
                chave: item
                for chave, item in vars(valor).items()
                if not str(chave).startswith("_")
            }
            return bruto if isinstance(bruto, dict) else {}
        except Exception:
            pass

    return {}


def _normalizar_valor_xcom(valor: Any) -> dict[str, Any]:
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
            if isinstance(convertido, dict):
                return convertido

            if isinstance(convertido, list):
                return {"amostra": convertido}

            return {"valor": convertido}
        except Exception:
            return {"valor": texto}

    if isinstance(valor, dict):
        return valor

    if isinstance(valor, list):
        return {"amostra": valor}

    convertido_objeto = _converter_objeto_para_dict(valor)
    if convertido_objeto:
        return convertido_objeto

    return {"valor": valor}


def _mesclar_dicionarios_priorizando_base(
    base: dict[str, Any],
    complemento: dict[str, Any],
) -> dict[str, Any]:
    """
    Eu faço merge de dicionários preservando o valor já preenchido na base.

    Regra:
    - se a base já tem valor útil, ela vence
    - se a base não tem, eu uso o complemento
    - em subdicionários, eu mesclo recursivamente
    """
    resultado = dict(base)

    for chave, valor_complemento in (complemento or {}).items():
        valor_base = resultado.get(chave)

        if isinstance(valor_base, dict) and isinstance(valor_complemento, dict):
            resultado[chave] = _mesclar_dicionarios_priorizando_base(
                valor_base,
                valor_complemento,
            )
            continue

        if not _valor_preenchido(valor_base) and _valor_preenchido(valor_complemento):
            resultado[chave] = valor_complemento

    return resultado


def _obter_execution_date_ou_logical_date(dagrun: DagRun | None) -> Any:
    """Eu tento obter a data lógica da execução, respeitando variações entre versões."""
    if dagrun is None:
        return None

    if hasattr(dagrun, "logical_date") and dagrun.logical_date is not None:
        return dagrun.logical_date

    if hasattr(dagrun, "execution_date") and dagrun.execution_date is not None:
        return dagrun.execution_date

    return None


def _obter_schedule_dag(dag) -> str | None:
    """Eu tento obter o agendamento real da DAG com fallback entre versões."""
    if dag is None:
        return None

    valor = getattr(dag, "schedule_interval", None)
    if valor is not None:
        return str(valor)

    valor = getattr(dag, "schedule", None)
    if valor is not None:
        return str(valor)

    valor = getattr(dag, "timetable_description", None)
    if valor is not None:
        return str(valor)

    return None


def _obter_owner_dag(dag) -> str | None:
    """Eu tento obter o owner da DAG de forma segura."""
    if dag is None:
        return None

    owner = getattr(dag, "owner", None)
    if owner:
        return str(owner)

    owners = getattr(dag, "owners", None)
    if owners:
        try:
            return ", ".join(sorted(str(item) for item in owners if item))
        except Exception:
            return str(owners)

    return None


def _obter_descricao_dag(dag, dag_id: str | None = None) -> str:
    """Eu monto uma descrição amigável para a DAG."""
    if dag is None:
        if dag_id:
            return (
                "A DAG não está carregada no DagBag deste processo do Airflow. "
                "Mesmo assim, o dashboard foi montado a partir do DagRun, "
                "das Task Instances e dos XComs persistidos."
            )
        return "Sem descrição cadastrada."

    descricao = getattr(dag, "description", None)
    if descricao:
        return str(descricao)

    doc_md = getattr(dag, "doc_md", None)
    if doc_md:
        return str(doc_md)

    return "Sem descrição cadastrada."


def _obter_nome_dag(dag, dag_id: str | None = None) -> str:
    """Eu monto um nome amigável para a DAG."""
    if dag is None:
        return _humanizar_texto(dag_id) if dag_id else "-"

    descricao = getattr(dag, "description", None)
    if descricao:
        return str(descricao)

    dag_id_real = getattr(dag, "dag_id", None)
    if dag_id_real:
        return _humanizar_texto(dag_id_real)

    return _humanizar_texto(dag_id) if dag_id else "-"


def _obter_dag_real(dag_id: str):
    """Eu busco a DAG real carregada no Airflow."""
    try:
        dagbag = DagBag(include_examples=False)
        dag = dagbag.get_dag(dag_id)

        if dag is None:
            import_errors = getattr(dagbag, "import_errors", {}) or {}
            if import_errors:
                logger.warning(
                    "DAG '%s' não encontrada no DagBag. Existem %s erro(s) de importação no DagBag atual.",
                    dag_id,
                    len(import_errors),
                )
            else:
                logger.warning(
                    "DAG '%s' não encontrada no DagBag e nenhum import_error explícito foi retornado.",
                    dag_id,
                )

        return dag
    except Exception:
        logger.exception("Falha ao carregar a DAG '%s' a partir do DagBag.", dag_id)
        return None


def _obter_dagrun_real(dag_id: str, run_id: str | None):
    """Eu busco o DagRun real informado, ou o último se run_id vier vazio."""
    with create_session() as session:
        query = session.query(DagRun).filter(DagRun.dag_id == dag_id)

        campo_ordenacao = getattr(DagRun, "logical_date", None)
        if campo_ordenacao is None:
            campo_ordenacao = getattr(DagRun, "execution_date", None)

        if run_id:
            query = query.filter(DagRun.run_id == run_id)

        if campo_ordenacao is not None:
            dagrun = query.order_by(campo_ordenacao.desc()).first()
        else:
            dagrun = query.first()

        return dagrun


def _obter_task_instances(dag_id: str, run_id: str) -> list[TaskInstance]:
    """Eu busco as task instances reais daquela execução."""
    with create_session() as session:
        task_instances = (
            session.query(TaskInstance)
            .filter(TaskInstance.dag_id == dag_id, TaskInstance.run_id == run_id)
            .all()
        )

    return sorted(
        task_instances,
        key=lambda ti: (
            _serializar_datetime(getattr(ti, "start_date", None)) or "9999-12-31 23:59:59",
            getattr(ti, "task_id", "") or "",
        ),
    )


def _obter_task_instances_por_task_id(task_instances: list[TaskInstance]) -> dict[str, TaskInstance]:
    """Eu indexo as task instances por task_id."""
    return {ti.task_id: ti for ti in task_instances if getattr(ti, "task_id", None)}


class _TaskFallback:
    """Eu represento uma task sintética quando a DAG não está carregada no DagBag."""

    def __init__(
        self,
        task_id: str,
        tipo_task: str = "TaskInstance",
        task_display_name: str | None = None,
        doc_md: str | None = None,
    ) -> None:
        self.task_id = str(task_id)
        self.task_display_name = str(task_display_name or task_id)
        self.doc_md = doc_md
        self.upstream_task_ids: set[str] = set()
        self.downstream_task_ids: set[str] = set()
        self._tipo_task = str(tipo_task or "TaskInstance")


def _obter_operador_ti(ti: TaskInstance | None) -> str:
    """Eu tento descobrir o operador real da TaskInstance."""
    if ti is None:
        return "TaskInstance"

    candidatos = [
        getattr(ti, "operator", None),
        getattr(ti, "operator_name", None),
        getattr(ti, "task_type", None),
    ]

    for valor in candidatos:
        if _valor_preenchido(valor):
            return str(valor)

    return "TaskInstance"


def _obter_tasks_sinteticas(task_instances: list[TaskInstance]) -> list[_TaskFallback]:
    """Eu crio tasks sintéticas a partir das Task Instances quando a DAG não veio do DagBag."""
    tasks_sinteticas: list[_TaskFallback] = []
    vistos: set[str] = set()

    for ti in task_instances:
        task_id = str(getattr(ti, "task_id", "") or "").strip()
        if not task_id or task_id in vistos:
            continue

        vistos.add(task_id)
        tasks_sinteticas.append(
            _TaskFallback(
                task_id=task_id,
                tipo_task=_obter_operador_ti(ti),
                task_display_name=task_id,
                doc_md=None,
            )
        )

    return tasks_sinteticas


def _obter_tasks_para_montagem(dag, task_instances: list[TaskInstance]) -> list[Any]:
    """Eu devolvo as tasks reais; se não houver DAG carregada, uso fallback sintético."""
    tasks_reais = _obter_ordem_real_tasks(dag)
    if tasks_reais:
        return tasks_reais

    return _obter_tasks_sinteticas(task_instances)


def _obter_tipo_task(task, ti: TaskInstance | None = None) -> str:
    """Eu devolvo o tipo mais útil da task para exibição."""
    tipo_fallback = getattr(task, "_tipo_task", None)
    if _valor_preenchido(tipo_fallback):
        return str(tipo_fallback)

    tipo_classe = getattr(task.__class__, "__name__", None)
    if _valor_preenchido(tipo_classe) and str(tipo_classe) != "_TaskFallback":
        return str(tipo_classe)

    return _obter_operador_ti(ti)


def _calcular_tempo_execucao_ti(ti: TaskInstance | None) -> str | None:
    """Eu calculo o tempo de execução da task instance de forma segura."""
    if not ti:
        return None

    if not ti.start_date or not ti.end_date:
        return None

    try:
        return str(ti.end_date - ti.start_date)
    except Exception:
        return None


def _calcular_duracao_segundos_dagrun(dagrun: DagRun) -> float | None:
    """Eu calculo a duração em segundos da execução."""
    if not dagrun.start_date or not dagrun.end_date:
        return None

    try:
        return round((dagrun.end_date - dagrun.start_date).total_seconds(), 3)
    except Exception:
        return None


def _montar_health(task_instances: list[TaskInstance], dagrun: DagRun | None) -> dict[str, Any]:
    """Eu calculo health real com base nas task instances."""
    total = len(task_instances)
    sucesso = sum(1 for ti in task_instances if str(ti.state).lower() == "success")
    falha = sum(1 for ti in task_instances if str(ti.state).lower() in {"failed", "upstream_failed"})
    executando = sum(
        1 for ti in task_instances if str(ti.state).lower() in {"running", "queued", "scheduled"}
    )
    percentual = round((sucesso / total) * 100, 2) if total else 0.0

    return {
        "dag": str(dagrun.state) if dagrun and dagrun.state else None,
        "tasks_saudaveis": f"{sucesso}/{total}" if total else "0/0",
        "tasks_sucesso": sucesso,
        "tasks_falha": falha,
        "tasks_executando": executando,
        "broker": None,
        "fila": executando,
        "executando": executando,
        "workers_online": None,
        "percentual_geral": percentual,
    }


def _obter_valor_xcom_bruto_registro(registro: XComModel) -> Any:
    """Eu tento desserializar o XCom preservando o valor bruto."""
    try:
        if hasattr(registro, "orm_deserialize_value"):
            return registro.orm_deserialize_value()
    except Exception:
        pass

    try:
        if hasattr(type(registro), "deserialize_value"):
            return type(registro).deserialize_value(registro)
    except Exception:
        pass

    try:
        return getattr(registro, "value", None)
    except Exception:
        return None


def _obter_registros_xcom_task(dag_id: str, run_id: str, task_id: str) -> list[XComModel]:
    """Eu busco todos os XComs daquela task naquela execução."""
    with create_session() as session:
        query = session.query(XComModel).filter(
            XComModel.dag_id == dag_id,
            XComModel.run_id == run_id,
            XComModel.task_id == task_id,
        )

        campo_timestamp = getattr(XComModel, "timestamp", None)
        if campo_timestamp is not None:
            query = query.order_by(campo_timestamp.desc())

        return list(query.all())


def _eh_chave_auditoria(chave: str | None) -> bool:
    """Eu reconheço chaves compatíveis com payload de auditoria."""
    chave_normalizada = str(chave or "").strip().lower()

    if not chave_normalizada:
        return False

    if chave_normalizada in CHAVES_XCOM_AUDITORIA_COMPATIVEIS:
        return True

    if "auditoria" in chave_normalizada:
        return True

    if "audit" in chave_normalizada:
        return True

    if "resumo_execucao" in chave_normalizada:
        return True

    return False


def _desembrulhar_dicionario_auditoria(payload: dict[str, Any]) -> dict[str, Any]:
    """
    Eu tento encontrar o miolo real da auditoria quando ela vem embrulhada.
    """
    if not isinstance(payload, dict):
        return {}

    candidatos = [
        payload,
        payload.get("resumo"),
        payload.get("auditoria"),
        payload.get("audit"),
        payload.get("metadata"),
        payload.get("data"),
        payload.get("payload"),
        payload.get("resultado"),
        payload.get("result"),
    ]

    melhor = {}
    for candidato in candidatos:
        if isinstance(candidato, dict) and len(candidato) > len(melhor):
            melhor = candidato

    return melhor if isinstance(melhor, dict) else {}


def _montar_metadata_task_xcoms(
    dag_id: str,
    run_id: str,
    task_id: str,
) -> dict[str, Any]:
    """
    Eu consolido múltiplos XComs de uma task em uma metadata única.
    """
    registros = _obter_registros_xcom_task(dag_id=dag_id, run_id=run_id, task_id=task_id)

    metadata: dict[str, Any] = {}
    retorno_task = None
    chaves_xcom: list[str] = []

    for registro in registros:
        chave = str(getattr(registro, "key", "") or "")
        chaves_xcom.append(chave)

        bruto = _obter_valor_xcom_bruto_registro(registro)
        normalizado = _normalizar_valor_xcom(bruto)
        normalizado = _desembrulhar_dicionario_auditoria(normalizado)

        if _eh_chave_auditoria(chave):
            metadata = _mesclar_dicionarios_priorizando_base(metadata, normalizado)
            continue

        if chave == "return_value" and retorno_task is None:
            retorno_task = bruto

            if isinstance(bruto, dict):
                metadata = _mesclar_dicionarios_priorizando_base(
                    metadata,
                    _desembrulhar_dicionario_auditoria(_normalizar_valor_xcom(bruto)),
                )
            continue

        if chave.lower() in {"sql", "query", "consulta"} and not _valor_preenchido(metadata.get("sql_real")):
            metadata["sql_real"] = bruto
            continue

        if chave.lower() in {"sample_data", "amostra_dados", "preview"} and not _valor_preenchido(
            metadata.get("amostra_dados")
        ):
            metadata["amostra_dados"] = bruto
            continue

    if retorno_task is not None and not _valor_preenchido(
        _primeiro_preenchido(
            metadata.get("amostra_dados"),
            metadata.get("sample_data"),
            metadata.get("dados_amostra"),
            metadata.get("amostra"),
            metadata.get("preview"),
            metadata.get("table_preview"),
        )
    ):
        metadata["amostra_dados"] = retorno_task
        metadata.setdefault("fonte_dados", "XCom return_value")

    metadata["_xcom_keys_encontradas"] = chaves_xcom
    return metadata


def _extrair_metricas_extras(metadata_auditoria: dict[str, Any]) -> dict[str, Any]:
    """Eu localizo metricas_extras mesmo quando a auditoria foi serializada como objeto."""
    metricas_extras = metadata_auditoria.get("metricas_extras")
    if isinstance(metricas_extras, dict):
        return metricas_extras

    extras = metadata_auditoria.get("extras")
    if isinstance(extras, dict):
        return extras

    return {}


def _limpar_identificador_sql_bruto(valor: str | None) -> list[str]:
    """Eu removo colchetes/aspas e separo um nome qualificado em partes."""
    texto = str(valor or "").strip()
    if not texto:
        return []

    texto = texto.replace("[", "").replace("]", "").replace('"', "").replace("`", "")
    partes = [parte.strip() for parte in texto.split(".") if parte.strip()]
    return partes


def _obter_conn_id_sqlserver_padrao() -> str | None:
    """
    Eu obtenho o conn_id padrão do hook oficial de SQL Server do projeto.

    Se o hook estiver disponível, eu uso o valor padrão real do construtor.
    Se o import falhar por ambiente de teste/import circular, eu uso o fallback
    padrão do próprio projeto.
    """
    if HookSqlServer is not None:
        try:
            conn_id = str(HookSqlServer().conn_id or "").strip()
            if conn_id:
                return conn_id
        except Exception:
            logger.exception("Falha ao obter conn_id padrão a partir do HookSqlServer.")

    return "mssql_integracao"


def _parece_caminho_arquivo(texto: str | None) -> bool:
    """Eu identifico se a string parece representar um arquivo e não uma tabela."""
    valor = str(texto or "").strip()
    if not valor:
        return False

    if "/" in valor or "\\" in valor:
        return True

    nome_final = valor.split("?")[0].split("#")[0].split("/")[-1].split("\\")[-1]
    if "." not in nome_final:
        return False

    extensao = nome_final.rsplit(".", 1)[-1].strip().lower()
    return extensao in EXTENSOES_ARQUIVO_BAIXAVEL


def _dividir_segmentos_objeto_textual(texto: str | None) -> list[str]:
    """Eu separo possíveis múltiplos objetos declarados em uma única string."""
    valor = str(texto or "").strip()
    if not valor:
        return []

    segmentos = re.split(r"\s*(?:\+|;|\||\n|\r)\s*", valor)
    return [segmento.strip() for segmento in segmentos if segmento and segmento.strip()]


def _montar_objeto_referencia_struct(
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
    """Eu monto um objeto já no contrato final do front."""
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


def _aplicar_conn_id_padrao_em_objeto(
    objeto: dict[str, Any],
    conn_id_padrao: str | None,
) -> dict[str, Any]:
    """Eu completo conn_id/visualizavel quando a referência é de tabela e faltou a conexão."""
    resultado = dict(objeto)

    tipo = str(resultado.get("tipo", "") or "").strip().lower()
    schema = resultado.get("schema")
    tabela = resultado.get("tabela")
    caminho_arquivo = resultado.get("caminho_arquivo")

    if tipo == "tabela" and not caminho_arquivo:
        if not resultado.get("conn_id") and conn_id_padrao and schema and tabela:
            resultado["conn_id"] = conn_id_padrao

        resultado["visualizavel"] = bool(resultado.get("conn_id") and schema and tabela)
    else:
        resultado["visualizavel"] = False

    resultado["downloadable"] = bool(caminho_arquivo)
    return resultado


def _extrair_referencias_textuais_multiplas(
    texto: str | None,
    *,
    direcao: str,
    tipo_padrao: str,
    conn_id_padrao: str | None,
) -> list[dict[str, Any]]:
    """
    Eu extraio múltiplas referências quando a auditoria mandou tudo em uma única string.

    Exemplos tratados:
    - dbo.MovimentacaoFinanceiro + dbo.MovFinWatermark
    - MovimentacaoFinanceiro + dbo.MovFinWatermark
    - banco.dbo.TabelaA + dbo.TabelaB
    """
    valor = str(texto or "").strip()
    if not valor:
        return []

    if _parece_caminho_arquivo(valor):
        return [
            _montar_objeto_referencia_struct(
                tipo="Arquivo",
                nome=valor,
                descricao="Arquivo inferido a partir do texto da auditoria.",
                conn_id=None,
                banco=None,
                schema=None,
                tabela=None,
                caminho_arquivo=valor,
                formato=None,
                direcao=direcao,
            )
        ]

    referencias_extraidas: list[dict[str, Any]] = []

    referencias_qualificadas = []
    for match in PADRAO_REFERENCIA_QUALIFICADA_TEXTO.finditer(valor):
        bruto = match.group(1)
        partes = _limpar_identificador_sql_bruto(bruto)
        if len(partes) in {2, 3}:
            referencias_qualificadas.append(partes)

    banco_padrao = None
    schema_padrao = None

    for partes in referencias_qualificadas:
        if len(partes) == 3:
            banco_padrao, schema_padrao, _ = partes
            break
        if len(partes) == 2 and not schema_padrao:
            schema_padrao, _ = partes

    for partes in referencias_qualificadas:
        banco = None
        schema = None
        tabela = None

        if len(partes) == 3:
            banco, schema, tabela = partes
        elif len(partes) == 2:
            schema, tabela = partes

        nome = (
            f"{banco}.{schema}.{tabela}"
            if banco and schema and tabela
            else f"{schema}.{tabela}"
        )

        referencias_extraidas.append(
            _montar_objeto_referencia_struct(
                tipo="Tabela",
                nome=nome,
                descricao="Tabela inferida a partir do texto da auditoria.",
                conn_id=conn_id_padrao,
                banco=banco,
                schema=schema,
                tabela=tabela,
                caminho_arquivo=None,
                formato=None,
                direcao=direcao,
            )
        )

    for segmento in _dividir_segmentos_objeto_textual(valor):
        if PADRAO_REFERENCIA_QUALIFICADA_TEXTO.search(segmento):
            continue

        if _parece_caminho_arquivo(segmento):
            referencias_extraidas.append(
                _montar_objeto_referencia_struct(
                    tipo="Arquivo",
                    nome=segmento,
                    descricao="Arquivo inferido a partir do texto da auditoria.",
                    conn_id=None,
                    banco=None,
                    schema=None,
                    tabela=None,
                    caminho_arquivo=segmento,
                    formato=None,
                    direcao=direcao,
                )
            )
            continue

        partes = _limpar_identificador_sql_bruto(segmento)
        if len(partes) != 1:
            continue

        identificador = partes[0]
        if not PADRAO_IDENTIFICADOR_SIMPLES.fullmatch(identificador):
            continue

        if not schema_padrao:
            continue

        nome = (
            f"{banco_padrao}.{schema_padrao}.{identificador}"
            if banco_padrao
            else f"{schema_padrao}.{identificador}"
        )

        referencias_extraidas.append(
            _montar_objeto_referencia_struct(
                tipo="Tabela",
                nome=nome,
                descricao="Tabela inferida a partir de nome simples e schema herdado do mesmo texto.",
                conn_id=conn_id_padrao,
                banco=banco_padrao,
                schema=schema_padrao,
                tabela=identificador,
                caminho_arquivo=None,
                formato=None,
                direcao=direcao,
            )
        )

    return _deduplicar_objetos(
        [_aplicar_conn_id_padrao_em_objeto(item, conn_id_padrao) for item in referencias_extraidas]
    )


def _normalizar_referencia_tabela(
    item: Any,
    direcao: str = "neutro",
    tipo_padrao: str = "Tabela",
) -> dict[str, Any] | None:
    """Eu normalizo qualquer referência de tabela/arquivo/procedure para o contrato do front."""
    if item is None:
        return None

    if isinstance(item, dict):
        tipo = _primeiro_preenchido(
            item.get("tipo"),
            item.get("type"),
            item.get("object_type"),
            tipo_padrao,
        )
        conn_id = _primeiro_preenchido(
            item.get("conn_id"),
            item.get("conexao_id"),
            item.get("connection_id"),
        )
        banco = _primeiro_preenchido(
            item.get("banco"),
            item.get("database"),
            item.get("catalog"),
        )
        schema = _primeiro_preenchido(
            item.get("schema"),
            item.get("esquema"),
        )
        tabela = _primeiro_preenchido(
            item.get("tabela"),
            item.get("table"),
            item.get("nome_tabela"),
            item.get("table_name"),
        )
        caminho_arquivo = _primeiro_preenchido(
            item.get("caminho_arquivo"),
            item.get("arquivo"),
            item.get("path"),
            item.get("filepath"),
            item.get("file_path"),
            item.get("filename"),
            item.get("nome_arquivo"),
        )
        formato = _primeiro_preenchido(item.get("formato"), item.get("format"))
        descricao = _primeiro_preenchido(
            item.get("descricao"),
            item.get("description"),
            item.get("detalhe"),
            "",
        )

        nome = _primeiro_preenchido(
            item.get("nome"),
            item.get("name"),
            item.get("objeto"),
            item.get("texto"),
            item.get("referencia"),
            item.get("reference"),
        )

        if not nome and _valor_preenchido(caminho_arquivo):
            nome = str(caminho_arquivo)

        if not nome and _valor_preenchido(tabela):
            if _valor_preenchido(banco):
                nome = f"{banco}.{schema}.{tabela}" if _valor_preenchido(schema) else f"{banco}.{tabela}"
            else:
                nome = f"{schema}.{tabela}" if _valor_preenchido(schema) else str(tabela)

        if not nome:
            nome = "-"

        return _montar_objeto_referencia_struct(
            tipo=str(tipo),
            nome=str(nome),
            descricao=str(descricao or ""),
            conn_id=conn_id,
            banco=banco,
            schema=schema,
            tabela=tabela,
            caminho_arquivo=caminho_arquivo,
            formato=formato,
            direcao=direcao,
        )

    if isinstance(item, str):
        texto = item.strip()
        if not texto:
            return None

        if _parece_caminho_arquivo(texto):
            return _montar_objeto_referencia_struct(
                tipo="Arquivo",
                nome=texto,
                descricao="Arquivo inferido a partir do texto da auditoria.",
                conn_id=None,
                banco=None,
                schema=None,
                tabela=None,
                caminho_arquivo=texto,
                formato=None,
                direcao=direcao,
            )

        partes = _limpar_identificador_sql_bruto(texto)

        if len(partes) == 3:
            banco, schema, tabela = partes
            return _montar_objeto_referencia_struct(
                tipo=tipo_padrao,
                nome=f"{banco}.{schema}.{tabela}",
                descricao="",
                conn_id=None,
                banco=banco,
                schema=schema,
                tabela=tabela,
                caminho_arquivo=None,
                formato=None,
                direcao=direcao,
            )

        if len(partes) == 2:
            schema, tabela = partes
            return _montar_objeto_referencia_struct(
                tipo=tipo_padrao,
                nome=f"{schema}.{tabela}",
                descricao="",
                conn_id=None,
                banco=None,
                schema=schema,
                tabela=tabela,
                caminho_arquivo=None,
                formato=None,
                direcao=direcao,
            )

        return _montar_objeto_referencia_struct(
            tipo=tipo_padrao,
            nome=texto,
            descricao="",
            conn_id=None,
            banco=None,
            schema=None,
            tabela=None,
            caminho_arquivo=None,
            formato=None,
            direcao=direcao,
        )

    return _montar_objeto_referencia_struct(
        tipo=tipo_padrao,
        nome=str(item),
        descricao="",
        conn_id=None,
        banco=None,
        schema=None,
        tabela=None,
        caminho_arquivo=None,
        formato=None,
        direcao=direcao,
    )


def _normalizar_referencias_objeto(
    item: Any,
    *,
    direcao: str,
    tipo_padrao: str,
    conn_id_padrao: str | None,
) -> list[dict[str, Any]]:
    """
    Eu normalizo um item podendo gerar um ou vários objetos finais.

    Regra:
    - se vier estruturado, preservo o que já veio correto;
    - se vier como texto agrupado, separo as referências;
    - se vier sem conn_id mas for tabela válida, aplico o conn_id padrão do hook;
    - se nada der certo, devolvo o objeto genérico original.
    """
    objetos: list[dict[str, Any]] = []

    normalizado_unico = _normalizar_referencia_tabela(
        item=item,
        direcao=direcao,
        tipo_padrao=tipo_padrao,
    )
    if normalizado_unico:
        objetos.append(_aplicar_conn_id_padrao_em_objeto(normalizado_unico, conn_id_padrao))

    textos_candidatos: list[str] = []

    if isinstance(item, dict):
        for chave in (
            "referencia",
            "reference",
            "nome",
            "name",
            "objeto",
            "texto",
        ):
            valor = item.get(chave)
            if isinstance(valor, str) and valor.strip():
                textos_candidatos.append(valor)
    elif isinstance(item, str):
        textos_candidatos.append(item)

    for texto in textos_candidatos:
        objetos.extend(
            _extrair_referencias_textuais_multiplas(
                texto=texto,
                direcao=direcao,
                tipo_padrao=tipo_padrao,
                conn_id_padrao=conn_id_padrao,
            )
        )

    objetos = _deduplicar_objetos(objetos)

    if len(objetos) <= 1:
        return objetos

    objetos_validos = [
        item_objeto
        for item_objeto in objetos
        if item_objeto.get("downloadable")
        or (
            str(item_objeto.get("tipo", "") or "").lower() == "tabela"
            and item_objeto.get("schema")
            and item_objeto.get("tabela")
        )
    ]

    if objetos_validos:
        return _deduplicar_objetos(objetos_validos)

    return objetos


def _deduplicar_objetos(objetos: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Eu removo objetos duplicados sem perder a ordem."""
    vistos: set[tuple[Any, ...]] = set()
    resultado: list[dict[str, Any]] = []

    for item in objetos:
        chave = (
            item.get("tipo"),
            item.get("nome"),
            item.get("direcao"),
            item.get("conn_id"),
            item.get("banco"),
            item.get("schema"),
            item.get("tabela"),
            item.get("caminho_arquivo"),
        )
        if chave in vistos:
            continue

        vistos.add(chave)
        resultado.append(item)

    return resultado


def _inferir_conn_id_task(task, metadata_auditoria: dict[str, Any]) -> str | None:
    """Eu tento descobrir a conexão principal da task."""
    metricas_extras = _extrair_metricas_extras(metadata_auditoria)

    candidatos = [
        metadata_auditoria.get("conn_id"),
        metadata_auditoria.get("conexao_id"),
        metadata_auditoria.get("connection_id"),
        metricas_extras.get("conn_id"),
        metricas_extras.get("conexao_id"),
        getattr(task, "conn_id", None),
        getattr(task, "mssql_conn_id", None),
        getattr(task, "postgres_conn_id", None),
        getattr(task, "mysql_conn_id", None),
        getattr(task, "oracle_conn_id", None),
        getattr(task, "sqlite_conn_id", None),
    ]

    op_kwargs = getattr(task, "op_kwargs", None)
    if isinstance(op_kwargs, dict):
        candidatos.extend(
            [
                op_kwargs.get("conn_id"),
                op_kwargs.get("conexao_id"),
                op_kwargs.get("connection_id"),
            ]
        )

    params = getattr(task, "params", None)
    if isinstance(params, dict):
        candidatos.extend(
            [
                params.get("conn_id"),
                params.get("conexao_id"),
                params.get("connection_id"),
            ]
        )

    conn_id_encontrado = _primeiro_preenchido(*candidatos)
    if _valor_preenchido(conn_id_encontrado):
        return str(conn_id_encontrado)

    return _obter_conn_id_sqlserver_padrao()


def _extrair_objetos_sql(sql_real: str | None, conn_id_padrao: str | None = None) -> list[dict[str, Any]]:
    """Eu extraio objetos a partir do texto SQL quando a task não publicou auditoria suficiente."""
    if not sql_real or not str(sql_real).strip():
        return []

    sql_texto = str(sql_real)
    objetos: list[dict[str, Any]] = []

    for correspondencia in PADRAO_IDENTIFICADOR_SQL.finditer(sql_texto):
        bruto = correspondencia.group(1)
        partes = _limpar_identificador_sql_bruto(bruto)

        banco = None
        schema = None
        tabela = None

        if len(partes) == 3:
            banco, schema, tabela = partes
        elif len(partes) == 2:
            schema, tabela = partes
        elif len(partes) == 1:
            tabela = partes[0]

        direcao = "entrada"
        trecho = correspondencia.group(0).lower()

        if any(token in trecho for token in ["into", "update", "merge into", "truncate table", "insert into"]):
            direcao = "saida"

        nome = None
        if banco and schema and tabela:
            nome = f"{banco}.{schema}.{tabela}"
        elif schema and tabela:
            nome = f"{schema}.{tabela}"
        elif tabela:
            nome = tabela

        objetos.append(
            _montar_objeto_referencia_struct(
                tipo="Tabela",
                nome=nome or bruto,
                descricao="Inferido automaticamente a partir da SQL da task.",
                conn_id=conn_id_padrao,
                banco=banco,
                schema=schema,
                tabela=tabela,
                caminho_arquivo=None,
                formato=None,
                direcao=direcao,
            )
        )

    for correspondencia in PADRAO_PROCEDURE_SQL.finditer(sql_texto):
        bruto = correspondencia.group(1)
        partes = _limpar_identificador_sql_bruto(bruto)

        nome = ".".join(partes) if partes else bruto

        objetos.append(
            _montar_objeto_referencia_struct(
                tipo="Procedure",
                nome=nome,
                descricao="Inferido automaticamente a partir do EXEC/EXECUTE da task.",
                conn_id=conn_id_padrao,
                banco=partes[0] if len(partes) == 3 else None,
                schema=partes[-2] if len(partes) >= 2 else None,
                tabela=partes[-1] if len(partes) >= 1 else None,
                caminho_arquivo=None,
                formato=None,
                direcao="saida",
            )
        )

    return _deduplicar_objetos(
        [_aplicar_conn_id_padrao_em_objeto(item, conn_id_padrao) for item in objetos]
    )


def _extrair_objetos_task(task, metadata_auditoria: dict[str, Any]) -> list[dict[str, Any]]:
    """Eu tento inferir objetos da própria task quando a auditoria veio incompleta."""
    objetos: list[dict[str, Any]] = []
    conn_id_padrao = _inferir_conn_id_task(task, metadata_auditoria)

    metricas_extras = _extrair_metricas_extras(metadata_auditoria)

    referencias_diretas = [
        ("entrada", metricas_extras.get("tabela_origem")),
        ("saida", metricas_extras.get("tabela_destino")),
        ("entrada", metadata_auditoria.get("origem_dados")),
        ("saida", metadata_auditoria.get("destino_dados")),
        ("entrada", metadata_auditoria.get("fonte_dados")),
    ]

    for direcao, referencia in referencias_diretas:
        objetos.extend(
            _normalizar_referencias_objeto(
                item=referencia,
                direcao=direcao,
                tipo_padrao="Tabela",
                conn_id_padrao=conn_id_padrao,
            )
        )

    caminho_arquivo = _primeiro_preenchido(
        getattr(task, "filepath", None),
        getattr(task, "path", None),
        getattr(task, "filename", None),
        getattr(task, "file_path", None),
        metricas_extras.get("arquivo_saida"),
        metricas_extras.get("caminho_arquivo"),
    )
    if caminho_arquivo:
        objetos.append(
            _montar_objeto_referencia_struct(
                tipo="Arquivo",
                nome=str(caminho_arquivo),
                descricao="Arquivo inferido a partir dos atributos da task.",
                conn_id=None,
                banco=None,
                schema=None,
                tabela=None,
                caminho_arquivo=str(caminho_arquivo),
                formato=None,
                direcao="saida",
            )
        )

    schema_task = _primeiro_preenchido(
        getattr(task, "schema", None),
        metricas_extras.get("schema"),
        metricas_extras.get("esquema"),
    )
    tabela_task = _primeiro_preenchido(
        getattr(task, "table", None),
        getattr(task, "table_name", None),
        getattr(task, "tablename", None),
        metricas_extras.get("tabela"),
        metricas_extras.get("nome_tabela"),
    )
    banco_task = _primeiro_preenchido(
        getattr(task, "database", None),
        metricas_extras.get("banco"),
        metricas_extras.get("database"),
    )

    if schema_task and tabela_task:
        objetos.append(
            _montar_objeto_referencia_struct(
                tipo="Tabela",
                nome=f"{schema_task}.{tabela_task}" if not banco_task else f"{banco_task}.{schema_task}.{tabela_task}",
                descricao="Tabela inferida a partir dos atributos do operador.",
                conn_id=conn_id_padrao,
                banco=banco_task,
                schema=schema_task,
                tabela=tabela_task,
                caminho_arquivo=None,
                formato=None,
                direcao="saida",
            )
        )

    return _deduplicar_objetos(
        [_aplicar_conn_id_padrao_em_objeto(item, conn_id_padrao) for item in objetos]
    )


def _montar_objetos(
    metadata_auditoria: dict[str, Any],
    task=None,
    sql_real: str | None = None,
) -> list[dict[str, Any]]:
    """
    Eu transformo os objetos reais da auditoria no formato do front.
    """
    objetos: list[dict[str, Any]] = []
    conn_id_padrao = _inferir_conn_id_task(task, metadata_auditoria) if task else _obter_conn_id_sqlserver_padrao()

    for chave in ("objetos_entrada", "objetos_saida", "objetos"):
        if chave == "objetos_entrada":
            direcao = "entrada"
        elif chave == "objetos_saida":
            direcao = "saida"
        else:
            direcao = "neutro"

        for item in _garantir_lista(metadata_auditoria.get(chave)):
            objetos.extend(
                _normalizar_referencias_objeto(
                    item=item,
                    direcao=direcao,
                    tipo_padrao="Objeto",
                    conn_id_padrao=conn_id_padrao,
                )
            )

    objetos.extend(_extrair_objetos_task(task, metadata_auditoria) if task else [])
    objetos.extend(_extrair_objetos_sql(sql_real=sql_real, conn_id_padrao=conn_id_padrao))

    return _deduplicar_objetos(objetos)


def _procurar_sql_em_mapeamento(mapa: dict[str, Any] | None) -> str | None:
    """Eu procuro campos com SQL em um dict."""
    if not isinstance(mapa, dict):
        return None

    candidatos = [
        mapa.get("sql_real"),
        mapa.get("procedure_real"),
        mapa.get("sql"),
        mapa.get("query"),
        mapa.get("consulta"),
        mapa.get("statement"),
        mapa.get("bash_command"),
    ]

    valor = _primeiro_preenchido(*candidatos)
    if valor is None:
        return None

    return str(valor)


def _sql_real_da_task(task, metadata_auditoria: dict[str, Any]) -> str | None:
    """
    Eu tento obter a SQL real por prioridade.
    """
    valor = _primeiro_preenchido(
        metadata_auditoria.get("sql_real"),
        metadata_auditoria.get("procedure_real"),
        metadata_auditoria.get("sql"),
        metadata_auditoria.get("query"),
        metadata_auditoria.get("consulta"),
        getattr(task, "sql", None),
        getattr(task, "query", None),
        getattr(task, "bash_command", None),
    )

    if valor is None:
        valor = _procurar_sql_em_mapeamento(getattr(task, "op_kwargs", None))

    if valor is None:
        valor = _procurar_sql_em_mapeamento(getattr(task, "params", None))

    if valor is None:
        metricas_extras = _extrair_metricas_extras(metadata_auditoria)
        procedure = _primeiro_preenchido(
            metricas_extras.get("procedure_final"),
            metricas_extras.get("procedure"),
            metadata_auditoria.get("procedure"),
            metadata_auditoria.get("procedure_name"),
        )
        if procedure:
            valor = f"EXEC {procedure};"

    if valor is None:
        return None

    return str(valor)


def _normalizar_guia_transformacoes(metadata_auditoria: dict[str, Any]) -> list[dict[str, Any]]:
    """Eu normalizo o guia de transformações para o formato do front."""
    bruto = _primeiro_preenchido(
        metadata_auditoria.get("guia_transformacoes"),
        metadata_auditoria.get("transformation_guide"),
        metadata_auditoria.get("guia"),
        metadata_auditoria.get("transformacoes"),
    )

    itens = _garantir_lista(bruto)
    guia: list[dict[str, Any]] = []

    for item in itens:
        if isinstance(item, dict):
            guia.append(
                {
                    "titulo": _primeiro_preenchido(
                        item.get("titulo"),
                        item.get("title"),
                        item.get("etapa"),
                        "Transformação",
                    ),
                    "descricao": _primeiro_preenchido(
                        item.get("descricao"),
                        item.get("description"),
                        item.get("detalhe"),
                        "-",
                    ),
                }
            )
        else:
            guia.append(
                {
                    "titulo": "Transformação",
                    "descricao": str(item),
                }
            )

    return guia


def _normalizar_regras_upsert(metadata_auditoria: dict[str, Any]) -> list[dict[str, Any]]:
    """Eu normalizo regras de upsert para o formato do front."""
    bruto = _primeiro_preenchido(
        metadata_auditoria.get("regras_upsert"),
        metadata_auditoria.get("upsert_rules"),
        metadata_auditoria.get("upsert"),
    )

    itens = _garantir_lista(bruto)
    regras: list[dict[str, Any]] = []

    for item in itens:
        if isinstance(item, dict):
            regras.append(
                {
                    "titulo": _primeiro_preenchido(
                        item.get("titulo"),
                        item.get("title"),
                        item.get("nome"),
                        "Regra de Upsert",
                    ),
                    "descricao": _primeiro_preenchido(
                        item.get("descricao"),
                        item.get("description"),
                        item.get("detalhe"),
                        "-",
                    ),
                }
            )
        else:
            regras.append(
                {
                    "titulo": "Regra de Upsert",
                    "descricao": str(item),
                }
            )

    return regras


def _inferir_fonte_dados(
    metadata_auditoria: dict[str, Any],
    objetos: list[dict[str, Any]],
) -> str:
    """Eu tento montar uma fonte amigável para a aba de dados."""
    metricas_extras = _extrair_metricas_extras(metadata_auditoria)

    fonte = _primeiro_preenchido(
        metadata_auditoria.get("fonte_dados"),
        metadata_auditoria.get("origem_dados"),
        metadata_auditoria.get("source"),
        metricas_extras.get("tabela_destino"),
        metricas_extras.get("tabela_origem"),
        metricas_extras.get("origem"),
    )

    if _valor_preenchido(fonte):
        return str(fonte)

    for objeto in objetos:
        if objeto.get("tipo", "").lower() in {"tabela", "tabela_sql"}:
            if objeto.get("banco") and objeto.get("schema") and objeto.get("tabela"):
                return f"{objeto['banco']}.{objeto['schema']}.{objeto['tabela']}"
            if objeto.get("schema") and objeto.get("tabela"):
                return f"{objeto['schema']}.{objeto['tabela']}"
            if objeto.get("nome"):
                return str(objeto["nome"])

    return "Não registrada"


def _normalizar_amostra_dados(
    metadata_auditoria: dict[str, Any],
    objetos: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """
    Eu converto qualquer formato razoável de amostra em tabela compatível com o front.
    """
    objetos = objetos or []
    metricas_extras = _extrair_metricas_extras(metadata_auditoria)

    fonte = _inferir_fonte_dados(metadata_auditoria, objetos)

    bruto = _primeiro_preenchido(
        metadata_auditoria.get("tabela"),
        metadata_auditoria.get("table"),
        metadata_auditoria.get("table_preview"),
        metadata_auditoria.get("preview"),
        metadata_auditoria.get("amostra_dados"),
        metadata_auditoria.get("sample_data"),
        metadata_auditoria.get("dados_amostra"),
        metadata_auditoria.get("amostra"),
        metricas_extras.get("amostra"),
        metricas_extras.get("sample"),
        [],
    )

    if isinstance(bruto, dict):
        colunas = _garantir_lista(
            _primeiro_preenchido(
                bruto.get("colunas"),
                bruto.get("columns"),
                [],
            )
        )

        linhas = _garantir_lista(
            _primeiro_preenchido(
                bruto.get("linhas"),
                bruto.get("rows"),
                bruto.get("dados"),
                bruto.get("data"),
                [],
            )
        )

        fonte = _primeiro_preenchido(
            bruto.get("fonte"),
            bruto.get("source"),
            fonte,
        )

        return {
            "fonte": str(fonte),
            "colunas": [str(coluna) for coluna in colunas],
            "linhas": linhas,
        }

    if isinstance(bruto, list):
        if len(bruto) == 0:
            return {
                "fonte": str(fonte),
                "colunas": [],
                "linhas": [],
            }

        primeiro_item = bruto[0]

        if isinstance(primeiro_item, dict):
            colunas = list(primeiro_item.keys())
            linhas = []

            for item in bruto:
                if isinstance(item, dict):
                    linhas.append([item.get(coluna) for coluna in colunas])
                else:
                    linhas.append([str(item)])

            return {
                "fonte": str(fonte),
                "colunas": [str(coluna) for coluna in colunas],
                "linhas": linhas,
            }

        if isinstance(primeiro_item, (list, tuple)):
            quantidade_colunas = max(
                len(item) if isinstance(item, (list, tuple)) else 1 for item in bruto
            )
            colunas = [f"coluna_{indice + 1}" for indice in range(quantidade_colunas)]
            linhas = []

            for item in bruto:
                if isinstance(item, (list, tuple)):
                    linha = list(item)
                else:
                    linha = [item]

                if len(linha) < quantidade_colunas:
                    linha.extend([None] * (quantidade_colunas - len(linha)))

                linhas.append(linha)

            return {
                "fonte": str(fonte),
                "colunas": colunas,
                "linhas": linhas,
            }

        return {
            "fonte": str(fonte),
            "colunas": ["valor"],
            "linhas": [[item] for item in bruto],
        }

    if isinstance(bruto, str) and bruto.strip():
        return {
            "fonte": str(fonte),
            "colunas": ["valor"],
            "linhas": [[bruto]],
        }

    return {
        "fonte": str(fonte),
        "colunas": [],
        "linhas": [],
    }


def _obter_tags_task(task, metadata_auditoria: dict[str, Any]) -> list[str]:
    """Eu normalizo tags da task."""
    tags = metadata_auditoria.get("tags")

    if isinstance(tags, list):
        return [str(tag) for tag in tags if _valor_preenchido(tag)]

    if isinstance(tags, str) and tags.strip():
        return [tags.strip()]

    extras = _extrair_metricas_extras(metadata_auditoria)
    tags_extras = extras.get("tags")
    if isinstance(tags_extras, list):
        return [str(tag) for tag in tags_extras if _valor_preenchido(tag)]

    return []


def _obter_ordem_real_tasks(dag) -> list[Any]:
    """Eu retorno as tasks em ordem topológica real da DAG."""
    if dag is None:
        return []

    try:
        if hasattr(dag, "topological_sort"):
            ordenadas = list(dag.topological_sort())
            if ordenadas:
                return ordenadas
    except Exception:
        logger.exception("Falha ao usar topological_sort da DAG %s.", getattr(dag, "dag_id", None))

    try:
        return list(dag.tasks)
    except Exception:
        return []


def listar_execucoes_reais(
    dag_id: str | None = None,
    status: str | None = None,
    limite: int = 50,
) -> dict[str, Any]:
    """Eu listo execuções reais para a página inicial da auditoria."""
    limite = max(1, min(int(limite), 500))

    with create_session() as session:
        query = session.query(DagRun)

        if dag_id:
            query = query.filter(DagRun.dag_id.ilike(f"%{dag_id}%"))

        if status:
            query = query.filter(DagRun.state == status)

        campo_ordenacao = getattr(DagRun, "logical_date", None)
        if campo_ordenacao is None:
            campo_ordenacao = getattr(DagRun, "execution_date", None)

        if campo_ordenacao is not None:
            execucoes = query.order_by(campo_ordenacao.desc()).limit(limite).all()
        else:
            execucoes = query.limit(limite).all()

    itens: list[dict[str, Any]] = []
    for dagrun in execucoes:
        itens.append(
            {
                "dag_id": dagrun.dag_id,
                "run_id": dagrun.run_id,
                "status": str(dagrun.state) if dagrun.state else None,
                "run_type": str(dagrun.run_type) if getattr(dagrun, "run_type", None) else None,
                "execution_date": _serializar_datetime(_obter_execution_date_ou_logical_date(dagrun)),
                "start_date": _serializar_datetime(dagrun.start_date),
                "end_date": _serializar_datetime(dagrun.end_date),
                "duration_seconds": _calcular_duracao_segundos_dagrun(dagrun),
            }
        )

    return {
        "total": len(itens),
        "itens": itens,
    }


def montar_dashboard_real(dag_id: str, run_id: str | None = None) -> dict[str, Any]:
    """Eu monto o dashboard usando DAG real + execução real + metadata real das tasks."""
    dagrun = _obter_dagrun_real(dag_id=dag_id, run_id=run_id)
    if not dagrun:
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

        metadata_auditoria = _montar_metadata_task_xcoms(
            dag_id=dag_id,
            run_id=dagrun.run_id,
            task_id=task_id,
        )

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
        objetos = _montar_objetos(
            metadata_auditoria=metadata_auditoria,
            task=task,
            sql_real=sql_real,
        )
        tabela_normalizada = _normalizar_amostra_dados(
            metadata_auditoria=metadata_auditoria,
            objetos=objetos,
        )
        guia_transformacoes = _normalizar_guia_transformacoes(metadata_auditoria)
        regras_upsert = _normalizar_regras_upsert(metadata_auditoria)

        task_nome = getattr(task, "task_display_name", None) or task_id
        task_tipo = _obter_tipo_task(task, ti=ti)
        task_status = str(ti.state) if ti and ti.state else "none"

        task_operacao = _primeiro_preenchido(
            metadata_auditoria.get("operacao"),
            metadata_auditoria.get("operation"),
            metadata_auditoria.get("nome_amigavel"),
            task_id,
        )

        linhas_processadas = _primeiro_preenchido(
            metadata_auditoria.get("linhas_processadas"),
            metadata_auditoria.get("rows_processed"),
            metadata_auditoria.get("quantidade_linhas"),
            metadata_auditoria.get("row_count"),
            metadata_auditoria.get("linhas_lidas"),
            metadata_auditoria.get("linhas_inseridas"),
            metadata_auditoria.get("linhas_atualizadas"),
        )

        if not _valor_preenchido(linhas_processadas):
            linhas_processadas = (
                len(tabela_normalizada.get("linhas", []))
                if tabela_normalizada.get("linhas")
                else "Não registrado"
            )

        tarefas.append(
            {
                "id": task_id,
                "task_id": task_id,
                "nome": str(task_nome),
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
                "objetivo": objetivo,
                "operacao": task_operacao,
                "descricao": descricao,
                "origem_dados": _primeiro_preenchido(
                    metadata_auditoria.get("origem_dados"),
                    metadata_auditoria.get("source"),
                ),
                "destino_dados": _primeiro_preenchido(
                    metadata_auditoria.get("destino_dados"),
                    metadata_auditoria.get("target"),
                ),
                "origem_tabela": metadata_auditoria.get("origem_tabela"),
                "destino_tabela": metadata_auditoria.get("destino_tabela"),
                "fonte_dados": tabela_normalizada.get("fonte"),
                "metricas_extras": _extrair_metricas_extras(metadata_auditoria),
                "validacoes": _garantir_lista(metadata_auditoria.get("validacoes")),
                "observacoes": _garantir_lista(metadata_auditoria.get("observacoes")),
                "metricas": {
                    "linhas_processadas": linhas_processadas,
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
                "start_date": _serializar_datetime(ti.start_date) if ti else None,
                "end_date": _serializar_datetime(ti.end_date) if ti else None,
                "xcom_keys_encontradas": metadata_auditoria.get("_xcom_keys_encontradas", []),
            }
        )

    descricao_dag = _obter_descricao_dag(dag, dag_id=dag_id)
    health = _montar_health(task_instances=task_instances, dagrun=dagrun)
    tags_dag = [tag.name if hasattr(tag, "name") else str(tag) for tag in ((dag.tags or []) if dag else [])]

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
        "inicio": _serializar_datetime(dagrun.start_date),
        "fim": _serializar_datetime(dagrun.end_date),
        "owner": _obter_owner_dag(dag),
        "tags": tags_dag,
        "health": health,
        "tasks": tarefas,
        "dag_carregada_no_dagbag": dag is not None,
    }