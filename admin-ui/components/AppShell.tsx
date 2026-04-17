"use client";

import Sidebar from "./Sidebar";

export default function AppShell({ children }: { children: React.ReactNode }) {
  return (
    <div style={{ display: "flex", minHeight: "100vh", width: "100%", overflow: "hidden" }}>
      <Sidebar />
      <main
        style={{
          flex: 1,
          minWidth: 0,
          padding: "1.75rem 2.25rem 2.5rem",
          overflowY: "auto",
          overflowX: "hidden",
          maxHeight: "100vh",
        }}
      >
        <div style={{ maxWidth: "1280px" }}>
          {children}
        </div>
      </main>
    </div>
  );
}
