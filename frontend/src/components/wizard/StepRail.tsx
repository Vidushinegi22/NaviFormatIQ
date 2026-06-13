import { Link } from "react-router-dom";
import { pathForStep } from "@/lib/stepRouting";
import type { WizardStep, WizardStepKey } from "@/types/workflow";

interface StepRailProps {
  steps: WizardStep[];
  activeKey: WizardStepKey;
  completedKeys: Set<WizardStepKey>;
}

/**
 * Chevron/arrow-shaped horizontal step rail (QA Assist style).
 *
 * Every step — including the first — has the inward arrow cut on the left
 * and the outward arrow point on the right. Steps overlap via negative
 * margin so the right arrow of each step slides over the next step's left
 * indent.
 *
 * The currently active step is highlighted with a brighter background,
 * slight scale, and a glow effect so it clearly stands out.
 */
export function StepRail({ steps, activeKey, completedKeys }: StepRailProps) {
  const activeIndex = steps.findIndex((s) => s.key === activeKey);
  const total = steps.length;

  // Same clip-path for ALL steps — including the first.
  // Left: inward arrow cut at 20px; Right: outward arrow point at 20px.
  const CLIP =
    "polygon(0 0, calc(100% - 20px) 0, 100% 50%, calc(100% - 20px) 100%, 0 100%, 20px 50%)";

  return (
    <nav
      aria-label="Wizard progress"
      className="overflow-x-auto py-3"
    >
      <div className="flex items-center justify-center">
        {steps.map((step, idx) => {
          const isActive = step.key === activeKey;
          const isComplete = completedKeys.has(step.key) && !isActive;
          const isPast = idx < activeIndex;
          const navigable = isComplete || isPast;
          const isFirst = idx === 0;
          const isFilled = isActive || isComplete || isPast;

          // Z-index: active step gets the highest z-index so the glow is visible;
          // otherwise highest on left so right arrows overlap correctly.
          const zIndex = isActive ? total + 1 : total - idx;

          // Colors — active step uses the brand action blue; completed/past
          // steps use the lighter navy contrast; upcoming stays light.
          const bg = isActive
            ? "#051D60"
            : isFilled
            ? "#182954"
            : "#EEF2F4";
          const color = isFilled ? "#FFFFFF" : "#5A6976";
          const fontWeight = isActive ? 700 : 600;

          // Crisp outline that traces the clip-path. CSS borders can't follow a
          // clip-path, so we stack four thin directional drop-shadows to draw a
          // ~1px outline around the arrow, then layer depth/glow on top.
          const outlineColor = isFilled ? "#0D131F" : "#B7C2C8";
          const outline = [
            `drop-shadow(0.75px 0 0 ${outlineColor})`,
            `drop-shadow(-0.75px 0 0 ${outlineColor})`,
            `drop-shadow(0 0.75px 0 ${outlineColor})`,
            `drop-shadow(0 -0.75px 0 ${outlineColor})`,
          ].join(" ");
          const dropShadow = isActive
            ? `${outline} drop-shadow(0 0 7px rgba(5,29,96,0.45)) drop-shadow(0 2px 5px rgba(0,0,0,0.16))`
            : `${outline} drop-shadow(0 1px 2px rgba(0,0,0,0.07))`;

          // Label only — no tick icon
          const content = (
            <span className="flex items-center gap-1.5">
              <span>{step.label}</span>
            </span>
          );

          const sharedStyle: React.CSSProperties = {
            clipPath: CLIP,
            zIndex,
            padding: "14px 44px",
            marginLeft: isFirst ? 0 : "-12px",
            minWidth: "150px",
            backgroundColor: bg,
            color,
            fontWeight,
            fontSize: isActive ? "15px" : "14px",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            position: "relative",
            whiteSpace: "nowrap",
            userSelect: "none",
            transition: "all 0.25s ease",
            letterSpacing: isActive ? "0.03em" : "0.01em",
            filter: dropShadow,
            transform: isActive ? "scale(1.04)" : "scale(1)",
          };

          if (navigable) {
            return (
              <Link
                key={step.key}
                to={pathForStep(step.key)}
                style={sharedStyle}
                aria-current={isActive ? "step" : undefined}
                aria-label={`Step ${idx + 1}: ${step.label}${
                  isComplete ? " (completed)" : ""
                }`}
              >
                {content}
              </Link>
            );
          }

          return (
            <span
              key={step.key}
              style={sharedStyle}
              aria-current={isActive ? "step" : undefined}
              aria-label={`Step ${idx + 1}: ${step.label}${
                isActive ? " (current)" : " (upcoming)"
              }`}
            >
              {content}
            </span>
          );
        })}
      </div>
    </nav>
  );
}

