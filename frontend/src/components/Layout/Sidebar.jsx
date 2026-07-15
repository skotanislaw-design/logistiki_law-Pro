import { useState } from "react";
import { NavLink, useNavigate } from "react-router-dom";
import {
  SquaresFour, FileText, Receipt, ChartLineUp, Users, Calculator,
  CalendarCheck, GearSix, SignOut, CaretLeft, CaretRight, Scales, Sparkle, Files,
  Briefcase, Scroll, Ticket,
} from "@phosphor-icons/react";
import { useAuth } from "@/context/AuthContext";

const NAV_GROUPS = [
  {
    label: "ΚΥΡΙΑ",
    items: [{ to: "/", label: "Πίνακας Ελέγχου", icon: SquaresFour, testid: "nav-dashboard" }],
  },
  {
    label: "AI ΕΡΓΑΛΕΙΑ",
    items: [
      { to: "/copilot", label: "Nomos AI", icon: Sparkle, testid: "nav-copilot" },
      { to: "/documents", label: "Έγγραφα", icon: Files, testid: "nav-documents" },
    ],
  },
  {
    label: "ΔΙΚΑΣΤΙΚΑ",
    items: [
      { to: "/cases", label: "Υποθέσεις", icon: Briefcase, testid: "nav-cases" },
      { to: "/grammatia", label: "Γραμμάτια", icon: Scroll, testid: "nav-grammatia" },
      { to: "/paravola", label: "Παράβολα", icon: Ticket, testid: "nav-paravola" },
    ],
  },
  {
    label: "ΟΙΚΟΝΟΜΙΚΑ",
    items: [
      { to: "/invoices", label: "Τιμολόγια", icon: FileText, testid: "nav-invoices" },
      { to: "/expenses", label: "Έξοδα", icon: Receipt, testid: "nav-expenses" },
      { to: "/vat", label: "ΦΠΑ", icon: ChartLineUp, testid: "nav-vat" },
      { to: "/payroll", label: "Μισθοδοσία", icon: Calculator, testid: "nav-payroll" },
    ],
  },
  {
    label: "ΣΥΜΜΟΡΦΩΣΗ",
    items: [
      { to: "/mydata", label: "myDATA", icon: Scales, testid: "nav-mydata" },
      { to: "/tax-calendar", label: "Φορολ. Ημερολόγιο", icon: CalendarCheck, testid: "nav-tax-calendar" },
    ],
  },
  {
    label: "ΓΡΑΦΕΙΟ",
    items: [
      { to: "/clients", label: "Πελάτες", icon: Users, testid: "nav-clients" },
      { to: "/settings", label: "Ρυθμίσεις", icon: GearSix, testid: "nav-settings" },
    ],
  },
];

export function Sidebar() {
  const [collapsed, setCollapsed] = useState(false);
  const { user, logout } = useAuth();
  const navigate = useNavigate();

  const handleLogout = async () => {
    await logout();
    navigate("/login");
  };

  return (
    <aside
      className={`h-screen sticky top-0 z-20 flex flex-col bg-[hsl(var(--sidebar))]/80 backdrop-blur-xl border-r border-[hsl(var(--gold))]/15 transition-[width] duration-200 ${collapsed ? "w-[68px]" : "w-[248px]"}`}
      data-testid="sidebar"
    >
      <div className="flex items-center gap-3 px-4 h-[72px] border-b border-[hsl(var(--gold))]/15">
        <img src="/nomos-logo.png" alt="Nomos One" className="w-10 h-10 rounded-full shrink-0" />
        {!collapsed && (
          <div className="min-w-0">
            <div className="font-brand font-bold text-[17px] leading-tight text-[hsl(var(--gold))] tracking-widest">NOMOS ONE</div>
            <div className="text-[9px] tracking-[0.22em] text-muted-foreground">PERSONAL LOGISTICS</div>
          </div>
        )}
      </div>

      <nav className="flex-1 py-3 flex flex-col overflow-y-auto">
        {NAV_GROUPS.map((group) => (
          <div key={group.label} className="mb-2">
            {!collapsed && (
              <div className="px-4 pt-3 pb-1.5 text-[9.5px] tracking-[0.22em] text-muted-foreground/70">{group.label}</div>
            )}
            <div className="px-2 flex flex-col gap-0.5">
              {group.items.map(({ to, label, icon: Icon, testid }) => (
                <NavLink
                  key={to}
                  to={to}
                  data-testid={testid}
                  className={({ isActive }) =>
                    `flex items-center gap-3 px-3.5 py-2.5 rounded-xl text-sm transition-colors duration-200 ${
                      isActive
                        ? "bg-gradient-to-r from-[hsl(var(--gold))]/15 to-[hsl(var(--gold))]/[0.03] text-[hsl(var(--gold))] font-medium shadow-[inset_0_1px_0_rgba(255,255,255,0.06)]"
                        : "text-foreground/60 hover:bg-white/5 hover:text-foreground"
                    }`
                  }
                >
                  <Icon size={19} weight="regular" />
                  {!collapsed && <span className="truncate">{label}</span>}
                </NavLink>
              ))}
            </div>
          </div>
        ))}
      </nav>

      <div className="border-t border-[hsl(var(--gold))]/15 p-3 space-y-2">
        {!collapsed && (
          <div className="text-xs text-muted-foreground truncate" data-testid="current-user-label">
            {user?.name} · {user?.role}
          </div>
        )}
        <div className="flex items-center justify-between">
          <button
            onClick={handleLogout}
            className="flex items-center gap-2 text-sm text-foreground/60 hover:text-[hsl(var(--gold))] transition-colors duration-200"
            data-testid="logout-button"
          >
            <SignOut size={18} />
            {!collapsed && <span>Αποσύνδεση</span>}
          </button>
          <button
            onClick={() => setCollapsed(!collapsed)}
            className="p-1.5 rounded-sm text-foreground/50 hover:bg-white/5 hover:text-foreground transition-colors duration-200"
            data-testid="sidebar-toggle"
          >
            {collapsed ? <CaretRight size={15} /> : <CaretLeft size={15} />}
          </button>
        </div>
      </div>
    </aside>
  );
}
