"use client";

import { useCallback, useEffect, useState } from "react";
import { useAuth } from "@/components/AuthProvider";
import {
  Alert,
  Badge,
  Button,
  Card,
  Empty,
  Field,
  Input,
  Select,
} from "@/components/ui";
import { api, type Role, type User } from "@/lib/api";
import { timeAgo } from "@/lib/format";

const ROLE_HELP: Record<Role, string> = {
  operator: "Drives the robot, records demonstrations, sees only their own recordings.",
  reviewer: "Cannot drive the robot; trims, labels, approves, exports and trains.",
  admin: "Everything, plus user management.",
};

export default function AdminPage() {
  const { user } = useAuth();
  const [users, setUsers] = useState<User[]>([]);
  const [form, setForm] = useState({
    username: "",
    password: "",
    display_name: "",
    role: "operator" as Role,
  });
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    setUsers(await api.users());
  }, []);

  useEffect(() => {
    if (user?.role !== "admin") return;
    void load().catch(() => undefined);
  }, [user, load]);

  if (!user) return null;
  if (user.role !== "admin") return <Alert tone="info">Administrators only.</Alert>;

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-lg font-semibold">Users and roles</h1>
        <p className="mt-0.5 text-sm text-ink-400">
          The operator/reviewer split is what makes the review step meaningful: the person who
          approves a demonstration is not the person who recorded it.
        </p>
      </div>

      <div className="grid gap-3 sm:grid-cols-3">
        {(Object.keys(ROLE_HELP) as Role[]).map((role) => (
          <div key={role} className="rounded-lg border border-ink-700/60 bg-ink-850/50 p-4">
            <Badge tone={role === "admin" ? "info" : role === "reviewer" ? "warn" : "ok"}>
              {role}
            </Badge>
            <p className="mt-2 text-xs text-ink-300">{ROLE_HELP[role]}</p>
          </div>
        ))}
      </div>

      <Card title="Add a user">
        <div className="grid gap-3 sm:grid-cols-4">
          <Field label="Username">
            <Input
              value={form.username}
              onChange={(e) => setForm({ ...form, username: e.target.value })}
            />
          </Field>
          <Field label="Display name">
            <Input
              value={form.display_name}
              onChange={(e) => setForm({ ...form, display_name: e.target.value })}
            />
          </Field>
          <Field label="Password">
            <Input
              type="password"
              value={form.password}
              onChange={(e) => setForm({ ...form, password: e.target.value })}
            />
          </Field>
          <Field label="Role">
            <Select
              value={form.role}
              onChange={(e) => setForm({ ...form, role: e.target.value as Role })}
            >
              <option value="operator">operator</option>
              <option value="reviewer">reviewer</option>
              <option value="admin">admin</option>
            </Select>
          </Field>
        </div>
        <div className="mt-4">
          <Button
            variant="primary"
            disabled={busy || !form.username || form.password.length < 4}
            onClick={async () => {
              setBusy(true);
              setError(null);
              try {
                await api.createUser(form);
                setForm({ username: "", password: "", display_name: "", role: "operator" });
                await load();
              } catch (exc) {
                setError(exc instanceof Error ? exc.message : "Failed");
              } finally {
                setBusy(false);
              }
            }}
          >
            Create user
          </Button>
        </div>
        {error && (
          <div className="mt-3">
            <Alert>{error}</Alert>
          </div>
        )}
      </Card>

      <Card title="All users">
        {users.length === 0 ? (
          <Empty>No users.</Empty>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="text-left text-xs uppercase tracking-wider text-ink-400">
                <tr>
                  <th className="pb-2">Username</th>
                  <th className="pb-2">Display name</th>
                  <th className="pb-2">Role</th>
                  <th className="pb-2">Created</th>
                  <th className="pb-2">Active</th>
                </tr>
              </thead>
              <tbody>
                {users.map((item) => (
                  <tr key={item.id} className="border-t border-ink-700/50">
                    <td className="py-2 font-medium">{item.username}</td>
                    <td className="py-2 text-ink-300">{item.display_name}</td>
                    <td className="py-2">
                      <Select
                        className="w-36"
                        value={item.role}
                        disabled={item.id === user.id}
                        onChange={async (e) => {
                          await api.updateUser(item.id, { role: e.target.value as Role });
                          await load();
                        }}
                      >
                        <option value="operator">operator</option>
                        <option value="reviewer">reviewer</option>
                        <option value="admin">admin</option>
                      </Select>
                    </td>
                    <td className="py-2 text-xs text-ink-400">{timeAgo(item.created_at)}</td>
                    <td className="py-2">
                      <Button
                        variant={item.is_active ? "ghost" : "subtle"}
                        disabled={item.id === user.id}
                        onClick={async () => {
                          await api.updateUser(item.id, { is_active: !item.is_active });
                          await load();
                        }}
                      >
                        {item.is_active ? "Disable" : "Enable"}
                      </Button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  );
}
