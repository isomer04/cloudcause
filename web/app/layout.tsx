import type { Metadata, Viewport } from "next";
import { Inter, JetBrains_Mono } from "next/font/google";

import { Rail } from "@/components/rail";
import "./globals.css";

const inter = Inter({
  subsets: ["latin"],
  display: "swap",
  variable: "--font-inter",
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
    <html lang="en" className={`${inter.variable} ${mono.variable}`}>
      <body className="min-h-dvh">
        <a
          href="#main-content"
          className="skip-link"
        >
          Skip to main content
        </a>
        <div className="lg:grid lg:min-h-dvh lg:grid-cols-[14rem_minmax(0,1fr)]">
          <Rail />
          <main id="main-content" tabIndex={-1} className="min-w-0">{children}</main>
        </div>
      </body>
    </html>
  );
}
