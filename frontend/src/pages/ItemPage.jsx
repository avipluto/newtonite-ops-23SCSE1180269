// Item detail: fields, actions (claim / status changes / edit / assign), activity timeline and comments.
import { useState } from "react";
import { useParams } from "react-router-dom";
import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, newIdempotencyKey } from "../api";
import { useAuth } from "../auth";
import { PRIORITY, STATUS, TYPE, formatDate } from "../labels";

const REFRESH_MS = 10_000;

export default function ItemPage() {
  const { id } = useParams();
  const item = useQuery({
    queryKey: ["item", id],
    queryFn: () => api(`/items/${id}`),
    refetchInterval: REFRESH_MS,
  });

  if (item.isLoading) return <p className="muted">Loading…</p>;
  if (item.error) return <p className="error">{item.error.message}</p>;
  return (
    <div className="detail">
      <div>
        <ItemSummary item={item.data} />
        <Activity id={id} canComment={item.data.can.comment} />
      </div>
      <aside>
        <Actions item={item.data} />
      </aside>
    </div>
  );
}

function ItemSummary({ item }) {
  return (
    <section className="card">
      <h1>#{item.id} {item.title}</h1>
      <p className="meta">
        <span className={`badge s-${item.status}`}>{STATUS[item.status]}</span>
        <span className={`badge p-${item.priority}`}>{PRIORITY[item.priority]}</span>
        {TYPE[item.type]} · {item.team_name} · version {item.version}
      </p>
      <dl>
        <dt>Assignee</dt><dd>{item.assignee?.name ?? "Unassigned"}</dd>
        <dt>Created by</dt><dd>{item.created_by.name} on {formatDate(item.created_at)}</dd>
        <dt>Due</dt><dd>{item.due_date ?? "—"}</dd>
        <dt>Last update</dt><dd>{formatDate(item.updated_at)}</dd>
      </dl>
      {item.description && <p className="description">{item.description}</p>}
    </section>
  );
}

// ------------------------------------------------------------------ actions

function Actions({ item }) {
  const { user } = useAuth();
  const queryClient = useQueryClient();
  const [notice, setNotice] = useState(null);
  const [editing, setEditing] = useState(false);

  // Claim and status changes are optimistic: the page updates at once, and if the server
  // says no (409/403) we put back what the server says is true and show its reason.
  const action = useMutation({
    mutationFn: ({ request }) => request(),
    onMutate: async ({ optimistic }) => {
      setNotice(null);
      await queryClient.cancelQueries({ queryKey: ["item", String(item.id)] });
      const previous = queryClient.getQueryData(["item", String(item.id)]);
      queryClient.setQueryData(["item", String(item.id)], {
        ...previous,
        ...optimistic,
        can: { ...previous.can, claim: false, transitions: [] }, // no double actions while pending
      });
      return { previous };
    },
    onError: (error, _variables, context) => {
      // Roll back: to the server's latest copy if it sent one (409), else to what we had.
      queryClient.setQueryData(["item", String(item.id)], error.current ?? context.previous);
      setNotice({ kind: "error", text: error.message });
    },
    onSuccess: (updated) => queryClient.setQueryData(["item", String(item.id)], updated),
    onSettled: () => {
      queryClient.invalidateQueries({ queryKey: ["activity", String(item.id)] });
      queryClient.invalidateQueries({ queryKey: ["items"] });
    },
  });

  const claim = () =>
    action.mutate({
      optimistic: { assignee: { id: user.id, name: user.display_name } },
      request: () => api(`/items/${item.id}/claim`, { method: "POST", idempotencyKey: newIdempotencyKey() }),
    });

  const move = (toStatus) =>
    action.mutate({
      optimistic: { status: toStatus },
      request: () =>
        api(`/items/${item.id}/transition`, {
          method: "POST",
          idempotencyKey: newIdempotencyKey(),
          body: { to_status: toStatus, version: item.version },
        }),
    });

  return (
    <section className="card actions">
      <h2>Actions</h2>
      {notice && <p className={notice.kind}>{notice.text}</p>}
      {item.can.claim && <button onClick={claim}>Claim</button>}
      {item.can.transitions.map((t) => (
        <button key={t.to} onClick={() => move(t.to)}>{t.label}</button>
      ))}
      {item.can.edit && !editing && <button className="secondary" onClick={() => setEditing(true)}>Edit</button>}
      {!item.can.claim && item.can.transitions.length === 0 && !item.can.edit && (
        <p className="muted">You can view this item but not change it.</p>
      )}
      {editing && <EditForm item={item} onDone={() => setEditing(false)} />}
    </section>
  );
}

