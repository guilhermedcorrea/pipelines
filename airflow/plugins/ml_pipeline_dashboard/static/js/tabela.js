(function (window, document) {
  "use strict";

  const utils = window.MLPipelineDashboardUtils;
  if (!utils) {
    throw new Error("utils.js precisa ser carregado antes de tabela.js");
  }

  function iniciarTabelaDados(opcoes = {}) {
    const formulario = document.querySelector(opcoes.seletorFormulario || ".filtros-form, .ml-filtros-form");
    if (!formulario) return;

    const tabela = document.querySelector(opcoes.seletorTabela || "table, .ml-tabela");
    const campoBusca = document.getElementById(opcoes.idCampoBusca || "campoQ");
    const campoPaginaTamanho = document.getElementById(opcoes.idCampoPaginaTamanho || "campoPaginaTamanho");
    const campoDirecao = document.getElementById(opcoes.idCampoDirecao || "campoDirecao");
    const selectsMultiplos = Array.from(formulario.querySelectorAll("select[multiple]"));
    const inputsData = Array.from(formulario.querySelectorAll('input[type="date"]'));
    const inputsTexto = Array.from(formulario.querySelectorAll('input[type="text"]'));

    function garantirCampoPagina() {
      let campoPagina = formulario.querySelector('input[name="pagina"]');
      if (!campoPagina) {
        campoPagina = document.createElement("input");
        campoPagina.type = "hidden";
        campoPagina.name = "pagina";
        campoPagina.value = "1";
        formulario.appendChild(campoPagina);
      }
      return campoPagina;
    }

    function resetarPagina() {
      const campoPagina = garantirCampoPagina();
      campoPagina.value = "1";
    }

    function submeter() {
      formulario.submit();
    }

    const submeterDebounce = utils.debounce(() => {
      resetarPagina();
      submeter();
    }, 350);

    campoBusca?.addEventListener("keydown", (evento) => {
      if (evento.key === "Enter") {
        evento.preventDefault();
        resetarPagina();
        submeter();
      }
    });

    campoBusca?.addEventListener("input", () => {
      const tamanho = utils.texto(campoBusca.value).length;
      if (tamanho === 0 || tamanho >= 3) {
        submeterDebounce();
      }
    });

    campoPaginaTamanho?.addEventListener("change", () => {
      resetarPagina();
      submeter();
    });

    campoDirecao?.addEventListener("change", () => {
      resetarPagina();
      submeter();
    });

    selectsMultiplos.forEach((select) => {
      select.addEventListener("change", () => {
        resetarPagina();
      });
    });

    inputsData.forEach((input) => {
      input.addEventListener("change", () => {
        resetarPagina();
        submeter();
      });
    });

    inputsTexto.forEach((input) => {
      if (input === campoBusca) return;
      input.addEventListener("keydown", (evento) => {
        if (evento.key === "Enter") {
          evento.preventDefault();
          resetarPagina();
          submeter();
        }
      });
    });

    Array.from(document.querySelectorAll("[data-pagina-destino]"))
      .forEach((botao) => {
        botao.addEventListener("click", (evento) => {
          evento.preventDefault();
          const destino = botao.getAttribute("data-pagina-destino");
          const campoPagina = garantirCampoPagina();
          campoPagina.value = String(destino || 1);
          submeter();
        });
      });

    Array.from(document.querySelectorAll("th a[data-ordenacao-coluna]"))
      .forEach((link) => {
        link.addEventListener("click", (evento) => {
          evento.preventDefault();
          const coluna = link.getAttribute("data-ordenacao-coluna");
          const direcao = link.getAttribute("data-ordenacao-direcao") || "asc";
          let inputOrdenar = formulario.querySelector('input[name="ordenar_por"]');
          if (!inputOrdenar) {
            inputOrdenar = document.createElement("input");
            inputOrdenar.type = "hidden";
            inputOrdenar.name = "ordenar_por";
            formulario.appendChild(inputOrdenar);
          }
          let inputDirecao = formulario.querySelector('input[name="direcao"]');
          if (!inputDirecao) {
            inputDirecao = document.createElement("input");
            inputDirecao.type = "hidden";
            inputDirecao.name = "direcao";
            formulario.appendChild(inputDirecao);
          }
          inputOrdenar.value = coluna || "";
          inputDirecao.value = direcao;
          resetarPagina();
          submeter();
        });
      });

    Array.from(document.querySelectorAll("[data-limpar-multiselect]"))
      .forEach((botao) => {
        botao.addEventListener("click", (evento) => {
          evento.preventDefault();
          const alvo = botao.getAttribute("data-limpar-multiselect");
          const select = document.getElementById(alvo);
          if (!select) return;
          Array.from(select.options).forEach((option) => {
            option.selected = false;
          });
          resetarPagina();
          submeter();
        });
      });

    Array.from(document.querySelectorAll("[data-copiar-linha]"))
      .forEach((botao) => {
        botao.addEventListener("click", async (evento) => {
          evento.preventDefault();
          const linha = botao.closest("tr");
          if (!linha) return;
          const conteudo = Array.from(linha.querySelectorAll("td")).map((td) => td.textContent || "").join("\t");
          try {
            await utils.copiarTexto(conteudo);
            const textoOriginal = botao.textContent || "Copiar";
            botao.textContent = "Copiado";
            window.setTimeout(() => { botao.textContent = textoOriginal; }, 1200);
          } catch (erro) {
            console.error(erro);
          }
        });
      });

    if (tabela) {
      Array.from(tabela.querySelectorAll("tbody tr")).forEach((linha) => {
        linha.addEventListener("dblclick", async () => {
          const conteudo = Array.from(linha.querySelectorAll("td")).map((td) => td.textContent || "").join("\t");
          try {
            await utils.copiarTexto(conteudo);
          } catch (erro) {
            console.error(erro);
          }
        });
      });
    }
  }

  window.MLPipelineDashboardTabela = { iniciarTabelaDados };
})(window, document);