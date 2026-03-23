import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";

/**
 * Estilos globais.
 *
 * Esses imports assumem que estes arquivos existem:
 * - src/styles/tokens.css
 * - src/styles/layout.css
 * - src/styles/components.css
 *
 * Se ainda não existirem, você pode:
 * 1) criar os arquivos depois
 * 2) comentar temporariamente essas 3 linhas
 */
import "./styles/tokens.css";
import "./styles/layout.css";
import "./styles/components.css";

/**
 * Este é o ponto de entrada do React.
 * Ele monta o App dentro da div#root do index.html.
 */
ReactDOM.createRoot(document.getElementById("root") as HTMLElement).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);