export type StatusPipeline = "healthy" | "degraded" | "critical" | "paused";
export type StatusDependencia = "ok" | "warning" | "unstable" | "down";

export interface ResumoHealth {
  total_pipelines: number;
  healthy: number;
  degraded: number;
  critical: number;
  paused: number;
  incidents_last_24h: number;
}

export interface PipelineItem {
  dag_id: string;
  nome: string;
  health_score: number;
  status: StatusPipeline;
  last_run: string | null;
  duration_atual_min: number | null;
  duration_media_min: number | null;
  last_failure: string | null;
  dependency: string | null;
  data_quality: string | null;
}

export interface DependenciaItem {
  nome: string;
  status: StatusDependencia;
  latencia_ms: number | null;
}

const URL_BASE_API = "/pipeline-health-monitor/api";

async function buscarJson<T>(url: string): Promise<T> {
  const resposta = await fetch(url, {
    method: "GET",
    headers: {
      Accept: "application/json",
    },
    credentials: "same-origin",
  });

  if (!resposta.ok) {
    throw new Error(`Erro HTTP ${resposta.status} ao buscar ${url}`);
  }

  return (await resposta.json()) as T;
}

export async function buscarResumoHealth(): Promise<ResumoHealth> {
  return buscarJson<ResumoHealth>(`${URL_BASE_API}/resumo`);
}

export async function buscarPipelines(): Promise<PipelineItem[]> {
  return buscarJson<PipelineItem[]>(`${URL_BASE_API}/pipelines`);
}

export async function buscarDependencias(): Promise<DependenciaItem[]> {
  return buscarJson<DependenciaItem[]>(`${URL_BASE_API}/dependencias`);
}

export function traduzirStatusPipeline(status: StatusPipeline): string {
  switch (status) {
    case "healthy":
      return "Healthy";
    case "degraded":
      return "Degraded";
    case "critical":
      return "Critical";
    case "paused":
      return "Paused";
    default:
      return status;
  }
}

export function traduzirStatusDependencia(status: StatusDependencia): string {
  switch (status) {
    case "ok":
      return "OK";
    case "warning":
      return "Warning";
    case "unstable":
      return "Unstable";
    case "down":
      return "Down";
    default:
      return status;
  }
}

export function obterClasseStatusPipeline(status: StatusPipeline): string {
  return `phm-badge phm-badge--${status}`;
}

export function obterClasseStatusDependencia(status: StatusDependencia): string {
  return `phm-badge phm-badge--${status}`;
}

export function formatarDataHora(valor: string | null): string {
  if (!valor) {
    return "—";
  }

  const data = new Date(valor);

  if (Number.isNaN(data.getTime())) {
    return valor;
  }

  return new Intl.DateTimeFormat("pt-BR", {
    dateStyle: "short",
    timeStyle: "short",
  }).format(data);
}

export function formatarDuracaoComparativa(
  atual: number | null,
  media: number | null
): string {
  if (atual == null && media == null) {
    return "—";
  }

  const atualTexto = atual == null ? "—" : `${atual}m`;
  const mediaTexto = media == null ? "—" : `${media}m`;

  return `${atualTexto} / ${mediaTexto}`;
}