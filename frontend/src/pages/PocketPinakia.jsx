import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import axios, { API, dateFmt, errMsg } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Play, Pause, CheckCircle, Warning, Gavel, Timer, ArrowLeft, ArrowClockwise, Bell } from "@phosphor-icons/react";
import { toast } from "sonner";

const STATUS_ICON = {
  scheduled: { icon: Timer, color: "text-muted-foreground", label: "Προγραμματισμένο" },
  in_progress: { icon: Play, color: "text-[hsl(var(--gold))]", label: "Εκδικάζεται" },
  interrupted: { icon: Pause, color: "text-[hsl(var(--gold))]", label: "Διακοπή" },
  completed: { icon: CheckCircle, color: "text-[hsl(var(--success))]", label: "Ολοκληρώθηκε" },
  postponed: { icon: ArrowClockwise, color: "text-muted-foreground", label: "Αναβλήθηκε" },
};

export default function PocketPinakia() {
  const [hearings, setHearings] = useState([]);
  const [alerts, setAlerts] = useState([]);
  const [refreshing, setRefreshing] = useState(false);
  const [tick, setTick] = useState(0);

  const load = async () => {
    setRefreshing(true);
    try {
      const [h, a] = await Promise.all([
        axios.get(`${API}/hearings`),
        axios.get(`${API}/hearings/alerts/list`),
      ]);
      const today = new Date().toISOString().slice(0, 10);
      const relevant = h.data.filter((x) => x.date >= today || (x.status && !["completed", "postponed"].includes(x.status)));
      setHearings(relevant.sort((a, b) => (a.date || "").localeCompare(b.date || "")));
      setAlerts(a.data);
    } catch (e) { toast.error(errMsg(e)); }
    finally { setRefreshing(false); }
  };

  useEffect(() => { load(); }, []);
  useEffect(() => {
    const t = setInterval(() => setTick((v) => v + 1), 30000);
    return () => clearInterval(t);
  }, []);
  useEffect(() => { if (tick > 0) load(); /* eslint-disable-next-line */ }, [tick]);

  const liveUpdate = async (hid, payload) => {
    try {
      await axios.post(`${API}/hearings/${hid}/live-update`, payload);
      toast.success("Καταχωρήθηκε");
      load();
    } catch (e) { toast.error(errMsg(e)); }
  };

  const incrementCurrent = async (h) => {
    const next = (h.current_number || 0) + 1;
    await liveUpdate(h.id, { current_number: next });
    if (h.pinakio_number - next === 1) {
      try { navigator.vibrate?.([100, 50, 100]); } catch {}
    }
  };

  const setStatus = (h, status) => liveUpdate(h.id, { status });

  return (
    <div className="min-h-screen bg-background" data-testid="pocket-page">
      <div className="sticky top-0 z-10 bg-background/95 backdrop-blur-md border-b border-border/50 px-4 py-3 flex items-center justify-between">
        <Link to="/" className="flex items-center gap-1 text-sm text-muted-foreground">
          <ArrowLeft size={18} /> Πλήρης προβολή
        </Link>
        <h1 className="font-brand text-lg font-bold flex items-center gap-1.5">
          <Gavel size={18} className="text-[hsl(var(--gold))]" /> Πινάκιο Τσέπης
        </h1>
        <Button size="icon" variant="ghost" onClick={load} disabled={refreshing}>
          <ArrowClockwise size={16} className={refreshing ? "animate-spin" : ""} />
        </Button>
      </div>

      <div className="p-4 space-y-4 pb-24">
        {alerts.length > 0 && (
          <div className="space-y-2" data-testid="pocket-alerts">
            {alerts.map((a) => (
              <div key={a.id + a.level} className={`p-3 rounded-xl text-sm flex items-start gap-2 ${
                a.level === "critical" ? "bg-[hsl(var(--destructive))]/10 border border-[hsl(var(--destructive))]/40" :
                a.level === "warning" ? "bg-[hsl(var(--gold))]/10 border border-[hsl(var(--gold))]/40" :
                "bg-muted border border-border"
              }`}>
                <Bell size={16} className={a.level === "critical" ? "text-[hsl(var(--destructive))]" : a.level === "warning" ? "text-[hsl(var(--gold))]" : "text-muted-foreground"} />
                <div className="flex-1">{a.message}</div>
              </div>
            ))}
          </div>
        )}

        {hearings.length === 0 ? (
          <div className="lux-card p-8 text-center text-muted-foreground">
            <Gavel size={40} className="mx-auto text-[hsl(var(--gold))]/30 mb-3" />
            <p>Δεν υπάρχουν σημερινά ή επερχόμενα πινάκια.</p>
          </div>
        ) : hearings.map((h) => {
          const S = STATUS_ICON[h.status] || STATUS_ICON.scheduled;
          const remaining = (h.pinakio_number || 0) - (h.current_number || 0);
          const critical = h.status === "in_progress" && remaining <= 0;
          const warn = h.status === "in_progress" && remaining > 0 && remaining <= (h.notify_threshold || 5);
          return (
            <div key={h.id} className={`lux-card p-4 space-y-3 ${critical ? "ring-2 ring-[hsl(var(--destructive))]" : warn ? "ring-1 ring-[hsl(var(--gold))]" : ""}`} data-testid={`pocket-hearing-${h.id}`}>
              <div className="flex items-start justify-between gap-3">
                <div className="flex-1 min-w-0">
                  <div className="font-medium text-sm truncate">{h.case_title || "Χωρίς τίτλο"}</div>
                  <div className="text-xs text-muted-foreground truncate mt-0.5">{h.court}</div>
                  <div className="text-xs text-muted-foreground mt-0.5">
                    {dateFmt(h.date)} {h.time && `· ${h.time}`}
                    {h.gak && ` · ΓΑΚ ${h.gak}`}
                  </div>
                </div>
                <Badge variant="outline" className={S.color}>
                  <S.icon size={12} className="mr-1" /> {S.label}
                </Badge>
              </div>

              {/* Pinakio number display */}
              <div className="grid grid-cols-2 gap-2">
                <div className="lux-card p-3 text-center">
                  <div className="text-[10px] text-muted-foreground uppercase tracking-wider">Πινάκιο</div>
                  <div className="font-mono text-3xl font-bold text-[hsl(var(--gold))]" data-testid={`pocket-my-number-${h.id}`}>{h.pinakio_number}</div>
                </div>
                <div className="lux-card p-3 text-center">
                  <div className="text-[10px] text-muted-foreground uppercase tracking-wider">Εκδικάζεται</div>
                  <div className={`font-mono text-3xl font-bold ${critical ? "text-[hsl(var(--destructive))]" : warn ? "text-[hsl(var(--gold))]" : ""}`} data-testid={`pocket-current-${h.id}`}>
                    {h.current_number || 0}
                  </div>
                </div>
              </div>

              {critical ? (
                <div className="p-3 rounded-lg bg-[hsl(var(--destructive))]/10 text-center font-medium text-[hsl(var(--destructive))] animate-pulse">
                  🔔 ΣΕΙΡΑ ΣΑΣ ΤΩΡΑ!
                </div>
              ) : warn ? (
                <div className="p-2 rounded-lg bg-[hsl(var(--gold))]/10 text-center text-sm text-[hsl(var(--gold))]">
                  <Warning size={14} className="inline mr-1" /> Απομένουν {remaining} νούμερα
                </div>
              ) : null}

              {/* Quick actions */}
              <div className="grid grid-cols-4 gap-1.5">
                <Button size="sm" variant="outline" onClick={() => setStatus(h, "in_progress")} data-testid={`pocket-start-${h.id}`}>
                  <Play size={14} />
                </Button>
                <Button size="sm" variant="outline" onClick={() => incrementCurrent(h)} data-testid={`pocket-next-${h.id}`}>
                  +1
                </Button>
                <Button size="sm" variant="outline" onClick={() => setStatus(h, "postponed")} data-testid={`pocket-postpone-${h.id}`}>
                  Αναβ.
                </Button>
                <Button size="sm" onClick={() => setStatus(h, "completed")} data-testid={`pocket-complete-${h.id}`}>
                  <CheckCircle size={14} />
                </Button>
              </div>

              {h.events?.length > 0 && (
                <details className="text-xs">
                  <summary className="cursor-pointer text-muted-foreground">Ιστορικό ({h.events.length})</summary>
                  <ul className="mt-2 space-y-1 text-muted-foreground">
                    {h.events.slice(-5).reverse().map((ev, i) => (
                      <li key={i}>· {new Date(ev.at).toLocaleTimeString("el-GR", { hour: "2-digit", minute: "2-digit" })} — {ev.event}</li>
                    ))}
                  </ul>
                </details>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
