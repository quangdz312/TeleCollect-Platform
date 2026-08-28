"use client";

/**
 * Export dialog for a selection of raw episodes.
 *
 * Conversion used to be a separate page, so exporting meant leaving the review
 * screen and losing sight of what was ticked. There are only a handful of
 * options, so a dialog over the table keeps the selection in view.
 *
 * Only approved episodes reach a dataset — the export API drops the rest
 * server-side, so the dialog states the real number up front instead of
 * letting the run come back empty.
 */

import { useEffect, useRef, useState } from "react";
import { Alert, Button, Field, Input, Select } from "@/components/ui";
import { api, type DatasetExport } from "@/lib/api";
import type { RawEpisode } from "@/lib/raw";

const POLL_INTERVAL_MS = 750;
const POLL_LIMIT = 160;

function delay(ms: number) {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}

export function ExportDialog({
  episodes,
  onClose,
  onExported,
}: {
  episodes: RawEpisode[];
  onClose: () => void;
  onExported?: (dataset: DatasetExport) => void;
}) {
  const [name, setName] = useState("");
  const [format, setFormat] = useState("robomimic");
  const [includeFailures, setIncludeFailures] = useState(false);
  const [overwrite, setOverwrite] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [status, setStatus] = useState<string | null>(null);
  const [result, setResult] = useState<DatasetExport | null>(null);
  const dialogRef = useRef<HTMLDivElement>(null);

  const approved = episodes.filter((episode) => episode.review_status === "approved");
  const tasks = Array.from(new Set(approved.map((episode) => episode.task)));
  const sources = new Set(approved.map((episode) => episode.source));
  const dataSource: "teleop" | "scripted" | "both" =
    sources.size > 1 ? "both" : sources.has("teleop") ? "teleop" : "scripted";
  const frames = approved.reduce((total, episode) => total + episode.length, 0);

  // robomimic writes one file per task, so a mixed selection has no single
  // target to write into.
  const tooManyTasks = format === "robomimic" && tasks.length > 1;
  // The LeRobot writer reads teleop recording directories; scripted episodes
  // live inside multi-demo HDF5 files and never reach it, so a scripted-only
  // selection would fail after the job started rather than here.
  const noTeleop = format === "lerobot" && !sources.has("teleop");
  const canSubmit =
    approved.length > 0 && name.trim().length > 0 && !tooManyTasks && !noTeleop && !busy;

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !busy) onClose();
    };
    window.addEventListener("keydown", onKey);
    dialogRef.current?.focus();
    return () => window.removeEventListener("keydown", onKey);
  }, [busy, onClose]);

  async function run() {
    setBusy(true);
    setError(null);
    setStatus("Creating dataset…");
    try {
      const created = await api.createExport({
        name: name.trim(),
        format,
        tasks,
        include_failures: includeFailures,
        overwrite,
        data_source: dataSource,
        episode_ids: approved.map((episode) => episode.episode_id),
      });
      let current = created;
      for (let attempt = 0; attempt < POLL_LIMIT && current.status === "building"; attempt += 1) {
        setStatus(`Building ${current.name}…`);
        await delay(POLL_INTERVAL_MS);
        current = await api.exportInfo(created.id);
      }
      if (current.status === "failed") {
        throw new Error(current.error_message || `Conversion ${current.name} failed.`);
      }
      setResult(current);
      setStatus(null);
      onExported?.(current);
    } catch (problem) {
      setStatus(null);
      setError(problem instanceof Error ? problem.message : "Conversion failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget && !busy) onClose();
      }}
    >
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-label="Export selected episodes"
        tabIndex={-1}
        className="max-h-[90vh] w-full max-w-lg overflow-y-auto rounded-2xl border border-ink-700 bg-ink-900 p-5 shadow-xl focus:outline-none"
      >
        <h2 className="font-heading text-lg font-bold text-ink-100">Export selection</h2>

        {result ? (
          <div className="mt-4 space-y-4">
            <Alert tone="ok">
              {result.name} is ready — {result.num_episodes.toLocaleString()} episodes,{" "}
              {result.num_frames.toLocaleString()} frames.
            </Alert>
            <p className="break-all font-mono text-xs text-ink-400">{result.path}</p>
            <Button variant="primary" onClick={onClose}>
              Done
            </Button>
          </div>
        ) : (
          <div className="mt-4 space-y-4">
            <p className="text-sm text-ink-300">
              <strong className="text-ink-100">{approved.length}</strong> approved of{" "}
              {episodes.length} selected · {frames.toLocaleString()} frames
              {tasks.length > 0 ? ` · ${tasks.join(", ")}` : ""}
            </p>

            {approved.length === 0 ? (
              <Alert tone="bad">
                Datasets are built from approved episodes only, and none of this selection is
                approved yet. Accept some in the Verdict panel first.
              </Alert>
            ) : null}

            {tooManyTasks ? (
              <Alert tone="bad">
                The robomimic format writes one file per task, but this selection spans{" "}
                {tasks.length} tasks. Filter to a single task, or pick another format.
              </Alert>
            ) : null}

            {noTeleop ? (
              <Alert tone="bad">
                LeRobot exports teleop recordings, and this selection has none. Pick
                teleop episodes, or choose the robomimic format for scripted data.
              </Alert>
            ) : null}

            <Field label="Dataset name">
              <Input
                value={name}
                placeholder="can-v1"
                disabled={busy}
                onChange={(event) => setName(event.target.value)}
              />
            </Field>

            <Field label="Format">
              <Select
                value={format}
                disabled={busy}
                onChange={(event) => setFormat(event.target.value)}
              >
                <option value="robomimic">RoboMimic (HDF5)</option>
                <option value="lerobot">LeRobot v3</option>
                <option disabled>RLDS (planned)</option>
              </Select>
            </Field>

            <div className="space-y-2">
              <label className="flex items-center gap-2 text-sm text-ink-300">
                <input
                  type="checkbox"
                  checked={includeFailures}
                  disabled={busy}
                  onChange={(event) => setIncludeFailures(event.target.checked)}
                />
                Include episodes the simulator recorded as failures
              </label>
              <label className="flex items-center gap-2 text-sm text-ink-300">
                <input
                  type="checkbox"
                  checked={overwrite}
                  disabled={busy}
                  onChange={(event) => setOverwrite(event.target.checked)}
                />
                Overwrite an existing dataset with this name
              </label>
            </div>

            {error ? <Alert tone="bad">{error}</Alert> : null}
            {status ? <Alert tone="info">{status}</Alert> : null}

            <div className="flex justify-end gap-2 border-t border-ink-700 pt-4">
              <Button onClick={onClose} disabled={busy}>
                Cancel
              </Button>
              <Button variant="primary" disabled={!canSubmit} onClick={() => void run()}>
                {busy ? "Exporting…" : "Export"}
              </Button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
