import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// In development, `npm run dev` serves the UI on :5173 and forwards /api calls to FastAPI on :8000.
// In Docker, FastAPI serves the built files itself, so no proxy is needed.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: { "/api": "http://localhost:8000" },
  },
});
