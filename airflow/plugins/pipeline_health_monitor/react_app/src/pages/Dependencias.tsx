import { useEffect, useState } from "react";
import PainelDependencias from "../components/PainelDependencias";
import {
  DependenciaItem,
  buscarDependencias,
} from "../services/api";
import useTemaAirflow from "../hooks/useTemaAirflow";

interface EstadoTela {
  carregando: boolean;
  erro: string | null;
}

function calcularResumoDependencias(dependencias: DependenciaItem[]) {
  let ok = 0;
  let warning = 0;
  let unstable = 0;
  let down = 0;

  for (const dependencia of dependencias) {
    switch (dependencia.status) {
      case "ok":
        ok += 1;
        break;
      case "warning":
        warning += 1;
        break;
      case "unstable":
        unstable += 1;
        break;
      case "down":
        down += 1;
        break;
      default:
        break;
    }
  }

  return {
    total: dependencias.length,
    ok,
    warning,
    unstable,
    down,
  };
}

export default function Dependencias(): JSX.Element {
  const { classeTema } = useTemaAirflow();

  const [estadoTela, setEstadoTela] = useState<EstadoTela>({
    carregando: true,
    erro: null,
  });

  const [dependencias, setDependencias] = useState<DependenciaItem[]>([]);

  useEffect(() => {
    let cancelado = false;

    async function carregarDados(): Promise<void> {
      try {
        setEstadoTela({
          carregando: true,
          erro: null,
        });

        const dados = await buscarDependencias();

        if (cancelado) {
          return;
        }

        setDependencias(dados);
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
            : "Erro inesperado ao carregar as dependências.";

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

  const resumo = calcularResumoDependencias(dependencias);

  return (
    <main className={`phm-app ${classeTema}`}>
      <div className="phm-container">
        <header className="phm-topo">
          <div className="phm-topo__titulo-bloco">
            <div className="phm-topo__logo" aria-hidden="true">
              🔌
            </div>

            <div>
              <h1 className="phm-topo__titulo">Dependências</h1>
              <p className="phm-topo__subtitulo">
                Visão operacional dos serviços e integrações usados pelos pipelines
              </p>
            </div>
          </div>
        </header>

        {estadoTela.carregando ? (
          <section className="phm-card">
            <div className="phm-loading">Carregando dependências...</div>
          </section>
        ) : estadoTela.erro ? (
          <section className="phm-card phm-card-erro">
            <h2 className="phm-card__titulo">Erro ao carregar dependências</h2>
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

              <article className="phm-card phm-card-indicador phm-card-indicador--healthy">
                <span className="phm-card-indicador__titulo">OK</span>
                <strong className="phm-card-indicador__valor">{resumo.ok}</strong>
              </article>

              <article className="phm-card phm-card-indicador phm-card-indicador--degraded">
                <span className="phm-card-indicador__titulo">Warning</span>
                <strong className="phm-card-indicador__valor">{resumo.warning}</strong>
              </article>

              <article className="phm-card phm-card-indicador phm-card-indicador--incidents">
                <span className="phm-card-indicador__titulo">Unstable</span>
                <strong className="phm-card-indicador__valor">{resumo.unstable}</strong>
              </article>

              <article className="phm-card phm-card-indicador phm-card-indicador--critical">
                <span className="phm-card-indicador__titulo">Down</span>
                <strong className="phm-card-indicador__valor">{resumo.down}</strong>
              </article>
            </section>

            <PainelDependencias dependencias={dependencias} />
          </>
        )}
      </div>
    </main>
  );
}