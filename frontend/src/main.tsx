import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter, NavLink, Route, Routes } from "react-router-dom";
import "./styles.css";
import { useSnapshot } from "./api";
import Dashboard from "./pages/Dashboard";
import Controllers from "./pages/Controllers";
import ControllerDetail from "./pages/ControllerDetail";
import Backups from "./pages/Backups";
import Show from "./pages/Show";
import BackupCompare from "./pages/BackupCompare";
import Content from "./pages/Content";
import Fleet from "./pages/Fleet";

function App() {
  const { snap, connected } = useSnapshot();
  return (
    <BrowserRouter>
      <header className="topbar">
        <div className="brand"><img src="/favicon.svg" alt="" /> FPP Command Center</div>
        <nav>
          <NavLink to="/" end>Dashboard</NavLink>
          <NavLink to="/show">Show</NavLink>
          <NavLink to="/content">Content</NavLink>
          <NavLink to="/controllers">Controllers</NavLink>
          <NavLink to="/fleet">Fleet</NavLink>
          <NavLink to="/backups">Backups</NavLink>
        </nav>
        <div className={`conn ${connected ? "ok" : "bad"}`} title={connected ? "Live updates connected" : "Reconnecting…"}>
          <span className="dot" /> {connected ? "Live" : "Offline"}
        </div>
      </header>
      <main>
        <Routes>
          <Route path="/" element={<Dashboard snap={snap} />} />
          <Route path="/controllers" element={<Controllers snap={snap} />} />
          <Route path="/hosts/:id" element={<ControllerDetail snap={snap} />} />
          <Route path="/show" element={<Show snap={snap} />} />
          <Route path="/content" element={<Content snap={snap} />} />
          <Route path="/fleet" element={<Fleet snap={snap} />} />
          <Route path="/backups" element={<Backups snap={snap} />} />
          <Route path="/backups/compare" element={<BackupCompare />} />
          <Route path="*" element={<p className="muted">Page not found.</p>} />
        </Routes>
      </main>
    </BrowserRouter>
  );
}

ReactDOM.createRoot(document.getElementById("root")!).render(<React.StrictMode><App /></React.StrictMode>);
