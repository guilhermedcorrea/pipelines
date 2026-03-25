(function (window) {
  "use strict";

  function texto(valor) {
    return String(valor ?? "").trim();
  }

  function escaparHtml(valor) {
    return String(valor ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#39;");
  }

  function truncarTexto(valor, limite = 180) {
    const textoNormalizado = texto(valor);
    if (!textoNormalizado) return "";
    if (textoNormalizado.length <= limite) return textoNormalizado;
    return `${textoNormalizado.slice(0, limite).trim()}...`;
  }

  function arraySeguro(valor) {
    return Array.isArray(valor) ? valor : [];
  }

  function objetoSeguro(valor) {
    return valor && typeof valor === "object" && !Array.isArray(valor) ? valor : {};
  }

  function normalizarStatus(status) {
    const valor = texto(status).toLowerCase();
    if (!valor) return "unknown";
    if (valor.includes("success")) return "success";
    if (valor.includes("fail")) return "failed";
    if (valor.includes("error")) return "failed";
    if (valor.includes("up_for_retry")) return "running";
    if (valor.includes("run")) return "running";
    if (valor.includes("queue")) return "queued";
    return valor;
  }

  function classeStatus(status) {
    const valor = normalizarStatus(status);
    if (valor === "success") return "ml-status-success";
    if (valor === "failed") return "ml-status-failed";
    if (valor === "running") return "ml-status-running";
    if (valor === "queued") return "ml-status-queued";
    return "ml-status-default";
  }

  function atribuirTexto(elemento, valor, fallback = "") {
    if (!elemento) return;
    const textoFinal =
      valor === undefined || valor === null || String(valor) === ""
        ? fallback
        : String(valor);
    elemento.textContent = textoFinal;
  }

  function atribuirHtml(elemento, html, fallback = "") {
    if (!elemento) return;
    const htmlFinal = texto(html) ? html : fallback;
    elemento.innerHTML = htmlFinal;
  }

  function formatarNumero(valor, casas = 2) {
    const numero = Number(valor);
    if (!Number.isFinite(numero)) return "-";
    return numero.toLocaleString("pt-BR", {
      minimumFractionDigits: casas,
      maximumFractionDigits: casas,
    });
  }

  function formatarInteiro(valor) {
    const numero = Number(valor);
    if (!Number.isFinite(numero)) return "-";
    return numero.toLocaleString("pt-BR", {
      maximumFractionDigits: 0,
    });
  }

  function formatarPercentual(valor, casas = 2) {
    const numero = Number(valor);
    if (!Number.isFinite(numero)) return "-";
    return numero.toLocaleString("pt-BR", {
      style: "percent",
      minimumFractionDigits: casas,
      maximumFractionDigits: casas,
    });
  }

  function formatarValorPorFormato(valor, formato = "auto") {
    const numero = Number(valor);

    if (formato === "texto") {
      return String(valor ?? "-");
    }

    if (!Number.isFinite(numero)) {
      return valor === null || valor === undefined || valor === "" ? "-" : String(valor);
    }

    if (formato === "percentual") {
      return formatarPercentual(numero, 2);
    }

    if (formato === "decimal_4") {
      return formatarNumero(numero, 4);
    }

    if (formato === "decimal_2") {
      return formatarNumero(numero, 2);
    }

    if (formato === "inteiro") {
      return formatarInteiro(numero);
    }

    if (formato === "auto") {
      if (Number.isInteger(numero)) return formatarInteiro(numero);
      if (Math.abs(numero) <= 1) return formatarNumero(numero, 4);
      return formatarNumero(numero, 2);
    }

    return formatarNumero(numero, 4);
  }

  function slugify(valor) {
    return texto(valor)
      .normalize("NFD")
      .replace(/[\u0300-\u036f]/g, "")
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/^-+|-+$/g, "");
  }

  function gerarIdUnico(prefixo = "id") {
    return `${prefixo}-${Math.random().toString(36).slice(2, 10)}`;
  }

  function formatarDataHora(valor) {
    const textoValor = texto(valor);
    if (!textoValor) return "-";

    const data = new Date(textoValor);
    if (Number.isNaN(data.getTime())) return textoValor;

    return data.toLocaleString("pt-BR");
  }

  function abrirUrlComParametros(urlBase, parametros = {}) {
    const url = new URL(urlBase, window.location.origin);

    Object.entries(objetoSeguro(parametros)).forEach(([chave, valor]) => {
      const textoValor = texto(valor);
      if (textoValor) {
        url.searchParams.set(chave, textoValor);
      }
    });

    return url.toString();
  }

  async function obterJson(url) {
    const resposta = await window.fetch(url, {
      credentials: "same-origin",
      headers: {
        Accept: "application/json",
      },
    });

    if (!resposta.ok) {
      const textoErro = await resposta.text();
      throw new Error(`Falha HTTP ${resposta.status}: ${textoErro || "sem detalhes."}`);
    }

    return resposta.json();
  }

  async function copiarTexto(valor) {
    const conteudo = String(valor ?? "");

    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(conteudo);
      return;
    }

    const area = document.createElement("textarea");
    area.value = conteudo;
    area.setAttribute("readonly", "");
    area.style.position = "fixed";
    area.style.left = "-9999px";
    document.body.appendChild(area);
    area.select();
    document.execCommand("copy");
    document.body.removeChild(area);
  }

  function debounce(funcao, espera = 250) {
    let temporizador = null;

    return function (...args) {
      const contexto = this;
      window.clearTimeout(temporizador);
      temporizador = window.setTimeout(() => {
        funcao.apply(contexto, args);
      }, espera);
    };
  }

  window.MLPipelineDashboardUtils = {
    texto,
    escaparHtml,
    truncarTexto,
    arraySeguro,
    objetoSeguro,
    normalizarStatus,
    classeStatus,
    atribuirTexto,
    atribuirHtml,
    formatarNumero,
    formatarInteiro,
    formatarPercentual,
    formatarValorPorFormato,
    slugify,
    gerarIdUnico,
    formatarDataHora,
    abrirUrlComParametros,
    obterJson,
    copiarTexto,
    debounce,
  };
})(window);