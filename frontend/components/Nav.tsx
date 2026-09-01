"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useState } from "react";
import { useAuth } from "@/components/AuthProvider";
import { Badge, Button, cx } from "@/components/ui";
import { COLLECTION_ENABLED } from "@/lib/features";
import { type RailBack } from "@/components/AppShell";
import { Icon, type IconName } from "@/components/icons";

/**
 * Direct collection is available in the local/desktop build only. Keep its
 * permanent navigation entry behind the same build flag that protects the
 * page and backend endpoints, so deployed review-only builds stay unchanged.
 */
const LINKS: { href: string; label: string; icon: IconName; roles?: string[] }[] = [
  { href: "/", label: "Overview", icon: "overview" },
  ...(COLLECTION_ENABLED ? [{ href: "/collect", label: "Collect data", icon: "collect" as const }] : []),
  { href: "/raw", label: "Review", icon: "review", roles: ["reviewer", "admin"] },
  { href: "/datasets", label: "Datasets", icon: "datasets" },
  { href: "/training", label: "Training", icon: "training" },
  { href: "/evaluate", label: "Evaluate", icon: "evaluate", roles: ["reviewer", "admin"] },
  { href: "/settings", label: "Settings", icon: "settings" },
  { href: "/admin", label: "Users", icon: "users", roles: ["admin"] },
];

const RAIL_BACK_STYLE =
  "flex items-center gap-1.5 rounded px-1 py-0.5 text-xs text-ink-400 transition-colors " +
  "hover:text-ink-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-500/60";

export function Nav({
  rail = null,
  railBack = null,
}: {
  rail?: React.ReactNode;
  railBack?: RailBack | null;
}) {
  const { user, logout } = useAuth();
  const pathname = usePathname();
  const [open, setOpen] = useState(false);
  if (!user) return null;

  const visible = LINKS.filter((link) => !link.roles || link.roles.includes(user.role));

  // Mot cho ve duy nhat cho nut quay lai: dat o dau thanh, ngay tren thu no se
  // hoan lai. Trang chi khai bao nhan va dich, khong tu ve.
  const back = railBack ? (
    railBack.href ? (
      <Link href={railBack.href} className={RAIL_BACK_STYLE}>
        <span aria-hidden="true">←</span> {railBack.label}
      </Link>
    ) : (
      <button type="button" onClick={railBack.onClick} className={RAIL_BACK_STYLE}>
        <span aria-hidden="true">←</span> {railBack.label}
      </button>
    )
  ) : null;

  const links = (
    <nav className="flex flex-col gap-1">
      {visible.map((link) => {
        const active = link.href === "/" ? pathname === "/" : pathname.startsWith(link.href);
        return (
          <Link
            key={link.href}
            href={link.href}
            onClick={() => setOpen(false)}
            className={cx(
              "group relative flex items-center gap-3 rounded-xl px-3 py-2.5 text-[13px] font-medium transition-all",
              active
                ? "bg-accent-500/10 font-semibold text-accent-500 shadow-[inset_0_0_0_1px_rgba(37,99,235,0.06)]"
                : "text-ink-300 hover:bg-ink-850 hover:text-accent-500",
            )}
          >
            {active && <span className="absolute inset-y-2 -left-4 w-[3px] rounded-r-full bg-accent-500" />}
            <Icon name={link.icon} className="h-[18px] w-[18px] shrink-0" />
            {link.label}
          </Link>
        );
      })}
    </nav>
  );

  const account = (
    <div className="rounded-xl border border-ink-700 bg-ink-850/70 p-3">
      <div className="flex items-center gap-2.5">
        <span className="grid h-8 w-8 shrink-0 place-items-center rounded-full bg-ink-100 text-xs font-bold text-white">{(user.display_name || user.username).slice(0, 1).toUpperCase()}</span>
        <div className="min-w-0">
        <div className="truncate text-[13px] font-semibold leading-tight text-ink-100">
          {user.display_name || user.username}
        </div>
        <div className="mt-0.5 truncate text-[11px] text-ink-400">{user.username}</div>
        </div>
      </div>
      <div className="mt-2 flex items-center justify-between gap-2 px-1">
        <Badge tone={user.role === "admin" ? "info" : user.role === "reviewer" ? "warn" : "ok"}>
          {user.role}
        </Badge>
        <Button variant="ghost" onClick={logout} className="px-2.5">
          <Icon name="logout" className="h-4 w-4" /> Sign out
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
      <span className="grid h-10 w-10 shrink-0 place-items-center rounded-[11px] bg-[#0861f7] text-white shadow-[0_7px_16px_rgba(8,97,247,0.2)]">
        <TeleCollectMark className="h-8 w-8" />
      </span>
      <span className="min-w-0"><span className="block font-heading text-[15px] font-bold leading-tight tracking-[-0.025em] text-[#00123b]">TeleCollect</span><span className="mt-1 block whitespace-nowrap text-[7px] font-semibold uppercase leading-none tracking-[0.15em] text-[#536d91]">Robotics data platform</span></span>
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
          <Icon name="menu" className="h-[18px] w-[18px]" />
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
            {back}
            {rail ?? links}
            <div className="mt-auto">{account}</div>
          </div>
        </div>
      ) : null}

      <aside className="fixed inset-y-0 left-0 z-30 hidden w-72 flex-col gap-5 border-r border-ink-700 bg-ink-900 px-4 py-5 shadow-[4px_0_22px_rgba(15,23,42,0.025)] lg:flex">
        {brand}
        {/* Scrolls on its own so a long rail never pushes the account block
            off the bottom of the column. */}
        {back}
        <div className="min-h-0 flex-1 overflow-y-auto">{rail ?? links}</div>
        <div className="mt-auto">{account}</div>
      </aside>
    </>
  );
}

