import {
  DependenciaItem,
  obterClasseStatusDependencia,
  traduzirStatusDependencia,
} from "../services/api";

interface PropsPainelDependencias {
  dependencias: DependenciaItem[];
}

export default function PainelDependencias(
  props: PropsPainelDependencias
): JSX.Element {
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