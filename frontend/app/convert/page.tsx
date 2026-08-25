"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { useAuth } from "@/components/AuthProvider";
import { Alert, Badge, Button, Card, Empty, Field, Input, Select, Stat } from "@/components/ui";
import { api, type DatasetExport, type Summary } from "@/lib/api";
import { bytes } from "@/lib/format";
import { labeling, type TaskOption } from "@/lib/labeling";
import type { RawEpisode } from "@/lib/raw";
import { clearConvertSelection, readConvertSelection, writeConvertSelection } from "@/lib/convert-selection";

const EXPORT_POLL_INTERVAL_MS = 750;
const EXPORT_POLL_LIMIT = 160;

function delay(milliseconds: number) {
  return new Promise((resolve) => window.setTimeout(resolve, milliseconds));
}

export default function ConvertPage() {
  const { user } = useAuth();
  const [tasks, setTasks] = useState<TaskOption[]>([]);
  const [summary, setSummary] = useState<Summary | null>(null);
  const [dvc, setDvc] = useState<{ available: boolean; reason?: string } | null>(null);
  const [name, setName] = useState("v1");
  const [format, setFormat] = useState("robomimic");
  const [taskFilter, setTaskFilter] = useState("");
  const [dataSource, setDataSource] = useState<"teleop" | "scripted" | "both">("both");
  const [includeFailures, setIncludeFailures] = useState(false);
  const [overwrite, setOverwrite] = useState(false);
  const [batchId, setBatchId] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);
  const [result, setResult] = useState<DatasetExport | null>(null);
  const [selectedEpisodes, setSelectedEpisodes] = useState<RawEpisode[]>([]);

  useEffect(() => {
    if (!user) return;
    void Promise.all([
      api.summary(),
      api.dvc(),
      labeling.config().catch(() => null),
    ]).then(([teleopSummary, dvcStatus, scripted]) => {
      setSummary(scripted ? {
        ...teleopSummary,
        approved_successes:
          teleopSummary.approved_successes + scripted.workspace.approved_successes,
      } : teleopSummary);
      setDvc(dvcStatus);
      setTasks(scripted?.tasks ?? []);
    }).catch((problem) => {
      setError(problem instanceof Error ? problem.message : "Could not load conversion options");
    });
  }, [user]);

  useEffect(() => {
    const selection = readConvertSelection();
    if (selection.length === 0) return;
    setSelectedEpisodes(selection);
    const selectedTasks = Array.from(new Set(selection.map((episode) => episode.task)));
    if (selectedTasks.length === 1) setTaskFilter(selectedTasks[0]);
    const selectedSources = new Set(selection.map((episode) => episode.source));
    setDataSource(selectedSources.size > 1 ? "both" : selectedSources.has("scripted") ? "scripted" : "teleop");
    setIncludeFailures(selection.some((episode) => episode.recorded_success === false));
  }, []);

  if (!user) return null;
  if (user.role === "operator") {
    return <Alert tone="info">Dataset conversion is available to reviewers and administrators.</Alert>;
  }

  async function createExport() {
    setBusy(true);
    setError(null);
    setInfo(null);
    setResult(null);
    try {
      const created = await api.createExport({
        name,
        format,
        tasks: taskFilter ? [taskFilter] : [],
        include_failures: includeFailures,
        overwrite,
        data_source: dataSource,
        collection_batch_id: selectedEpisodes.length > 0 || dataSource === "teleop" ? undefined : batchId,
        episode_ids: selectedEpisodes.map((episode) => episode.episode_id),
      });
      setResult(created);
      setInfo(`Building ${created.name}…`);
      let completed = created;
      for (let attempt = 0; attempt < EXPORT_POLL_LIMIT && completed.status === "building"; attempt += 1) {
        await delay(EXPORT_POLL_INTERVAL_MS);
        completed = await api.exportInfo(created.id);
        setResult(completed);
      }
      if (completed.status === "failed") {
        throw new Error(completed.error_message || `Conversion ${completed.name} failed.`);
      }
      if (completed.status !== "ready") {
        setInfo(`Conversion ${completed.name} is still running. You can follow it from Datasets.`);
        return;
      }
      setInfo(
        `Converted ${completed.num_episodes} episodes / ${completed.num_frames.toLocaleString()} frames ` +
          `(${bytes(completed.size_bytes)}).`,
      );
      clearConvertSelection();
    } catch (problem) {
      setError(problem instanceof Error ? problem.message : "Conversion failed");
    } finally {
      setBusy(false);
    }
  }

  function removeSelectedEpisode(episodeId: string) {
    setSelectedEpisodes((current) => {
      const next = current.filter((episode) => episode.episode_id !== episodeId);
      writeConvertSelection(next);
      return next;
    });
  }

  const selectedTasks = Array.from(new Set(selectedEpisodes.map((episode) => episode.task)));
  const incompatibleSelection = selectedTasks.length > 1;
  const selectedFrames = selectedEpisodes.reduce((totalFrames, episode) => totalFrames + episode.length, 0);
  const selectedBatches = Array.from(new Set(
    selectedEpisodes
      .map((episode) => episode.collection_batch_id)
      .filter((batch): batch is string => Boolean(batch)),
  ));
  const selectedBatchDisplay = selectedBatches.length === 1
    ? selectedBatches[0]
    : selectedBatches.length > 1
      ? `Multiple batches (${selectedBatches.length})`
      : "No batch metadata";

  return (
    <div className="space-y-5">
      <div>
        <h1 className="font-heading text-[22px] font-bold tracking-tight">Convert data</h1>
        <p className="mt-0.5 text-sm text-ink-400">
          Convert approved raw episodes into an immutable, training-ready dataset.
        </p>
      </div>

      <div className="grid gap-3 sm:grid-cols-3">
        <Stat
          label={selectedEpisodes.length ? "Selected episodes" : "Eligible successes"}
          value={selectedEpisodes.length ? selectedEpisodes.length.toLocaleString() : summary?.approved_successes.toLocaleString() ?? "—"}
        />
        <Stat label="Output" value="RoboMimic HDF5" />
        <Stat label="Selection rule" value="Approved episodes" />
      </div>

      {selectedEpisodes.length > 0 && (
        <Card title="Selected raw episodes" subtitle={`${selectedEpisodes.length} episodes · ${selectedFrames.toLocaleString()} frames`}>
          {incompatibleSelection && (
            <div className="mb-3"><Alert>RoboMimic requires one task per dataset. Remove episodes until only one task remains.</Alert></div>
          )}
          <div className="max-h-80 overflow-auto">
            <table className="w-full min-w-[720px] text-sm">
              <thead className="text-left text-xs uppercase text-ink-400">
                <tr><th className="pb-2">Episode</th><th className="pb-2">Source</th><th className="pb-2">Task</th><th className="pb-2">Outcome</th><th className="pb-2">Quality</th><th className="pb-2 text-right">Frames</th><th /></tr>
              </thead>
              <tbody>
                {selectedEpisodes.map((episode) => (
                  <tr key={`${episode.source}:${episode.episode_id}`} className="border-t border-ink-700/60">
                    <td className="max-w-72 truncate py-2 font-mono text-xs" title={episode.episode_id}>{episode.display_name}</td>
                    <td className="py-2"><Badge tone={episode.source === "scripted" ? "info" : "neutral"}>{episode.source}</Badge></td>
                    <td className="py-2">{episode.task}</td>
                    <td className="py-2">{episode.recorded_success === true ? "Success" : episode.recorded_success === false ? "Failure" : "Unknown"}</td>
                    <td className="py-2">{episode.quality ?? "—"}</td>
                    <td className="py-2 text-right">{episode.length.toLocaleString()}</td>
                    <td className="py-2 text-right"><Button variant="ghost" onClick={() => removeSelectedEpisode(episode.episode_id)}>Remove</Button></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="mt-3"><Button variant="subtle" onClick={() => { setSelectedEpisodes([]); clearConvertSelection(); }}>Clear manual selection</Button></div>
        </Card>
      )}

      <Card title="Conversion settings" subtitle="One task per dataset keeps environment metadata and observations consistent.">
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          <Field label="Dataset name" hint="Used as the immutable export name">
            <Input value={name} onChange={(event) => setName(event.target.value)} />
          </Field>
          <Field label="Output format">
            <Select value={format} onChange={(event) => setFormat(event.target.value)}>
              <option value="robomimic">RoboMimic (HDF5)</option>
              <option disabled>LeRobot (planned)</option>
              <option disabled>RLDS (planned)</option>
            </Select>
          </Field>
          <Field label="Task" hint="Required for RoboMimic training">
            <Select value={taskFilter} onChange={(event) => setTaskFilter(event.target.value)}>
              <option value="">Select a task…</option>
              {tasks.map((task) => (
                <option key={task.task} value={task.task}>{task.tool_label ?? task.task}</option>
              ))}
            </Select>
          </Field>
          <Field label="Data source" hint="Convert one source or mix both">
            <Select
              value={dataSource}
              onChange={(event) => setDataSource(event.target.value as typeof dataSource)}
            >
              <option value="teleop">Teleop only</option>
              <option value="scripted">Scripted only</option>
              <option value="both">Teleop + Scripted</option>
            </Select>
          </Field>
          <Field
            label="Collection batch"
            hint={selectedEpisodes.length > 0
              ? "Read from the selected episodes; conversion uses their exact episode IDs."
              : "Required when selecting scripted episodes by filters."}
          >
            <Input
              value={selectedEpisodes.length > 0 ? selectedBatchDisplay : batchId}
              disabled={selectedEpisodes.length > 0 || dataSource === "teleop"}
              placeholder={dataSource === "teleop" ? "Not applicable" : "Example: lift-e2e-test-v1"}
              onChange={(event) => setBatchId(event.target.value)}
            />
          </Field>
          <div className="flex flex-col justify-end gap-2 pb-1 text-xs">
            <label className="flex items-center gap-2">
              <input
                type="checkbox"
                checked={includeFailures}
                onChange={(event) => setIncludeFailures(event.target.checked)}
              />
              Include approved failures
            </label>
            <label className="flex items-center gap-2">
              <input
                type="checkbox"
                checked={overwrite}
                onChange={(event) => setOverwrite(event.target.checked)}
              />
              Replace an export with the same name
            </label>
          </div>
        </div>

        <div className="mt-4 flex flex-wrap items-center gap-3">
          <Button
            variant="primary"
            disabled={busy || incompatibleSelection || !name.trim() || !taskFilter || (selectedEpisodes.length === 0 && dataSource !== "teleop" && !batchId.trim())}
            onClick={() => void createExport()}
          >
            {busy ? "Converting…" : "Convert dataset"}
          </Button>
          {dvc && (
            <Badge tone={dvc.available ? "ok" : "neutral"}>
              DVC {dvc.available ? "tracking enabled" : (dvc.reason ?? "unavailable")}
            </Badge>
          )}
        </div>
        <p className="mt-3 text-xs text-ink-400">
          Reviewer trims are applied during conversion. Raw recordings are never modified.
        </p>
      </Card>

      {error && <Alert>{error}</Alert>}
      {info && <Alert tone={result?.status === "ready" ? "ok" : "info"}>{info}</Alert>}

      {result && (
        <Card title="Conversion result">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <div className="font-medium text-ink-100">{result.name}</div>
              <div className="mt-1 text-xs text-ink-400">
                {result.status} · {result.num_episodes} episodes · {result.num_frames.toLocaleString()} frames
              </div>
            </div>
            <div className="flex gap-2">
              <Link href={`/datasets/${result.id}`}><Button variant="primary">Open dataset</Button></Link>
              <Link href="/datasets"><Button variant="subtle">All datasets</Button></Link>
            </div>
          </div>
        </Card>
      )}

      {!result && (
        <Card title="What gets converted?">
          <p className="text-sm text-ink-300">
            The converter selects reviewed episodes that match the task, source, batch, and outcome settings above.
            The generated artifact records its episode inventory and observation schema for traceability.
          </p>
        </Card>
      )}
    </div>
  );
}
