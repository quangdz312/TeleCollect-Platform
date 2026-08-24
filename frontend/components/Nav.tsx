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
  { href: "/raw", label: "Raw episodes", roles: ["reviewer", "admin"] },
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
    <header className="slide-down sticky top-0 z-30 border-b border-ink-700 bg-ink-900/90 backdrop-blur-md">
      <div className="mx-auto flex w-full max-w-[1500px] flex-wrap items-center gap-5 px-5 py-3">
        <Link href="/" className="flex items-center gap-2 transition-opacity hover:opacity-80">
          <span className="grid h-8 w-8 place-items-center rounded-[9px] bg-gradient-to-br from-accent-500 to-cyan-600 text-[13px] font-bold text-white">
            TC
          </span>
          <span className="text-[15px] font-bold tracking-tight text-ink-100">TeleCollect</span>
        </Link>

        <nav className="order-3 flex flex-1 flex-wrap items-center gap-0.5 sm:order-none sm:w-auto sm:flex-1">
          {visible.map((link) => {
            const active =
              link.href === "/" ? pathname === "/" : pathname.startsWith(link.href);
            return (
              <Link
                key={link.href}
                href={link.href}
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

        <div className="ml-auto flex items-center gap-3.5">
          <div className="hidden text-right sm:block">
            <div className="text-[13px] font-semibold leading-tight text-ink-100">
              {user.display_name || user.username}
            </div>
            <div className="mt-0.5 text-[11px] text-ink-400">{user.username}</div>
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
