"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useAuth } from "@/components/AuthProvider";
import { Badge, Button, cx } from "@/components/ui";

const LINKS: { href: string; label: string; roles?: string[] }[] = [
  { href: "/", label: "Overview" },
  { href: "/collect", label: "Collect data", roles: ["operator", "reviewer", "admin"] },
  { href: "/upload", label: "Upload" },
  { href: "/review", label: "Review" },
  { href: "/diversity", label: "Data diversity" },
  { href: "/datasets", label: "Datasets" },
  { href: "/training", label: "Training" },
  { href: "/admin", label: "Users", roles: ["admin"] },
];

export function Nav() {
  const { user, logout } = useAuth();
  const pathname = usePathname();
  if (!user) return null;

  const visible = LINKS.filter((link) => !link.roles || link.roles.includes(user.role));

  return (
    <header className="sticky top-0 z-30 border-b border-ink-700/60 bg-ink-950/80 backdrop-blur">
      <div className="mx-auto flex w-full max-w-[1500px] items-center gap-6 px-5 py-3">
        <Link href="/" className="flex items-center gap-2">
          <span className="grid h-7 w-7 place-items-center rounded-md bg-accent-500 text-[13px] font-bold text-white">
            TC
          </span>
          <span className="text-sm font-semibold tracking-wide">TeleCollect</span>
        </Link>

        <nav className="flex flex-1 flex-wrap items-center gap-1">
          {visible.map((link) => {
            const active =
              link.href === "/" ? pathname === "/" : pathname.startsWith(link.href);
            return (
              <Link
                key={link.href}
                href={link.href}
                className={cx(
                  "rounded-lg px-3 py-1.5 text-sm transition-colors",
                  active
                    ? "bg-ink-800 text-ink-100"
                    : "text-ink-400 hover:bg-ink-850 hover:text-ink-100",
                )}
              >
                {link.label}
              </Link>
            );
          })}
        </nav>

        <div className="flex items-center gap-3">
          <div className="hidden text-right sm:block">
            <div className="text-sm leading-tight">{user.display_name || user.username}</div>
            <div className="text-[11px] text-ink-400">{user.username}</div>
          </div>
          <Badge tone={user.role === "admin" ? "info" : user.role === "reviewer" ? "warn" : "ok"}>
            {user.role}
          </Badge>
          <Button variant="ghost" onClick={logout}>
            Sign out
          </Button>
        </div>
      </div>
    </header>
  );
}
