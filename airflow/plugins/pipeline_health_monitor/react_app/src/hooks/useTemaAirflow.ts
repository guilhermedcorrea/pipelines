import { useEffect, useMemo, useState } from "react";

export type ModoTema = "light" | "dark";

interface ResultadoTemaAirflow {
  tema: ModoTema;
  ehEscuro: boolean;
  ehClaro: boolean;
  classeTema: string;
}

/**
 * Tenta descobrir o tema atual da interface.
 *
 * Ordem de prioridade:
 * 1) atributo data-theme no html
 * 2) classe "dark" no html/body
 * 3) preferência do sistema operacional
 * 4) fallback = dark
 */
function detectarTemaInicial(): ModoTema {
  if (typeof window === "undefined" || typeof document === "undefined") {
    return "dark";
  }

  const html = document.documentElement;
  const body = document.body;

  const dataThemeHtml = html.getAttribute("data-theme");
  const dataThemeBody = body?.getAttribute("data-theme");

  if (dataThemeHtml === "dark" || dataThemeBody === "dark") {
    return "dark";
  }

  if (dataThemeHtml === "light" || dataThemeBody === "light") {
    return "light";
  }

  if (html.classList.contains("dark") || body?.classList.contains("dark")) {
    return "dark";
  }

  if (html.classList.contains("light") || body?.classList.contains("light")) {
    return "light";
  }

  if (window.matchMedia?.("(prefers-color-scheme: dark)").matches) {
    return "dark";
  }

  return "light";
}

function lerTemaAtual(): ModoTema {
  return detectarTemaInicial();
}

export default function useTemaAirflow(): ResultadoTemaAirflow {
  const [tema, setTema] = useState<ModoTema>(() => detectarTemaInicial());

  useEffect(() => {
    if (typeof window === "undefined" || typeof document === "undefined") {
      return;
    }

    const html = document.documentElement;
    const body = document.body;

    const atualizarTema = (): void => {
      setTema(lerTemaAtual());
    };

    /**
     * Observa mudanças de atributos/classe no HTML/BODY
     * para reagir quando o tema da aplicação mudar.
     */
    const observerHtml = new MutationObserver(() => {
      atualizarTema();
    });

    const observerBody = new MutationObserver(() => {
      atualizarTema();
    });

    observerHtml.observe(html, {
      attributes: true,
      attributeFilter: ["class", "data-theme"],
    });

    if (body) {
      observerBody.observe(body, {
        attributes: true,
        attributeFilter: ["class", "data-theme"],
      });
    }

    /**
     * Observa mudança do tema do sistema operacional.
     */
    const mediaQuery = window.matchMedia?.("(prefers-color-scheme: dark)");

    const aoMudarTemaSistema = (): void => {
      atualizarTema();
    };

    if (mediaQuery) {
      if (typeof mediaQuery.addEventListener === "function") {
        mediaQuery.addEventListener("change", aoMudarTemaSistema);
      } else if (typeof mediaQuery.addListener === "function") {
        mediaQuery.addListener(aoMudarTemaSistema);
      }
    }

    /**
     * Atualiza no mount para garantir consistência.
     */
    atualizarTema();

    return () => {
      observerHtml.disconnect();
      observerBody.disconnect();

      if (mediaQuery) {
        if (typeof mediaQuery.removeEventListener === "function") {
          mediaQuery.removeEventListener("change", aoMudarTemaSistema);
        } else if (typeof mediaQuery.removeListener === "function") {
          mediaQuery.removeListener(aoMudarTemaSistema);
        }
      }
    };
  }, []);

  const resultado = useMemo<ResultadoTemaAirflow>(() => {
    const ehEscuro = tema === "dark";
    const ehClaro = tema === "light";

    return {
      tema,
      ehEscuro,
      ehClaro,
      classeTema: ehEscuro ? "phm-tema-dark" : "phm-tema-light",
    };
  }, [tema]);

  return resultado;
}