import { Link } from "react-router-dom";
import type { ReactNode } from "react";
import {
  RefreshIcon,
  AlertTriangleIcon,
  CloudUploadIcon,
} from "@/components/icons/Icons";

/** Centered status card used across wizard pages for empty/loading/error. */
export function StatePanel({
  icon,
  tone = "neutral",
  title,
  message,
  action,
}: {
  icon: ReactNode;
  tone?: "neutral" | "error" | "busy";
  title: string;
  message?: string;
  action?: ReactNode;
}) {
  const ring =
    tone === "error"
      ? "bg-rose-50 text-rose-600 ring-rose-100"
      : tone === "busy"
      ? "bg-brand-50 text-brand-500 ring-brand-100"
      : "bg-ink-100 text-ink-500 ring-ink-200";
  return (
    <div className="nf-card flex flex-col items-center justify-center gap-3 px-6 py-14 text-center">
      <span
        className={`flex h-14 w-14 items-center justify-center rounded-2xl ring-1 ring-inset ${ring}`}
      >
        {icon}
      </span>
      <h2 className="text-lg font-bold tracking-tight text-ink-800">{title}</h2>
      {message && <p className="max-w-md text-sm text-ink-500">{message}</p>}
      {action && <div className="mt-2">{action}</div>}
    </div>
  );
}

/** Shown when a step needs a document but none has been uploaded. */
export function NeedsSourcePanel({ message }: { message?: string }) {
  return (
    <StatePanel
      icon={<CloudUploadIcon className="h-7 w-7" />}
      title="No document yet"
      message={message ?? "Upload a document to continue this workflow."}
      action={
        <Link to="/upload" className="nf-btn-primary">
          <CloudUploadIcon className="h-4 w-4" />
          Go to Upload
        </Link>
      }
    />
  );
}

/** Generic busy/loading panel. */
export function LoadingPanel({ title, message }: { title: string; message?: string }) {
  return (
    <StatePanel
      tone="busy"
      icon={<RefreshIcon className="h-7 w-7 animate-spin" />}
      title={title}
      message={message}
    />
  );
}

/** Compact amber list for run warnings (degraded modes, advisories). */
export function WarningsList({ warnings }: { warnings?: string[] | null }) {
  const items = (warnings ?? []).filter(Boolean);
  if (items.length === 0) return null;
  return (
    <ul className="mt-3 space-y-1.5" data-testid="run-warnings">
      {items.map((w, i) => (
        <li
          key={i}
          className="rounded-lg bg-amber-50 px-3 py-2 text-[12px] leading-snug text-amber-800"
        >
          {w}
        </li>
      ))}
    </ul>
  );
}

/** Generic error panel with an optional retry. */
export function ErrorPanel({
  title = "Something went wrong",
  message,
  onRetry,
}: {
  title?: string;
  message?: string;
  onRetry?: () => void;
}) {
  return (
    <StatePanel
      tone="error"
      icon={<AlertTriangleIcon className="h-7 w-7" />}
      title={title}
      message={message}
      action={
        onRetry && (
          <button type="button" onClick={onRetry} className="nf-btn-primary">
            <RefreshIcon className="h-4 w-4" />
            Try again
          </button>
        )
      }
    />
  );
}
