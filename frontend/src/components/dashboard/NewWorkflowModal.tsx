import { useEffect, useState } from "react";
import {
  DocumentDuplicateIcon,
  PaintBrushIcon,
  ClipboardListIcon,
  ArrowRightIcon,
} from "@/components/icons/Icons";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { WORKFLOW_DEFINITIONS, WORKFLOW_ORDER } from "@/config/wizardSteps";
import type { WorkflowType } from "@/types/workflow";

interface NewWorkflowModalProps {
  open: boolean;
  onClose: () => void;
  onStart: (name: string, type: WorkflowType) => void;
}

const WORKFLOW_ICONS: Record<WorkflowType, typeof DocumentDuplicateIcon> = {
  "second-version": DocumentDuplicateIcon,
  "style-update": PaintBrushIcon,
  "compliance-check": ClipboardListIcon,
};

/**
 * Modal dialog for starting a new workflow.
 *
 * Prompts the user for a workflow name and lets them pick the workflow
 * type before creating the backend project and navigating to upload.
 * Built on the shadcn `Dialog` (Radix) — focus trap, scroll lock and
 * Escape-to-close are handled by the primitive.
 */
export function NewWorkflowModal({ open, onClose, onStart }: NewWorkflowModalProps) {
  const [name, setName] = useState("");
  const [selectedType, setSelectedType] = useState<WorkflowType>("second-version");

  // Reset fields whenever the dialog is (re)opened.
  useEffect(() => {
    if (open) {
      setName("");
      setSelectedType("second-version");
    }
  }, [open]);

  const canSubmit = name.trim().length > 0;

  const handleSubmit = () => {
    if (canSubmit) onStart(name.trim(), selectedType);
  };

  return (
    <Dialog open={open} onOpenChange={(o) => !o && onClose()}>
      <DialogContent
        className="max-w-lg"
        onOpenAutoFocus={(e) => {
          // Focus the name field rather than the close button.
          e.preventDefault();
          const el = e.currentTarget as HTMLElement;
          el.querySelector<HTMLInputElement>("#workflow-name")?.focus();
        }}
      >
        <DialogHeader>
          <DialogTitle>Start New Project</DialogTitle>
        </DialogHeader>

        {/* Body */}
        <div className="space-y-5 px-6 py-5">
          {/* Workflow name */}
          <div>
            <label
              htmlFor="workflow-name"
              className="mb-1.5 block text-sm font-semibold text-ink-700"
            >
              Project Name
            </label>
            <Input
              id="workflow-name"
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && canSubmit) handleSubmit();
              }}
              placeholder="e.g. Email Demo, Q3 Compliance Review"
            />
          </div>

          {/* Workflow type */}
          <div>
            <label className="mb-2 block text-sm font-semibold text-ink-700">
              Select Workflow Type
            </label>
            <div className="space-y-2">
              {WORKFLOW_ORDER.map((type) => {
                const def = WORKFLOW_DEFINITIONS[type];
                const Icon = WORKFLOW_ICONS[type];
                const isSelected = selectedType === type;
                return (
                  <button
                    key={type}
                    type="button"
                    onClick={() => setSelectedType(type)}
                    className={`flex w-full items-center gap-3.5 rounded-xl border px-4 py-3 text-left transition-all duration-200 ${
                      isSelected
                        ? "border-brand-500 bg-brand-50/60 shadow-sm ring-1 ring-inset ring-brand-200"
                        : "border-ink-200 bg-white hover:border-ink-300 hover:bg-ink-50/50"
                    }`}
                    aria-pressed={isSelected}
                  >
                    <div
                      className={`flex h-10 w-10 shrink-0 items-center justify-center rounded-lg transition-colors ${
                        isSelected
                          ? "bg-brand-500 text-white"
                          : "bg-ink-100 text-ink-500"
                      }`}
                    >
                      <Icon className="h-5 w-5" />
                    </div>
                    <div className="min-w-0 flex-1">
                      <p className={`text-sm font-semibold ${isSelected ? "text-brand-700" : "text-ink-700"}`}>
                        {def.title}
                      </p>
                      <p className="truncate text-xs text-ink-500">
                        {def.category}
                      </p>
                    </div>
                    {/* Selection indicator — a filled dot, not a tick. */}
                    <span
                      aria-hidden
                      className={`flex h-4 w-4 shrink-0 items-center justify-center rounded-full transition-colors ${
                        isSelected ? "bg-brand-500" : "ring-1 ring-inset ring-ink-300"
                      }`}
                    >
                      {isSelected && <span className="h-1.5 w-1.5 rounded-full bg-white" />}
                    </span>
                  </button>
                );
              })}
            </div>
          </div>
        </div>

        <DialogFooter>
          <Button type="button" variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button type="button" disabled={!canSubmit} onClick={handleSubmit}>
            Create Project
            <ArrowRightIcon className="h-4 w-4" />
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
