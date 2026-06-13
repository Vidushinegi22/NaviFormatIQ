import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useLocation } from "react-router-dom";
import { WizardLayout } from "@/components/wizard/WizardLayout";
import { NeedsSourcePanel } from "@/components/wizard/States";
import {
  DownloadIcon,
  FileTextIcon,
  MessageSquareIcon,
  SendIcon,
  SparklesIcon,
  ClipboardListIcon,
  RefreshIcon,
} from "@/components/icons/Icons";
import { useDocument } from "@/context/DocumentContext";
import { useWorkflow } from "@/context/WorkflowContext";
import { createChatSession, postChatMessage, getGuideline, ApiError } from "@/lib/api";
import { parseBlocks } from "@/lib/docBlocks";
import type { GuidelineDetail } from "@/types/api";

interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  steps?: Array<Record<string, unknown>>;
  error?: boolean;
}

const SUGGESTED_PROMPTS = [
  "What type of document is this, and what version?",
  "Summarize this document.",
  "What formatting does it use — fonts, margins, headings?",
  "What would change if I generate a new version?",
];

const COMPLIANCE_PROMPTS = [
  "Where does my document fall short of the guideline?",
  "What are my critical findings and why?",
  "How do I fix the sections with the lowest scores?",
  "Which required sections are missing?",
];

/** Friendly labels for the tool steps the agent reports. */
const TOOL_LABELS: Record<string, string> = {
  profile_document: "Profiled document",
  describe_formatting: "Read formatting",
  get_content_structure: "Read structure",
  get_styling_json: "Exported styling",
  summarize_document: "Summarized content",
  diff_documents: "Compared versions",
  apply_styling_to_content: "Applied styling",
  transfer_style_between_docs: "Transferred style",
  retrieve_domain_context: "Searched references",
  search_guideline: "Cited the guideline",
  get_document_section: "Quoted your document",
};

/** A directly-fetchable artifact uri from a step result (http(s) or /api), if any. */
function stepArtifactHref(step: Record<string, unknown>): string | null {
  const result = step.result as { artifact_uri?: unknown } | undefined;
  const uri = typeof result?.artifact_uri === "string" ? result.artifact_uri : null;
  if (!uri) return null;
  return /^https?:\/\//.test(uri) || uri.startsWith("/api") ? uri : null;
}

function stepLabel(step: Record<string, unknown>): string {
  const v = (step.tool ?? step.name ?? step.action ?? step.type) as
    | string
    | undefined;
  const base = (v && TOOL_LABELS[v]) || v || "step";
  // For guideline citations, surface the section numbers retrieved.
  if (v === "search_guideline") {
    const result = step.result as { passages?: { section?: string }[] } | undefined;
    const secs = (result?.passages ?? [])
      .map((p) => p.section)
      .filter(Boolean)
      .slice(0, 3);
    if (secs.length) return `${base} ${secs.join(", ")}`;
  }
  return base;
}

/* ── Markdown-lite rendering (bullets, numbers, **bold**, `code`) ─────────── */

