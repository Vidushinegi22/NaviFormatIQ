import tailwindcssAnimate from "tailwindcss-animate";

/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      // Navi FormatiQ brand palette — Deep Navy primary.
      colors: {
        // Interactive accent (buttons, links, icon accents). Anchored on
        // #051D60 at 500 so `bg-brand-500` buttons render the requested color.
        brand: {
          50: "#ECEFF8",
          100: "#D2DAF0",
          200: "#A6B6E1",
          300: "#7088C9",
          400: "#3A57A3",
          500: "#051D60", // Primary action color (buttons)
          600: "#04174D",
          700: "#03103A",
          800: "#020A28",
          900: "#010517",
        },
        // Dark UI chrome / surfaces ("anything else") + a lighter contrast step.
        navy: {
          DEFAULT: "#0D131F", // primary dark surface (navbar, filled chrome)
          light: "#182954",   // lighter contrast (completed steps, accents)
        },
        ink: {
          50: "#F7F9FA",
          100: "#EEF2F4",
          200: "#DCE3E7",
          300: "#B7C2C8",
          400: "#8593A0",
          500: "#5A6976",
          600: "#3E4C58",
          700: "#2B3640",
          800: "#1A222A",
          900: "#0D131F",
        },
        // shadcn/ui semantic tokens (see :root in index.css). These power the
        // primitives in components/ui/* and resolve to the brand palette above.
        border: "hsl(var(--border))",
        input: "hsl(var(--input))",
        ring: "hsl(var(--ring))",
        background: "hsl(var(--background))",
        foreground: "hsl(var(--foreground))",
        primary: {
          DEFAULT: "hsl(var(--primary))",
          foreground: "hsl(var(--primary-foreground))",
        },
        secondary: {
          DEFAULT: "hsl(var(--secondary))",
          foreground: "hsl(var(--secondary-foreground))",
        },
        destructive: {
          DEFAULT: "hsl(var(--destructive))",
          foreground: "hsl(var(--destructive-foreground))",
        },
        muted: {
          DEFAULT: "hsl(var(--muted))",
          foreground: "hsl(var(--muted-foreground))",
        },
        accent: {
          DEFAULT: "hsl(var(--accent))",
          foreground: "hsl(var(--accent-foreground))",
        },
        popover: {
          DEFAULT: "hsl(var(--popover))",
          foreground: "hsl(var(--popover-foreground))",
        },
        card: {
          DEFAULT: "hsl(var(--card))",
          foreground: "hsl(var(--card-foreground))",
        },
      },
      fontFamily: {
        sans: [
          "Inter",
          "ui-sans-serif",
          "system-ui",
          "-apple-system",
          "Segoe UI",
          "Roboto",
          "sans-serif",
        ],
      },
      boxShadow: {
        card: "0 1px 2px rgba(14, 20, 26, 0.04), 0 1px 3px rgba(14, 20, 26, 0.06)",
        "card-hover":
          "0 10px 25px -8px rgba(10,90,102,0.18), 0 4px 10px -4px rgba(14, 20, 26, 0.08)",
        nav: "0 1px 0 rgba(14, 20, 26, 0.06)",
        campaign:
          "0 1px 3px rgba(14, 20, 26, 0.06), 0 2px 8px rgba(14, 20, 26, 0.04)",
        "campaign-hover":
          "0 4px 16px rgba(10,90,102,0.12), 0 2px 8px rgba(14, 20, 26, 0.06)",
        modal:
          "0 20px 60px rgba(14, 20, 26, 0.25), 0 8px 20px rgba(14, 20, 26, 0.10)",
        footer:
          "0 -2px 10px rgba(14, 20, 26, 0.06)",
      },
      borderRadius: {
        xl: "0.875rem",
        "2xl": "1.125rem",
      },
      keyframes: {
        "fade-up": {
          "0%": { opacity: "0", transform: "translateY(8px)" },
          "100%": { opacity: "1", transform: "translateY(0)" },
        },
        "fade-in": {
          "0%": { opacity: "0" },
          "100%": { opacity: "1" },
        },
        "scale-in": {
          "0%": { opacity: "0", transform: "scale(0.95)" },
          "100%": { opacity: "1", transform: "scale(1)" },
        },
        "slide-up": {
          "0%": { opacity: "0", transform: "translateY(16px)" },
          "100%": { opacity: "1", transform: "translateY(0)" },
        },
        // Indeterminate progress bar — a segment sweeping across the track.
        "progress-loop": {
          "0%": { left: "-45%" },
          "100%": { left: "100%" },
        },
        // Subtle highlight shimmer that travels across a surface.
        shimmer: {
          "0%": { transform: "translateX(-100%)" },
          "100%": { transform: "translateX(100%)" },
        },
      },
      animation: {
        "fade-up": "fade-up 0.4s ease-out both",
        "fade-in": "fade-in 0.2s ease-out both",
        "scale-in": "scale-in 0.25s ease-out both",
        "slide-up": "slide-up 0.35s ease-out both",
        "progress-loop": "progress-loop 1.3s ease-in-out infinite",
        shimmer: "shimmer 1.6s ease-in-out infinite",
      },
    },
  },
  plugins: [tailwindcssAnimate],
};
