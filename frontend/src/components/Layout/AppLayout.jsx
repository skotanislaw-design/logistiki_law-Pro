import { Sidebar } from "./Sidebar";
import { Topbar } from "./Topbar";

export function AppLayout({ children }) {
  return (
    <div className="flex min-h-screen bg-background">
      <Sidebar />
      <div className="flex-1 min-w-0 flex flex-col">
        <Topbar />
        <main className="flex-1 p-6 md:p-8">{children}</main>
        <footer className="flex items-center justify-between px-6 md:px-8 py-3 border-t border-[hsl(var(--gold))]/10 text-[11px] text-muted-foreground/70">
          <span>Nomos One v1.0 — Σκοτάνης &amp; Συνεργάτες</span>
          <span className="hidden sm:inline">Εμπιστευτική Πλατφόρμα Νομικών Λειτουργιών</span>
        </footer>
      </div>
    </div>
  );
}