function renderInline(text: string): ReactNode[] {
  const out: ReactNode[] = [];
  const re = /(\*\*[^*]+\*\*|`[^`]+`)/g;
  let last = 0;
  let m: RegExpExecArray | null;
  let i = 0;
  while ((m = re.exec(text)) !== null) {
    if (m.index > last) out.push(text.slice(last, m.index));
    const tok = m[0];
    if (tok.startsWith("**")) out.push(<strong key={i++}>{tok.slice(2, -2)}</strong>);
    else
      out.push(
        <code key={i++} className="rounded bg-ink-100 px-1 py-0.5 text-[12px]">
          {tok.slice(1, -1)}
        </code>
      );
    last = m.index + tok.length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

function MarkdownLite({ text }: { text: string }) {
  const blocks = useMemo(() => parseBlocks(text), [text]);
  return (
    <div className="space-y-1.5">
      {blocks.map((b, i) => {
        if (b.kind === "empty") return <div key={i} className="h-1" />;
        if (b.kind === "heading")
          return (
            <p key={i} className="text-[13.5px] font-bold">
              {renderInline(b.text)}
            </p>
          );
        if (b.kind === "list-item")
          return (
            <div key={i} className="flex gap-2 pl-1">
              <span className="select-none text-ink-400">{b.ordered ? b.marker : "•"}</span>
              <span>{renderInline(b.text)}</span>
            </div>
          );
        if (b.kind === "table-row")
          return (
            <div key={i} className="text-[12.5px] text-ink-600">
              {b.cells.join("  ·  ")}
            </div>
          );
        return <p key={i}>{renderInline(b.text)}</p>;
      })}
    </div>
  );
}

/**
 * Chat page.
 *
 * Creates a backend chat session bound to the uploaded document and routes
 * messages through the RAG tool-calling agent, rendering the real answers
 * and the tool steps it took.
 */
export function ChatPage() {
  const { project, source, runId, guidelineId, markComplete, setCurrentStep } = useDocument();
  const { selectedWorkflow } = useWorkflow();
  const location = useLocation();
  const isCompliance = selectedWorkflow === "compliance-check";
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [sessionError, setSessionError] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [guideline, setGuideline] = useState<GuidelineDetail | null>(null);
  const scrollerRef = useRef<HTMLDivElement | null>(null);
  const createdRef = useRef(false);
  const prefillRef = useRef(false);
  const idRef = useRef(0);
  const nextId = () => `m_${idRef.current++}`;

  // Track current step for resume-on-reopen
  useEffect(() => {
    setCurrentStep("chat");
  }, [setCurrentStep]);

  // Load the bound guideline (for the context panel + greeting).
  useEffect(() => {
    if (!guidelineId) return;
    getGuideline(guidelineId).then(setGuideline).catch(() => {});
  }, [guidelineId]);

  // Prefill the composer when arriving from a finding's "Ask about this".
  useEffect(() => {
    const prefill = (location.state as { prefill?: string } | null)?.prefill;
    if (prefill && !prefillRef.current) {
      prefillRef.current = true;
      setInput(prefill);
    }
  }, [location.state]);

  // Create the session once the document is known.
  useEffect(() => {
    if (!source || createdRef.current) return;
    createdRef.current = true;
    createChatSession({
      project_id: project?.id,
      subject_document_id: source.documentId,
      subject_run_id: runId ?? undefined,
      guideline_id: guidelineId ?? undefined,
      title: isCompliance ? `Compliance · ${source.filename}` : `Chat · ${source.filename}`,
    })
      .then((s) => {
        setSessionId(s.id);
        setMessages([
          {
            id: nextId(),
            role: "assistant",
            content: isCompliance
              ? `I can explain your compliance findings and how to fix them, grounded in the guideline and your document. Ask away.`
              : `Ask me anything about ${source.filename}. I'll use its content to answer.`,
          },
        ]);
      })
      .catch((e) => {
        createdRef.current = false;
        setSessionError(e instanceof ApiError ? e.message : (e as Error).message);
      });
  }, [source, project, runId, guidelineId, isCompliance]);

  useEffect(() => {
    scrollerRef.current?.scrollTo({
      top: scrollerRef.current.scrollHeight,
      behavior: "smooth",
    });
  }, [messages, sending]);

  const send = async (text: string) => {
    const msg = text.trim();
    if (!msg || !sessionId || sending) return;
    setInput("");
    setMessages((m) => [...m, { id: nextId(), role: "user", content: msg }]);
    setSending(true);
    try {
      const res = await postChatMessage(sessionId, {
        message: msg,
        subject_document_id: source?.documentId,
        subject_run_id: runId ?? undefined,
        guideline_id: guidelineId ?? undefined,
      });
      setMessages((m) => [
        ...m,
        {
          id: nextId(),
          role: "assistant",
          content: res.answer || "(no answer)",
          steps: res.steps?.length ? res.steps : undefined,
        },
      ]);
      markComplete("chat");
    } catch (e) {
      setMessages((m) => [
        ...m,
        {
          id: nextId(),
          role: "assistant",
          content:
            e instanceof ApiError
              ? `Sorry — ${e.message}`
              : `Sorry — ${(e as Error).message}`,
          error: true,
        },
      ]);
    } finally {
      setSending(false);
    }
  };

  if (!source) {
    return (
      <WizardLayout activeKey="chat" title="Chat with your document">
        <NeedsSourcePanel message="Upload a document to chat about it." />
      </WizardLayout>
    );
  }

  return (
    <WizardLayout
      activeKey="chat"
      title="Chat with your document"
      subtitle="Ask questions and refine your document with the assistant — answers are grounded in the file you uploaded."
    >
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-12">
        {/* Transcript + composer */}
        <div className="lg:col-span-8">
          <div className="nf-card flex h-[60vh] flex-col">
            <div ref={scrollerRef} className="flex-1 space-y-4 overflow-auto p-5">
              {sessionError && (
                <p className="rounded-lg bg-rose-50 px-3 py-2 text-sm text-rose-700">
                  Could not start chat: {sessionError}
                </p>
              )}
              {!sessionId && !sessionError && (
                <p className="text-center text-sm text-ink-400">Connecting…</p>
              )}
              {messages.map((m) => (
                <MessageBubble key={m.id} message={m} />
              ))}
              {sending && <ThinkingIndicator compliance={isCompliance} />}
            </div>

            <div className="border-t border-ink-100 p-4">
              <div className="mb-2 flex flex-wrap gap-1.5">
                {(isCompliance ? COMPLIANCE_PROMPTS : SUGGESTED_PROMPTS).map((p) => (
                  <button
                    key={p}
                    type="button"
                    disabled={!sessionId || sending}
                    onClick={() => send(p)}
                    className="rounded-full bg-ink-50 px-3 py-1 text-[12px] font-medium text-ink-600 ring-1 ring-inset ring-ink-100 transition-colors hover:bg-brand-50 hover:text-brand-700 disabled:opacity-50"
                  >
                    {p}
                  </button>
                ))}
              </div>
              <form
                onSubmit={(e) => {
                  e.preventDefault();
                  send(input);
                }}
                className="flex items-end gap-2"
              >
                <textarea
                  value={input}
                  onChange={(e) => setInput(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && !e.shiftKey) {
                      e.preventDefault();
                      send(input);
                    }
                  }}
                  rows={1}
                  placeholder="Ask about your document…"
                  disabled={!sessionId}
                  className="max-h-32 min-h-[2.75rem] flex-1 resize-none rounded-lg border border-ink-200 bg-white px-3 py-2.5 text-sm text-ink-800 placeholder:text-ink-400 focus:border-brand-400 focus:outline-none focus:ring-2 focus:ring-brand-100 disabled:bg-ink-50"
                />
                <button
                  type="submit"
                  disabled={!sessionId || sending || !input.trim()}
                  className="nf-btn-primary h-[2.75rem]"
                  aria-label="Send message"
                >
                  <SendIcon className="h-4 w-4" />
                </button>
              </form>
            </div>
          </div>
        </div>

        {/* Context panel */}
        <aside className="lg:col-span-4">
          <div className="nf-card p-5">
            <header className="mb-3 flex items-center gap-2">
              <MessageSquareIcon className="h-4 w-4 text-brand-500" />
              <h2 className="text-sm font-bold text-ink-800">Context</h2>
            </header>
            <div className="flex items-center gap-3 rounded-lg bg-ink-50/60 px-3 py-2.5">
              <span className="flex h-9 w-9 items-center justify-center rounded-lg bg-white text-brand-500 ring-1 ring-ink-100">
                <FileTextIcon className="h-4 w-4" />
              </span>
              <div className="min-w-0">
                <p className="truncate text-[13px] font-semibold text-ink-800">
                  {source.filename}
                </p>
                <p className="text-[11px] text-ink-500">{source.kind.toUpperCase()} · grounding source</p>
              </div>
            </div>
            {guideline && (
              <div className="mt-2 flex items-center gap-3 rounded-lg bg-ink-50/60 px-3 py-2.5">
                <span className="flex h-9 w-9 items-center justify-center rounded-lg bg-white text-brand-500 ring-1 ring-ink-100">
                  <ClipboardListIcon className="h-4 w-4" />
                </span>
                <div className="min-w-0">
                  <p className="truncate text-[13px] font-semibold text-ink-800">
                    {guideline.code} — {guideline.title}
                  </p>
                  <p className="text-[11px] text-ink-500">Audited against this guideline</p>
                </div>
              </div>
            )}
            <p className="mt-3 inline-flex items-start gap-1.5 text-[12px] text-ink-500">
              <SparklesIcon className="mt-0.5 h-3.5 w-3.5 shrink-0 text-brand-500" />
              {isCompliance
                ? "Answers are grounded in the guideline and your findings, and cite the sections used."
                : "The assistant reads your document to answer. Responses may cite the tools it used."}
            </p>
          </div>
        </aside>
      </div>
    </WizardLayout>
  );
}

