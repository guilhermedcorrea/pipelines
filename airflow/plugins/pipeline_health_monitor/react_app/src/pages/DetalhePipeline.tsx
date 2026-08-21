import { useEffect, useMemo, useState } from "react";
import useTemaAirflow from "../hooks/useTemaAirflow";

interface TimelineItem {
  run_id: string;
  status: string;
  duracao_min: number;
}

interface ProblemaItem {
  tipo: string;
  titulo: string;
  descricao: string;
}

interface DetalhePipelineResponse {
  dag_id: string;
  health_score: number;
  status: string;
  agendamento: number;
  execucao: number;
  performance: number;
  dependencias: number;
  dados: number;
  confiabilidade: number;
  timeline: TimelineItem[];
  top_problemas: ProblemaItem[];
}

interface EstadoTela {
  carregando: boolean;
  erro: string | null;
}

const URL_BASE_API = "/pipeline-health-monitor/api";

async function buscarDetalhePipeline(
  dagId: string
): Promise<DetalhePipelineResponse> {
  const resposta = await fetch(
    `${URL_BASE_API}/pipelines/${encodeURIComponent(dagId)}`,
    {
      method: "GET",
      headers: {
        Accept: "application/json",
      },
      credentials: "same-origin",
    }
  );

  if (!resposta.ok) {
    throw new Error(
      `Erro HTTP ${resposta.status} ao buscar detalhe do pipeline ${dagId}`
    );
  }

  return (await resposta.json()) as DetalhePipelineResponse;
}

function obterDagIdDaUrl(): string {
  if (typeof window === "undefined") {
    return "";
  }

  const url = new URL(window.location.href);
  return url.searchParams.get("dag_id") ?? "";
}

function traduzirStatus(status: string): string {
  switch (status) {
    case "healthy":
      return "Healthy";
    case "degraded":
      return "Degraded";
    case "critical":
      return "Critical";
    case "paused":
      return "Paused";
    case "success":
      return "Success";
    case "failed":
      return "Failed";
    default:
      return status;
  }
}

function obterClasseBadge(status: string): string {
  return `phm-badge phm-badge--${status}`;
}

function obterClassePontuacao(valor: number): string {
  if (valor >= 90) {
    return "phm-metrica-score phm-metrica-score--healthy";
  }

  if (valor >= 75) {
    return "phm-metrica-score phm-metrica-score--degraded";
  }

  if (valor >= 50) {
    return "phm-metrica-score phm-metrica-score--incidents";
  }

  return "phm-metrica-score phm-metrica-score--critical";
}

function obterClasseTimeline(status: string): string {
  return `phm-badge phm-badge--${status}`;
}

function CardDimensao(props: {
  titulo: string;
  valor: number;
}): JSX.Element {
  const { titulo, valor } = props;

  return (
    <article className="phm-card phm-card-indicador">
      <span className="phm-card-indicador__titulo">{titulo}</span>
      <strong className={obterClassePontuacao(valor)}>{valor}</strong>
    </article>
  );
}

