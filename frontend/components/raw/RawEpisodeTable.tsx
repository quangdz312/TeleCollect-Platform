import Link from "next/link";
import { Badge, Button, Empty } from "@/components/ui";
import { bytes, duration, timeAgo } from "@/lib/format";
import type { RawEpisode } from "@/lib/raw";

function outcomeBadge(value: boolean | null) {
  if (value === true) return <Badge tone="ok">Success</Badge>;
  if (value === false) return <Badge tone="bad">Failure</Badge>;
  return <Badge>Unknown</Badge>;
}

function reviewTone(value: RawEpisode["review_status"]): "ok" | "bad" | "warn" | "neutral" {
  if (value === "approved") return "ok";
  if (value === "rejected") return "bad";
  if (value === "pending") return "warn";
  return "neutral";
}

function CameraFlag({ available, children }: { available: boolean; children: string }) {
  return (
    <span className={available ? "font-semibold text-ok-600" : "text-ink-400"}>
      {children}{available ? " ✓" : " —"}
    </span>
  );
}

export function RawEpisodeTable({
  items,
  loading,
  selectedIds,
  onToggle,
  onTogglePage,
}: {
  items: RawEpisode[];
  loading: boolean;
  selectedIds: Set<string>;
  onToggle: (episode: RawEpisode) => void;
  onTogglePage: (episodes: RawEpisode[], selected: boolean) => void;
}) {
  if (!loading && items.length === 0) return <Empty>No raw episodes match these filters.</Empty>;

  const eligible = items.filter((episode) => episode.review_status === "approved");
  const allEligibleSelected = eligible.length > 0 && eligible.every((episode) => selectedIds.has(episode.episode_id));

  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[1280px] text-sm [&_td]:px-2 [&_th]:px-2">
        <thead className="text-left text-xs uppercase tracking-wider text-ink-400">
          <tr>
            <th className="pb-2">
              <input
                aria-label="Select all approved episodes on this page"
                type="checkbox"
                checked={allEligibleSelected}
                disabled={loading || eligible.length === 0}
                onChange={(event) => onTogglePage(eligible, event.target.checked)}
              />
            </th>
            <th className="pb-2">Episode</th>
            <th className="pb-2">Source</th>
            <th className="pb-2">Task</th>
            <th className="pb-2">Outcome</th>
            <th className="pb-2">Quality</th>
            <th className="pb-2 text-right">Duration</th>
            <th className="pb-2 text-right">Frames</th>
            <th className="pb-2">Cameras</th>
            <th className="pb-2">Review</th>
            <th className="pb-2 text-right">Size</th>
            <th className="pb-2">Created</th>
            <th className="pb-2" />
          </tr>
        </thead>
        <tbody className="tabular">
          {loading ? (
            <tr className="border-t border-ink-700/50">
              <td colSpan={13} className="py-10 text-center text-ink-400">Loading raw episodes…</td>
            </tr>
          ) : items.map((episode) => (
            <tr key={`${episode.source}:${episode.episode_id}`} className="border-t border-ink-700/50 hover:bg-ink-850/70">
              <td className="py-3">
                <input
                  aria-label={`Select ${episode.display_name}`}
                  title={episode.review_status === "approved" ? "Select for conversion" : "Only approved episodes can be converted"}
                  type="checkbox"
                  checked={selectedIds.has(episode.episode_id)}
                  disabled={episode.review_status !== "approved"}
                  onChange={() => onToggle(episode)}
                />
              </td>
              <td className="max-w-[260px] py-3 pr-4">
                <div className="truncate font-medium" title={episode.display_name}>{episode.display_name}</div>
                <div className="mt-0.5 truncate font-mono text-[11px] text-ink-400" title={episode.episode_id}>
                  {episode.episode_id}
                </div>
              </td>
              <td className="py-3"><Badge tone={episode.source === "scripted" ? "info" : "neutral"}>{episode.source}</Badge></td>
              <td className="py-3 font-medium">{episode.task}</td>
              <td className="py-3">{outcomeBadge(episode.recorded_success)}</td>
              <td className="py-3">{episode.quality ? <Badge>{episode.quality}</Badge> : <span className="text-ink-400">—</span>}</td>
              <td className="py-3 text-right">{episode.duration_s === null ? "—" : duration(episode.duration_s)}</td>
              <td className="py-3 text-right">{episode.length.toLocaleString()}</td>
              <td className="py-3 text-[11px]">
                <div className="flex gap-2">
                  <CameraFlag available={episode.cameras.front}>Front</CameraFlag>
                  <CameraFlag available={episode.cameras.birdview}>Bird</CameraFlag>
                  <CameraFlag available={episode.cameras.wrist}>Wrist</CameraFlag>
                </div>
              </td>
              <td className="py-3"><Badge tone={reviewTone(episode.review_status)}>{episode.review_status}</Badge></td>
              <td className="py-3 text-right">{episode.size_bytes === null ? "—" : bytes(episode.size_bytes)}</td>
              <td className="py-3 text-xs text-ink-400">{episode.created_at ? timeAgo(episode.created_at) : "—"}</td>
              <td className="py-3 text-right">
                <Link href={`/raw/${encodeURIComponent(episode.episode_id)}`}>
                  <Button variant="subtle">Open</Button>
                </Link>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
