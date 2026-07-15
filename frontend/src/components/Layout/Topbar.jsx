import { useEffect, useState } from "react";
import { useLocation } from "react-router-dom";
import { useAuth } from "@/context/AuthContext";

const TITLES = {
  "/": "Πίνακας Ελέγχου",
  "/invoices": "Τιμολόγια",
  "/expenses": "Έξοδα",
  "/vat": "ΦΠΑ",
  "/mydata": "myDATA",
  "/payroll": "Μισθοδοσία",
  "/clients": "Πελάτες",
  "/tax-calendar": "Φορολογικό Ημερολόγιο",
  "/settings": "Ρυθμίσεις",
};

const ROLE_LABEL = { admin: "Διαχειριστής", accountant: "Λογιστής", lawyer: "Δικηγόρος" };

export function Topbar() {
  const { user } = useAuth();
  const { pathname } = useLocation();
  const [now, setNow] = useState(new Date());

  useEffect(() => {
    const t = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(t);
  }, []);

  const initials = (user?.name || "?").split(" ").map((w) => w[0]).slice(0, 2).join("").toUpperCase();

  return (
    <header className="sticky top-0 z-20 h-[72px] flex items-center justify-between px-6 md:px-8 border-b border-[hsl(var(--gold))]/15 bg-background/85 backdrop-blur-md" data-testid="topbar">
      <div>
        <h2 className="font-heading text-lg font-semibold tracking-tight">{TITLES[pathname] || "Nomos One"}</h2>
        <p className="text-xs text-muted-foreground capitalize">
          {now.toLocaleDateString("el-GR", { weekday: "long", day: "numeric", month: "long", year: "numeric" })}
        </p>
      </div>
      <div className="flex items-center gap-4">
        <div className="hidden sm:flex items-center gap-2 border border-[hsl(var(--gold))]/25 rounded-sm px-3 py-1.5 bg-[hsl(var(--gold))]/5" data-testid="topbar-clock">
          <span className="w-1.5 h-1.5 rounded-full bg-[hsl(var(--success))] animate-pulse" />
          <span className="font-mono text-sm text-[hsl(var(--gold))]">{now.toLocaleTimeString("el-GR")}</span>
        </div>
        <span className="hidden sm:inline text-[hsl(var(--gold))]/40 text-[9px]">◆</span>
        <div className="flex items-center gap-2.5" data-testid="topbar-user">
          <div className="w-9 h-9 rounded-full bg-[hsl(var(--gold))]/15 ring-1 ring-[hsl(var(--gold))]/50 ring-offset-2 ring-offset-background flex items-center justify-center text-xs font-semibold text-[hsl(var(--gold))]">
            {initials}
          </div>
          <div className="hidden md:block leading-tight">
            <div className="text-sm font-medium">{user?.name}</div>
            <div className="text-[11px] text-muted-foreground">{ROLE_LABEL[user?.role] || user?.role}</div>
          </div>
        </div>
      </div>
    </header>
  );
}
