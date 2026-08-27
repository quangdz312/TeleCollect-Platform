"use client";

import { notFound, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { useAuth } from "@/components/AuthProvider";
import { COLLECTION_ENABLED } from "@/lib/features";
import { Alert, Button, Card, Dropzone, Field, Select } from "@/components/ui";
import { ApiError, api, type Task } from "@/lib/api";

const VIDEO_ACCEPT = "video/mp4,video/quicktime,.mp4,.mov";

/** Maps a failed upload to a user-facing message — 413/422/404 bodies come
 * straight from `src/api/demos.py` (`detail` string). */
function uploadErrorMessage(exc: unknown): string {
  if (exc instanceof ApiError) {
    if (exc.status === 413) return `File exceeds the size limit: ${exc.message}`;
    if (exc.status === 422) return exc.message;
    if (exc.status === 404) {
      return "The selected task no longer exists — reload the task list and pick again.";
    }
    if (exc.status === 0) return "Could not reach the server — check the network and try again.";
    return exc.message;
  }
  return "Upload failed";
}

export default function UploadPage() {
  // Collection lives in the packaged app, not the deployed web build. The
  // backend drops `/teleop` and `/demos` under the same flag, so without this
  // the page would render and then fail on every request.
  if (!COLLECTION_ENABLED) notFound();
  const { user } = useAuth();
  const router = useRouter();

  const [tasks, setTasks] = useState<Task[]>([]);
  const [taskId, setTaskId] = useState("");
  const [front, setFront] = useState<File | null>(null);
  const [wrist, setWrist] = useState<File | null>(null);
  const [trajectory, setTrajectory] = useState<File | null>(null);

  const [busy, setBusy] = useState(false);
  const [progress, setProgress] = useState(0);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!user) return;
    void api.tasks().then((loaded) => {
      setTasks(loaded);
      setTaskId((current) => current || loaded[0]?.id || "");
    });
  }, [user]);

  if (!user) return null;

  const canSubmit = !busy && taskId !== "" && front !== null;

  async function submit() {
    if (!front) return;
    setBusy(true);
    setProgress(0);
    setError(null);
    try {
      const demo = await api.uploadDemo({
        taskName: taskId,
        front,
        wrist,
        trajectory,
        onProgress: setProgress,
      });
      router.push(`/raw/${encodeURIComponent(demo.id)}`);
    } catch (exc) {
      setError(uploadErrorMessage(exc));
      setBusy(false);
    }
  }

  return (
    <div className="space-y-5">
      <div>
        <h1 className="font-heading text-[22px] font-bold tracking-tight">Upload demo</h1>
        <p className="mt-0.5 text-sm text-ink-400">
          Add a recording captured elsewhere (a real robot, for instance). The demo enters the review queue right away
          sau khi upload xong.
        </p>
      </div>

      <Card title="New upload">
        <Field label="Task" className="mb-4 max-w-xs">
          <Select value={taskId} onChange={(e) => setTaskId(e.target.value)} disabled={busy}>
            {tasks.length === 0 && <option value="">Loading tasks…</option>}
            {tasks.map((task) => (
              <option key={task.id} value={task.id}>
                {task.title}
              </option>
            ))}
          </Select>
        </Field>

        <div className="grid gap-3 sm:grid-cols-3">
          <Dropzone
            required
            icon={
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75">
                <path d="M15 8l6-3v14l-6-3" />
                <rect x="1" y="5" width="14" height="14" rx="2" />
              </svg>
            }
            title="Front camera"
            tag="Required · .mp4 / .mov"
            fileName={front?.name}
            inputProps={{
              accept: VIDEO_ACCEPT,
              disabled: busy,
              onChange: (e) => setFront(e.target.files?.[0] ?? null),
            }}
          />
          <Dropzone
            icon={
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75">
                <path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7-10-7-10-7Z" />
                <circle cx="12" cy="12" r="3" />
              </svg>
            }
            title="Wrist camera"
            tag="Optional · .mp4 / .mov"
            fileName={wrist?.name}
            inputProps={{
              accept: VIDEO_ACCEPT,
              disabled: busy,
              onChange: (e) => setWrist(e.target.files?.[0] ?? null),
            }}
          />
          <Dropzone
            icon={
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75">
                <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8Z" />
                <path d="M14 2v6h6" />
              </svg>
            }
            title="Trajectory"
            tag="Optional · .json"
            fileName={trajectory?.name}
            inputProps={{
              accept: "application/json",
              disabled: busy,
              onChange: (e) => setTrajectory(e.target.files?.[0] ?? null),
            }}
          />
        </div>

        <div className="mt-5 flex flex-wrap items-center gap-3">
          <Button variant="primary" disabled={!canSubmit} onClick={submit}>
            {busy ? `Uploading… ${progress}%` : "Upload"}
          </Button>
        </div>

        {busy && (
          <div className="mt-3 h-1.5 w-full overflow-hidden rounded-full bg-ink-700">
            <div
              className="h-full rounded-full bg-accent-500 transition-[width]"
              style={{ width: `${progress}%` }}
            />
          </div>
        )}

        {error && (
          <div className="mt-3">
            <Alert>{error}</Alert>
          </div>
        )}
      </Card>
    </div>
  );
}
