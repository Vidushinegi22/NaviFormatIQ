/**
 * Navi FormatiQ brand logo.
 *
 * Renders the white wordmark shipped at `public/logo.svg`. It is a white SVG,
 * so it sits cleanly on the dark navy navbar. Size it via `className` (set a
 * height; width follows the SVG's aspect ratio).
 */
export function Logo({ className }: { className?: string }) {
  return (
    <img
      src="/logo.svg"
      alt=""
      aria-hidden="true"
      className={className}
      draggable={false}
    />
  );
}
