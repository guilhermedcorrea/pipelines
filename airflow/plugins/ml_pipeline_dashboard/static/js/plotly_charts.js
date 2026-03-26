(function (window) {
  "use strict";

  const utils = window.MLPipelineDashboardUtils || {};

  const PALETA = {
    primario: "rgba(85,65,137,.86)",
    primarioLinha: "rgba(85,65,137,1)",
    secundario: "rgba(75,123,236,.82)",
    secundarioLinha: "rgba(75,123,236,1)",
    sucesso: "rgba(46,159,87,.84)",
    sucessoLinha: "rgba(46,159,87,1)",
    alerta: "rgba(200,145,28,.86)",
    alertaLinha: "rgba(200,145,28,1)",
    erro: "rgba(211,84,84,.86)",
    erroLinha: "rgba(211,84,84,1)",
    neutro: "rgba(142,164,199,.86)",
    neutroLinha: "rgba(142,164,199,1)",
    base: "rgba(151,165,186,.75)",
    baseLinha: "rgba(151,165,186,1)",
  };

  function escaparHtml(valor) {
    if (typeof utils.escaparHtml === "function") return utils.escaparHtml(valor);
    return String(valor ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#39;");
  }

  function arraySeguro(valor) {
    if (typeof utils.arraySeguro === "function") return utils.arraySeguro(valor);
    return Array.isArray(valor) ? valor : [];
  }

  function objetoSeguro(valor) {
    if (typeof utils.objetoSeguro === "function") return utils.objetoSeguro(valor);
    return valor && typeof valor === "object" && !Array.isArray(valor) ? valor : {};
  }

  function textoSeguro(valor) {
    if (typeof utils.texto === "function") return utils.texto(valor);
    return String(valor ?? "").trim();
  }

  function formatarNumero(valor, casas = 4) {
    if (typeof utils.formatarNumero === "function") return utils.formatarNumero(valor, casas);
    const numero = Number(valor);
    if (!Number.isFinite(numero)) return "-";
    return numero.toLocaleString("pt-BR", {
      minimumFractionDigits: casas,
      maximumFractionDigits: casas,
    });
  }

  function formatarPercentual(valor, casas = 2) {
    const numero = Number(valor);
    if (!Number.isFinite(numero)) return "-";
    return `${(numero * 100).toLocaleString("pt-BR", {
      minimumFractionDigits: casas,
      maximumFractionDigits: casas,
    })}%`;
  }

  function plotlyDisponivel() {
    return typeof window.Plotly !== "undefined";
  }

  function limparContainer(container) {
    if (!container) return;
    container.innerHTML = "";
  }

  function exibirMensagem(container, mensagem) {
    if (!container) return;
    container.innerHTML = `<div class="estado-vazio">${escaparHtml(mensagem)}</div>`;
  }

  function layoutBase(titulo) {
    return {
      title: {
        text: titulo || "",
        font: {
          color: "#17325c",
          size: 16,
        },
      },
      paper_bgcolor: "rgba(255,255,255,0)",
      plot_bgcolor: "rgba(255,255,255,0)",
      font: {
        color: "#17325c",
        family: "Inter, Segoe UI, Arial, sans-serif",
      },
      margin: {
        t: 56,
        r: 22,
        b: 56,
        l: 62,
      },
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
        orientation: "h",
        yanchor: "bottom",
        y: 1.02,
        xanchor: "left",
        x: 0,
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
      modeBarButtonsToRemove: [
        "select2d",
        "lasso2d",
        "autoScale2d",
        "toggleSpikelines",
      ],
    };
  }

  function normalizarArrayNumerico(valores) {
    return arraySeguro(valores).map((valor) => {
      const numero = Number(valor);
      return Number.isFinite(numero) ? numero : null;
    });
  }

  function temPeloMenosUmNumero(valores) {
    return normalizarArrayNumerico(valores).some((valor) => valor !== null);
  }

  function obterLayoutMesclado(titulo, layoutCustomizado = {}) {
    const base = layoutBase(titulo);
    const custom = objetoSeguro(layoutCustomizado);
    const xaxis = objetoSeguro(custom.xaxis);
    const yaxis = objetoSeguro(custom.yaxis);
    const yaxis2 = objetoSeguro(custom.yaxis2);

    return {
      ...base,
      ...custom,
      xaxis: {
        ...base.xaxis,
        ...xaxis,
        title: custom.xaxis_title || xaxis.title || base.xaxis.title,
        tickformat: custom.xaxis_tickformat || xaxis.tickformat,
      },
      yaxis: {
        ...base.yaxis,
        ...yaxis,
        title: custom.yaxis_title || yaxis.title || base.yaxis.title,
        tickformat: custom.yaxis_tickformat || yaxis.tickformat,
      },
      yaxis2: {
        ...yaxis2,
        title: custom.yaxis2_title || yaxis2.title,
        tickformat: custom.yaxis2_tickformat || yaxis2.tickformat,
      },
    };
  }

  function criarTraceLinha(nome, x, y, opcoes = {}) {
    return {
      type: "scatter",
      mode: opcoes.mode || "lines",
      name: nome,
      x,
      y,
      yaxis: opcoes.eixoY || "y",
      line: {
        width: opcoes.espessura || 2.5,
        color: opcoes.corLinha || PALETA.primarioLinha,
        dash: opcoes.dash || "solid",
      },
      marker: {
        size: opcoes.tamanhoMarcador || 7,
        color: opcoes.corMarcador || opcoes.corLinha || PALETA.primarioLinha,
      },
      fill: opcoes.fill,
      opacity: opcoes.opacidade ?? 1,
      hovertemplate: opcoes.hovertemplate,
      showlegend: opcoes.showlegend !== false,
    };
  }

  function criarTraceBarra(orientacao, x, y, texto, nome, cor) {
    return {
      type: "bar",
      orientation: orientacao,
      x,
      y,
      name: nome,
      text: texto,
      textposition: "auto",
      marker: {
        color: cor,
        line: {
          color: "rgba(75,123,236,.22)",
          width: 1,
        },
      },
      hoverinfo: "x+y+text",
    };
  }

  function renderizarComTraces(container, traces, titulo, layoutCustomizado = {}) {
    if (!container) return;

    if (!plotlyDisponivel()) {
      exibirMensagem(container, "Plotly não está disponível na página.");
      return;
    }

    if (!arraySeguro(traces).length) {
      exibirMensagem(container, "Não há dados suficientes para montar este gráfico.");
      return;
    }

    window.Plotly.newPlot(
      container,
      traces,
      obterLayoutMesclado(titulo, layoutCustomizado),
      configuracaoBase(),
    );
  }

  function renderizarBarrasMetricas(container, itens, titulo = "Métricas principais por task") {
    if (!container) return;

    const linhas = arraySeguro(itens).filter((item) => Number.isFinite(Number(item?.valor)));
    if (!linhas.length) {
      exibirMensagem(container, "Não há métricas numéricas suficientes para montar este gráfico.");
      return;
    }

    const eixoX = linhas.map((item) => `${item.task || "-"}<br>${item.metric_label || item.label || "Métrica"}`);
    const eixoY = linhas.map((item) => Number(item.valor));
    const hover = linhas.map((item) => `${item.task || "-"} • ${item.metric_label || item.label || "Métrica"}: ${formatarNumero(item.valor, 4)}`);

    renderizarComTraces(
      container,
      [
        {
          type: "bar",
          x: eixoX,
          y: eixoY,
          text: eixoY.map((valor) => formatarNumero(valor, 4)),
          textposition: "outside",
          hovertext: hover,
          hoverinfo: "text",
          marker: {
            color: PALETA.primario,
            line: {
              color: "rgba(75,123,236,.30)",
              width: 1,
            },
          },
        },
      ],
      titulo,
      {
        yaxis_title: "Valor",
        xaxis_title: "Task / Métrica",
      },
    );
  }

  function renderizarSerieFolds(container, itens, titulo = "Evolução das métricas por fold") {
    if (!container) return;

    const linhas = arraySeguro(itens).filter((item) => Number.isFinite(Number(item?.valor)));
    if (!linhas.length) {
      exibirMensagem(container, "Não há dados de folds disponíveis para o gráfico.");
      return;
    }

    const agrupado = new Map();

    linhas.forEach((item) => {
      const chave = `${item.task || "-"}__${item.metric_label || item.label || "Métrica"}`;
      if (!agrupado.has(chave)) {
        agrupado.set(chave, {
          nome: `${item.task || "-"} • ${item.metric_label || item.label || "Métrica"}`,
          x: [],
          y: [],
        });
      }
      const grupo = agrupado.get(chave);
      grupo.x.push(Number(item.fold));
      grupo.y.push(Number(item.valor));
    });

    const cores = [
      PALETA.primarioLinha,
      PALETA.secundarioLinha,
      PALETA.sucessoLinha,
      PALETA.alertaLinha,
      PALETA.erroLinha,
      PALETA.neutroLinha,
    ];

    const traces = Array.from(agrupado.values()).map((grupo, indice) =>
      criarTraceLinha(grupo.nome, grupo.x, grupo.y, {
        mode: "lines+markers",
        corLinha: cores[indice % cores.length],
        corMarcador: cores[indice % cores.length],
      }),
    );

    renderizarComTraces(
      container,
      traces,
      titulo,
      {
        xaxis_title: "Fold",
        yaxis_title: "Valor",
      },
    );
  }

  function renderizarStatusTasks(container, tasks, titulo = "Distribuição de status das tasks") {
    if (!container) return;

    const itens = arraySeguro(tasks);
    if (!itens.length) {
      exibirMensagem(container, "Nenhuma task disponível para compor o gráfico de status.");
      return;
    }

    const contadores = {};
    itens.forEach((task) => {
      const status = typeof utils.normalizarStatus === "function"
        ? utils.normalizarStatus(task?.status)
        : String(task?.status || "unknown").toLowerCase();
      contadores[status || "unknown"] = (contadores[status || "unknown"] || 0) + 1;
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

    renderizarComTraces(
      container,
      [
        {
          type: "pie",
          labels,
          values,
          hole: 0.48,
          textinfo: "label+percent",
          hoverinfo: "label+value+percent",
          marker: { colors: cores },
        },
      ],
      titulo,
      {
        margin: { t: 46, r: 20, b: 20, l: 20 },
      },
    );
  }

  function renderizarGraficoPlotly(container, especificacao = {}) {
    if (!container) return;

    const spec = objetoSeguro(especificacao);
    const subtipo = textoSeguro(spec.subtipo).toLowerCase();
    const titulo = spec.titulo || "Gráfico analítico";
    const layout = objetoSeguro(spec.layout);
    const dados = objetoSeguro(spec.dados);
    const series = arraySeguro(spec.series);

    if (!subtipo) {
      exibirMensagem(container, "O widget de gráfico veio sem subtipo definido.");
      return;
    }

    if (!plotlyDisponivel()) {
      exibirMensagem(container, "Plotly não está disponível na página.");
      return;
    }

    const renderizadores = {
      bar_vertical: () => {
        const x = arraySeguro(dados.x);
        const y = normalizarArrayNumerico(dados.y);
        if (!x.length || !temPeloMenosUmNumero(y)) {
          exibirMensagem(container, "Não há dados suficientes para o gráfico de barras verticais.");
          return;
        }

        renderizarComTraces(
          container,
          [
            criarTraceBarra(
              "v",
              x,
              y,
              arraySeguro(dados.texto).length ? dados.texto : y.map((valor) => formatarNumero(valor, 4)),
              dados.nome || titulo,
              PALETA.primario,
            ),
          ],
          titulo,
          layout,
        );
      },

      bar_horizontal: () => {
        const x = normalizarArrayNumerico(dados.x);
        const y = arraySeguro(dados.y);
        if (!y.length || !temPeloMenosUmNumero(x)) {
          exibirMensagem(container, "Não há dados suficientes para o gráfico de barras horizontais.");
          return;
        }

        renderizarComTraces(
          container,
          [
            criarTraceBarra(
              "h",
              x,
              y,
              arraySeguro(dados.texto).length ? dados.texto : x.map((valor) => formatarNumero(valor, 4)),
              dados.nome || titulo,
              PALETA.secundario,
            ),
          ],
          titulo,
          layout,
        );
      },

      roc_curve: () => {
        const x = normalizarArrayNumerico(dados.x);
        const y = normalizarArrayNumerico(dados.y);

        if (!temPeloMenosUmNumero(x) || !temPeloMenosUmNumero(y)) {
          exibirMensagem(container, "Não há pontos suficientes para a curva ROC.");
          return;
        }

        const traces = [
          criarTraceLinha("Modelo", x, y, {
            corLinha: PALETA.primarioLinha,
            corMarcador: PALETA.primarioLinha,
            mode: "lines",
            hovertemplate: "FPR: %{x:.2%}<br>TPR: %{y:.2%}<extra></extra>",
          }),
        ];

        if (arraySeguro(dados.linha_base_x).length && arraySeguro(dados.linha_base_y).length) {
          traces.push(
            criarTraceLinha("Linha base", dados.linha_base_x, dados.linha_base_y, {
              corLinha: PALETA.baseLinha,
              dash: "dash",
              mode: "lines",
              hovertemplate: "Base: %{x:.2%}<br>Base: %{y:.2%}<extra></extra>",
            }),
          );
        }

        renderizarComTraces(container, traces, titulo, layout);
      },

      pr_curve: () => {
        const x = normalizarArrayNumerico(dados.x);
        const y = normalizarArrayNumerico(dados.y);

        if (!temPeloMenosUmNumero(x) || !temPeloMenosUmNumero(y)) {
          exibirMensagem(container, "Não há pontos suficientes para a curva Precision-Recall.");
          return;
        }

        const traces = [
          criarTraceLinha("Modelo", x, y, {
            corLinha: PALETA.sucessoLinha,
            corMarcador: PALETA.sucessoLinha,
            mode: "lines",
            hovertemplate: "Recall: %{x:.2%}<br>Precision: %{y:.2%}<extra></extra>",
          }),
        ];

        if (arraySeguro(dados.linha_base_x).length && arraySeguro(dados.linha_base_y).length) {
          traces.push(
            criarTraceLinha("Taxa base", dados.linha_base_x, dados.linha_base_y, {
              corLinha: PALETA.baseLinha,
              dash: "dash",
              mode: "lines",
              hovertemplate: "Taxa base: %{y:.2%}<extra></extra>",
            }),
          );
        }

        renderizarComTraces(container, traces, titulo, layout);
      },

      ks_curve: () => {
        const x = normalizarArrayNumerico(dados.x);
        const positivos = normalizarArrayNumerico(dados.positivos);
        const negativos = normalizarArrayNumerico(dados.negativos);
        const distancia = normalizarArrayNumerico(dados.distancia);

        if (!temPeloMenosUmNumero(x) || !temPeloMenosUmNumero(positivos) || !temPeloMenosUmNumero(negativos)) {
          exibirMensagem(container, "Não há pontos suficientes para a curva KS.");
          return;
        }

        const traces = [
          criarTraceLinha("Positivos acumulados", x, positivos, {
            corLinha: PALETA.sucessoLinha,
            mode: "lines",
            hovertemplate: "População: %{x:.2%}<br>Positivos acumulados: %{y:.2%}<extra></extra>",
          }),
          criarTraceLinha("Negativos acumulados", x, negativos, {
            corLinha: PALETA.secundarioLinha,
            mode: "lines",
            hovertemplate: "População: %{x:.2%}<br>Negativos acumulados: %{y:.2%}<extra></extra>",
          }),
        ];

        if (temPeloMenosUmNumero(distancia)) {
          traces.push(
            criarTraceLinha("Distância absoluta", x, distancia, {
              corLinha: PALETA.alertaLinha,
              dash: "dot",
              mode: "lines",
              hovertemplate: "População: %{x:.2%}<br>Distância: %{y:.2%}<extra></extra>",
            }),
          );
        }

        if (Number.isFinite(Number(dados.x_ponto_maximo))) {
          traces.push({
            type: "scatter",
            mode: "markers",
            name: `KS máximo ${formatarNumero(dados.ks_maximo, 4)}`,
            x: [Number(dados.x_ponto_maximo), Number(dados.x_ponto_maximo)],
            y: [
              Number(dados.y_negativo_ponto_maximo),
              Number(dados.y_positivo_ponto_maximo),
            ],
            marker: {
              size: 10,
              color: PALETA.erroLinha,
            },
            hovertemplate: `KS máximo: ${formatarPercentual(dados.ks_maximo, 2)}<extra></extra>`,
          });
        }

        renderizarComTraces(container, traces, titulo, layout);
      },

      histograma_duas_series: () => {
        const x = normalizarArrayNumerico(dados.x);
        const classe0 = normalizarArrayNumerico(dados.classe_0);
        const classe1 = normalizarArrayNumerico(dados.classe_1);

        if (!temPeloMenosUmNumero(classe0) && !temPeloMenosUmNumero(classe1)) {
          exibirMensagem(container, "Não há dados suficientes para o histograma por classe.");
          return;
        }

        renderizarComTraces(
          container,
          [
            {
              type: "bar",
              name: dados.nome_classe_0 || "Classe 0",
              x,
              y: classe0,
              opacity: 0.72,
              marker: { color: PALETA.secundario },
              hovertemplate: "Probabilidade: %{x:.2f}<br>Classe 0: %{y}<extra></extra>",
            },
            {
              type: "bar",
              name: dados.nome_classe_1 || "Classe 1",
              x,
              y: classe1,
              opacity: 0.72,
              marker: { color: PALETA.sucesso },
              hovertemplate: "Probabilidade: %{x:.2f}<br>Classe 1: %{y}<extra></extra>",
            },
          ],
          titulo,
          {
            ...layout,
            barmode: "overlay",
          },
        );
      },

      calibration_curve: () => {
        const x = normalizarArrayNumerico(dados.x);
        const y = normalizarArrayNumerico(dados.y);

        if (!temPeloMenosUmNumero(x) || !temPeloMenosUmNumero(y)) {
          exibirMensagem(container, "Não há dados suficientes para o gráfico de calibração.");
          return;
        }

        const traces = [];

        if (arraySeguro(dados.linha_base_x).length && arraySeguro(dados.linha_base_y).length) {
          traces.push(
            criarTraceLinha("Linha ideal", dados.linha_base_x, dados.linha_base_y, {
              corLinha: PALETA.baseLinha,
              dash: "dash",
              mode: "lines",
            }),
          );
        }

        traces.push(
          criarTraceLinha("Modelo", x, y, {
            corLinha: PALETA.primarioLinha,
            corMarcador: PALETA.primarioLinha,
            mode: "lines+markers",
            hovertemplate: "Prob. média prevista: %{x:.2%}<br>Taxa real: %{y:.2%}<extra></extra>",
          }),
        );

        renderizarComTraces(container, traces, titulo, layout);
      },

      linha_multiplas_series: () => {
        if (!series.length) {
          exibirMensagem(container, "Não há séries suficientes para o gráfico temporal por múltiplas linhas.");
          return;
        }

        const cores = [
          PALETA.primarioLinha,
          PALETA.secundarioLinha,
          PALETA.sucessoLinha,
          PALETA.alertaLinha,
          PALETA.erroLinha,
          PALETA.neutroLinha,
        ];

        const traces = series.map((serie, indice) =>
          criarTraceLinha(
            serie.nome || `Série ${indice + 1}`,
            arraySeguro(serie.x),
            normalizarArrayNumerico(serie.y),
            {
              mode: "lines+markers",
              corLinha: cores[indice % cores.length],
              corMarcador: cores[indice % cores.length],
            },
          ),
        );

        renderizarComTraces(container, traces, titulo, layout);
      },

      linha_duas_series: () => {
        const x = arraySeguro(dados.x);
        const serie1 = normalizarArrayNumerico(dados.serie_1_y);
        const serie2 = normalizarArrayNumerico(dados.serie_2_y);

        if (!x.length || (!temPeloMenosUmNumero(serie1) && !temPeloMenosUmNumero(serie2))) {
          exibirMensagem(container, "Não há dados suficientes para o gráfico de duas séries.");
          return;
        }

        const usarEixoSecundario = Boolean(layout.serie_2_eixo_secundario);

        const traces = [
          criarTraceLinha(dados.serie_1_nome || "Série 1", x, serie1, {
            mode: "lines+markers",
            corLinha: PALETA.secundarioLinha,
            corMarcador: PALETA.secundarioLinha,
          }),
          criarTraceLinha(dados.serie_2_nome || "Série 2", x, serie2, {
            mode: "lines+markers",
            corLinha: PALETA.alertaLinha,
            corMarcador: PALETA.alertaLinha,
            eixoY: usarEixoSecundario ? "y2" : "y",
          }),
        ];

        const layoutFinal = {
          ...layout,
          yaxis2: usarEixoSecundario
            ? {
                overlaying: "y",
                side: "right",
                showgrid: false,
                title: layout.yaxis2_title || dados.serie_2_nome || "Série 2",
                tickformat: layout.yaxis2_tickformat,
              }
            : objetoSeguro(layout.yaxis2),
        };

        renderizarComTraces(container, traces, titulo, layoutFinal);
      },

      roc_dupla: () => {
        const oof = objetoSeguro(dados.oof);
        const oot = objetoSeguro(dados.oot);
        const traces = [];

        if (temPeloMenosUmNumero(oof.x) && temPeloMenosUmNumero(oof.y)) {
          traces.push(
            criarTraceLinha("Walk-forward OOF", normalizarArrayNumerico(oof.x), normalizarArrayNumerico(oof.y), {
              corLinha: PALETA.secundarioLinha,
              mode: "lines",
            }),
          );
        }

        if (temPeloMenosUmNumero(oot.x) && temPeloMenosUmNumero(oot.y)) {
          traces.push(
            criarTraceLinha("Teste final OOT", normalizarArrayNumerico(oot.x), normalizarArrayNumerico(oot.y), {
              corLinha: PALETA.primarioLinha,
              mode: "lines",
            }),
          );
        }

        if (arraySeguro(oot.linha_base_x).length && arraySeguro(oot.linha_base_y).length) {
          traces.push(
            criarTraceLinha("Linha base", oot.linha_base_x, oot.linha_base_y, {
              corLinha: PALETA.baseLinha,
              dash: "dash",
              mode: "lines",
            }),
          );
        }

        if (!traces.length) {
          exibirMensagem(container, "Não há dados suficientes para a comparação ROC OOF vs OOT.");
          return;
        }

        renderizarComTraces(container, traces, titulo, layout);
      },

      pr_dupla: () => {
        const oof = objetoSeguro(dados.oof);
        const oot = objetoSeguro(dados.oot);
        const traces = [];

        if (temPeloMenosUmNumero(oof.x) && temPeloMenosUmNumero(oof.y)) {
          traces.push(
            criarTraceLinha("Walk-forward OOF", normalizarArrayNumerico(oof.x), normalizarArrayNumerico(oof.y), {
              corLinha: PALETA.secundarioLinha,
              mode: "lines",
            }),
          );
        }

        if (temPeloMenosUmNumero(oot.x) && temPeloMenosUmNumero(oot.y)) {
          traces.push(
            criarTraceLinha("Teste final OOT", normalizarArrayNumerico(oot.x), normalizarArrayNumerico(oot.y), {
              corLinha: PALETA.sucessoLinha,
              mode: "lines",
            }),
          );
        }

        const linhaBaseX = arraySeguro(oot.linha_base_x).length ? oot.linha_base_x : oof.linha_base_x;
        const linhaBaseY = arraySeguro(oot.linha_base_y).length ? oot.linha_base_y : oof.linha_base_y;

        if (arraySeguro(linhaBaseX).length && arraySeguro(linhaBaseY).length) {
          traces.push(
            criarTraceLinha("Taxa base", linhaBaseX, linhaBaseY, {
              corLinha: PALETA.baseLinha,
              dash: "dash",
              mode: "lines",
            }),
          );
        }

        if (!traces.length) {
          exibirMensagem(container, "Não há dados suficientes para a comparação Precision-Recall OOF vs OOT.");
          return;
        }

        renderizarComTraces(container, traces, titulo, layout);
      },

      calibration_dupla: () => {
        const traces = [];

        if (arraySeguro(dados.linha_base_x).length && arraySeguro(dados.linha_base_y).length) {
          traces.push(
            criarTraceLinha("Linha ideal", dados.linha_base_x, dados.linha_base_y, {
              corLinha: PALETA.baseLinha,
              dash: "dash",
              mode: "lines",
            }),
          );
        }

        if (temPeloMenosUmNumero(dados.oof_x) && temPeloMenosUmNumero(dados.oof_y)) {
          traces.push(
            criarTraceLinha("Walk-forward OOF", normalizarArrayNumerico(dados.oof_x), normalizarArrayNumerico(dados.oof_y), {
              corLinha: PALETA.secundarioLinha,
              corMarcador: PALETA.secundarioLinha,
              mode: "lines+markers",
            }),
          );
        }

        if (temPeloMenosUmNumero(dados.oot_x) && temPeloMenosUmNumero(dados.oot_y)) {
          traces.push(
            criarTraceLinha("Teste final OOT", normalizarArrayNumerico(dados.oot_x), normalizarArrayNumerico(dados.oot_y), {
              corLinha: PALETA.primarioLinha,
              corMarcador: PALETA.primarioLinha,
              mode: "lines+markers",
            }),
          );
        }

        if (!traces.length) {
          exibirMensagem(container, "Não há dados suficientes para a comparação de calibração OOF vs OOT.");
          return;
        }

        renderizarComTraces(container, traces, titulo, layout);
      },

      histograma_dupla_comparacao: () => {
        const oof = objetoSeguro(dados.oof);
        const oot = objetoSeguro(dados.oot);
        const traces = [];

        if (temPeloMenosUmNumero(oof.x) && (temPeloMenosUmNumero(oof.classe_0) || temPeloMenosUmNumero(oof.classe_1))) {
          traces.push({
            type: "bar",
            name: "OOF - Classe 0",
            x: normalizarArrayNumerico(oof.x),
            y: normalizarArrayNumerico(oof.classe_0),
            opacity: 0.42,
            marker: { color: PALETA.secundario },
          });

          traces.push({
            type: "bar",
            name: "OOF - Classe 1",
            x: normalizarArrayNumerico(oof.x),
            y: normalizarArrayNumerico(oof.classe_1),
            opacity: 0.42,
            marker: { color: PALETA.sucesso },
          });
        }

        if (temPeloMenosUmNumero(oot.x) && (temPeloMenosUmNumero(oot.classe_0) || temPeloMenosUmNumero(oot.classe_1))) {
          traces.push({
            type: "scatter",
            mode: "lines",
            name: "OOT - Classe 0",
            x: normalizarArrayNumerico(oot.x),
            y: normalizarArrayNumerico(oot.classe_0),
            line: {
              color: PALETA.secundarioLinha,
              width: 2.5,
            },
          });

          traces.push({
            type: "scatter",
            mode: "lines",
            name: "OOT - Classe 1",
            x: normalizarArrayNumerico(oot.x),
            y: normalizarArrayNumerico(oot.classe_1),
            line: {
              color: PALETA.sucessoLinha,
              width: 2.5,
            },
          });
        }

        if (!traces.length) {
          exibirMensagem(container, "Não há dados suficientes para o histograma comparativo OOF vs OOT.");
          return;
        }

        renderizarComTraces(
          container,
          traces,
          titulo,
          {
            ...layout,
            barmode: "overlay",
          },
        );
      },
    };

    const renderizador = renderizadores[subtipo];
    if (!renderizador) {
      exibirMensagem(container, `O subtipo de gráfico '${subtipo}' ainda não foi suportado pelo front-end.`);
      return;
    }

    renderizador();
  }

  window.MLPipelineDashboardCharts = {
    limparContainer,
    exibirMensagem,
    renderizarBarrasMetricas,
    renderizarSerieFolds,
    renderizarStatusTasks,
    renderizarGraficoPlotly,
  };
})(window);