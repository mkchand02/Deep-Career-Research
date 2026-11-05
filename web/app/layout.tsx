import "./globals.css";
import type { Metadata } from "next";
import Link from "next/link";
import { ThemeToggle } from "@/components/theme";

export const metadata: Metadata = {
  title: "Deep Career Research",
  description: "Aggregate-only hiring-trend research from public professional profiles.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="min-h-screen font-sans antialiased">
        <header className="border-b border-border bg-surface">
          <nav className="mx-auto flex max-w-6xl flex-wrap items-center gap-x-5 gap-y-2 px-4 py-3 text-sm">
            <Link href="/" className="font-semibold text-ink">
              Deep Career Research
            </Link>
            <Link href="/studies/new" className="text-ink2 hover:text-ink">New study</Link>
            <Link href="/compare" className="text-ink2 hover:text-ink">Compare</Link>
            <Link href="/methodology" className="text-ink2 hover:text-ink">Methodology</Link>
            <span className="ml-auto rounded-full border border-border px-2 py-0.5 text-xs text-ink2">
              Aggregate-only · k-anonymized
            </span>
            <ThemeToggle />
          </nav>
        </header>
        <main className="mx-auto max-w-6xl px-4 py-6">{children}</main>
      </body>
    </html>
  );
}
