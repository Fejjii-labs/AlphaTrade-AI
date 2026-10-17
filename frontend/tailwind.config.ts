import type { Config } from "tailwindcss";

export default {
  content: [
    "./src/pages/**/*.{js,ts,jsx,tsx,mdx}",
    "./src/components/**/*.{js,ts,jsx,tsx,mdx}",
    "./src/app/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  darkMode: ["class", '[data-theme="dark"]'],
  theme: {
    extend: {
      colors: {
        background: "var(--color-background)",
        foreground: "var(--color-text-primary)",
        surface: {
          0: "var(--color-surface-0)",
          1: "var(--color-surface-1)",
          2: "var(--color-surface-2)",
          raised: "var(--color-surface-raised)",
        },
        border: {
          DEFAULT: "var(--color-border)",
          subtle: "var(--color-border-subtle)",
          strong: "var(--color-border-strong)",
        },
        text: {
          primary: "var(--color-text-primary)",
          secondary: "var(--color-text-secondary)",
          muted: "var(--color-text-muted)",
          inverse: "var(--color-text-inverse)",
          disabled: "var(--color-text-disabled)",
        },
        accent: {
          DEFAULT: "var(--color-accent)",
          hover: "var(--color-accent-hover)",
          muted: "var(--color-accent-muted)",
          border: "var(--color-accent-border)",
          foreground: "var(--color-accent-foreground)",
          solid: "var(--color-accent-solid)",
        },
        highlight: "var(--color-highlight)",
        success: {
          DEFAULT: "var(--color-success)",
          muted: "var(--color-success-muted)",
          border: "var(--color-success-border)",
        },
        warning: {
          DEFAULT: "var(--color-warning)",
          muted: "var(--color-warning-muted)",
          border: "var(--color-warning-border)",
        },
        danger: {
          DEFAULT: "var(--color-danger)",
          muted: "var(--color-danger-muted)",
          border: "var(--color-danger-border)",
        },
        info: {
          DEFAULT: "var(--color-info)",
          muted: "var(--color-info-muted)",
          border: "var(--color-info-border)",
        },
        blocked: {
          DEFAULT: "var(--color-blocked)",
          muted: "var(--color-blocked-muted)",
          border: "var(--color-blocked-border)",
        },
        positive: "var(--color-positive)",
        negative: "var(--color-negative)",
        paper: {
          DEFAULT: "var(--color-paper)",
          muted: "var(--color-paper-muted)",
          border: "var(--color-paper-border)",
        },
        mode: {
          paper: {
            DEFAULT: "var(--color-mode-paper)",
            muted: "var(--color-mode-paper-muted)",
            border: "var(--color-mode-paper-border)",
          },
          demo: {
            DEFAULT: "var(--color-mode-demo)",
            muted: "var(--color-mode-demo-muted)",
            border: "var(--color-mode-demo-border)",
          },
        },
        risk: {
          info: "var(--color-risk-info)",
          low: "var(--color-risk-low)",
          medium: "var(--color-risk-medium)",
          high: "var(--color-risk-high)",
          critical: "var(--color-risk-critical)",
        },
        stale: {
          DEFAULT: "var(--color-stale)",
          muted: "var(--color-stale-muted)",
          border: "var(--color-stale-border)",
        },
      },
      borderRadius: {
        control: "var(--radius-control)",
        card: "var(--radius-card)",
        pill: "var(--radius-pill)",
      },
      boxShadow: {
        elevation1: "var(--elevation-1)",
        elevation2: "var(--elevation-2)",
      },
      maxWidth: {
        content: "var(--content-max)",
      },
      spacing: {
        section: "var(--section-gap)",
        gutter: "var(--page-gutter-mobile)",
        "gutter-lg": "var(--page-gutter-desktop)",
      },
      fontFamily: {
        sans: ["var(--font-figtree)", "ui-sans-serif", "system-ui", "sans-serif"],
        mono: ["var(--font-mono)", "ui-monospace", "SFMono-Regular", "Menlo", "monospace"],
      },
      fontSize: {
        eyebrow: [
          "var(--text-eyebrow)",
          { lineHeight: "1.3", fontWeight: "600", letterSpacing: "0.12em" },
        ],
        caption: ["var(--text-caption)", { lineHeight: "1.4", fontWeight: "400" }],
        body: ["var(--text-body)", { lineHeight: "1.55", fontWeight: "400" }],
        label: ["var(--text-label)", { lineHeight: "1.4", fontWeight: "500" }],
        section: ["var(--text-section)", { lineHeight: "1.35", fontWeight: "600" }],
        title: [
          "var(--text-title)",
          { lineHeight: "1.25", fontWeight: "650", letterSpacing: "-0.015em" },
        ],
        display: ["var(--text-display)", { lineHeight: "1.15", fontWeight: "700" }],
      },
      ringColor: {
        focus: "var(--color-focus-ring)",
      },
      screens: {
        sm: "640px",
        md: "768px",
        lg: "1024px",
      },
    },
  },
  plugins: [],
} satisfies Config;
