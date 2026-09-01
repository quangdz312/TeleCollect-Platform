import { Stat } from "@/components/ui";
import { Icon } from "@/components/icons";
import type { RawEpisodeSummary } from "@/lib/raw";

export function RawSummaryCards({ summary }: { summary: RawEpisodeSummary }) {
  return (
    <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-5">
      <Stat label="Episodes" value={summary.total.toLocaleString()} hint={`${summary.archived.toLocaleString()} archived`} sparkline={false} icon={<Icon name="datasets" className="h-6 w-6" />} />
      <Stat label="Teleop" value={summary.teleop.toLocaleString()} hint="Across all matching pages" sparkline={false} icon={<Icon name="teleop" className="h-6 w-6" />} />
      <Stat label="Scripted" value={summary.scripted.toLocaleString()} hint="Across all matching pages" sparkline={false} icon={<Icon name="scripted" className="h-6 w-6" />} />
      <Stat
        label="Simulator outcome"
        value={<><span className="text-ok-600">{summary.successes.toLocaleString()}</span><span className="text-ink-400"> / </span><span className="text-bad-600">{summary.failures.toLocaleString()}</span></>}
        hint="Success / Failure"
        sparkline={false}
        icon={<Icon name="outcome" className="h-6 w-6 text-ok-600" />}
      />
      <Stat label="Pending review" value={summary.pending.toLocaleString()} hint="Needs a reviewer verdict" tone={summary.pending ? "warn" : "ok"} sparkline={false} icon={<Icon name="pending" className="h-6 w-6 text-warn-400" />} />
    </div>
  );
}
