import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, newIdempotencyKey } from "../api";
import { useAuth } from "../auth";
import { PRIORITY, TYPE } from "../labels";

export default function NewItemPage() {
  const { user } = useAuth();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const teams = user.teams.filter((t) => t.role !== "viewer");
  const [form, setForm] = useState({
    team_id: teams[0]?.id ?? "",
    title: "",
    description: "",
    type: "incident",
    priority: "medium",
    due_date: "",
    assignee_id: "",
  });
  // One key for this form. A double-click or a retry after a network error sends the
  // same key, so the server creates the item only once.
  const [idempotencyKey] = useState(newIdempotencyKey);

  const role = teams.find((t) => t.id === Number(form.team_id))?.role;
  const isLead = role === "lead" || role === "admin";
  const members = useQuery({
    queryKey: ["members", form.team_id],
    queryFn: () => api(`/teams/${form.team_id}/members`),
    enabled: isLead,
  });

  const create = useMutation({
    mutationFn: () =>
      api("/items", {
        method: "POST",
        idempotencyKey,
        body: {
          ...form,
          team_id: Number(form.team_id),
          due_date: form.due_date || null,
          assignee_id: form.assignee_id ? Number(form.assignee_id) : null,
        },
      }),
    onSuccess: (item) => {
      queryClient.invalidateQueries({ queryKey: ["items"] });
      navigate(`/items/${item.id}`);
    },
  });

  const set = (name) => (e) => setForm({ ...form, [name]: e.target.value });

  if (teams.length === 0) return <p>You are a viewer in all your teams, so you cannot create items.</p>;

  return (
    <form className="card form" onSubmit={(e) => { e.preventDefault(); create.mutate(); }}>
      <h1>New work item</h1>
      <label>Team
        <select value={form.team_id} onChange={(e) => setForm({ ...form, team_id: e.target.value, assignee_id: "" })}>
          {teams.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
        </select>
      </label>
      <label>Title<input value={form.title} onChange={set("title")} maxLength={200} required /></label>
      <label>Description<textarea value={form.description} onChange={set("description")} rows={4} /></label>
      <div className="row">
        <label>Type
          <select value={form.type} onChange={set("type")}>
            {Object.entries(TYPE).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </select>
        </label>
        <label>Priority
          <select value={form.priority} onChange={set("priority")}>
            {Object.entries(PRIORITY).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </select>
        </label>
        <label>Due date<input type="date" value={form.due_date} onChange={set("due_date")} /></label>
        <label>Assignee
          <select value={form.assignee_id} onChange={set("assignee_id")}>
            <option value="">Unassigned</option>
            {isLead
              ? members.data?.filter((m) => m.role !== "viewer").map((m) => <option key={m.id} value={m.id}>{m.name}</option>)
              : <option value={user.id}>Me</option>}
          </select>
        </label>
      </div>
      {create.error && <p className="error">{create.error.message}</p>}
      <button disabled={create.isPending}>{create.isPending ? "Creating…" : "Create item"}</button>
    </form>
  );
}
