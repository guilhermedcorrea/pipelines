import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "node:path";

/**
 * Nome do arquivo:
 * vite.config.ts
 *
 * O que ele faz:
 * - Configura o Vite para buildar o React app
 * - Gera a saída em react_app/dist
 * - Usa caminhos relativos no build para funcionar dentro do Airflow
 * - Define nomes estáveis para os arquivos finais
 *
 * Por que isso importa:
 * O plugin.py procura o bundle dentro de react_app/dist.
 * Então o build precisa gerar os arquivos exatamente ali.
 */

export default defineConfig({
  plugins: [react()],

  /**
   * base "./" é importante porque o app será servido dentro do Airflow
   * em uma rota do plugin, e não na raiz do domínio.
   *
   * Se você usar "/", os caminhos dos assets podem quebrar.
   */
  base: "./",

  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },

  server: {
    host: "0.0.0.0",
    port: 5173,
    strictPort: true,
  },

  preview: {
    host: "0.0.0.0",
    port: 4173,
    strictPort: true,
  },

  build: {
    outDir: "dist",
    emptyOutDir: true,
    sourcemap: false,
    target: "es2020",

    /**
     * Aqui eu deixo os nomes dos arquivos mais previsíveis.
     * Isso ajuda o plugin.py a localizar o bundle com menos chance de erro.
     */
    rollupOptions: {
      output: {
        entryFileNames: "main.js",
        chunkFileNames: "chunks/[name].js",
        assetFileNames: "assets/[name].[ext]",
      },
    },
  },
});