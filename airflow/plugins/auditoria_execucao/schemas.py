from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any


CHAVE_XCOM_AUDITORIA = "auditoria_resumo_execucao"


def serializar_json_seguro(valor: Any) -> str:
    """Serializa objetos para JSON sem quebrar com datas e tipos não nativos."""
    return json.dumps(valor, ensure_ascii=False, default=str)


@dataclass
class ValidacaoAuditoria:
    """Representa uma validação de qualidade executada pela task."""

    nome: str
    status: str
    detalhe: str


@dataclass
class ReferenciaTabelaSql:
    """
    Representa uma referência estruturada para tabela SQL.

    Eu mantenho essa estrutura separada porque texto puro não é suficiente
    para habilitar preview seguro, paginação e futuras evoluções da UI.
    """

    conexao_id: str
    schema: str
    tabela: str
    tipo: str = "tabela_sql"
    preview_habilitado: bool = True
    texto: str | None = None

    def __post_init__(self) -> None:
        """
        Eu garanto um texto amigável padrão no formato schema.tabela
        quando ele não vier preenchido.
        """
        if not self.texto:
            self.texto = f"{self.schema}.{self.tabela}"

    def para_dict(self) -> dict[str, Any]:
        """Converte a referência para dicionário."""
        return asdict(self)


@dataclass
class ResumoAuditoriaTask:
    """Resumo estruturado da execução de uma task."""

    dag_id: str | None = None
    run_id: str | None = None
    task_id: str | None = None
    try_number: int | None = None

    nome_amigavel: str = ""
    descricao_etapa: str = ""

    """
    Estes dois campos continuam existindo por compatibilidade com:
    - persistência atual no banco
    - templates que ainda usam texto simples
    - tasks antigas já registradas
    """
    origem_dados: str | None = None
    destino_dados: str | None = None

    """
    Estes dois campos novos guardam a versão estruturada da referência.
    Eles não quebram o modelo antigo e abrem caminho para UI mais rica.
    """
    origem_tabela: ReferenciaTabelaSql | None = None
    destino_tabela: ReferenciaTabelaSql | None = None

    status: str | None = None
    linhas_lidas: int | None = None
    linhas_inseridas: int | None = None
    linhas_atualizadas: int | None = None
    linhas_descartadas: int | None = None

    amostra: list[dict[str, Any]] = field(default_factory=list)
    validacoes: list[dict[str, Any]] = field(default_factory=list)
    metricas_extras: dict[str, Any] = field(default_factory=dict)
    observacoes: list[str] = field(default_factory=list)

    erro_tecnico: str | None = None
    erro_traduzido: str | None = None
    causa_provavel: str | None = None
    acao_sugerida: str | None = None

    criado_em: str = field(default_factory=lambda: datetime.utcnow().isoformat())

    def __post_init__(self) -> None:
        """
        Eu sincronizo automaticamente os campos textuais com a referência estruturada
        quando ela existir, para manter compatibilidade com a persistência atual.
        """
        if self.origem_tabela and not self.origem_dados:
            self.origem_dados = self.origem_tabela.texto

        if self.destino_tabela and not self.destino_dados:
            self.destino_dados = self.destino_tabela.texto

    def definir_origem_tabela(
        self,
        conexao_id: str,
        schema: str,
        tabela: str,
        texto: str | None = None,
        preview_habilitado: bool = True,
    ) -> None:
        """
        Eu preencho a origem como referência estruturada e também atualizo o texto simples.
        """
        self.origem_tabela = ReferenciaTabelaSql(
            conexao_id=conexao_id,
            schema=schema,
            tabela=tabela,
            texto=texto,
            preview_habilitado=preview_habilitado,
        )
        self.origem_dados = self.origem_tabela.texto

    def definir_destino_tabela(
        self,
        conexao_id: str,
        schema: str,
        tabela: str,
        texto: str | None = None,
        preview_habilitado: bool = True,
    ) -> None:
        """
        Eu preencho o destino como referência estruturada e também atualizo o texto simples.
        """
        self.destino_tabela = ReferenciaTabelaSql(
            conexao_id=conexao_id,
            schema=schema,
            tabela=tabela,
            texto=texto,
            preview_habilitado=preview_habilitado,
        )
        self.destino_dados = self.destino_tabela.texto

    def para_dict(self) -> dict[str, Any]:
        """
        Converte o resumo para dicionário.

        Observação importante:
        Eu mantenho tanto os campos textuais quanto os estruturados, porque:
        - o texto é útil para compatibilidade
        - o estruturado é útil para UI e API
        """
        resultado = asdict(self)

        if self.origem_tabela:
            resultado["origem_tabela"] = self.origem_tabela.para_dict()

        if self.destino_tabela:
            resultado["destino_tabela"] = self.destino_tabela.para_dict()

        return resultado

    def para_json(self) -> str:
        """Converte o resumo para JSON."""
        return serializar_json_seguro(self.para_dict())


@dataclass
class EventoTaskPersistencia:
    """Payload persistido da execução de task."""

    dag_id: str
    run_id: str
    task_id: str
    try_number: int | None
    status: str
    operator: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    duracao_segundos: float | None = None
    nome_amigavel: str | None = None
    descricao_etapa: str | None = None

    """
    Estes campos continuam em texto porque a tabela física atual do SQL Server
    persiste origem e destino como nvarchar.
    """
    origem_dados: str | None = None
    destino_dados: str | None = None

    linhas_lidas: int | None = None
    linhas_inseridas: int | None = None
    linhas_atualizadas: int | None = None
    linhas_descartadas: int | None = None
    validacoes_json: str | None = None
    amostra_json: str | None = None
    metricas_json: str | None = None
    observacoes_json: str | None = None
    erro_tecnico: str | None = None
    erro_traduzido: str | None = None
    causa_provavel: str | None = None
    acao_sugerida: str | None = None
    host_execucao: str | None = None

    def para_dict(self) -> dict[str, Any]:
        """Converte o evento persistido para dicionário."""
        return asdict(self)

    def para_json(self) -> str:
        """Converte o evento persistido para JSON."""
        return serializar_json_seguro(self.para_dict())


@dataclass
class EventoDagPersistencia:
    """Payload persistido da execução de DAG."""

    dag_id: str
    run_id: str
    status: str
    run_type: str | None = None
    queued_at: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    duracao_segundos: float | None = None
    mensagem_resumo: str | None = None

    def para_dict(self) -> dict[str, Any]:
        """Converte o evento de DAG para dicionário."""
        return asdict(self)

    def para_json(self) -> str:
        """Converte o evento de DAG para JSON."""
        return serializar_json_seguro(self.para_dict())