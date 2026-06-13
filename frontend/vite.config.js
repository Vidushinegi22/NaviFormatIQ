import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "node:path";
// Vite configuration for the Navi FormatiQ frontend.
// - Uses the React plugin with the new JSX transform
// - Aliases "@/" to "src/" for clean imports
// - Default dev server on port 5173, proxies /api to FastAPI backend on 8000
export default defineConfig({
    plugins: [react()],
    resolve: {
        alias: {
            "@": path.resolve(__dirname, "src"),
        },
    },
    server: {
        port: 5173,
        proxy: {
            // Backend routes are mounted under /api/v1/* — forward the path
            // through unchanged (no rewrite) so it reaches FastAPI as-is.
            "/api": {
                target: "http://localhost:8000",
                changeOrigin: true,
            },
        },
    },
    build: {
        outDir: "dist",
        sourcemap: true,
    },
});
