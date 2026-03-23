interface PropsCardIndicador {
  titulo: string;
  valor: number | string;
  variante?:
    | "default"
    | "healthy"
    | "degraded"
    | "critical"
    | "paused"
    | "incidents";
}

export default function CardIndicador(props: PropsCardIndicador): JSX.Element {
  const { titulo, valor, variante = "default" } = props;

  return (
    <article className={`phm-card phm-card-indicador phm-card-indicador--${variante}`}>
      <span className="phm-card-indicador__titulo">{titulo}</span>
      <strong className="phm-card-indicador__valor">{valor}</strong>
    </article>
  );
}