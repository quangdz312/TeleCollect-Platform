"use client";

/**
 * Review-status shortcut in the left bar.
 *
 * The old review page only ever listed what still needed a decision, so that
 * view disappeared when review folded into this page. These bring it back as
 * one click — "Needs review" is the working queue, and the counts make the
 * remaining workload visible without opening the filter panel.
 *
 * Styled as icon-plus-label rows rather than bordered buttons, matching the app
 * links they share the bar with: five outlined boxes read as five separate
 * controls and took enough height to push the filters below the fold, where the
 * search box was a scroll away.
 */

import { cx } from "@/components/ui";
import type { RawEpisodeReviewStatus, RawEpisodeSummary } from "@/lib/raw";

/** `diversity` is a view, not a status — it swaps the table for the report. */
export const DIVERSITY_VIEW = "diversity";

type Value = RawEpisodeReviewStatus | "" | typeof DIVERSITY_VIEW;

function Icon({ path }: { path: string }) {
  return (
    <svg
      width="15"
      height="15"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.9"
      strokeLinecap="round"
      strokeLinejoin="round"
      className="shrink-0"
      aria-hidden="true"
    >
      <path d={path} />
    </svg>
  );
}

const ICONS = {
  all: "M3 6h18M3 12h18M3 18h18",
  pending: "M12 7v5l3 2M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0Z",
  approved: "M20 6 9 17l-5-5",
  rejected: "M18 6 6 18M6 6l12 12",
  archived: "M3 7h18v4H3zM5 11v9h14v-9M10 15h4",
  diversity: "M3 3v18h18M7 15l4-5 3 3 4-6",
} as const;

const TABS: {
  value: Value;
  label: string;
  key: keyof RawEpisodeSummary | null;
  icon: keyof typeof ICONS;
}[] = [
  { value: "", label: "All", key: null, icon: "all" },
  { value: "pending", label: "Needs review", key: "pending", icon: "pending" },
  { value: "approved", label: "Approved", key: "approved", icon: "approved" },
  { value: "rejected", label: "Rejected", key: "rejected", icon: "rejected" },
  { value: "archived", label: "Archived", key: "archived", icon: "archived" },
];

export function ReviewStatusTabs({
  value,
  counts,
  onChange,
}: {
  value: Value;
  counts: RawEpisodeSummary;
  onChange: (value: Value) => void;
}) {
  const rowClass = (active: boolean) =>
    cx(
      "flex w-full items-center gap-2.5 rounded-lg px-2.5 py-1.5 text-[13px] font-medium transition-colors",
      "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent-500/60",
      active
        ? "bg-accent-500/10 font-semibold text-accent-500"
        : "text-ink-300 hover:bg-ink-800 hover:text-accent-500",
    );

  return (
    <div role="tablist" aria-label="Review status" className="flex flex-col gap-0.5">
      {TABS.map((tab) => {
        const count = tab.key === null ? counts.total : counts[tab.key];
        const active = value === tab.value;
        return (
          <button
            key={tab.value || "all"}
            type="button"
            role="tab"
            aria-selected={active}
            onClick={() => onChange(tab.value)}
            className={rowClass(active)}
          >
            <Icon path={ICONS[tab.icon]} />
            <span className="truncate">{tab.label}</span>
            <span className="ml-auto tabular-nums text-[11px] text-ink-400">
              {typeof count === "number" ? count.toLocaleString() : 0}
            </span>
          </button>
        );
      })}

      <button
        type="button"
        role="tab"
        aria-selected={value === DIVERSITY_VIEW}
        onClick={() => onChange(DIVERSITY_VIEW)}
        className={cx(rowClass(value === DIVERSITY_VIEW), "mt-1")}
      >
        <Icon path={ICONS.diversity} />
        <span className="truncate">Data diversity</span>
      </button>
    </div>
  );
}
