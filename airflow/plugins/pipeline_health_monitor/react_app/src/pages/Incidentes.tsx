import { useEffect, useMemo, useState } from "react";
import useTemaAirflow from "../hooks/useTemaAirflow";

interface IncidenteItem {
  titulo: string;
  severidade: string;
  status: string;
  inicio: string;
  fim?: string | null;
  causa_raiz?: string | null;
  impacto?: string | null;
}

interface EstadoTela {
  carregando: boolean;
  erro: string | null;
}

const URL_BASE_API = "/pipeline-health-monitor/api";

async function buscarIncidentes(): Promise<IncidenteItem[]> {
  const resposta = await fetch(`${URL_BASE_API}/incidentes`, {
    method: "GET",
    headers: {
      Accept: "application/json",
    },
    credentials: "same-origin",
  });

  if (!resposta.ok) {
    throw new Error(`Erro HTTP ${resposta.status} ao buscar incidentes`);
  }

  return (await resposta.json()) as IncidenteItem[];
}

function formatarDataHora(valor: string | null | undefined): string {
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

function traduzirSeveridade(severidade: string): string {
  switch (severidade) {
    case "critical":
      return "Critical";
    case "warning":
      return "Warning";
    case "info":
      return "Info";
    default:
      return severidade;
  }
}

function traduzirStatus(status: string): string {
  switch (status) {
    case "open":
      return "Open";
    case "resolved":
      return "Resolved";
    case "closed":
      return "Closed";
    default:
      return status;
  }
}

function obterClasseBadgeSeveridade(severidade: string): string {
  if (severidade === "critical") {
    return "phm-badge phm-badge--critical";
  }

  if (severidade === "warning") {
    return "phm-badge phm-badge--degraded";
  }

  return "phm-badge phm-badge--unstable";
}

function obterClasseBadgeStatus(status: string): string {
  if (status === "resolved" || status === "closed") {
    return "phm-badge phm-badge--healthy";
  }

  if (status === "open") {
    return "phm-badge phm-badge--critical";
  }

  return "phm-badge phm-badge--unstable";
}

export default function Incidentes(): JSX.Element {
  const { classeTema } = useTemaAirflow();

  const [estadoTela, setEstadoTela] = useState<EstadoTela>({
    carregando: true,
    erro: null,
  });

  const [incidentes, setIncidentes] = useState<IncidenteItem[]>([]);

  useEffect(() => {
    let cancelado = false;

    async function carregarDados(): Promise<void> {
      try {
        setEstadoTela({
          carregando: true,
          erro: null,
        });

        const dados = await buscarIncidentes();

        if (cancelado) {
          return;
        }

        setIncidentes(dados);
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
            : "Erro inesperado ao carregar os incidentes.";

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

  const resumo = useMemo(() => {
    let open = 0;
    let resolved = 0;
    let critical = 0;
    let warning = 0;

    for (const incidente of incidentes) {
      if (incidente.status === "open") {
        open += 1;
      }

      if (incidente.status === "resolved" || incidente.status === "closed") {
        resolved += 1;
      }

      if (incidente.severidade === "critical") {
        critical += 1;
      }

      if (incidente.severidade === "warning") {
        warning += 1;
      }
    }

    return {
      total: incidentes.length,
      open,
      resolved,
      critical,
      warning,
    };
  }, [incidentes]);

  return (
    <main className={`phm-app ${classeTema}`}>
      <div className="phm-container">
        <header className="phm-topo">
          <div className="phm-topo__titulo-bloco">
            <div className="phm-topo__logo" aria-hidden="true">
              🚨
            </div>

            <div>
              <h1 className="phm-topo__titulo">Incidentes</h1>
              <p className="phm-topo__subtitulo">
                Acompanhamento de incidentes operacionais e técnicos dos pipelines
              </p>
            </div>
          </div>
        </header>

        {estadoTela.carregando ? (
          <section className="phm-card">
            <div className="phm-loading">Carregando incidentes...</div>
          </section>
        ) : estadoTela.erro ? (
          <section className="phm-card phm-card-erro">
            <h2 className="phm-card__titulo">Erro ao carregar incidentes</h2>
            <p className="phm-erro-texto">{estadoTela.erro}</p>
            <p className="phm-erro-texto">
              Verifique se o endpoint do plugin está respondendo corretamente.
            </p>
          </section>
        ) : (
          <>
            <section className="phm-grid-indicadores">
              <article className="phm-card phm-card-indicador phm-card-indicador--default">
                <span className="phm-card-indicador__titulo">Total</span>
                <strong className="phm-card-indicador__valor">{resumo.total}</strong>
              </article>

              <article className="phm-card phm-card-indicador phm-card-indicador--critical">
                <span className="phm-card-indicador__titulo">Open</span>
                <strong className="phm-card-indicador__valor">{resumo.open}</strong>
              </article>

              <article className="phm-card phm-card-indicador phm-card-indicador--healthy">
                <span className="phm-card-indicador__titulo">Resolved</span>
                <strong className="phm-card-indicador__valor">{resumo.resolved}</strong>
              </article>

              <article className="phm-card phm-card-indicador phm-card-indicador--critical">
                <span className="phm-card-indicador__titulo">Critical</span>
                <strong className="phm-card-indicador__valor">{resumo.critical}</strong>
              </article>

              <article className="phm-card phm-card-indicador phm-card-indicador--degraded">
                <span className="phm-card-indicador__titulo">Warning</span>
                <strong className="phm-card-indicador__valor">{resumo.warning}</strong>
              </article>
            </section>

            <section className="phm-card phm-card-tabela">
              <div className="phm-card__header">
                <h2 className="phm-card__titulo">Incident List</h2>
              </div>

              <div className="phm-tabela-wrapper">
                <table className="phm-tabela">
                  <thead>
                    <tr>
                      <th>Título</th>
                      <th>Severidade</th>
                      <th>Status</th>
                      <th>Início</th>
                      <th>Fim</th>
                      <th>Causa raiz</th>
                      <th>Impacto</th>
                    </tr>
                  </thead>

                  <tbody>
                    {incidentes.length === 0 ? (
                      <tr>
                        <td colSpan={7} className="phm-tabela__vazio">
                          Nenhum incidente encontrado.
                        </td>
                      </tr>
                    ) : (
                      incidentes.map((incidente, indice) => (
                        <tr key={`${incidente.titulo}-${indice}`}>
                          <td>
                            <div className="phm-pipeline-info">
                              <strong className="phm-pipeline-info__nome">
                                {incidente.titulo}
                              </strong>
                            </div>
                          </td>

                          <td>
                            <span className={obterClasseBadgeSeveridade(incidente.severidade)}>
                              {traduzirSeveridade(incidente.severidade)}
                            </span>
                          </td>

                          <td>
                            <span className={obterClasseBadgeStatus(incidente.status)}>
                              {traduzirStatus(incidente.status)}
                            </span>
                          </td>

                          <td>{formatarDataHora(incidente.inicio)}</td>
                          <td>{formatarDataHora(incidente.fim)}</td>
                          <td>{incidente.causa_raiz ?? "—"}</td>
                          <td>{incidente.impacto ?? "—"}</td>
                        </tr>
                      ))
                    )}
                  </tbody>
                </table>
              </div>
            </section>
          </>
        )}
      </div>
    </main>
  );
}