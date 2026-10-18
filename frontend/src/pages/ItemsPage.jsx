// Filterable list. Filters live in the URL (?status=open&q=refund) so a view can be bookmarked or shared.
// Filtering, search and pagination all happen on the server; the browser only ever holds the pages shown.
import { useSearchParams } from "react-router-dom";
import { useInfiniteQuery } from "@tanstack/react-query";
import { api, toQuery } from "../api";
import { useAuth } from "../auth";
import { PRIORITY, STATUS, TYPE } from "../labels";
import ItemTable from "../components/ItemTable";

const VIEWS = {
  "": "All my teams",
  assigned_to_me: "Assigned to me",
  unassigned_urgent: "Unassigned urgent",
  awaiting_my_approval: "Awaiting my approval",
};

function Select({ name, label, options, value, onChange }) {
  return (
    <label>
      {label}
      <select value={value} onChange={(e) => onChange(name, e.target.value)}>
        <option value="">Any</option>
        {Object.entries(options).map(([key, text]) => <option key={key} value={key}>{text}</option>)}
      </select>
    </label>
  );
}

export default function ItemsPage() {
  const { user } = useAuth();
  const [searchParams, setSearchParams] = useSearchParams();
  const filters = {
    view: searchParams.get("view") ?? "",
    team_id: searchParams.get("team_id") ?? "",
    status: searchParams.get("status") ?? "",
    priority: searchParams.get("priority") ?? "",
    type: searchParams.get("type") ?? "",
    assignee: searchParams.get("assignee") ?? "",
    q: searchParams.get("q") ?? "",
  };

  function setFilter(name, value) {
    const next = new URLSearchParams(searchParams);
    if (value) next.set(name, value);
    else next.delete(name);
    setSearchParams(next);
  }

  // Keyset pagination: each page returns next_cursor; "Load more" asks for the page after it.
  const query = useInfiniteQuery({
    queryKey: ["items", filters],
    queryFn: ({ pageParam }) => api(`/items${toQuery({ ...filters, limit: 25, cursor: pageParam })}`),
    initialPageParam: null,
    getNextPageParam: (lastPage) => lastPage.next_cursor ?? undefined,
  });
  const items = query.data?.pages.flatMap((page) => page.items) ?? [];
  const teams = Object.fromEntries(user.teams.map((t) => [t.id, t.name]));

  return (
    <>
      <h1>Work items</h1>
      <form
        className="filters card"
        onSubmit={(e) => {
          e.preventDefault();
          setFilter("q", new FormData(e.target).get("q").trim());
        }}
      >
        <label>
          View
          <select value={filters.view} onChange={(e) => setFilter("view", e.target.value)}>
            {Object.entries(VIEWS).map(([key, text]) => <option key={key} value={key}>{text}</option>)}
          </select>
        </label>
        <Select name="team_id" label="Team" options={teams} value={filters.team_id} onChange={setFilter} />
        <Select name="status" label="Status" options={STATUS} value={filters.status} onChange={setFilter} />
        <Select name="priority" label="Priority" options={PRIORITY} value={filters.priority} onChange={setFilter} />
        <Select name="type" label="Type" options={TYPE} value={filters.type} onChange={setFilter} />
        <Select name="assignee" label="Assignee" options={{ me: "Me", none: "Unassigned" }}
                value={filters.assignee} onChange={setFilter} />
        <label className="grow">
          Search
          <input name="q" key={filters.q} defaultValue={filters.q} placeholder="e.g. refund merchant" />
        </label>
        <button>Search</button>
      </form>

      {query.isLoading && <p className="muted">Loading…</p>}
      {query.error && <p className="error">{query.error.message}</p>}
      {query.data && <ItemTable items={items} />}
      {query.hasNextPage && (
        <button className="more" onClick={() => query.fetchNextPage()} disabled={query.isFetchingNextPage}>
          {query.isFetchingNextPage ? "Loading…" : "Load more"}
        </button>
      )}
    </>
  );
}
