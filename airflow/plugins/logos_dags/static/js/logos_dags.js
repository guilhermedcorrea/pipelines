(() => {
    "use strict";

    const SELETOR_CARDS = "[data-testid='card-list'] > *";
    const CLASSE_BLOCO = "bloco-logos-dag";

    let mapaTagsLote = null;
    let promessaMapaTagsLote = null;
    const cacheTagsPorDag = new Map();

    const normalizar = (texto) =>
        (texto || "")
            .normalize("NFD")
            .replace(/[\u0300-\u036f]/g, "")
            .toLowerCase()
            .trim();

    const criarLogo = (urlImagem, titulo, classeExtra = "") => {
        const logo = document.createElement("span");
        logo.className = `logo-pill ${classeExtra}`.trim();
        logo.title = titulo;
        logo.setAttribute("aria-label", titulo);
        logo.setAttribute("role", "img");
        logo.style.backgroundImage = `url("${urlImagem}")`;
        return logo;
    };

    const criarSeparador = () => {
        const sep = document.createElement("span");
        sep.className = "separador-logos";
        return sep;
    };

    const serializarTags = (tags) =>
        tags
            .map((tag) => normalizar(tag))
            .filter(Boolean)
            .sort()
            .join("|");

    const obterDagId = (card) => {
        const el = card.querySelector("[data-testid='dag-id']");
        return el ? (el.textContent || "").trim() : "";
    };

    const obterEstruturaGrupoEsquerdo = (card) => {
        const dagId = card.querySelector("[data-testid='dag-id']");
        const listaTags = card.querySelector("[data-testid='limited-items-list']");

        if (!dagId || !listaTags) {
            return null;
        }

        let atual = dagId.parentElement;

        while (atual && atual !== card) {
            if (atual.contains(dagId) && atual.contains(listaTags)) {
                return {
                    grupoEsquerdo: atual,
                    dagId,
                    listaTags,
                };
            }
            atual = atual.parentElement;
        }

        return null;
    };

    const obterTagsVisiveis = (card) => {
        const links = Array.from(
            card.querySelectorAll("[data-testid='limited-items-list'] a")
        );

        return links
            .map((el) => normalizar(el.textContent || ""))
            .filter(Boolean);
    };

    const temTagsDeLogo = (tags) => {
        const conjunto = new Set(tags.map(normalizar));
        return (
            conjunto.has("euromidia") ||
            conjunto.has("shempo") ||
            conjunto.has("sinamovel") ||
            conjunto.has("omie") ||
            conjunto.has("granatum")
        );
    };

    const construirMapaTagsLote = async () => {
        if (mapaTagsLote) {
            return mapaTagsLote;
        }

        if (promessaMapaTagsLote) {
            return promessaMapaTagsLote;
        }

        promessaMapaTagsLote = fetch("/api/v1/dags?limit=1000", {
            method: "GET",
            credentials: "same-origin",
            headers: {
                Accept: "application/json",
            },
        })
            .then(async (resposta) => {
                if (!resposta.ok) {
                    throw new Error(`Falha ao buscar dags em lote: ${resposta.status}`);
                }

                const dados = await resposta.json();
                const dags = Array.isArray(dados.dags) ? dados.dags : [];
                const mapa = new Map();

                dags.forEach((dag) => {
                    const dagId = (dag.dag_id || "").trim();
                    if (!dagId) {
                        return;
                    }

                    const tags = Array.isArray(dag.tags)
                        ? dag.tags
                              .map((tag) =>
                                  normalizar(typeof tag === "string" ? tag : tag?.name || "")
                              )
                              .filter(Boolean)
                        : [];

                    mapa.set(dagId, tags);
                });

                mapaTagsLote = mapa;
                return mapa;
            })
            .catch((erro) => {
                console.error("Erro ao buscar DAGs em lote:", erro);
                mapaTagsLote = new Map();
                return mapaTagsLote;
            })
            .finally(() => {
                promessaMapaTagsLote = null;
            });

        return promessaMapaTagsLote;
    };

    const buscarTagsDaDagEspecifica = async (dagId) => {
        if (!dagId) {
            return [];
        }

        if (cacheTagsPorDag.has(dagId)) {
            return cacheTagsPorDag.get(dagId);
        }

        try {
            const resposta = await fetch(`/api/v1/dags/${encodeURIComponent(dagId)}`, {
                method: "GET",
                credentials: "same-origin",
                headers: {
                    Accept: "application/json",
                },
            });

            if (!resposta.ok) {
                throw new Error(`Falha ao buscar DAG ${dagId}: ${resposta.status}`);
            }

            const dados = await resposta.json();

            const tags = Array.isArray(dados.tags)
                ? dados.tags
                      .map((tag) =>
                          normalizar(typeof tag === "string" ? tag : tag?.name || "")
                      )
                      .filter(Boolean)
                : [];

            cacheTagsPorDag.set(dagId, tags);
            return tags;
        } catch (erro) {
            console.error("Erro ao buscar DAG específica:", dagId, erro);
            cacheTagsPorDag.set(dagId, []);
            return [];
        }
    };

    const unirTags = (...listas) => {
        const conjunto = new Set();

        listas.flat().forEach((tag) => {
            const valor = normalizar(tag);
            if (valor) {
                conjunto.add(valor);
            }
        });

        return Array.from(conjunto);
    };

    const montarBlocoLogos = (tags) => {
        const bloco = document.createElement("div");
        bloco.className = CLASSE_BLOCO;

        const temEuromidia = tags.includes("euromidia");
        const temShempo = tags.includes("shempo");
        const temSinamovel = tags.includes("sinamovel");
        const temOmie = tags.includes("omie");
        const temGranatum = tags.includes("granatum");

        let quantidade = 0;
        let temEmpresa = false;

        if (temEuromidia) {
            bloco.appendChild(
                criarLogo(
                    "/logos-dags-static/imagens/LogoEmpresaProprietaria/euromidia.png",
                    "Euromidia",
                    "logo-empresa"
                )
            );
            quantidade += 1;
            temEmpresa = true;
        }

        if (temShempo) {
            bloco.appendChild(
                criarLogo(
                    "/logos-dags-static/imagens/LogoEmpresaProprietaria/Shempo.jpg",
                    "Shempo",
                    "logo-empresa"
                )
            );
            quantidade += 1;
            temEmpresa = true;
        }

        if (temSinamovel) {
            bloco.appendChild(
                criarLogo(
                    "/logos-dags-static/imagens/LogoEmpresaProprietaria/sinamovel.png",
                    "Sinamovel",
                    "logo-empresa"
                )
            );
            quantidade += 1;
            temEmpresa = true;
        }

        if (temEmpresa && (temOmie || temGranatum)) {
            bloco.appendChild(criarSeparador());
        }

        if (temOmie) {
            bloco.appendChild(
                criarLogo(
                    "/logos-dags-static/imagens/LogoSistemas/omie.jpg",
                    "Omie",
                    "logo-sistema"
                )
            );
            quantidade += 1;
        }

        if (temGranatum) {
            bloco.appendChild(
                criarLogo(
                    "/logos-dags-static/imagens/LogoSistemas/granatum.png",
                    "Granatum",
                    "logo-sistema"
                )
            );
            quantidade += 1;
        }

        return quantidade > 0 ? bloco : null;
    };

    const resolverTagsDoCard = async (card, dagId, mapaLote) => {
        const tagsVisiveis = obterTagsVisiveis(card);
        const tagsLote = mapaLote.get(dagId) || [];

        let tags = unirTags(tagsVisiveis, tagsLote);

        if (!temTagsDeLogo(tags)) {
            const tagsDag = await buscarTagsDaDagEspecifica(dagId);
            tags = unirTags(tags, tagsDag);
        }

        return tags;
    };

    const aplicarNoCard = async (card, mapaLote) => {
        const dagId = obterDagId(card);
        if (!dagId) {
            return;
        }

        const estrutura = obterEstruturaGrupoEsquerdo(card);
        if (!estrutura) {
            return;
        }

        const tags = await resolverTagsDoCard(card, dagId, mapaLote);
        const assinatura = serializarTags(tags);

        const blocoExistente = estrutura.grupoEsquerdo.querySelector(`.${CLASSE_BLOCO}`);

        if (blocoExistente && blocoExistente.dataset.tagsAssinatura === assinatura) {
            return;
        }

        if (blocoExistente) {
            blocoExistente.remove();
        }

        const blocoNovo = montarBlocoLogos(tags);
        if (!blocoNovo) {
            return;
        }

        blocoNovo.dataset.tagsAssinatura = assinatura;

        estrutura.grupoEsquerdo.classList.add("grupo-esquerdo-com-logos");
        estrutura.listaTags.insertAdjacentElement("afterend", blocoNovo);
    };

    const aplicarEmTodosOsCards = async () => {
        const mapaLote = await construirMapaTagsLote();
        const cards = Array.from(document.querySelectorAll(SELETOR_CARDS));

        await Promise.all(cards.map((card) => aplicarNoCard(card, mapaLote)));
    };

    let timer = null;

    const agendarAplicacao = () => {
        if (timer) {
            clearTimeout(timer);
        }

        timer = setTimeout(() => {
            aplicarEmTodosOsCards().catch((erro) => {
                console.error("Erro ao aplicar logos:", erro);
            });
        }, 150);
    };

    agendarAplicacao();

    const observer = new MutationObserver((mutacoes) => {
        const precisaReaplicar = mutacoes.some((mutacao) => {
            if (mutacao.type !== "childList") {
                return false;
            }

            const adicionados = Array.from(mutacao.addedNodes || []);
            return adicionados.some((node) => {
                if (!(node instanceof HTMLElement)) {
                    return false;
                }

                if (node.classList?.contains(CLASSE_BLOCO)) {
                    return false;
                }

                return (
                    node.matches?.(SELETOR_CARDS) ||
                    node.querySelector?.("[data-testid='dag-id']") ||
                    node.querySelector?.("[data-testid='card-list']")
                );
            });
        });

        if (precisaReaplicar) {
            agendarAplicacao();
        }
    });

    observer.observe(document.body, {
        childList: true,
        subtree: true,
    });
})();