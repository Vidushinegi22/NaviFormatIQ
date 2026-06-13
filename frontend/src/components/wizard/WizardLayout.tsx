import { useMemo, type ReactNode } from "react";
import { Link, useNavigate } from "react-router-dom";
import { TopNavBar } from "@/components/layout/TopNavBar";
import { StepRail } from "./StepRail";
import { ArrowRightIcon } from "@/components/icons/Icons";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { WORKFLOW_DEFINITIONS } from "@/config/wizardSteps";
import { useWorkflow } from "@/context/WorkflowContext";
import { useDocument } from "@/context/DocumentContext";
import { pathForStep } from "@/lib/stepRouting";
import type { WizardStepKey } from "@/types/workflow";

interface WizardLayoutProps {
  /** Which wizard step the current page represents. */
  activeKey: WizardStepKey;
  /** Page title shown under the rail. */
  title: string;
  /** Short helper subtitle. */
  subtitle?: string;
  /** Action buttons (e.g. secondary export). Rendered top-right. */
  headerActions?: ReactNode;
  /** Primary page content. */
  children: ReactNode;
  /** Override the default footer Continue button. */
  primaryAction?: {
    label: string;
    onClick: () => void;
    disabled?: boolean;
  };
  /** Hide the footer entirely. */
  hideFooter?: boolean;
}

/**
 * Standard chrome for every wizard page (GSK QA Assist style).
 *
 * Layout:
 *   1. TopNavBar with workflow name chip (sticky top)
 *   2. Chevron step rail
 *   3. Page header (title + subtitle + actions)
 *   4. <main> body slot
 *   5. Fixed footer with Back / Continue (sticky bottom)
 *
 * The component reads the selected workflow from `WorkflowContext` and
 * the completion map from `DocumentContext` so the rail and footer can
 * stay in sync without each page wiring them up.
 */
export function WizardLayout({
  activeKey,
  title,
  subtitle,
  headerActions,
  children,
  primaryAction,
  hideFooter,
}: WizardLayoutProps) {
  const navigate = useNavigate();
  const { selectedWorkflow, workflowName } = useWorkflow();
  const { completion, project } = useDocument();

  // Resolve the steps for the selected workflow; fall back to a sane
  // default so the page never crashes if the user deep-links into a
  // wizard page without first picking a workflow.
  const def = selectedWorkflow
    ? WORKFLOW_DEFINITIONS[selectedWorkflow]
    : WORKFLOW_DEFINITIONS["second-version"];

  const completedKeys = useMemo(() => {
    const s = new Set<WizardStepKey>();
    if (completion.upload) s.add("upload");
    if (completion.extract) s.add("extract");
    if (completion.review) s.add("review");
    if (completion.compliance) s.add("compliance");
    if (completion.style) s.add("style");
    if (completion.generate) s.add("generate");
    if (completion.chat) s.add("chat");
    if (completion.export) s.add("export");
    return s;
  }, [completion]);

  const activeIndex = def.steps.findIndex((s) => s.key === activeKey);
  const prevStep = activeIndex > 0 ? def.steps[activeIndex - 1] : null;
  const nextStep =
    activeIndex >= 0 && activeIndex < def.steps.length - 1
      ? def.steps[activeIndex + 1]
      : null;

  const goNext = () => {
    if (primaryAction) {
      primaryAction.onClick();
      return;
    }
    if (nextStep) navigate(pathForStep(nextStep.key));
  };

  // Generate a campaign ID chip for the nav bar
  const campaignId = project
    ? `CAMP-${new Date().getFullYear()}-${project.id.slice(0, 3).toUpperCase()}`
    : undefined;

  return (
    <div className="flex min-h-screen flex-col bg-ink-50">
      <TopNavBar
        title={workflowName ?? def.title}
        campaignId={campaignId}
      />

      {/* Chevron step rail */}
      <div className="mx-auto w-full max-w-[1280px] px-4 pt-3 sm:px-6 lg:px-8">
        <StepRail
          steps={def.steps}
          activeKey={activeKey}
          completedKeys={completedKeys}
        />
      </div>

      <main className="mx-auto w-full max-w-[1280px] flex-1 px-4 pb-24 pt-4 sm:px-6 lg:px-8">
        {/* Page header */}
        <header className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
          <div className="min-w-0">
            <h1 className="text-2xl font-extrabold tracking-tight text-ink-800 sm:text-[28px]">
              {title}
            </h1>
            {subtitle && (
              <p className="mt-1 max-w-2xl text-sm text-ink-500">
                {subtitle}
              </p>
            )}
          </div>
          {headerActions && (
            <div className="flex shrink-0 items-center gap-2">
              {headerActions}
            </div>
          )}
        </header>

        {/* Body */}
        <section className="mt-6 animate-fade-up">{children}</section>
      </main>

      {/* Fixed footer */}
      {!hideFooter && (
        <footer className="nf-fixed-footer">
          <Button asChild variant="ghost">
            <Link
              to={prevStep ? pathForStep(prevStep.key) : "/"}
              aria-label={prevStep ? `Back to ${prevStep.label}` : "Back to dashboard"}
            >
              <ArrowRightIcon className="h-4 w-4 rotate-180" />
              Back
            </Link>
          </Button>
          <div className="flex items-center justify-end gap-2">
            {nextStep || primaryAction ? (
              <Button onClick={goNext} disabled={primaryAction?.disabled}>
                {primaryAction?.label ?? "Next"}
                <ArrowRightIcon className="h-4 w-4" />
              </Button>
            ) : (
              <Badge variant="success" className="px-3 py-1.5 text-[13px]">
                Workflow complete
              </Badge>
            )}
          </div>
        </footer>
      )}
    </div>
  );
}
