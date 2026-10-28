import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import basicSsl from "@vitejs/plugin-basic-ssl";

// The browser talks only to Vite, which forwards /api (including the web-call
// WebSocket) to the backend. That is what makes the app work from a phone: the
// phone can't reach "localhost:8000" (that's the phone itself), but it can
// reach this one address. xfwd lets the backend see who is really calling.
const backend = { target: "http://127.0.0.1:8000", ws: true, xfwd: true };
const proxy = { "/api": backend, "/health": backend };

// `npm run phone` (= vite --mode phone): listen on the Wi-Fi and serve HTTPS —
// phones only allow the microphone on a secure page. The certificate is
// self-signed, so the phone shows a warning once; accept it.
export default defineConfig(({ mode }) => {
  const phone = mode === "phone";
  return {
    plugins: [react(), tailwindcss(), ...(phone ? [basicSsl()] : [])],
    server: { port: 5174, host: phone, proxy },
    preview: { proxy },
  };
});
