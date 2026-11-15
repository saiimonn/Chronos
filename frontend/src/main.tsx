import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import App from "./App";
import "./styles/index.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
    {import.meta.env.VITE_ENVIRONMENT === "test" && (
      <div
        role="status"
        className="pointer-events-none fixed bottom-3 left-1/2 z-50 -translate-x-1/2 rounded-full bg-error px-4 py-1 text-xs font-semibold text-on-error shadow"
      >
        Test environment — changes here never reach the official system
      </div>
    )}
  </StrictMode>,
);
