import type { Metadata, Viewport } from "next";
import { Figtree, JetBrains_Mono } from "next/font/google";

import "./globals.css";

const figtree = Figtree({
  subsets: ["latin"],
  variable: "--font-figtree",
  display: "swap",
});

const jetbrainsMono = JetBrains_Mono({
  subsets: ["latin"],
  variable: "--font-mono",
  display: "swap",
});

export const metadata: Metadata = {
  title: "AlphaTrade AI",
  description: "Human-in-the-loop paper trading copilot",
  manifest: "/manifest.json",
  appleWebApp: {
    capable: true,
    statusBarStyle: "black-translucent",
    title: "AlphaTrade AI",
  },
};

// No maximumScale / userScalable cap: browser pinch zoom must stay available (WCAG 1.4.4).
// viewportFit cover is required for env(safe-area-inset-*) to apply on iOS Safari.
export const viewport: Viewport = {
  themeColor: "#080b14",
  width: "device-width",
  initialScale: 1,
  viewportFit: "cover",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" className="dark" data-theme="dark">
      <body
        className={`${figtree.variable} ${jetbrainsMono.variable} min-h-screen bg-background font-sans antialiased`}
      >
        {children}
      </body>
    </html>
  );
}