/** Rotating status hints while the agent works through its tool steps. */
const THINKING_HINTS = [
  "Thinking…",
  "Reading the document…",
  "Checking the formatting…",
];
const COMPLIANCE_THINKING_HINTS = [
  "Thinking…",
  "Searching the guideline…",
  "Reading the document…",
  "Reviewing your findings…",
];

function ThinkingIndicator({ compliance }: { compliance: boolean }) {
  const hints = compliance ? COMPLIANCE_THINKING_HINTS : THINKING_HINTS;
  const [idx, setIdx] = useState(0);
  useEffect(() => {
    const t = setInterval(() => setIdx((i) => (i + 1) % hints.length), 2500);
    return () => clearInterval(t);
  }, [hints.length]);
  return (
    <div className="flex items-center gap-2 text-sm text-ink-400">
      <RefreshIcon className="h-4 w-4 animate-spin" />
      <span className="transition-opacity">{hints[idx]}</span>
    </div>
  );
}

function MessageBubble({ message }: { message: ChatMessage }) {
  const isUser = message.role === "user";
  return (
    <div className={`flex ${isUser ? "justify-end" : "justify-start"}`}>
      <div className={`max-w-[85%] ${isUser ? "items-end" : "items-start"}`}>
        <div
          className={`rounded-2xl px-4 py-2.5 text-[13.5px] leading-relaxed ${
            isUser
              ? "whitespace-pre-wrap bg-brand-500 text-white"
              : message.error
              ? "whitespace-pre-wrap bg-rose-50 text-rose-700"
              : "bg-ink-50 text-ink-800"
          }`}
        >
          {isUser || message.error ? message.content : <MarkdownLite text={message.content} />}
        </div>
        {message.steps && message.steps.length > 0 && (
          <div className="mt-1.5 flex flex-wrap gap-1.5">
            {message.steps.map((s, i) => {
              const href = stepArtifactHref(s);
              return href ? (
                <a
                  key={i}
                  href={href}
                  target="_blank"
                  rel="noreferrer"
                  className="inline-flex items-center gap-1 rounded-full bg-white px-2 py-0.5 text-[11px] font-medium text-brand-600 ring-1 ring-inset ring-brand-100 transition-colors hover:bg-brand-50"
                  title="Open the generated file"
                >
                  {stepLabel(s)}
                  <DownloadIcon className="h-3 w-3" />
                </a>
              ) : (
                <span
                  key={i}
                  className="rounded-full bg-white px-2 py-0.5 text-[11px] font-medium text-ink-500 ring-1 ring-inset ring-ink-100"
                >
                  {stepLabel(s)}
                </span>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}
