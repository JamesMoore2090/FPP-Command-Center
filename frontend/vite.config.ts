import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// FPPCC_API lets a dev server target a backend other than the production one on :8000
const proxy = { "/api": { target: process.env.FPPCC_API ?? "http://127.0.0.1:8000", ws: true } };

export default defineConfig({
  plugins: [react()],
  server: { host: true, proxy },
  preview: { proxy },
});