function TeleCollectMark({ className }: { className?: string }) {
  return (
    <svg
      viewBox="0 0 36 36"
      fill="none"
      className={className}
      aria-hidden="true"
    >
      {/* Compact actuator housing */}
      <rect x="11.6" y="5.4" width="12.8" height="3.2" rx=".45" stroke="currentColor" strokeWidth="1.7" />
      <path d="M14.2 8.6v5h7.6v-5" stroke="currentColor" strokeWidth="1.6" strokeLinejoin="round" />
      <circle cx="18" cy="10.4" r=".8" fill="#67e8f9" />
      <circle cx="18" cy="12.2" r=".68" fill="#67e8f9" />

      {/* Angular mirrored linkages and square gripping fingers */}
      <path d="m14.2 10.2-5 5.1" stroke="currentColor" strokeWidth="1.65" strokeLinecap="round" />
      <path d="m21.8 10.2 5 5.1" stroke="currentColor" strokeWidth="1.65" strokeLinecap="round" />
      <circle cx="8.6" cy="17" r="2.15" stroke="currentColor" strokeWidth="1.65" />
      <circle cx="27.4" cy="17" r="2.15" stroke="currentColor" strokeWidth="1.65" />
      <circle cx="8.6" cy="17" r=".62" fill="#67e8f9" />
      <circle cx="27.4" cy="17" r=".62" fill="#67e8f9" />
      <path d="M7.4 18.9v7.2l3.1 2.2h1.5v-5.1M28.6 18.9v7.2l-3.1 2.2H24v-5.1" stroke="currentColor" strokeWidth="1.7" strokeLinecap="square" strokeLinejoin="miter" />

      {/* Isometric data cube */}
      <path d="m18 19.7 5 2.9v5.8L18 31.3l-5-2.9v-5.8l5-2.9Z" stroke="currentColor" strokeWidth="1.7" strokeLinejoin="round" />
      <path d="m13.3 22.7 4.7 2.7 4.7-2.7M18 25.4v5.6" stroke="currentColor" strokeWidth="1.25" strokeLinejoin="round" />
      <circle cx="18" cy="22.3" r=".6" fill="#67e8f9" />
      <circle cx="15.4" cy="26.4" r=".55" fill="#67e8f9" />
      <circle cx="20.6" cy="26.4" r=".55" fill="#67e8f9" />
    </svg>
  );
}
