import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api, toQuery } from "../api";
import { formatDate } from "../labels";
import ItemTable from "../components/ItemTable";

const PANELS = [
  { view: "assigned_to_me", title: "Assigned to me" },
  { view: "unassigned_urgent", title: "Unassigned urgent" },
  { view: "awaiting_my_approval", title: "Awaiting my approval" },
];

// Poll every 15 s instead of using websockets: simple, and good enough for this app.
const REFRESH_MS = 15_000;

function Panel({ view, title }) {
  const params = { view, limit: 10 };
  const { data, isLoading, error } = useQuery({
    queryKey: ["items", params],
    queryFn: () => api(`/items${toQuery(params)}`),
    refetchInterval: REFRESH_MS,
  });
  return (
    <section className="card">
      <h2>
        {title} <Link className="small" to={`/items?view=${view}`}>see all →</Link>
      </h2>
      {isLoading && <p className="muted">Loading…</p>}
      {error && <p className="error">{error.message}</p>}
      {data && <ItemTable items={data.items} />}
    </section>
  );
}

function Notifications() {
  const { data } = useQuery({
    queryKey: ["notifications"],
    queryFn: () => api("/notifications"),
    refetchInterval: REFRESH_MS,
  });
  if (!data?.length) return null;
  return (
    <section className="card">
      <h2>Notifications</h2>
      <ul className="notifications">
        {data.map((n) => (
          <li key={n.id}>
            <Link to={`/items/${n.item_id}`}>{n.message}</Link>
            <span className="muted small"> · {formatDate(n.created_at)}</span>
          </li>
        ))}
      </ul>
    </section>
  );
}

export default function DashboardPage() {
  return (
    <>
      <h1>Needs attention</h1>
      {PANELS.map((panel) => <Panel key={panel.view} {...panel} />)}
      <Notifications />
    </>
  );
}
