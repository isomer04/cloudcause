"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const LINKS = [
  { href: "/", label: "Investigate" },
  { href: "/history", label: "History" },
] as const;

export function NavLinks() {
  const pathname = usePathname();

  return (
    <nav aria-label="Sections" className="flex gap-1 lg:mt-7 lg:flex-col lg:gap-0.5">
      {LINKS.map((link) => {
        const active =
          link.href === "/"
            ? pathname === "/" || pathname.startsWith("/investigations")
            : pathname.startsWith(link.href);
        return (
          <Link
            key={link.href}
            href={link.href}
            aria-current={active ? "page" : undefined}
            className={`rounded-sm px-2.5 py-1.5 text-sm transition-colors duration-150 ${
              active
                ? "bg-white/14 text-on-brand"
                : "text-on-brand-mute hover:bg-white/8 hover:text-on-brand"
            }`}
          >
            {link.label}
          </Link>
        );
      })}
    </nav>
  );
}
