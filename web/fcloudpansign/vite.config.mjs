import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";
import federation from "@originjs/vite-plugin-federation";
export default defineConfig({
  base: "",
  define: {
    __FCLOUDPAN_ICON__: JSON.stringify(
      "data:image/png;base64," +
        readFileSync(
          new URL("../../icons/fcloudpansign.png", import.meta.url),
        ).toString("base64"),
    ),
  },
  plugins: [
    vue(),
    federation({
      name: "FCloudpanSign",
      filename: "remoteEntry.js",
      exposes: { "./Page": "./Page.vue", "./Config": "./Config.vue" },
      shared: {
        vue: { import: false, generate: false, requiredVersion: "^3.5.0" },
        vuetify: { import: false, generate: false, requiredVersion: "^3.7.0" },
      },
    }),
  ],
  build: {
    assetsDir: "",
    rollupOptions: { input: "./entry.js" },
    target: "esnext",
    outDir: fileURLToPath(
      new URL("../../plugins/fcloudpansign/dist/assets", import.meta.url),
    ),
    emptyOutDir: true,
    minify: "esbuild",
    assetsInlineLimit: Infinity,
  },
});