function EditForm({ item, onDone }) {
  const queryClient = useQueryClient();
  // Remember the version we started from. The server only accepts the save if the item
  // is still at this version (optimistic locking).
  const [baseVersion] = useState(item.version);
  const [draft, setDraft] = useState({
    title: item.title,
    description: item.description,
    type: item.type,
    priority: item.priority,
    due_date: item.due_date ?? "",
    assignee_id: item.assignee?.id ?? "",
  });
  const members = useQuery({
    queryKey: ["members", item.team_id],
    queryFn: () => api(`/teams/${item.team_id}/members`),
    enabled: item.can.assign,
  });

  const save = useMutation({
    mutationFn: () => {
      const changes = {};
      for (const [name, value] of Object.entries(draft)) {
        const original = name === "assignee_id" ? item.assignee?.id ?? "" : item[name] ?? "";
        if (String(value) === String(original)) continue; // send only what changed
        const clearable = name === "due_date" || name === "assignee_id"; // "" means "remove it"
        changes[name] = clearable && value === "" ? null : value;
      }
      if ("assignee_id" in changes && changes.assignee_id !== null) changes.assignee_id = Number(changes.assignee_id);
      return api(`/items/${item.id}`, { method: "PATCH", body: { version: baseVersion, ...changes } });
    },
    onSuccess: (updated) => {
      queryClient.setQueryData(["item", String(item.id)], updated);
      queryClient.invalidateQueries({ queryKey: ["activity", String(item.id)] });
      queryClient.invalidateQueries({ queryKey: ["items"] });
      onDone();
    },
  });

  const conflict = save.error?.code === "version_conflict";
  // Polling may bring in a newer version while we edit: warn before the user even presses Save.
  const changedUnderneath = item.version !== baseVersion;
  const set = (name) => (e) => setDraft({ ...draft, [name]: e.target.value });

  function reloadLatest() {
    queryClient.invalidateQueries({ queryKey: ["item", String(item.id)] });
    onDone();
  }

  return (
    <form className="form" onSubmit={(e) => { e.preventDefault(); save.mutate(); }}>
      {(conflict || changedUnderneath) && (
        <div className="warning">
          This item was changed by someone else since you started editing.
          {conflict && " Your changes were not saved."}{" "}
          <button type="button" className="link" onClick={reloadLatest}>Reload latest (discard my edits)</button>
        </div>
      )}
      <label>Title<input value={draft.title} onChange={set("title")} maxLength={200} required /></label>
      <label>Description<textarea value={draft.description} onChange={set("description")} rows={4} /></label>
      <label>Type
        <select value={draft.type} onChange={set("type")}>
          {Object.entries(TYPE).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
        </select>
      </label>
      <label>Priority
        <select value={draft.priority} onChange={set("priority")}>
          {Object.entries(PRIORITY).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
        </select>
      </label>
      <label>Due date<input type="date" value={draft.due_date} onChange={set("due_date")} /></label>
      {item.can.assign && (
        <label>Assignee
          <select value={draft.assignee_id} onChange={set("assignee_id")}>
            <option value="">Unassigned</option>
            {members.data?.filter((m) => m.role !== "viewer").map((m) => <option key={m.id} value={m.id}>{m.name}</option>)}
          </select>
        </label>
      )}
      {save.error && !conflict && <p className="error">{save.error.message}</p>}
      <div className="row">
        <button disabled={save.isPending || conflict}>{save.isPending ? "Saving…" : "Save"}</button>
        <button type="button" className="secondary" onClick={onDone}>Cancel</button>
      </div>
    </form>
  );
}

// ------------------------------------------------------------------ history

function describe(event) {
  if (event.type === "created") return "created the item";
  if (event.type === "commented") return "commented";
  if (event.field === "assignee") {
    if (!event.from && event.to === event.actor) return "claimed it";
    if (!event.from) return `assigned it to ${event.to}`;
    if (!event.to) return `unassigned ${event.from}`;
    return `reassigned it from ${event.from} to ${event.to}`;
  }
  const show = (value) => (event.field === "status" ? STATUS[value] : event.field === "priority" ? PRIORITY[value] : value);
  if (event.field === "description") return "edited the description";
  return `changed ${event.field.replace("_", " ")} from ${show(event.from) ?? "—"} to ${show(event.to) ?? "—"}`;
}

function Activity({ id, canComment }) {
  const history = useInfiniteQuery({
    queryKey: ["activity", id],
    queryFn: ({ pageParam }) => api(`/items/${id}/activity${pageParam ? `?before=${pageParam}` : ""}`),
    initialPageParam: null,
    getNextPageParam: (lastPage) => lastPage.next_before ?? undefined,
    refetchInterval: REFRESH_MS,
  });
  const events = history.data?.pages.flatMap((page) => page.events) ?? [];

  return (
    <section className="card">
      <h2>Activity</h2>
      {canComment && <CommentForm id={id} />}
      <ol className="timeline">
        {events.map((event) => (
          <li key={event.id}>
            <strong>{event.actor}</strong> {describe(event)}
            <span className="muted small"> · {formatDate(event.created_at)}</span>
            {event.comment && <blockquote>{event.comment}</blockquote>}
          </li>
        ))}
      </ol>
      {history.hasNextPage && (
        <button className="more secondary" onClick={() => history.fetchNextPage()}>Older activity</button>
      )}
    </section>
  );
}

function CommentForm({ id }) {
  const queryClient = useQueryClient();
  const [body, setBody] = useState("");
  const [key, setKey] = useState(newIdempotencyKey); // new key per comment, reused on retry
  const post = useMutation({
    mutationFn: () => api(`/items/${id}/comments`, { method: "POST", idempotencyKey: key, body: { body } }),
    onSuccess: () => {
      setBody("");
      setKey(newIdempotencyKey());
      queryClient.invalidateQueries({ queryKey: ["activity", id] });
    },
  });
  return (
    <form className="comment" onSubmit={(e) => { e.preventDefault(); post.mutate(); }}>
      <textarea value={body} onChange={(e) => setBody(e.target.value)} rows={2} placeholder="Add a comment…" required />
      {post.error && <p className="error">{post.error.message}</p>}
      <button disabled={post.isPending || !body.trim()}>{post.isPending ? "Posting…" : "Comment"}</button>
    </form>
  );
}
