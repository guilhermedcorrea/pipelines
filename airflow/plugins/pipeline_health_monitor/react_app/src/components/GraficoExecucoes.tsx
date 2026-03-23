import { useMemo } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { PipelineItem } from "../services/api";

interface PropsGraficoExecucoes {
  pipelines: PipelineItem[];
}

interface LinhaGraficoExecucao {
  nome: string;
  score: number;
  status: string;
}

function obterCorBarra(status: string): string {
  switch (status) {
    case "healthy":
      return "#22c55e";
    case "degraded":
      return "#f59e0b";
    case "critical":
      return "#ef4444";
    case "paused":
      return "#94a3b8";
    default:
      return "#38bdf8";
  }
}

export default function GraficoExecucoes(
  props: PropsGraficoExecucoes
): JSX.Element {
  const { pipelines } = props;

  const dadosGrafico = useMemo<LinhaGraficoExecucao[]>(() => {
    return pipelines.slice(0, 8).map((pipeline) => ({
      nome:
        pipeline.nome.length > 18
          ? `${pipeline.nome.slice(0, 18)}...`
          : pipeline.nome,
      score: pipeline.health_score,
      status: pipeline.status,
    }));
  }, [pipelines]);

  return (
    <section className="phm-card">
      <div className="phm-card__header">
        <h2 className="phm-card__titulo">Execution Health Score</h2>
      </div>

      {dadosGrafico.length === 0 ? (
        <div className="phm-vazio">Nenhum dado disponível para o gráfico.</div>
      ) : (
        <div style={{ width: "100%", height: 280 }}>
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={dadosGrafico} margin={{ top: 8, right: 8, left: -20, bottom: 8 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.08)" />
              <XAxis
                dataKey="nome"
                tick={{ fill: "#94a3b8", fontSize: 12 }}
                axisLine={{ stroke: "rgba(255,255,255,0.08)" }}
                tickLine={{ stroke: "rgba(255,255,255,0.08)" }}
              />
              <YAxis
                domain={[0, 100]}
                tick={{ fill: "#94a3b8", fontSize: 12 }}
                axisLine={{ stroke: "rgba(255,255,255,0.08)" }}
                tickLine={{ stroke: "rgba(255,255,255,0.08)" }}
              />
              <Tooltip
                contentStyle={{
                  backgroundColor: "#111827",
                  border: "1px solid rgba(255,255,255,0.08)",
                  borderRadius: "12px",
                  color: "#e5e7eb",
                }}
                cursor={{ fill: "rgba(255,255,255,0.03)" }}
              />
              <Bar dataKey="score" radius={[8, 8, 0, 0]}>
                {dadosGrafico.map((linha, indice) => (
                  <Cell key={`celula-${linha.nome}-${indice}`} fill={obterCorBarra(linha.status)} />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </div>
      )}
    </section>
  );
}