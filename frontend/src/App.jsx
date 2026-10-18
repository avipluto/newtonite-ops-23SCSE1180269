import { NavLink, Navigate, Route, Routes } from "react-router-dom";
import { useAuth } from "./auth";
import LoginPage from "./pages/LoginPage";
import DashboardPage from "./pages/DashboardPage";
import ItemsPage from "./pages/ItemsPage";
import NewItemPage from "./pages/NewItemPage";
import ItemPage from "./pages/ItemPage";

export default function App() {
  const { user, loading, logout } = useAuth();

  if (loading) return <p className="page">Loading…</p>;
  if (!user) return <LoginPage />;

  return (
    <>
      <header className="topbar">
        <strong>Newtonite Ops</strong>
        <nav>
          <NavLink to="/" end>Dashboard</NavLink>
          <NavLink to="/items" end>All items</NavLink>
          <NavLink to="/items/new">New item</NavLink>
        </nav>
        <span className="who">
          {user.display_name} · {user.teams.map((t) => `${t.name} (${t.role})`).join(", ")}
          <button className="link" onClick={logout}>Log out</button>
        </span>
      </header>
      <main className="page">
        <Routes>
          <Route path="/" element={<DashboardPage />} />
          <Route path="/items" element={<ItemsPage />} />
          <Route path="/items/new" element={<NewItemPage />} />
          <Route path="/items/:id" element={<ItemPage />} />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </main>
    </>
  );
}
