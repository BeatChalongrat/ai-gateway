import { PageEnter } from "@/components/PageEnter";
import { Sidebar } from "@/components/Sidebar";
import { SilentRefresh } from "@/components/SilentRefresh";

export default function AppLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="min-h-screen" style={{ background: "var(--bg-base)" }}>
      <SilentRefresh />
      <Sidebar />
      <div className="md:pl-60">
        <main className="app-main px-4 py-6 md:px-10 md:py-10 max-w-7xl mx-auto">
          <PageEnter>{children}</PageEnter>
        </main>
      </div>
    </div>
  );
}
