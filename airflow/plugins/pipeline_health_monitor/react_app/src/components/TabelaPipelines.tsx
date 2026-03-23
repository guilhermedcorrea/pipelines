import {
  PipelineItem,
  formatarDataHora,
  formatarDuracaoComparativa,
  obterClasseStatusPipeline,
  traduzirStatusPipeline,
} from "../services/api";

interface PropsTabelaPipelines {
  pipelines: PipelineItem[];
}

export default function TabelaPipelines(
  props: PropsTabelaPipelines
): JSX.Element {
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