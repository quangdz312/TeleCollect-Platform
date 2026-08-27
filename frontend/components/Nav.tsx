"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useState } from "react";
import { useAuth } from "@/components/AuthProvider";
import { Badge, Button, cx } from "@/components/ui";

/**
 * Collect data, Upload and Data diversity are deliberately absent.
 *
 * They are still routed and still reachable — Overview links to /collect, and
 * /scripted and /teleop redirect into it — they just no longer earn a permanent
 * slot in the sidebar.
 */
const LINKS: { href: string; label: string; roles?: string[] }[] = [
  { href: "/", label: "Overview" },
  { href: "/raw", label: "Review", roles: ["reviewer", "admin"] },
  { href: "/datasets", label: "Datasets" },
  { href: "/training", label: "Training" },
  { href: "/evaluate", label: "Evaluate", roles: ["reviewer", "admin"] },
  { href: "/settings", label: "Settings" },
  { href: "/admin", label: "Users", roles: ["admin"] },
];

export function Nav({ rail = null }: { rail?: React.ReactNode }) {
  const { user, logout } = useAuth();
  const pathname = usePathname();
  const [open, setOpen] = useState(false);
  if (!user) return null;

  const visible = LINKS.filter((link) => !link.roles || link.roles.includes(user.role));

  const links = (
    <nav className="flex flex-col gap-0.5">
      {visible.map((link) => {
        const active = link.href === "/" ? pathname === "/" : pathname.startsWith(link.href);
        return (
          <Link
            key={link.href}
            href={link.href}
            onClick={() => setOpen(false)}
            className={cx(
              "rounded-lg px-3.5 py-2 text-[13px] font-medium transition-colors",
              active
                ? "bg-accent-500/10 font-semibold text-accent-500"
                : "text-ink-300 hover:bg-ink-800 hover:text-accent-500",
            )}
          >
            {link.label}
          </Link>
        );
      })}
    </nav>
  );

  const account = (
    <div className="border-t border-ink-700 pt-3">
      <div className="px-1">
        <div className="truncate text-[13px] font-semibold leading-tight text-ink-100">
          {user.display_name || user.username}
        </div>
        <div className="mt-0.5 truncate text-[11px] text-ink-400">{user.username}</div>
      </div>
      <div className="mt-2 flex items-center justify-between gap-2 px-1">
        <Badge tone={user.role === "admin" ? "info" : user.role === "reviewer" ? "warn" : "ok"}>
          {user.role}
        </Badge>
        <Button variant="ghost" onClick={logout}>
          Sign out
        </Button>
      </div>
    </div>
  );

  const brand = (
    <Link
      href="/"
      onClick={() => setOpen(false)}
      className="flex items-center gap-2 transition-opacity hover:opacity-80"
    >
      <span className="grid h-8 w-8 shrink-0 place-items-center rounded-[9px] bg-gradient-to-br from-accent-500 to-cyan-600 text-[13px] font-bold text-white">
        TC
      </span>
      <span className="text-[15px] font-bold tracking-tight text-ink-100">TeleCollect</span>
    </Link>
  );

  return (
    <>
      {/* Narrow screens keep a slim bar, because a fixed sidebar would eat the
          width the episode table needs. */}
      <header className="slide-down sticky top-0 z-30 flex items-center gap-3 border-b border-ink-700 bg-ink-900/90 px-4 py-3 backdrop-blur-md lg:hidden">
        <button
          type="button"
          aria-label="Toggle navigation"
          aria-expanded={open}
          onClick={() => setOpen((value) => !value)}
          className="rounded-lg p-2 text-ink-300 transition-colors hover:bg-ink-800 hover:text-accent-500"
        >
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <path d="M3 6h18M3 12h18M3 18h18" />
          </svg>
        </button>
        {brand}
      </header>

      {open ? (
        <div className="fixed inset-0 z-40 lg:hidden">
          <div
            className="absolute inset-0 bg-black/50"
            onClick={() => setOpen(false)}
            aria-hidden="true"
          />
          <div className="absolute inset-y-0 left-0 flex w-64 flex-col gap-4 overflow-y-auto border-r border-ink-700 bg-ink-900 p-4">
            {brand}
            {rail ?? links}
            <div className="mt-auto">{account}</div>
          </div>
        </div>
      ) : null}

      <aside className="fixed inset-y-0 left-0 z-30 hidden w-60 flex-col gap-4 border-r border-ink-700 bg-ink-900 p-4 lg:flex">
        {brand}
        {/* Scrolls on its own so a long rail never pushes the account block
            off the bottom of the column. */}
        <div className="min-h-0 flex-1 overflow-y-auto">{rail ?? links}</div>
        <div className="mt-auto">{account}</div>
      </aside>
    </>
  );
}
