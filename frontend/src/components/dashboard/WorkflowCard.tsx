import type { ComponentType, SVGProps } from "react";
import { ArrowRightIcon } from "@/components/icons/Icons";
import type { WorkflowDefinition } from "@/types/workflow";

interface WorkflowCardProps {
  workflow: WorkflowDefinition;
  /** Icon component to render inside the colored tile. */
  Icon: ComponentType<SVGProps<SVGSVGElement>>;
  /** Click handler — receives the workflow type so the caller can navigate. */
  onSelect: (workflow: WorkflowDefinition) => void;
  /** Optional index — used to stagger entry animation. */
  index?: number;
}

/**
 * Workflow selection card.
 *
 * Design references: Microsoft 365 task launcher, Adobe Creative Cloud
 * tile, Notion template gallery. The card uses a 1px border + soft
 * shadow at rest and lifts to a richer brand-tinted shadow on hover. The
 * icon tile slightly scales and brightens to give clear affordance.
 *
 * Accessibility:
 *   - Each card is a single button so keyboard users can activate it
 *     with Enter / Space anywhere on the surface.
 *   - The inner "Select Workflow" pill is decorative (aria-hidden) to
 *     avoid double-announcing the action.
 */
export function WorkflowCard({
  workflow,
  Icon,
  onSelect,
  index = 0,
}: WorkflowCardProps) {
  // Hard-cap stagger so we never delay beyond a perceptible threshold.
  const delayMs = Math.min(index, 4) * 60;

  return (
    <button
      type="button"
      onClick={() => onSelect(workflow)}
      style={{ animationDelay: `${delayMs}ms` }}
      className="group relative flex h-full flex-col items-start gap-5 rounded-2xl border border-ink-100 bg-white p-6 text-left shadow-card transition-all duration-300 ease-out animate-fade-up hover:-translate-y-1 hover:border-brand-200 hover:shadow-card-hover focus-visible:ring-2 focus-visible:ring-brand-500 focus-visible:ring-offset-2 sm:p-7"
      aria-label={`${workflow.title}. ${workflow.description}`}
    >
      {/* Decorative teal accent bar that reveals on hover. */}
      <span
        aria-hidden="true"
        className="absolute inset-x-6 top-0 h-0.5 origin-left scale-x-0 rounded-full bg-gradient-to-r from-brand-500 to-brand-300 transition-transform duration-300 group-hover:scale-x-100"
      />

      {/* Icon tile */}
      <div className="relative">
        <div
          aria-hidden="true"
          className="flex h-14 w-14 items-center justify-center rounded-xl bg-brand-50 text-brand-500 ring-1 ring-inset ring-brand-100 transition-all duration-300 group-hover:bg-brand-500 group-hover:text-white group-hover:ring-brand-500 group-hover:scale-[1.04]"
        >
          <Icon className="h-7 w-7" />
        </div>
      </div>

      {/* Body */}
      <div className="flex flex-1 flex-col gap-2">
        <span className="text-[11px] font-semibold uppercase tracking-[0.14em] text-brand-600/80">
          {workflow.category}
        </span>
        <h3 className="text-lg font-bold tracking-tight text-ink-800">
          {workflow.title}
        </h3>
        <p className="text-sm leading-relaxed text-ink-500">
          {workflow.description}
        </p>
      </div>

      {/* Footer action */}
      <div
        aria-hidden="true"
        className="mt-2 inline-flex w-full items-center justify-between rounded-lg border border-ink-100 bg-ink-50/60 px-3 py-2 text-sm font-semibold text-ink-700 transition-all duration-200 group-hover:border-brand-200 group-hover:bg-brand-500 group-hover:text-white"
      >
        <span>Select Workflow</span>
        <ArrowRightIcon className="h-4 w-4 transition-transform duration-300 group-hover:translate-x-0.5" />
      </div>
    </button>
  );
}
