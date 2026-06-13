import * as React from "react";
import { cva, type VariantProps } from "class-variance-authority";

import { cn } from "@/lib/utils";

/**
 * Badge — small status pill. Variants use the app's established
 * "tinted surface + inset ring" treatment so they sit naturally next to the
 * existing severity/status chips. No icons — labels carry the meaning.
 */
const badgeVariants = cva(
  "inline-flex items-center rounded-full px-2.5 py-0.5 text-[11px] font-semibold ring-1 ring-inset transition-colors",
  {
    variants: {
      variant: {
        default: "bg-brand-50 text-brand-700 ring-brand-100",
        secondary: "bg-ink-100 text-ink-600 ring-ink-200",
        success: "bg-emerald-50 text-emerald-700 ring-emerald-200",
        warning: "bg-amber-50 text-amber-700 ring-amber-200",
        destructive: "bg-rose-50 text-rose-700 ring-rose-200",
        outline: "bg-transparent text-ink-600 ring-ink-200",
      },
    },
    defaultVariants: {
      variant: "default",
    },
  }
);

export interface BadgeProps
  extends React.HTMLAttributes<HTMLSpanElement>,
    VariantProps<typeof badgeVariants> {}

function Badge({ className, variant, ...props }: BadgeProps) {
  return (
    <span className={cn(badgeVariants({ variant }), className)} {...props} />
  );
}

export { Badge, badgeVariants };
