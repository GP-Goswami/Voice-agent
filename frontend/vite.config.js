import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Frontend dev server. The app reads the backend URL from VITE_API_BASE
// (see .env), defaulting to http://localhost:8000.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    // In dev the app calls relative /api/... — proxy those to the backend so
    // the same code works in dev and in production (same-origin).
    proxy: {
      "/api": "http://localhost:8000",
    },
  },
});
