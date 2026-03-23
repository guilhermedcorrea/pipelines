import { useEffect, useMemo, useState } from "react";

/**
 * Tipos principais da tela.
 */
type StatusPipeline = "healthy" | "degraded" | "critical" | "paused";
type StatusDependencia = "ok" | "warning" | "unstable" | "down";

interface ResumoHealth {
  total_pipelines: number;
  healthy: number;
  degraded: number;
  critical: number;
  paused: number;
  incidents_last_24h: number;
}

interface PipelineItem {
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

interface DependenciaItem {
  nome: string;
  status: StatusDependencia;
  latencia_ms: number | null;
}

interface EstadoTela {
  carregando: boolean;
  erro: string | null;
}

const URL_BASE_API = "/pipeline-health-monitor/api";

/**
 * Função utilitária para buscar JSON.
 */
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

/**
 * Traduz status do pipeline para texto amigável.
 */
function traduzirStatusPipeline(status: StatusPipeline): string {
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

/**
 * Traduz status de dependência para texto amigável.
 */
function traduzirStatusDependencia(status: StatusDependencia): string {
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

/**
 * Classe CSS do badge do pipeline.
 */
function obterClasseStatusPipeline(status: StatusPipeline): string {
  return `phm-badge phm-badge--${status}`;
}

/**
 * Classe CSS do badge da dependência.
 */
function obterClasseStatusDependencia(status: StatusDependencia): string {
  return `phm-badge phm-badge--${status}`;
}

/**
 * Formata data/hora vinda da API.
 */
function formatarDataHora(valor: string | null): string {
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

/**
 * Formata duração atual vs média.
 */
function formatarDuracaoComparativa(
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

/**
 * Card superior de indicador.
 */
function CardIndicador(props: {
  titulo: string;
  valor: number | string;
  variante?:
    | "default"
    | "healthy"
    | "degraded"
    | "critical"
    | "paused"
    | "incidents";
}): JSX.Element {
  const { titulo, valor, variante = "default" } = props;

  return (
    <article className={`phm-card phm-card-indicador phm-card-indicador--${variante}`}>
      <span className="phm-card-indicador__titulo">{titulo}</span>
      <strong className="phm-card-indicador__valor">{valor}</strong>
    </article>
  );
}

/**
 * Tabela principal de pipelines.
 */
function TabelaPipelines(props: { pipelines: PipelineItem[] }): JSX.Element {
  const { pipelines } = props;

  return (
    <section className="phm-card phm-card-tabela">
      <div className="phm-card__header">
        <h2 className="phm-card__titulo">Pipeline Health</h2>
      </div>

      <div className="phm-tabela-wrapper">
        <table className="phm-tabela">
          <thead>
            <tr>
              <th>DAG</th>
              <th>Health Score</th>
              <th>Status</th>
              <th>Last Run</th>
              <th>Duration vs Avg</th>
              <th>Last Failure</th>
              <th>Dependency</th>
              <th>Data Quality</th>
            </tr>
          </thead>

          <tbody>
            {pipelines.length === 0 ? (
              <tr>
                <td colSpan={8} className="phm-tabela__vazio">
                  Nenhum pipeline encontrado.
                </td>
              </tr>
            ) : (
              pipelines.map((pipeline) => (
                <tr key={pipeline.dag_id}>
                  <td>
                    <div className="phm-pipeline-info">
                      <strong className="phm-pipeline-info__nome">{pipeline.nome}</strong>
                      <span className="phm-pipeline-info__dag">{pipeline.dag_id}</span>
                    </div>
                  </td>

                  <td>
                    <span className="phm-score">{pipeline.health_score}</span>
                  </td>

                  <td>
                    <span className={obterClasseStatusPipeline(pipeline.status)}>
                      {traduzirStatusPipeline(pipeline.status)}
                    </span>
                  </td>

                  <td>{formatarDataHora(pipeline.last_run)}</td>

                  <td>
                    {formatarDuracaoComparativa(
                      pipeline.duration_atual_min,
                      pipeline.duration_media_min
                    )}
                  </td>

                  <td>{pipeline.last_failure ?? "—"}</td>
                  <td>{pipeline.dependency ?? "—"}</td>
                  <td>{pipeline.data_quality ?? "—"}</td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </section>
  );
}

/**
 * Painel simples de dependências.
 */
function PainelDependencias(props: {
  dependencias: DependenciaItem[];
}): JSX.Element {
  const { dependencias } = props;

  return (
    <section className="phm-card">
      <div className="phm-card__header">
        <h2 className="phm-card__titulo">Dependency Status</h2>
      </div>

      <div className="phm-dependencias">
        {dependencias.length === 0 ? (
          <div className="phm-vazio">Nenhuma dependência encontrada.</div>
        ) : (
          dependencias.map((dependencia) => (
            <article key={dependencia.nome} className="phm-dependencia-item">
              <div className="phm-dependencia-item__linha">
                <strong>{dependencia.nome}</strong>
                <span className={obterClasseStatusDependencia(dependencia.status)}>
                  {traduzirStatusDependencia(dependencia.status)}
                </span>
              </div>

              <div className="phm-dependencia-item__latencia">
                Latência:{" "}
                {dependencia.latencia_ms == null ? "—" : `${dependencia.latencia_ms} ms`}
              </div>
            </article>
          ))
        )}
      </div>
    </section>
  );
}

/**
 * Widget simples de alertas.
 *
 * Neste primeiro momento, eu calculo alertas a partir dos próprios pipelines,
 * sem depender de um endpoint separado.
 */
function AlertasQualidade(props: { pipelines: PipelineItem[] }): JSX.Element {
  const { pipelines } = props;

  const alertas = useMemo(() => {
    const lista: string[] = [];

    for (const pipeline of pipelines) {
      if (pipeline.data_quality && pipeline.data_quality.toLowerCase() !== "ok") {
        lista.push(`${pipeline.nome}: ${pipeline.data_quality}`);
      }

      if (pipeline.health_score < 50) {
        lista.push(`${pipeline.nome}: health score crítico (${pipeline.health_score})`);
      }

      if (pipeline.last_failure) {
        lista.push(`${pipeline.nome}: ${pipeline.last_failure}`);
      }
    }

    return lista.slice(0, 6);
  }, [pipelines]);

  return (
    <section className="phm-card">
      <div className="phm-card__header">
        <h2 className="phm-card__titulo">Health Alerts</h2>
      </div>

      <div className="phm-alertas">
        {alertas.length === 0 ? (
          <div className="phm-vazio">Nenhum alerta relevante no momento.</div>
        ) : (
          alertas.map((alerta, indice) => (
            <div key={`${alerta}-${indice}`} className="phm-alerta-item">
              {alerta}
            </div>
          ))
        )}
      </div>
    </section>
  );
}

/**
 * Widget simples de resumo operacional.
 */
function ResumoOperacional(props: { pipelines: PipelineItem[] }): JSX.Element {
  const { pipelines } = props;

  const mediaScore =
    pipelines.length > 0
      ? Math.round(
          pipelines.reduce((acumulado, item) => acumulado + item.health_score, 0) /
            pipelines.length
        )
      : 0;

  const quantidadeComFalha = pipelines.filter((item) => Boolean(item.last_failure)).length;

  return (
    <section className="phm-card">
      <div className="phm-card__header">
        <h2 className="phm-card__titulo">Operational Summary</h2>
      </div>

      <div className="phm-resumo-operacional">
        <div className="phm-resumo-operacional__item">
          <span className="phm-resumo-operacional__label">Pipelines monitorados</span>
          <strong className="phm-resumo-operacional__valor">{pipelines.length}</strong>
        </div>

        <div className="phm-resumo-operacional__item">
          <span className="phm-resumo-operacional__label">Score médio</span>
          <strong className="phm-resumo-operacional__valor">{mediaScore}</strong>
        </div>

        <div className="phm-resumo-operacional__item">
          <span className="phm-resumo-operacional__label">Com falha recente</span>
          <strong className="phm-resumo-operacional__valor">{quantidadeComFalha}</strong>
        </div>
      </div>
    </section>
  );
}

/**
 * App principal.
 */
export default function App(): JSX.Element {
  const [estadoTela, setEstadoTela] = useState<EstadoTela>({
    carregando: true,
    erro: null,
  });

  const [resumo, setResumo] = useState<ResumoHealth | null>(null);
  const [pipelines, setPipelines] = useState<PipelineItem[]>([]);
  const [dependencias, setDependencias] = useState<DependenciaItem[]>([]);

  useEffect(() => {
    let cancelado = false;

    async function carregarDados(): Promise<void> {
      try {
        setEstadoTela({ carregando: true, erro: null });

        const [resumoApi, pipelinesApi, dependenciasApi] = await Promise.all([
          buscarJson<ResumoHealth>(`${URL_BASE_API}/resumo`),
          buscarJson<PipelineItem[]>(`${URL_BASE_API}/pipelines`),
          buscarJson<DependenciaItem[]>(`${URL_BASE_API}/dependencias`),
        ]);

        if (cancelado) {
          return;
        }

        setResumo(resumoApi);
        setPipelines(pipelinesApi);
        setDependencias(dependenciasApi);
        setEstadoTela({ carregando: false, erro: null });
      } catch (erro) {
        if (cancelado) {
          return;
        }

        const mensagem =
          erro instanceof Error
            ? erro.message
            : "Erro inesperado ao carregar o Pipeline Health Monitor.";

        setEstadoTela({
          carregando: false,
          erro: mensagem,
        });
      }
    }

    void carregarDados();

    return () => {
      cancelado = true;
    };
  }, []);

  return (
    <main className="phm-app">
      <div className="phm-container">
        <header className="phm-topo">
          <div className="phm-topo__titulo-bloco">
            <div className="phm-topo__logo" aria-hidden="true">
              ♥
            </div>

            <div>
              <h1 className="phm-topo__titulo">Pipeline Health Monitor</h1>
              <p className="phm-topo__subtitulo">
                Monitor avançado de saúde operacional dos pipelines do Airflow
              </p>
            </div>
          </div>
        </header>

        {estadoTela.carregando ? (
          <section className="phm-card">
            <div className="phm-loading">Carregando monitor...</div>
          </section>
        ) : estadoTela.erro ? (
          <section className="phm-card phm-card-erro">
            <h2 className="phm-card__titulo">Erro ao carregar dados</h2>
            <p className="phm-erro-texto">{estadoTela.erro}</p>
            <p className="phm-erro-texto">
              Verifique se os endpoints do plugin estão respondendo corretamente.
            </p>
          </section>
        ) : (
          <>
            <section className="phm-grid-indicadores">
              <CardIndicador
                titulo="Total Pipelines"
                valor={resumo?.total_pipelines ?? 0}
                variante="default"
              />
              <CardIndicador
                titulo="Healthy"
                valor={resumo?.healthy ?? 0}
                variante="healthy"
              />
              <CardIndicador
                titulo="Degraded"
                valor={resumo?.degraded ?? 0}
                variante="degraded"
              />
              <CardIndicador
                titulo="Critical"
                valor={resumo?.critical ?? 0}
                variante="critical"
              />
              <CardIndicador
                titulo="Paused"
                valor={resumo?.paused ?? 0}
                variante="paused"
              />
              <CardIndicador
                titulo="Incidents Last 24h"
                valor={resumo?.incidents_last_24h ?? 0}
                variante="incidents"
              />
            </section>

            <TabelaPipelines pipelines={pipelines} />

            <section className="phm-grid-inferior">
              <ResumoOperacional pipelines={pipelines} />
              <PainelDependencias dependencias={dependencias} />
              <AlertasQualidade pipelines={pipelines} />
            </section>
          </>
        )}
      </div>
    </main>
  );
}