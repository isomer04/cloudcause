import type { Metadata, Viewport } from "next";
import { Inter, JetBrains_Mono, Newsreader } from "next/font/google";

import { Rail } from "@/components/rail";
import "./globals.css";

const inter = Inter({
  subsets: ["latin"],
  display: "swap",
  variable: "--font-inter",
});

const newsreader = Newsreader({
  subsets: ["latin"],
  display: "swap",
  weight: ["400", "500", "600"],
  style: ["normal", "italic"],
  variable: "--font-newsreader",
});

const mono = JetBrains_Mono({
  subsets: ["latin"],
  display: "swap",
  weight: ["400", "500", "700"],
  variable: "--font-mono-ui",
});

export const metadata: Metadata = {
  title: "CloudCause — cost spike investigations",
  description:
    "Evidence-grounded multi-cloud cost investigation across AWS, Azure, and Google Cloud. Read-only.",
};

export const viewport: Viewport = {
  themeColor: "#3a1518",
  width: "device-width",
  initialScale: 1,
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`${inter.variable} ${newsreader.variable} ${mono.variable}`}>
      <body className="min-h-dvh">
        <a
          href="#investigation"
          className="sr-only focus:not-sr-only focus:absolute focus:left-4 focus:top-4 focus:z-[var(--z-toast)] focus:rounded-sm focus:bg-brand focus:px-3 focus:py-2 focus:text-sm focus:text-on-brand"
        >
          Skip to investigation
        </a>
        <div className="lg:grid lg:min-h-dvh lg:grid-cols-[15rem_minmax(0,1fr)]">
          <Rail />
          <main className="min-w-0">{children}</main>
        </div>
      </body>
    </html>
  );
}