export default function DetalhePipeline(): JSX.Element {
  const { classeTema } = useTemaAirflow();

  const [estadoTela, setEstadoTela] = useState<EstadoTela>({
    carregando: true,
    erro: null,
  });

  const [dagId, setDagId] = useState<string>("");
  const [detalhe, setDetalhe] = useState<DetalhePipelineResponse | null>(null);

  useEffect(() => {
    const dagIdEncontrado = obterDagIdDaUrl();
    setDagId(dagIdEncontrado);

    if (!dagIdEncontrado) {
      setEstadoTela({
        carregando: false,
        erro: "Nenhum dag_id foi informado na URL.",
      });
      return;
    }

    let cancelado = false;

    async function carregarDados(): Promise<void> {
      try {
        setEstadoTela({
          carregando: true,
          erro: null,
        });

        const dados = await buscarDetalhePipeline(dagIdEncontrado);

        if (cancelado) {
          return;
        }

        setDetalhe(dados);
        setEstadoTela({
          carregando: false,
          erro: null,
        });
      } catch (erro) {
        if (cancelado) {
          return;
        }

        const mensagem =
          erro instanceof Error
            ? erro.message
            : "Erro inesperado ao carregar o detalhe do pipeline.";

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

  const resumoDimensoes = useMemo(() => {
    if (!detalhe) {
      return [];
    }

    return [
      { titulo: "Agendamento", valor: detalhe.agendamento },
      { titulo: "Execução", valor: detalhe.execucao },
      { titulo: "Performance", valor: detalhe.performance },
      { titulo: "Dependências", valor: detalhe.dependencias },
      { titulo: "Dados", valor: detalhe.dados },
      { titulo: "Confiabilidade", valor: detalhe.confiabilidade },
    ];
  }, [detalhe]);

  return (
    <main className={`phm-app ${classeTema}`}>
      <div className="phm-container">
        <header className="phm-topo">
          <div className="phm-topo__titulo-bloco">
            <div className="phm-topo__logo" aria-hidden="true">
              ⚙
            </div>

            <div>
              <h1 className="phm-topo__titulo">Detalhe do Pipeline</h1>
              <p className="phm-topo__subtitulo">
                Visão detalhada da saúde operacional e técnica do pipeline
              </p>
            </div>
          </div>
        </header>

        {estadoTela.carregando ? (
          <section className="phm-card">
            <div className="phm-loading">Carregando detalhe do pipeline...</div>
          </section>
        ) : estadoTela.erro ? (
          <section className="phm-card phm-card-erro">
            <h2 className="phm-card__titulo">Erro ao carregar detalhe</h2>
            <p className="phm-erro-texto">{estadoTela.erro}</p>
            <p className="phm-erro-texto">
              Informe um parâmetro válido na URL, por exemplo:
              {" "}
              <strong>?dag_id=etl_ctr_controle_contratos_midia</strong>
            </p>
          </section>
        ) : detalhe ? (
          <>
            <section className="phm-card">
              <div className="phm-card__header">
                <h2 className="phm-card__titulo">{detalhe.dag_id}</h2>
                <span className={obterClasseBadge(detalhe.status)}>
                  {traduzirStatus(detalhe.status)}
                </span>
              </div>

              <div className="phm-resumo-operacional">
                <div className="phm-resumo-operacional__item">
                  <span className="phm-resumo-operacional__label">DAG ID</span>
                  <strong className="phm-resumo-operacional__valor">
                    {dagId}
                  </strong>
                </div>

                <div className="phm-resumo-operacional__item">
                  <span className="phm-resumo-operacional__label">
                    Health Score
                  </span>
                  <strong
                    className={obterClassePontuacao(detalhe.health_score)}
                  >
                    {detalhe.health_score}
                  </strong>
                </div>

                <div className="phm-resumo-operacional__item">
                  <span className="phm-resumo-operacional__label">
                    Status Geral
                  </span>
                  <strong className="phm-resumo-operacional__valor">
                    {traduzirStatus(detalhe.status)}
                  </strong>
                </div>
              </div>
            </section>

            <section className="phm-grid-indicadores">
              {resumoDimensoes.map((item) => (
                <CardDimensao
                  key={item.titulo}
                  titulo={item.titulo}
                  valor={item.valor}
                />
              ))}
            </section>

            <section className="phm-grid-inferior">
              <section className="phm-card">
                <div className="phm-card__header">
                  <h2 className="phm-card__titulo">Timeline de Execuções</h2>
                </div>

                <div className="phm-dependencias">
                  {detalhe.timeline.length === 0 ? (
                    <div className="phm-vazio">
                      Nenhuma execução disponível.
                    </div>
                  ) : (
                    detalhe.timeline.map((item) => (
                      <article
                        key={item.run_id}
                        className="phm-dependencia-item"
                      >
                        <div className="phm-dependencia-item__linha">
                          <strong>{item.run_id}</strong>
                          <span className={obterClasseTimeline(item.status)}>
                            {traduzirStatus(item.status)}
                          </span>
                        </div>

                        <div className="phm-dependencia-item__latencia">
                          Duração: {item.duracao_min} min
                        </div>
                      </article>
                    ))
                  )}
                </div>
              </section>

              <section className="phm-card">
                <div className="phm-card__header">
                  <h2 className="phm-card__titulo">Top Problemas</h2>
                </div>

                <div className="phm-alertas">
                  {detalhe.top_problemas.length === 0 ? (
                    <div className="phm-vazio">
                      Nenhum problema relevante encontrado.
                    </div>
                  ) : (
                    detalhe.top_problemas.map((problema, indice) => (
                      <div
                        key={`${problema.titulo}-${indice}`}
                        className="phm-alerta-item"
                      >
                        <strong>{problema.tipo}</strong>
                        <br />
                        {problema.titulo}
                        <br />
                        <span style={{ opacity: 0.88 }}>
                          {problema.descricao}
                        </span>
                      </div>
                    ))
                  )}
                </div>
              </section>

              <section className="phm-card">
                <div className="phm-card__header">
                  <h2 className="phm-card__titulo">Leitura Rápida</h2>
                </div>

                <div className="phm-resumo-operacional">
                  <div className="phm-resumo-operacional__item">
                    <span className="phm-resumo-operacional__label">
                      Melhor dimensão
                    </span>
                    <strong className="phm-resumo-operacional__valor">
                      {
                        [...resumoDimensoes].sort(
                          (a, b) => b.valor - a.valor
                        )[0]?.titulo ?? "—"
                      }
                    </strong>
                  </div>

                  <div className="phm-resumo-operacional__item">
                    <span className="phm-resumo-operacional__label">
                      Pior dimensão
                    </span>
                    <strong className="phm-resumo-operacional__valor">
                      {
                        [...resumoDimensoes].sort(
                          (a, b) => a.valor - b.valor
                        )[0]?.titulo ?? "—"
                      }
                    </strong>
                  </div>

                  <div className="phm-resumo-operacional__item">
                    <span className="phm-resumo-operacional__label">
                      Score consolidado
                    </span>
                    <strong
                      className={obterClassePontuacao(detalhe.health_score)}
                    >
                      {detalhe.health_score}
                    </strong>
                  </div>
                </div>
              </section>
            </section>
          </>
        ) : (
          <section className="phm-card">
            <div className="phm-vazio">
              Nenhum detalhe disponível para o pipeline informado.
            </div>
          </section>
        )}
      </div>
    </main>
  );
}