import { Link } from "react-router-dom";
import { PRIORITY, STATUS, TYPE } from "../labels";

export default function ItemTable({ items, showTeam = true }) {
  if (items.length === 0) return <p className="muted">Nothing here.</p>;
  return (
    <table className="items">
      <thead>
        <tr>
          <th>#</th>
          <th>Title</th>
          {showTeam && <th>Team</th>}
          <th>Type</th>
          <th>Priority</th>
          <th>Status</th>
          <th>Assignee</th>
          <th>Due</th>
        </tr>
      </thead>
      <tbody>
        {items.map((item) => (
          <tr key={item.id}>
            <td>{item.id}</td>
            <td><Link to={`/items/${item.id}`}>{item.title}</Link></td>
            {showTeam && <td>{item.team_name}</td>}
            <td>{TYPE[item.type]}</td>
            <td><span className={`badge p-${item.priority}`}>{PRIORITY[item.priority]}</span></td>
            <td><span className={`badge s-${item.status}`}>{STATUS[item.status]}</span></td>
            <td>{item.assignee?.name ?? <span className="muted">Unassigned</span>}</td>
            <td>{item.due_date ?? ""}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
