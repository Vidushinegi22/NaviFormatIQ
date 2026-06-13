import { createBrowserRouter, Navigate } from "react-router-dom";
import { WorkflowSelectionDashboard } from "@/pages/WorkflowSelectionDashboard";
import { UploadPage } from "@/pages/UploadPage";
import { ExtractPage } from "@/pages/ExtractPage";
import { ReviewPage } from "@/pages/ReviewPage";
import { CompliancePage } from "@/pages/CompliancePage";
import { StylePage } from "@/pages/StylePage";
import { ChatPage } from "@/pages/ChatPage";
import { ExportPage } from "@/pages/ExportPage";
import { GeneratePage } from "@/pages/GeneratePage";

/**
 * Application router.
 *
 * Phase 1: dashboard at "/".
 * Phase 2: full wizard pipeline. Each step has its own route, the
 * wizard rail handles back-nav, and unknown paths fall back to "/".
 */
export const router = createBrowserRouter([
  { path: "/", element: <WorkflowSelectionDashboard /> },
  { path: "/upload", element: <UploadPage /> },
  { path: "/extract", element: <ExtractPage /> },
  { path: "/review", element: <ReviewPage /> },
  { path: "/compliance", element: <CompliancePage /> },
  { path: "/style", element: <StylePage /> },
  { path: "/chat", element: <ChatPage /> },
  { path: "/export", element: <ExportPage /> },
  { path: "/generate", element: <GeneratePage /> },
  { path: "*", element: <Navigate to="/" replace /> },
]);
