import { useEffect, useState } from "react";
import AlertasQualidade from "../components/AlertasQualidade";
import CardIndicador from "../components/CardIndicador";
import GraficoExecucoes from "../components/GraficoExecucoes";
import PainelDependencias from "../components/PainelDependencias";
import TabelaPipelines from "../components/TabelaPipelines";
import useTemaAirflow from "../hooks/useTemaAirflow";
import {
  DependenciaItem,
  PipelineItem,
  ResumoHealth,
  buscarDependencias,
  buscarPipelines,
  buscarResumoHealth,
} from "../services/api";

interface EstadoTela {
  carregando: boolean;
  erro: string | null;
}

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

export default function VisaoGeral(): JSX.Element {
  const { classeTema } = useTemaAirflow();

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
          buscarResumoHealth(),
          buscarPipelines(),
          buscarDependencias(),
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
    <main className={`phm-app ${classeTema}`}>
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

            <section className="phm-grid-inferior">
              <GraficoExecucoes pipelines={pipelines} />
            </section>
          </>
        )}
      </div>
    </main>
  );
}