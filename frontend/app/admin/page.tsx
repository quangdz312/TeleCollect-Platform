"use client";

import { useCallback, useEffect, useRef, useState } from "react";
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
  Skeleton,
} from "@/components/ui";
import { useToast } from "@/components/Toast";
import { api, type Role, type User } from "@/lib/api";
import { timeAgo } from "@/lib/format";

const ROLE_HELP: Record<Role, string> = {
  operator: "Drives the robot, records demonstrations, sees only their own recordings.",
  reviewer: "Cannot drive the robot; trims, labels, approves, exports and trains.",
  admin: "Everything, plus user management.",
};

export default function AdminPage() {
  const { user } = useAuth();
  const toast = useToast();
  const [users, setUsers] = useState<User[]>([]);
  const [form, setForm] = useState({
    username: "",
    password: "",
    display_name: "",
    role: "operator" as Role,
  });
  const [error, setError] = useState<string | null>(null);
  const [tableError, setTableError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [usersLoading, setUsersLoading] = useState(true);
  const rowLocks = useRef(new Set<string>());
  const [lockedIds, setLockedIds] = useState<Set<string>>(new Set());

  const load = useCallback(async () => {
    try {
      setUsers(await api.users());
    } finally {
      setUsersLoading(false);
    }
  }, []);

  async function withRowLock(id: string, action: () => Promise<void>) {
    if (rowLocks.current.has(id)) return;
    rowLocks.current.add(id);
    setLockedIds(new Set(rowLocks.current));
    setTableError(null);
    try {
      await action();
    } catch (exc) {
      setTableError(exc instanceof Error ? exc.message : "Failed");
    } finally {
      rowLocks.current.delete(id);
      setLockedIds(new Set(rowLocks.current));
    }
  }

  async function updateRole(id: string, role: Role) {
    await withRowLock(id, async () => {
      await api.updateUser(id, { role });
      await load();
    });
  }

  async function toggleActive(item: User) {
    await withRowLock(item.id, async () => {
      await api.updateUser(item.id, { is_active: !item.is_active });
      await load();
    });
  }

  useEffect(() => {
    if (user?.role !== "admin") return;
    void load().catch(() => undefined);
  }, [user, load]);

  if (!user) return null;
  if (user.role !== "admin") return <Alert tone="info">Administrators only.</Alert>;

  // Self-registration creates the account disabled, so anyone still inactive is
  // either waiting to be let in or was disabled on purpose — both need the same
  // button, and surfacing them here saves hunting through the whole table.
  const pending = users.filter((item) => !item.is_active);

  return (
    <div className="space-y-5">
      <div>
        <h1 className="font-heading text-[22px] font-bold tracking-tight">Users and roles</h1>
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

      {pending.length > 0 && (
        <Card title={`Waiting for approval (${pending.length})`}>
          <div className="space-y-2">
            {pending.map((item) => (
              <div
                key={item.id}
                className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-ink-700/50 p-3"
                aria-busy={lockedIds.has(item.id)}
              >
                <div>
                  <span className="font-medium">{item.username}</span>
                  <div className="mt-0.5 text-xs text-ink-400">
                    {item.display_name} · signed up {timeAgo(item.created_at)}
                  </div>
                </div>
                <Button
                  variant="primary"
                  disabled={lockedIds.has(item.id)}
                  onClick={() => toggleActive(item)}
                >
                  {lockedIds.has(item.id) ? "Approving…" : "Approve"}
                </Button>
              </div>
            ))}
          </div>
        </Card>
      )}

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
          <Field label="Password" hint="At least 8 characters">
            <Input
              type="password"
              value={form.password}
              onChange={(e) => setForm({ ...form, password: e.target.value })}
              minLength={8}
              maxLength={72}
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
            disabled={busy || !form.username || form.password.length < 8}
            onClick={async () => {
              setBusy(true);
              setError(null);
              try {
                await api.createUser(form);
                setForm({ username: "", password: "", display_name: "", role: "operator" });
                await load();
                toast(`User "${form.username}" created`, "ok");
              } catch (exc) {
                setError(exc instanceof Error ? exc.message : "Failed");
              } finally {
                setBusy(false);
              }
            }}
          >
            {busy ? "Creating…" : "Create user"}
          </Button>
        </div>
        {error && (
          <div className="mt-3">
            <Alert>{error}</Alert>
          </div>
        )}
      </Card>

      <Card title="All users">
        {tableError && (
          <div className="mb-3">
            <Alert>{tableError}</Alert>
          </div>
        )}
        {usersLoading ? (
          <div className="space-y-2" aria-busy="true">
            <Skeleton className="h-9 w-full" />
            <Skeleton className="h-9 w-full" />
            <Skeleton className="h-9 w-full" />
          </div>
        ) : users.length === 0 ? (
          <Empty>No users.</Empty>
        ) : (
          <>
            <div className="space-y-2 sm:hidden">
              {users.map((item) => (
                <div key={item.id} className="rounded-lg border border-ink-700/50 p-3">
                  <div className="flex items-center justify-between gap-2">
                    <span className="font-medium">{item.username}</span>
                    <Badge tone={item.role === "admin" ? "info" : item.role === "reviewer" ? "warn" : "ok"}>
                      {item.role}
                    </Badge>
                  </div>
                  <div className="mt-0.5 text-xs text-ink-400">
                    {item.display_name} · {timeAgo(item.created_at)}
                  </div>
                  <div className="mt-2 flex flex-wrap items-center gap-2" aria-busy={lockedIds.has(item.id)}>
                    <Select
                      className="w-36"
                      value={item.role}
                      disabled={item.id === user.id || lockedIds.has(item.id)}
                      onChange={(e) => updateRole(item.id, e.target.value as Role)}
                    >
                      <option value="operator">operator</option>
                      <option value="reviewer">reviewer</option>
                      <option value="admin">admin</option>
                    </Select>
                    <Button
                      variant={item.is_active ? "ghost" : "subtle"}
                      disabled={item.id === user.id || lockedIds.has(item.id)}
                      onClick={() => toggleActive(item)}
                    >
                      {lockedIds.has(item.id) ? "Updating…" : item.is_active ? "Disable" : "Enable"}
                    </Button>
                  </div>
                </div>
              ))}
            </div>

            <div className="hidden overflow-x-auto sm:block">
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
                      <td className="py-2" aria-busy={lockedIds.has(item.id)}>
                        <Select
                          className="w-36"
                          value={item.role}
                          disabled={item.id === user.id || lockedIds.has(item.id)}
                          onChange={(e) => updateRole(item.id, e.target.value as Role)}
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
                          disabled={item.id === user.id || lockedIds.has(item.id)}
                          onClick={() => toggleActive(item)}
                        >
                          {lockedIds.has(item.id) ? "Updating…" : item.is_active ? "Disable" : "Enable"}
                        </Button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        )}
      </Card>
    </div>
  );
}
