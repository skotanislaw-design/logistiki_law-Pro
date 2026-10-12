import { useCallback, useEffect, useState } from "react";
import axios, { API, dateFmt, errMsg } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { HearingFormDialog } from "@/components/HearingFormDialog";
import {
  Plus, Trash, Play, Pause, CheckCircle, ClockCounterClockwise, Gavel, CalendarX, ArrowSquareOut,
} from "@phosphor-icons/react";
import { toast } from "sonner";

const STATUS = {
  scheduled: { label: "Προγραμματισμένο", variant: "secondary" },
  in_progress: { label: "Εκδικάζεται", variant: "default" },
  interrupted: { label: "Διακοπή", variant: "outline" },
  completed: { label: "Ολοκληρώθηκε", variant: "outline" },
  postponed: { label: "Αναβλήθηκε", variant: "destructive" },
};

export default function Pinakia() {
  const [hearings, setHearings] = useState([]);
  const [selected, setSelected] = useState(null);
  const [showForm, setShowForm] = useState(false);
  const [manualNumber, setManualNumber] = useState("");

  const load = useCallback(async () => {
    try {
      const { data } = await axios.get(`${API}/hearings`);
      setHearings(data);
      setSelected((prev) => (prev ? data.find((h) => h.id === prev.id) || data[0] || null : data[0] || null));
    } catch (e) {
      toast.error(errMsg(e));
    }
  }, []);

  useEffect(() => { load(); }, [load]);
  useEffect(() => {
    const t = setInterval(load, 15000);
    return () => clearInterval(t);
  }, [load]);

  const liveUpdate = async (h, payload) => {
    try {
      await axios.post(`${API}/hearings/${h.id}/live-update`, payload);
      load();
    } catch (e) {
      toast.error(errMsg(e));
    }
  };

  const remove = async (h) => {
    if (!window.confirm("Διαγραφή πινακίου;")) return;
    try {
      await axios.delete(`${API}/hearings/${h.id}`);
      setSelected(null);
      load();
    } catch (e) {
      toast.error(errMsg(e));
    }
  };

  const remaining = selected ? selected.pinakio_number - selected.current_number : 0;
  const progress = selected ? Math.min(100, Math.round((selected.current_number / selected.pinakio_number) * 100)) : 0;
  const approaching = selected && selected.status === "in_progress" && remaining > 0 && remaining <= selected.notify_threshold;
  const myTurn = selected && remaining <= 0 && selected.status !== "completed" && selected.current_number > 0;

  return (
    <div className="space-y-8" data-testid="pinakia-page">
      <div className="flex items-start justify-between animate-in-up">
        <div>
          <div className="lux-overline mb-1.5">Nomos One · Δικαστικά</div>
          <h1 className="font-brand text-4xl font-bold tracking-wide">Ηλεκτρονικά Πινάκια</h1>
          <p className="text-sm text-muted-foreground mt-1">Ζωντανή παρακολούθηση εκδίκασης — ενημέρωση ροής & ειδοποίηση όταν πλησιάζει η σειρά σας</p>
        </div>
        <div className="flex gap-2">
          <a href="https://extapps.solon.gov.gr/mojwp/faces/TrackLdoPublic" target="_blank" rel="noreferrer" data-testid="solon-portal-link">
            <Button variant="outline"><ArrowSquareOut size={15} className="mr-1.5" /> ΣΟΛΩΝ — Πορεία Υπόθεσης</Button>
          </a>
          <Button onClick={() => setShowForm(true)} data-testid="new-hearing-button"><Plus size={16} className="mr-1.5" /> Νέο Πινάκιο</Button>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-[340px_1fr] gap-6 items-start">
        <div className="space-y-3 stagger">
          {hearings.length === 0 && (
            <div className="lux-card p-8 text-center text-muted-foreground text-sm">Δεν υπάρχουν καταχωρημένα πινάκια</div>
          )}
          {hearings.map((h) => (
            <button
              key={h.id}
              onClick={() => setSelected(h)}
              className={`w-full text-left lux-card p-4 ${selected?.id === h.id ? "!border-[hsl(var(--gold))]/50" : ""}`}
              data-testid={`hearing-card-${h.id}`}
            >
              <div className="flex items-center justify-between mb-1">
                <span className="font-mono text-sm text-[hsl(var(--gold))]">Νο {h.pinakio_number}</span>
                <Badge variant={STATUS[h.status]?.variant}>{STATUS[h.status]?.label}</Badge>
              </div>
              <p className="text-sm font-medium truncate">{h.case_title || h.court}</p>
              <p className="text-xs text-muted-foreground">{h.court} · {dateFmt(h.date)}{h.time ? ` · ${h.time}` : ""}</p>
            </button>
          ))}
        </div>

        {selected ? (
          <div className="space-y-4 animate-in-up">
            {(approaching || myTurn) && (
              <div className={`rounded-xl border p-4 flex items-center gap-3 ${myTurn ? "border-destructive/50 bg-destructive/10 animate-pulse" : "border-[hsl(var(--warning))]/50 bg-[hsl(var(--warning))]/10"}`} data-testid="hearing-alert-banner">
                <Gavel size={22} className={myTurn ? "text-destructive" : "text-[hsl(var(--warning))]"} />
                <p className="text-sm font-medium">
                  {myTurn ? "🔔 ΣΕΙΡΑ ΣΑΣ — εκδικάζεται το πινάκιό σας!" : `⚠ Πλησιάζει η σειρά σας — απομένουν ${remaining} νούμερα`}
                </p>
              </div>
            )}

            <div className="lux-card p-6">
              <div className="flex items-start justify-between mb-4">
                <div>
                  <h2 className="font-heading text-lg font-semibold">{selected.case_title || selected.court}</h2>
                  <p className="text-xs text-muted-foreground">{selected.court}{selected.room ? ` · Αίθουσα ${selected.room}` : ""} · {dateFmt(selected.date)}{selected.time ? ` · ${selected.time}` : ""}</p>
                </div>
                <Button variant="ghost" size="icon" onClick={() => remove(selected)} data-testid="delete-hearing-button"><Trash size={16} /></Button>
              </div>

              <div className="grid grid-cols-2 gap-4 mb-5">
                <div className="rounded-xl border border-[hsl(var(--gold))]/20 p-5 text-center">
                  <div className="lux-overline mb-1">Εκδικάζεται τώρα</div>
                  <div className="font-mono text-5xl text-[hsl(var(--gold))]" data-testid="current-number">{selected.current_number || "—"}</div>
                </div>
                <div className={`rounded-xl border p-5 text-center ${myTurn ? "border-destructive/50" : "border-[hsl(var(--gold))]/20"}`}>
                  <div className="lux-overline mb-1">Το πινάκιό σας</div>
                  <div className="font-mono text-5xl" data-testid="my-number">{selected.pinakio_number}</div>
                </div>
              </div>

              <div className="h-2 rounded-full bg-white/5 overflow-hidden mb-1">
                <div className="h-full bg-gradient-to-r from-[hsl(var(--gold))]/60 to-[hsl(var(--gold))] transition-[width] duration-700" style={{ width: `${progress}%` }} />
              </div>
              <p className="text-xs text-muted-foreground mb-5">{remaining > 0 ? `Απομένουν ${remaining} νούμερα` : selected.current_number > 0 ? "Η σειρά σας έφτασε" : "Αναμονή έναρξης"}</p>

              <div className="flex flex-wrap gap-2">
                {selected.status === "scheduled" && (
                  <Button size="sm" onClick={() => liveUpdate(selected, { status: "in_progress", current_number: selected.current_number || 1 })} data-testid="start-hearing-button">
                    <Play size={15} className="mr-1.5" /> Έναρξη Συνεδρίασης
                  </Button>
                )}
                {selected.status === "in_progress" && (
                  <>
                    <Button size="sm" variant="outline" onClick={() => liveUpdate(selected, { current_number: (selected.current_number || 0) + 1 })} data-testid="advance-number-button">
                      +1 Επόμενο Νούμερο
                    </Button>
                    <Button size="sm" variant="outline" onClick={() => liveUpdate(selected, { status: "interrupted" })} data-testid="interrupt-button">
                      <Pause size={15} className="mr-1.5" /> Διακοπή
                    </Button>
                  </>
                )}
                {selected.status === "interrupted" && (
                  <Button size="sm" onClick={() => liveUpdate(selected, { status: "in_progress" })} data-testid="resume-button">
                    <Play size={15} className="mr-1.5" /> Επανέναρξη
                  </Button>
                )}
                {selected.status !== "completed" && selected.status !== "scheduled" && (
                  <>
                    <Button size="sm" variant="outline" onClick={() => liveUpdate(selected, { status: "completed" })} data-testid="complete-button">
                      <CheckCircle size={15} className="mr-1.5" /> Εκδικάστηκε
                    </Button>
                    <Button size="sm" variant="ghost" onClick={() => liveUpdate(selected, { status: "postponed" })} data-testid="postpone-button">
                      <CalendarX size={15} className="mr-1.5" /> Αναβολή
                    </Button>
                  </>
                )}
                <div className="flex items-center gap-1.5 ml-auto">
                  <Input type="number" value={manualNumber} onChange={(e) => setManualNumber(e.target.value)} placeholder="Νο" className="w-20 h-8 text-sm" data-testid="manual-number-input" />
                  <Button size="sm" variant="outline" disabled={!manualNumber} onClick={() => { liveUpdate(selected, { current_number: Number(manualNumber) }); setManualNumber(""); }} data-testid="set-number-button">
                    Ορισμός
                  </Button>
                </div>
              </div>
            </div>

            <div className="lux-card p-6">
              <div className="lux-overline mb-3">Ροή Συνεδρίασης</div>
              <div className="space-y-0 max-h-[300px] overflow-y-auto">
                {[...(selected.events || [])].reverse().map((ev, i) => (
                  <div key={i} className="flex items-start gap-3 py-2 border-b border-border/40 last:border-0">
                    <ClockCounterClockwise size={14} className="text-[hsl(var(--gold))] mt-0.5 shrink-0" />
                    <div className="text-sm">{ev.event}<span className="block text-[11px] text-muted-foreground">{new Date(ev.at).toLocaleTimeString("el-GR")} · {dateFmt(ev.at)}</span></div>
                  </div>
                ))}
              </div>
            </div>
          </div>
        ) : (
          <div className="lux-card p-12 text-center text-muted-foreground animate-in-up">
            <Gavel size={40} className="mx-auto mb-3 text-[hsl(var(--gold))]/50" />
            Επιλέξτε ή καταχωρήστε ένα πινάκιο για ζωντανή παρακολούθηση
          </div>
        )}
      </div>

      <p className="text-[11px] text-muted-foreground/70">
        Η ροή ενημερώνεται χειροκίνητα ή από συνεργάτη στο ακροατήριο. Οι ειδοποιήσεις εμφανίζονται εδώ και στο καμπανάκι της πλατφόρμας όταν απομένουν λιγότερα νούμερα από το όριο ειδοποίησης.
      </p>

      <HearingFormDialog open={showForm} onOpenChange={setShowForm} onSaved={load} />
    </div>
  );
}
