"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { NEW_INVESTIGATION_EVENT } from "@/lib/nav-events";

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
            onClick={(event) => {
              // Already on the console: the router would do nothing, and the
              // console would keep showing the finished report. Clear it instead,
              // so the item means "start a new investigation" from either page.
              if (link.href !== "/" || pathname !== "/") return;
              event.preventDefault();
              window.dispatchEvent(new Event(NEW_INVESTIGATION_EVENT));
            }}
            className={`rounded-md px-3 py-2 text-sm font-medium transition-colors duration-150 ${
              active
                ? "bg-brand text-on-brand"
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
