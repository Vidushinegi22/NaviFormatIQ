import React from "react";
import ReactDOM from "react-dom/client";
import { RouterProvider } from "react-router-dom";
import { router } from "./router";
import { WorkflowProvider } from "./context/WorkflowContext";
import { DocumentProvider } from "./context/DocumentContext";
import { TooltipProvider } from "./components/ui/tooltip";
import "./index.css";

/**
 * Application bootstrap.
 *
 * Providers are nested top-down:
 *   - WorkflowProvider: which workflow the user picked.
 *   - DocumentProvider: the uploaded file + per-step completion flags.
 *   - RouterProvider:   the actual route tree.
 */
const rootEl = document.getElementById("root");
if (!rootEl) {
  throw new Error("Root element #root not found in index.html");
}

ReactDOM.createRoot(rootEl).render(
  <React.StrictMode>
    <WorkflowProvider>
      <DocumentProvider>
        <TooltipProvider delayDuration={200}>
          <RouterProvider router={router} />
        </TooltipProvider>
      </DocumentProvider>
    </WorkflowProvider>
  </React.StrictMode>
);
