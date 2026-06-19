import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Frontend dev server. The app reads the backend URL from VITE_API_BASE
// (see .env), defaulting to http://localhost:8000.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
  },
});
