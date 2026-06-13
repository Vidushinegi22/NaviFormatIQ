import { Link } from "react-router-dom";
import { Logo } from "./Logo";
import { UserCircleIcon } from "@/components/icons/Icons";

interface TopNavBarProps {
  /** Page title rendered in the center chip (e.g. workflow name | project ID). */
  title?: string;
  /** Optional subtitle / campaign ID shown beside the title. */
  campaignId?: string;
}

/**
 * Sticky enterprise navigation bar.
 *
 *   - Left:   Logo wordmark + pipe + "Navi FormatiQ"
 *   - Center: Workflow name chip (when in wizard)
 *   - Right:  User avatar icon
 *
 * Deep-navy background with high-contrast white iconography to match the brand.
 */
export function TopNavBar({ title, campaignId }: TopNavBarProps) {
  return (
    <header
      className="sticky top-0 z-40 w-full bg-navy text-white shadow-nav"
      role="banner"
    >
      <div className="relative mx-auto flex h-14 max-w-[1440px] items-center justify-between px-4 sm:px-6 lg:px-8">
        {/* Left: logo wordmark + pipe + product name */}
        <Link
          to="/"
          className="z-10 flex min-w-0 items-center gap-3 rounded-lg focus-visible:ring-2 focus-visible:ring-white/60"
          aria-label="Navi FormatiQ home"
        >
          <Logo className="h-7 w-auto shrink-0" />
          <span
            aria-hidden="true"
            className="hidden h-7 w-px bg-white/40 sm:block"
          />
          <span className="hidden text-2xl font-bold tracking-tight text-white sm:inline">
            Navi FormatiQ
          </span>
        </Link>

        {/* Center: workflow name chip — absolutely centered so it stays put
            regardless of the left/right cluster widths. */}
        {(title || campaignId) && (
          <div className="pointer-events-none absolute left-1/2 top-1/2 hidden max-w-[40%] -translate-x-1/2 -translate-y-1/2 xl:flex xl:items-center">
            <span className="inline-flex max-w-full items-center gap-2 rounded-full bg-white/15 px-4 py-1.5 text-[13px] font-semibold backdrop-blur-sm">
              {title && <span className="truncate">{title}</span>}
              {title && campaignId && (
                <span className="h-3.5 w-px shrink-0 bg-white/30" aria-hidden="true" />
              )}
              {campaignId && (
                <span className="shrink-0 text-white/80">{campaignId}</span>
              )}
            </span>
          </div>
        )}

        {/* Right: user avatar */}
        <button
          type="button"
          className="z-10 flex h-9 w-9 items-center justify-center rounded-full bg-white/15 text-white transition-colors hover:bg-white/25 focus-visible:ring-2 focus-visible:ring-white/60"
          aria-label="User menu"
        >
          <UserCircleIcon className="h-5 w-5" />
        </button>
      </div>
    </header>
  );
}
