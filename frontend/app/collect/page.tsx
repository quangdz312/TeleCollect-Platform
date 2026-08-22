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
        <h1 className="font-heading text-[22px] font-bold tracking-tight">Thu data</h1>
        <p className="mt-0.5 text-sm text-ink-400">Record by hand or generate scripted episodes. Saved episodes appear in Review.</p>
      </div>
      <div className="flex w-fit gap-1 rounded-lg border border-ink-700 bg-ink-850 p-1">
        <button
          className={`rounded-[7px] px-4 py-2 text-sm font-semibold transition-all ${mode === "manual" ? "bg-ink-900 text-accent-500 shadow-[0_1px_2px_rgba(15,23,42,0.04)]" : "text-ink-300 hover:text-accent-500"}`}
          onClick={() => setMode("manual")}
        >
          Manual
        </button>
        <button
          className={`rounded-[7px] px-4 py-2 text-sm font-semibold transition-all ${mode === "scripted" ? "bg-ink-900 text-accent-500 shadow-[0_1px_2px_rgba(15,23,42,0.04)]" : "text-ink-300 hover:text-accent-500"}`}
          onClick={() => setMode("scripted")}
        >
          Scripted
        </button>
      </div>
      {mode === "scripted" ? <ScriptedCollector /> : !tasks ? <Empty>Loading tasks…</Empty> : tasks.length === 0 ? <Alert>No simulator task available.</Alert> : <TeleopConsole tasks={tasks} />}
    </div>
  );
}
