import type { ReactNode } from "react";

export function PageHeader({ eyebrow, title, description, actions }: { eyebrow: string; title: string; description?: string; actions?: ReactNode }) {
  return (
    <header className="flex flex-wrap items-end justify-between gap-4 border-b border-ink-700/90 pb-4">
      <div className="min-w-0">
        <p className="text-[10px] font-bold uppercase tracking-[0.2em] text-accent-500">{eyebrow}</p>
        <h1 className="mt-1 font-heading text-[26px] font-bold tracking-[-0.025em] text-ink-100 sm:text-[30px]">{title}</h1>
        {description ? <p className="mt-1 max-w-3xl text-sm text-ink-400">{description}</p> : null}
      </div>
      {actions ? <div className="flex flex-wrap items-center gap-2">{actions}</div> : null}
    </header>
  );
}
