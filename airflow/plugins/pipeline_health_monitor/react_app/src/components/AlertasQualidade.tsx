import { useMemo } from "react";
import { PipelineItem } from "../services/api";

interface PropsAlertasQualidade {
  pipelines: PipelineItem[];
}

export default function AlertasQualidade(
  props: PropsAlertasQualidade
): JSX.Element {
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

      if (
        pipeline.duration_atual_min != null &&
        pipeline.duration_media_min != null &&
        pipeline.duration_media_min > 0 &&
        pipeline.duration_atual_min > pipeline.duration_media_min * 1.8
      ) {
        lista.push(
          `${pipeline.nome}: duração atual muito acima da média (${pipeline.duration_atual_min}m vs ${pipeline.duration_media_min}m)`
        );
      }
    }

    return lista.slice(0, 8);
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