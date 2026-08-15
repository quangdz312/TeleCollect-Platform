"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { useAuth } from "@/components/AuthProvider";
import { Alert, Button, Card, Field, Select } from "@/components/ui";
import { ApiError, api, type Task } from "@/lib/api";

const VIDEO_ACCEPT = "video/mp4,video/quicktime,.mp4,.mov";

/** Maps a failed upload to a user-facing message — 413/422/404 bodies come
 * straight from `src/api/demos.py` (`detail` string). */
function uploadErrorMessage(exc: unknown): string {
  if (exc instanceof ApiError) {
    if (exc.status === 413) return `File vượt quá dung lượng cho phép: ${exc.message}`;
    if (exc.status === 422) return exc.message;
    if (exc.status === 404) {
      return "Task đã chọn không còn tồn tại — tải lại danh sách task và chọn lại.";
    }
    if (exc.status === 0) return "Không kết nối được máy chủ — kiểm tra mạng rồi thử lại.";
    return exc.message;
  }
  return "Upload thất bại";
}

export default function UploadPage() {
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
      router.push(`/review/${demo.id}`);
    } catch (exc) {
      setError(uploadErrorMessage(exc));
      setBusy(false);
    }
  }

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-lg font-semibold">Upload demo</h1>
        <p className="mt-0.5 text-sm text-ink-400">
          Thêm một bản ghi thu ở nơi khác (ví dụ robot thật). Demo sẽ vào hàng đợi review ngay
          sau khi upload xong.
        </p>
      </div>

      <Card title="Upload mới">
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Task">
            <Select value={taskId} onChange={(e) => setTaskId(e.target.value)} disabled={busy}>
              {tasks.length === 0 && <option value="">Đang tải task…</option>}
              {tasks.map((task) => (
                <option key={task.id} value={task.id}>
                  {task.title}
                </option>
              ))}
            </Select>
          </Field>

          <Field label="Front camera" hint="Bắt buộc — .mp4 hoặc .mov">
            <input
              type="file"
              accept={VIDEO_ACCEPT}
              disabled={busy}
              onChange={(e) => setFront(e.target.files?.[0] ?? null)}
              className="block w-full text-sm text-ink-300 file:mr-3 file:rounded-lg file:border file:border-ink-600 file:bg-ink-850 file:px-3 file:py-1.5 file:text-sm file:text-ink-100 hover:file:bg-ink-800"
            />
          </Field>

          <Field label="Wrist camera" hint="Không bắt buộc — .mp4 hoặc .mov">
            <input
              type="file"
              accept={VIDEO_ACCEPT}
              disabled={busy}
              onChange={(e) => setWrist(e.target.files?.[0] ?? null)}
              className="block w-full text-sm text-ink-300 file:mr-3 file:rounded-lg file:border file:border-ink-600 file:bg-ink-850 file:px-3 file:py-1.5 file:text-sm file:text-ink-100 hover:file:bg-ink-800"
            />
          </Field>

          <Field label="Trajectory" hint="Không bắt buộc — .json">
            <input
              type="file"
              accept="application/json"
              disabled={busy}
              onChange={(e) => setTrajectory(e.target.files?.[0] ?? null)}
              className="block w-full text-sm text-ink-300 file:mr-3 file:rounded-lg file:border file:border-ink-600 file:bg-ink-850 file:px-3 file:py-1.5 file:text-sm file:text-ink-100 hover:file:bg-ink-800"
            />
          </Field>
        </div>

        <div className="mt-4 flex flex-wrap items-center gap-3">
          <Button variant="primary" disabled={!canSubmit} onClick={submit}>
            {busy ? `Đang upload… ${progress}%` : "Upload"}
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
