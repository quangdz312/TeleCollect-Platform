"use client";

import { useEffect, useState } from "react";
import { useAuth } from "@/components/AuthProvider";
import { ScriptedCollector } from "@/components/ScriptedCollector";
import { TeleopConsole } from "@/components/TeleopConsole";
import { Alert, Empty } from "@/components/ui";
import { api, type Task } from "@/lib/api";

type Mode = "manual" | "scripted";

export default function CollectPage() {
  const { user } = useAuth();
  const [mode, setMode] = useState<Mode>("manual");
  const [tasks, setTasks] = useState<Task[] | null>(null);

  useEffect(() => {
    if (!user || user.role === "reviewer") return;
    void api.teleopTasks().then(setTasks).catch(() => setTasks([]));
  }, [user]);

  if (!user) return null;
  if (user.role === "reviewer") return <ScriptedCollector />;

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-lg font-semibold">Thu data</h1>
        <p className="mt-0.5 text-sm text-ink-400">Chọn thu tay hoặc thu tự động. Episode đã lưu sẽ xuất hiện trong Review.</p>
      </div>
      <div className="flex gap-2 rounded-lg border border-ink-700 bg-ink-900/60 p-1">
        <button className={`rounded-md px-4 py-2 text-sm ${mode === "manual" ? "bg-accent-500 text-white" : "text-ink-300"}`} onClick={() => setMode("manual")}>Thu tay</button>
        <button className={`rounded-md px-4 py-2 text-sm ${mode === "scripted" ? "bg-accent-500 text-white" : "text-ink-300"}`} onClick={() => setMode("scripted")}>Thu tự động</button>
      </div>
      {mode === "scripted" ? <ScriptedCollector /> : !tasks ? <Empty>Đang tải task…</Empty> : tasks.length === 0 ? <Alert>Không có simulator task.</Alert> : <TeleopConsole tasks={tasks} />}
    </div>
  );
}
