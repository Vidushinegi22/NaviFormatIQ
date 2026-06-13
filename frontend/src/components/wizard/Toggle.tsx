/** A labelled switch (lifted from StylePage so Compliance can reuse it). */
export function Toggle({
  label,
  hint,
  checked,
  onChange,
}: {
  label: string;
  hint?: string;
  checked: boolean;
  onChange: (v: boolean) => void;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      onClick={() => onChange(!checked)}
      className="flex w-full items-start gap-3 rounded-lg bg-ink-50/60 px-3 py-2.5 text-left transition-colors hover:bg-ink-50"
    >
      <span
        className={`mt-0.5 flex h-5 w-9 shrink-0 items-center rounded-full px-0.5 transition-colors ${
          checked ? "bg-brand-500" : "bg-ink-300"
        }`}
      >
        <span
          className={`h-4 w-4 rounded-full bg-white transition-transform ${
            checked ? "translate-x-4" : "translate-x-0"
          }`}
        />
      </span>
      <span className="min-w-0">
        <span className="block text-[13px] font-semibold text-ink-800">{label}</span>
        {hint && <span className="block text-[12px] text-ink-500">{hint}</span>}
      </span>
    </button>
  );
}
