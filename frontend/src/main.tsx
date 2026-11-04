import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter, NavLink, Route, Routes } from "react-router-dom";
import "./styles.css";
import { useSnapshot } from "./api";
import Dashboard from "./pages/Dashboard";
import Controllers from "./pages/Controllers";

function App() {
  const { snap, connected } = useSnapshot();
  return (
    <BrowserRouter>
      <header className="topbar">
        <div className="brand"><img src="/favicon.svg" alt="" /> FPP Command Center</div>
        <nav>
          <NavLink to="/" end>Dashboard</NavLink>
          <NavLink to="/controllers">Controllers</NavLink>
        </nav>
        <div className={`conn ${connected ? "ok" : "bad"}`} title={connected ? "Live updates connected" : "Reconnecting…"}>
          <span className="dot" /> {connected ? "Live" : "Offline"}
        </div>
      </header>
      <main>
        <Routes>
          <Route path="/" element={<Dashboard snap={snap} />} />
          <Route path="/controllers" element={<Controllers snap={snap} />} />
          <Route path="*" element={<p className="muted">Page not found.</p>} />
        </Routes>
      </main>
    </BrowserRouter>
  );
}

ReactDOM.createRoot(document.getElementById("root")!).render(<React.StrictMode><App /></React.StrictMode>);
