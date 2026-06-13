import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

/**
 * `cn` — merge conditional class names and resolve Tailwind conflicts.
 *
 * Standard shadcn/ui helper: `clsx` handles conditional/array/object inputs,
 * `tailwind-merge` dedupes conflicting Tailwind utilities (e.g. the later
 * `px-4` wins over an earlier `px-2`).
 */
export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}
