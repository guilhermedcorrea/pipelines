(function (window) {
  "use strict";

  const utils = window.MLPipelineDashboardUtils;

  function plotlyDisponivel() {
    return typeof window.Plotly !== "undefined";
  }

  function limparContainer(container) {
    if (!container) return;
    container.innerHTML = "";
  }

  function exibirMensagem(container, mensagem) {
    if (!container) return;
    container.innerHTML = `<div class="estado-vazio">${utils.escaparHtml(mensagem)}</div>`;
  }

  function layoutBase(titulo) {
    return {
      title: {
        text: titulo || "",
        font: { color: "#17325c", size: 16 },
      },
      paper_bgcolor: "rgba(255,255,255,0)",
      plot_bgcolor: "rgba(255,255,255,0)",
      font: {
        color: "#17325c",
        family: "Inter, Segoe UI, Arial, sans-serif",
      },
      margin: { t: 46, r: 20, b: 50, l: 56 },
      xaxis: {
        gridcolor: "rgba(23,50,92,.08)",
        zerolinecolor: "rgba(23,50,92,.10)",
        tickfont: { color: "#5c7398" },
        titlefont: { color: "#5c7398" },
      },
      yaxis: {
        gridcolor: "rgba(23,50,92,.08)",
        zerolinecolor: "rgba(23,50,92,.10)",
        tickfont: { color: "#5c7398" },
        titlefont: { color: "#5c7398" },
      },
      legend: {
        font: { color: "#17325c" },
      },
      hoverlabel: {
        bgcolor: "#ffffff",
        bordercolor: "#d9e3f3",
        font: { color: "#17325c" },
      },
    };
  }

  function configuracaoBase() {
    return {
      responsive: true,
      displaylogo: false,
      modeBarButtonsToRemove: ["select2d", "lasso2d", "autoScale2d"],
    };
  }

  function renderizarBarrasMetricas(container, itens, titulo = "Métricas principais por task") {
    if (!container) return;

    if (!plotlyDisponivel()) {
      exibirMensagem(container, "Plotly não está disponível na página.");
      return;
    }

    const linhas = utils.arraySeguro(itens).filter((item) =>
      Number.isFinite(Number(item?.valor)),
    );

    if (!linhas.length) {
      exibirMensagem(container, "Não há métricas numéricas suficientes para montar este gráfico.");
      return;
    }

    const eixoX = linhas.map((item) => `${item.task}<br>${item.metric_label}`);
    const eixoY = linhas.map((item) => Number(item.valor));
    const hover = linhas.map(
      (item) =>
        `${item.task} • ${item.metric_label}: ${utils.formatarNumero(item.valor, 4)}`,
    );

    window.Plotly.newPlot(
      container,
      [
        {
          type: "bar",
          x: eixoX,
          y: eixoY,
          text: eixoY.map((valor) => utils.formatarNumero(valor, 4)),
          textposition: "outside",
          hovertext: hover,
          hoverinfo: "text",
          marker: {
            color: "rgba(85,65,137,.82)",
            line: {
              color: "rgba(75,123,236,.30)",
              width: 1,
            },
          },
        },
      ],
      {
        ...layoutBase(titulo),
        yaxis: {
          ...layoutBase(titulo).yaxis,
          title: "Valor",
        },
        xaxis: {
          ...layoutBase(titulo).xaxis,
          title: "Task / Métrica",
        },
      },
      configuracaoBase(),
    );
  }

  function renderizarSerieFolds(container, itens, titulo = "Evolução das métricas por fold") {
    if (!container) return;

    if (!plotlyDisponivel()) {
      exibirMensagem(container, "Plotly não está disponível na página.");
      return;
    }

    const linhas = utils.arraySeguro(itens).filter((item) =>
      Number.isFinite(Number(item?.valor)),
    );

    if (!linhas.length) {
      exibirMensagem(container, "Não há dados de folds disponíveis para o gráfico.");
      return;
    }

    const agrupado = new Map();

    linhas.forEach((item) => {
      const chave = `${item.task}__${item.metric_label}`;

      if (!agrupado.has(chave)) {
        agrupado.set(chave, {
          nome: `${item.task} • ${item.metric_label}`,
          x: [],
          y: [],
        });
      }

      const grupo = agrupado.get(chave);
      grupo.x.push(Number(item.fold));
      grupo.y.push(Number(item.valor));
    });

    const traces = Array.from(agrupado.values()).map((grupo) => ({
      type: "scatter",
      mode: "lines+markers",
      name: grupo.nome,
      x: grupo.x,
      y: grupo.y,
      line: {
        width: 2,
      },
      marker: {
        size: 7,
      },
    }));

    window.Plotly.newPlot(
      container,
      traces,
      {
        ...layoutBase(titulo),
        xaxis: {
          ...layoutBase(titulo).xaxis,
          title: "Fold",
        },
        yaxis: {
          ...layoutBase(titulo).yaxis,
          title: "Valor",
        },
      },
      configuracaoBase(),
    );
  }

  function renderizarStatusTasks(container, tasks, titulo = "Distribuição de status das tasks") {
    if (!container) return;

    if (!plotlyDisponivel()) {
      exibirMensagem(container, "Plotly não está disponível na página.");
      return;
    }

    const itens = utils.arraySeguro(tasks);

    if (!itens.length) {
      exibirMensagem(container, "Nenhuma task disponível para compor o gráfico de status.");
      return;
    }

    const contadores = {};

    itens.forEach((task) => {
      const status = utils.normalizarStatus(task?.status) || "unknown";
      contadores[status] = (contadores[status] || 0) + 1;
    });

    const labels = Object.keys(contadores);
    const values = labels.map((label) => contadores[label]);

    const cores = labels.map((label) => {
      if (label === "success") return "#2e9f57";
      if (label === "failed") return "#d35454";
      if (label === "running") return "#4b7bec";
      if (label === "queued") return "#c8911c";
      return "#8ea4c7";
    });

    window.Plotly.newPlot(
      container,
      [
        {
          type: "pie",
          labels,
          values,
          hole: 0.48,
          textinfo: "label+percent",
          hoverinfo: "label+value+percent",
          marker: {
            colors: cores,
          },
        },
      ],
      {
        ...layoutBase(titulo),
        margin: { t: 46, r: 20, b: 20, l: 20 },
      },
      configuracaoBase(),
    );
  }

  window.MLPipelineDashboardCharts = {
    limparContainer,
    exibirMensagem,
    renderizarBarrasMetricas,
    renderizarSerieFolds,
    renderizarStatusTasks,
  };
})(window);