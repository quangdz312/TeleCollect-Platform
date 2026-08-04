"use client";

import { useEffect, useState } from "react";
import { useAuth } from "@/components/AuthProvider";
import { TeleopConsole } from "@/components/TeleopConsole";
import { Alert, Empty } from "@/components/ui";
import { api, type Task } from "@/lib/api";

export default function TeleopPage() {
  const { user } = useAuth();
  const [tasks, setTasks] = useState<Task[] | null>(null);

  useEffect(() => {
    if (!user) return;
    void api.tasks().then(setTasks).catch(() => setTasks([]));
  }, [user]);

  if (!user) return null;
  if (user.role === "reviewer") {
    return (
      <Alert tone="info">
        Reviewers do not drive the robot. This separation is deliberate: the person who
        approves a demonstration should not be the person who recorded it.
      </Alert>
    );
  }
  if (!tasks) return <Empty>Loading tasks…</Empty>;
  if (tasks.length === 0) return <Empty>No tasks are configured.</Empty>;

  return <TeleopConsole tasks={tasks} />;
}
