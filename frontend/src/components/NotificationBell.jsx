import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import axios, { API } from "@/lib/api";
import { Bell } from "@phosphor-icons/react";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuLabel, DropdownMenuSeparator, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";
import { toast } from "sonner";

export function NotificationBell() {
  const [alerts, setAlerts] = useState([]);
  const [decisions, setDecisions] = useState([]);
  const seenRef = useRef(new Set());
  const navigate = useNavigate();

  useEffect(() => {
    const poll = async () => {
      try {
        const { data: st } = await axios.get(`${API}/automation/status`);
        if (st.open_items > 0) {
          const { data: items } = await axios.get(`${API}/automation/notifications?limit=20`);
          setDecisions(items.filter((n) => n.requires_action).slice(0, 8));
        } else {
          setDecisions([]);
        }
      } catch {}
      try {
        const { data } = await axios.get(`${API}/hearings/alerts/list`);
        setAlerts(data);
        data.forEach((a) => {
          const key = `${a.id}-${a.level}-${a.message}`;
          if (!seenRef.current.has(key)) {
            seenRef.current.add(key);
            if (a.level === "critical") toast.error(a.message, { duration: 10000 });
            else if (a.level === "warning") toast.warning(a.message, { duration: 8000 });
          }
        });
      } catch {}
    };
    poll();
    const t = setInterval(poll, 60000);
    return () => clearInterval(t);
  }, []);

  const urgent = alerts.filter((a) => a.level !== "info").length + decisions.filter((d) => d.level === "critical").length;
  const total = alerts.length + decisions.length;

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <button className="relative p-2 rounded-full hover:bg-white/5 transition-colors" data-testid="notification-bell">
          <Bell size={19} className={urgent > 0 ? "text-[hsl(var(--warning))]" : "text-foreground/60"} />
          {total > 0 && (
            <span className={`absolute -top-0.5 -right-0.5 w-4.5 h-4.5 min-w-[18px] px-1 rounded-full text-[10px] font-bold flex items-center justify-center ${urgent > 0 ? "bg-destructive text-white animate-pulse" : "bg-[hsl(var(--gold))] text-[hsl(var(--primary-foreground))]"}`} data-testid="notification-count">
              {total}
            </span>
          )}
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-96">
        {decisions.length > 0 && (
          <>
            <DropdownMenuLabel>Autopilot — χρειάζονται απόφαση</DropdownMenuLabel>
            <DropdownMenuSeparator />
            {decisions.map((d) => (
              <DropdownMenuItem key={d.id} onClick={() => navigate("/autopilot")} className="cursor-pointer py-2.5" data-testid={`autopilot-notification-${d.type}`}>
                <span className="text-sm leading-snug">{d.title}</span>
              </DropdownMenuItem>
            ))}
            <DropdownMenuSeparator />
          </>
        )}
        <DropdownMenuLabel>Ειδοποιήσεις Πινακίων</DropdownMenuLabel>
        <DropdownMenuSeparator />
        {alerts.length === 0 && <div className="px-3 py-4 text-sm text-muted-foreground text-center">Καμία ειδοποίηση</div>}
        {alerts.map((a, i) => (
          <DropdownMenuItem key={i} onClick={() => navigate("/pinakia")} className="cursor-pointer py-2.5" data-testid={`notification-item-${i}`}>
            <span className="text-sm leading-snug">{a.message}</span>
          </DropdownMenuItem>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
