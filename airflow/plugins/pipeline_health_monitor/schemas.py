from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


StatusPipeline = Literal["healthy", "degraded", "critical", "paused"]
StatusDependencia = Literal["ok", "warning", "unstable", "down"]


class ResumoHealthSchema(BaseModel):
    total_pipelines: int = Field(..., ge=0)
    healthy: int = Field(..., ge=0)
    degraded: int = Field(..., ge=0)
    critical: int = Field(..., ge=0)
    paused: int = Field(..., ge=0)
    incidents_last_24h: int = Field(..., ge=0)


class PipelineHealthItemSchema(BaseModel):
    dag_id: str
    nome: str
    health_score: int = Field(..., ge=0, le=100)
    status: StatusPipeline
    last_run: str | None = None
    duration_atual_min: int | None = Field(default=None, ge=0)
    duration_media_min: int | None = Field(default=None, ge=0)
    last_failure: str | None = None
    dependency: str | None = None
    data_quality: str | None = None


class TimelineItemSchema(BaseModel):
    run_id: str
    status: str
    duracao_min: int = Field(..., ge=0)


class ProblemaItemSchema(BaseModel):
    tipo: str
    titulo: str
    descricao: str


class DetalhePipelineSchema(BaseModel):
    dag_id: str
    health_score: int = Field(..., ge=0, le=100)
    status: StatusPipeline
    agendamento: int = Field(..., ge=0, le=100)
    execucao: int = Field(..., ge=0, le=100)
    performance: int = Field(..., ge=0, le=100)
    dependencias: int = Field(..., ge=0, le=100)
    dados: int = Field(..., ge=0, le=100)
    confiabilidade: int = Field(..., ge=0, le=100)
    timeline: list[TimelineItemSchema]
    top_problemas: list[ProblemaItemSchema]


class DependenciaItemSchema(BaseModel):
    nome: str
    status: StatusDependencia
    latencia_ms: int | None = Field(default=None, ge=0)


class IncidenteItemSchema(BaseModel):
    titulo: str
    severidade: str
    status: str
    inicio: str
    fim: str | None = None
    causa_raiz: str | None = None
    impacto: str | None = None