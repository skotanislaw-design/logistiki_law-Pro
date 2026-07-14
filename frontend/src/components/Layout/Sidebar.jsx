import { useState } from "react";
import { NavLink, useNavigate } from "react-router-dom";
import {
  SquaresFour, FileText, Receipt, ChartLineUp, Users, Calculator,
  CalendarCheck, GearSix, SignOut, CaretLeft, CaretRight, Scales,
} from "@phosphor-icons/react";
import { useAuth } from "@/context/AuthContext";

const NAV_ITEMS = [
  { to: "/", label: "Πίνακας Ελέγχου", icon: SquaresFour, testid: "nav-dashboard" },
  { to: "/invoices", label: "Τιμολόγια", icon: FileText, testid: "nav-invoices" },
  { to: "/expenses", label: "Έξοδα", icon: Receipt, testid: "nav-expenses" },
  { to: "/vat", label: "ΦΠΑ", icon: ChartLineUp, testid: "nav-vat" },
  { to: "/mydata", label: "myDATA", icon: Scales, testid: "nav-mydata" },
  { to: "/payroll", label: "Μισθοδοσία", icon: Calculator, testid: "nav-payroll" },
  { to: "/clients", label: "Πελάτες", icon: Users, testid: "nav-clients" },
  { to: "/tax-calendar", label: "Φορολ. Ημερολόγιο", icon: CalendarCheck, testid: "nav-tax-calendar" },
  { to: "/settings", label: "Ρυθμίσεις", icon: GearSix, testid: "nav-settings" },
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
      className={`h-screen sticky top-0 flex flex-col bg-primary text-primary-foreground border-r border-border transition-[width] duration-200 ${collapsed ? "w-[64px]" : "w-[240px]"}`}
      data-testid="sidebar"
    >
      <div className="flex items-center justify-between px-4 h-16 border-b border-white/10">
        {!collapsed && (
          <span className="font-heading font-bold text-lg tracking-tight">Logistiki<span className="opacity-60">.gr</span></span>
        )}
        <button
          onClick={() => setCollapsed(!collapsed)}
          className="p-1.5 rounded-sm hover:bg-white/10 transition-colors duration-200"
          data-testid="sidebar-toggle"
        >
          {collapsed ? <CaretRight size={16} /> : <CaretLeft size={16} />}
        </button>
      </div>

      <nav className="flex-1 py-4 flex flex-col gap-1 px-2 overflow-y-auto">
        {NAV_ITEMS.map(({ to, label, icon: Icon, testid }) => (
          <NavLink
            key={to}
            to={to}
            data-testid={testid}
            className={({ isActive }) =>
              `flex items-center gap-3 px-3 py-2.5 rounded-sm text-sm transition-colors duration-200 ${
                isActive ? "bg-white/15 font-medium" : "hover:bg-white/8 text-primary-foreground/75"
              }`
            }
          >
            <Icon size={19} weight={"regular"} />
            {!collapsed && <span>{label}</span>}
          </NavLink>
        ))}
      </nav>

      <div className="border-t border-white/10 p-3">
        {!collapsed && (
          <div className="text-xs text-primary-foreground/60 mb-2 truncate" data-testid="current-user-label">
            {user?.name} · {user?.role}
          </div>
        )}
        <button
          onClick={handleLogout}
          className="flex items-center gap-2 text-sm text-primary-foreground/75 hover:text-primary-foreground transition-colors duration-200"
          data-testid="logout-button"
        >
          <SignOut size={18} />
          {!collapsed && <span>Αποσύνδεση</span>}
        </button>
      </div>
    </aside>
  );
}
