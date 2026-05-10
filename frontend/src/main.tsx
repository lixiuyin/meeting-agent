import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { message } from "antd";
import App from "./App";
import { I18nProvider } from "./i18n/I18nProvider";
import { initMonitoring, reportWebVitals } from "./utils/monitoring";
import "./styles/index.css";

// Limit toast/notification stacking to prevent UI clutter
message.config({ maxCount: 3 });

// Initialize error monitoring (Sentry) and Web Vitals reporting
initMonitoring();
reportWebVitals();

const rootElement = document.getElementById("root");
if (!rootElement) {
  throw new Error("Root element #root not found in the DOM");
}

ReactDOM.createRoot(rootElement).render(
  <React.StrictMode>
    <I18nProvider>
      <BrowserRouter>
        <App />
      </BrowserRouter>
    </I18nProvider>
  </React.StrictMode>,
);
