import { Sidebar } from "@/components/Sidebar";

export default function AppLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="min-h-screen bg-bg-page">
      <Sidebar />
      <div className="md:pl-60">
        <main className="app-main px-4 py-6 md:px-10 md:py-10 max-w-7xl mx-auto">{children}</main>
      </div>
    </div>
  );
}
