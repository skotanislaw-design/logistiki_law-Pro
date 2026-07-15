import { useCallback, useEffect, useState } from "react";
import { useParams, useNavigate, Link } from "react-router-dom";
import axios, { API, money, dateFmt, errMsg } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { GrammatioFormDialog, GrammatioIssueDialog } from "@/components/GrammatioDialogs";
import { ParavoloFormDialog, ParavoloIssueDialog } from "@/components/ParavoloDialogs";
import { CASE_STATUS } from "./Cases";
import {
  ArrowLeft, User, Scales, CalendarBlank, PencilSimple, Plus, Archive,
  Lightning, NotePencil, FilePdf, PaperPlaneTilt, ClockCounterClockwise, CheckCircle,
} from "@phosphor-icons/react";
import { toast } from "sonner";

export default function CaseDetail() {
  const { id } = useParams();
  const navigate = useNavigate();
  const [data, setData] = useState(null);
  const [noteText, setNoteText] = useState("");
  const [showGrammatio, setShowGrammatio] = useState(false);
  const [showParavolo, setShowParavolo] = useState(false);
  const [issuingG, setIssuingG] = useState(null);
  const [issuingP, setIssuingP] = useState(null);
  const [editAction, setEditAction] = useState(false);
  const [actionForm, setActionForm] = useState({ next_action: "", next_action_date: "" });

  const load = useCallback(() => {
    axios.get(`${API}/cases/${id}`).then((r) => setData(r.data)).catch((e) => {
      toast.error(errMsg(e));
      navigate("/cases");
    });
  }, [id, navigate]);

  useEffect(() => { load(); }, [load]);

  if (!data) return <div className="text-muted-foreground p-8">Φόρτωση...</div>;
  const c = data.case;
  const caseInfo = { id: c.id, number: c.number, title: c.title, client_id: c.client_id, client_name: c.client_name };

  const addNote = async () => {
    if (!noteText.trim()) return;
    try {
      await axios.post(`${API}/cases/${id}/notes`, { text: noteText });
      setNoteText("");
      toast.success("Η σημείωση προστέθηκε");
      load();
    } catch (e) {
      toast.error(errMsg(e));
    }
  };

  const changeStatus = async (status) => {
    try {
      await axios.post(`${API}/cases/${id}/status`, { status });
      toast.success("Η κατάσταση άλλαξε");
      load();
    } catch (e) {
      toast.error(errMsg(e));
    }
  };

  const saveNextAction = async () => {
    try {
      await axios.post(`${API}/cases/${id}/next-action`, actionForm);
      setEditAction(false);
      toast.success("Η επόμενη ενέργεια ενημερώθηκε");
      load();
    } catch (e) {
      toast.error(errMsg(e));
    }
  };

  const downloadGrammatioPdf = async (g) => {
    try {
      const res = await axios.get(`${API}/grammatia/${g.id}/pdf`, { responseType: "blob" });
      const url = URL.createObjectURL(res.data);
      const a = document.createElement("a");
      a.href = url; a.download = `${g.number}.pdf`;
      document.body.appendChild(a); a.click(); a.remove();
      URL.revokeObjectURL(url);
    } catch (e) { toast.error(errMsg(e)); }
  };

  const markParavoloPaid = async (p) => {
    try {
      await axios.post(`${API}/paravola/${p.id}/mark-paid`);
      toast.success("Σημειώθηκε ως πληρωμένο");
      load();
    } catch (e) { toast.error(errMsg(e)); }
  };

  const GSTATUS = { draft: ["Προσχέδιο", "secondary"], issued: ["Εκδόθηκε", "default"], cancelled: ["Ακυρώθηκε", "destructive"] };
  const PSTATUS = { draft: ["Προσχέδιο", "secondary"], issued: ["Εκδόθηκε", "default"], paid: ["Πληρώθηκε", "outline"], cancelled: ["Ακυρώθηκε", "destructive"] };

  return (
    <div className="space-y-6" data-testid="case-detail-page">
      <Link to="/cases" className="inline-flex items-center gap-1.5 text-sm text-muted-foreground hover:text-[hsl(var(--gold))] transition-colors" data-testid="back-to-cases">
        <ArrowLeft size={15} /> Πίσω στις υποθέσεις
      </Link>

      <div className="grid grid-cols-1 lg:grid-cols-[300px_1fr_300px] gap-6 items-start">
        {/* ΑΡΙΣΤΕΡΗ ΣΤΗΛΗ */}
        <div className="space-y-4 stagger">
          <div className="lux-card p-5">
            <div className="flex items-center justify-between mb-2">
              <span className="font-mono text-sm text-[hsl(var(--gold))]">{c.number}</span>
              <Badge variant={CASE_STATUS[c.status]?.variant}>{CASE_STATUS[c.status]?.label}</Badge>
            </div>
            <h2 className="font-heading text-lg font-semibold leading-snug">{c.title}</h2>
            <p className="text-xs text-muted-foreground mt-1">{c.case_type}</p>
          </div>

          <div className="lux-card p-5">
            <div className="lux-overline mb-3">Πελάτης</div>
            <div className="flex items-center gap-3">
              <div className="w-9 h-9 rounded-full bg-[hsl(var(--gold))]/12 border border-[hsl(var(--gold))]/30 flex items-center justify-center">
                <User size={16} className="text-[hsl(var(--gold))]" />
              </div>
              <div className="text-sm font-medium">{c.client_name || "Χωρίς πελάτη"}</div>
            </div>
          </div>

          <div className="lux-card p-5 space-y-3 text-sm">
            <div className="flex items-start gap-2.5">
              <Scales size={16} className="text-[hsl(var(--gold))] mt-0.5" />
              <div><div className="text-[11px] text-muted-foreground">Δικαστήριο</div>{c.court || "—"}</div>
            </div>
            <div className="flex items-start gap-2.5">
              <User size={16} className="text-[hsl(var(--gold))] mt-0.5" />
              <div><div className="text-[11px] text-muted-foreground">Αντίδικος</div>{c.opposing_party || "—"}</div>
            </div>
            <div className="flex items-start gap-2.5">
              <CalendarBlank size={16} className="text-[hsl(var(--gold))] mt-0.5" />
              <div><div className="text-[11px] text-muted-foreground">Δημιουργία</div>{dateFmt(c.created_at)}</div>
            </div>
          </div>

          <div className="lux-card p-5">
            <div className="lux-overline mb-3">Οικονομικά</div>
            <div className="flex justify-between text-sm mb-2">
              <span className="text-muted-foreground">Αμοιβές (γραμμάτια)</span>
              <span className="font-mono text-[hsl(var(--success))]" data-testid="case-fees-total">{money(data.fees_total)}</span>
            </div>
            <div className="flex justify-between text-sm">
              <span className="text-muted-foreground">Έξοδα (παράβολα)</span>
              <span className="font-mono text-[hsl(var(--destructive))]" data-testid="case-expenses-total">{money(data.expenses_total)}</span>
            </div>
          </div>
        </div>

        {/* ΚΕΝΤΡΙΚΗ ΣΤΗΛΗ */}
        <div className="lux-card p-5 animate-in-up">
          <Tabs defaultValue="grammatia">
            <TabsList className="mb-4">
              <TabsTrigger value="grammatia" data-testid="tab-grammatia">Γραμμάτια ({data.grammatia.length})</TabsTrigger>
              <TabsTrigger value="paravola" data-testid="tab-paravola">Παράβολα ({data.paravola.length})</TabsTrigger>
              <TabsTrigger value="notes" data-testid="tab-notes">Σημειώσεις ({c.notes?.length || 0})</TabsTrigger>
              <TabsTrigger value="history" data-testid="tab-history">Ιστορικό</TabsTrigger>
            </TabsList>

            <TabsContent value="grammatia" className="space-y-3">
              {data.grammatia.length === 0 && <p className="text-sm text-muted-foreground py-8 text-center">Δεν υπάρχουν γραμμάτια για την υπόθεση</p>}
              {data.grammatia.map((g) => (
                <div key={g.id} className="border border-[hsl(var(--gold))]/15 rounded-xl p-4 flex items-center justify-between" data-testid={`case-grammatio-${g.id}`}>
                  <div>
                    <span className="font-mono text-sm text-[hsl(var(--gold))]">{g.number}</span>
                    <p className="text-sm">{g.court_type} — {g.procedure}</p>
                    <p className="text-xs text-muted-foreground font-mono">{money(g.total)}</p>
                  </div>
                  <div className="flex items-center gap-1">
                    <Badge variant={GSTATUS[g.status]?.[1]}>{GSTATUS[g.status]?.[0]}</Badge>
                    {g.status === "draft" && <Button variant="ghost" size="icon" title="Έκδοση" onClick={() => setIssuingG(g)}><PaperPlaneTilt size={15} /></Button>}
                    <Button variant="ghost" size="icon" title="PDF" onClick={() => downloadGrammatioPdf(g)}><FilePdf size={15} /></Button>
                  </div>
                </div>
              ))}
            </TabsContent>

            <TabsContent value="paravola" className="space-y-3">
              {data.paravola.length === 0 && <p className="text-sm text-muted-foreground py-8 text-center">Δεν υπάρχουν παράβολα για την υπόθεση</p>}
              {data.paravola.map((p) => (
                <div key={p.id} className="border border-[hsl(var(--gold))]/15 rounded-xl p-4 flex items-center justify-between" data-testid={`case-paravolo-${p.id}`}>
                  <div>
                    <span className="font-mono text-sm text-[hsl(var(--gold))]">{p.number}</span>
                    <p className="text-sm">{p.category}</p>
                    <p className="text-xs text-muted-foreground font-mono">{money(p.amount)}{p.eparavolo_code ? ` · ${p.eparavolo_code}` : ""}</p>
                  </div>
                  <div className="flex items-center gap-1">
                    <Badge variant={PSTATUS[p.status]?.[1]}>{PSTATUS[p.status]?.[0]}</Badge>
                    {p.status === "draft" && <Button variant="ghost" size="icon" title="Έκδοση" onClick={() => setIssuingP(p)}><PaperPlaneTilt size={15} /></Button>}
                    {p.status === "issued" && <Button variant="ghost" size="icon" title="Πληρώθηκε" onClick={() => markParavoloPaid(p)}><CheckCircle size={15} /></Button>}
                  </div>
                </div>
              ))}
            </TabsContent>

            <TabsContent value="notes" className="space-y-3">
              <div className="flex gap-2">
                <Textarea value={noteText} onChange={(e) => setNoteText(e.target.value)} rows={2} placeholder="Νέα σημείωση..." data-testid="case-note-input" />
                <Button onClick={addNote} disabled={!noteText.trim()} data-testid="case-note-save">Προσθήκη</Button>
              </div>
              {[...(c.notes || [])].reverse().map((n, i) => (
                <div key={i} className="border border-[hsl(var(--gold))]/12 rounded-xl p-4">
                  <p className="text-sm whitespace-pre-line">{n.text}</p>
                  <p className="text-[11px] text-muted-foreground mt-2">{n.author} · {dateFmt(n.at)}</p>
                </div>
              ))}
            </TabsContent>

            <TabsContent value="history">
              <div className="space-y-0">
                {[...(c.history || [])].reverse().map((h, i) => (
                  <div key={i} className="flex items-start gap-3 py-2.5 border-b border-border/50 last:border-0">
                    <ClockCounterClockwise size={15} className="text-[hsl(var(--gold))] mt-0.5 shrink-0" />
                    <div className="text-sm">{h.event}<span className="block text-[11px] text-muted-foreground">{dateFmt(h.at)}</span></div>
                  </div>
                ))}
              </div>
            </TabsContent>
          </Tabs>
        </div>

        {/* ΔΕΞΙΑ ΣΤΗΛΗ */}
        <div className="space-y-4 stagger">
          <div className="rounded-xl border border-[hsl(var(--warning))]/40 bg-[hsl(var(--warning))]/8 p-5" data-testid="next-action-card">
            <div className="flex items-center justify-between mb-2">
              <span className="text-[10px] uppercase tracking-[0.25em] text-[hsl(var(--warning))]">Επόμενη Ενέργεια</span>
              <button onClick={() => { setActionForm({ next_action: c.next_action || "", next_action_date: c.next_action_date || "" }); setEditAction(true); }} className="text-muted-foreground hover:text-[hsl(var(--warning))]" data-testid="edit-next-action">
                <PencilSimple size={14} />
              </button>
            </div>
            <p className="text-sm">{c.next_action || "Δεν έχει οριστεί"}</p>
            {c.next_action_date && <p className="text-xs text-muted-foreground mt-1">Έως: {dateFmt(c.next_action_date)}</p>}
          </div>

          <div className="lux-card p-5">
            <div className="lux-overline mb-3">Γρήγορες Ενέργειες</div>
            <div className="space-y-2">
              <Button variant="outline" className="w-full justify-start" onClick={() => setShowGrammatio(true)} data-testid="quick-new-grammatio">
                <Lightning size={15} className="mr-2 text-[hsl(var(--gold))]" /> Νέο Γραμμάτιο
              </Button>
              <Button variant="outline" className="w-full justify-start" onClick={() => setShowParavolo(true)} data-testid="quick-new-paravolo">
                <Plus size={15} className="mr-2 text-[hsl(var(--gold))]" /> Νέο Παράβολο
              </Button>
              <Button variant="outline" className="w-full justify-start" onClick={() => document.querySelector('[data-testid="tab-notes"]')?.click()} data-testid="quick-add-note">
                <NotePencil size={15} className="mr-2 text-[hsl(var(--gold))]" /> Προσθήκη Σημείωσης
              </Button>
            </div>
          </div>

          <div className="lux-card p-5">
            <div className="lux-overline mb-3">Αλλαγή Κατάστασης</div>
            <Select value={c.status} onValueChange={changeStatus}>
              <SelectTrigger data-testid="case-status-select"><SelectValue /></SelectTrigger>
              <SelectContent>
                {Object.entries(CASE_STATUS).map(([k, v]) => <SelectItem key={k} value={k}>{v.label}</SelectItem>)}
              </SelectContent>
            </Select>
          </div>

          {c.status !== "archived" && (
            <Button variant="ghost" className="w-full text-muted-foreground hover:text-destructive" onClick={() => changeStatus("archived")} data-testid="archive-case-button">
              <Archive size={15} className="mr-2" /> Αρχειοθέτηση
            </Button>
          )}
        </div>
      </div>

      <Dialog open={editAction} onOpenChange={setEditAction}>
        <DialogContent className="max-w-sm" aria-describedby={undefined}>
          <DialogHeader><DialogTitle>Επόμενη Ενέργεια</DialogTitle></DialogHeader>
          <div className="space-y-3">
            <Input value={actionForm.next_action} onChange={(e) => setActionForm({ ...actionForm, next_action: e.target.value })} placeholder="π.χ. Κατάθεση προτάσεων" data-testid="next-action-input" />
            <Input type="date" value={actionForm.next_action_date} onChange={(e) => setActionForm({ ...actionForm, next_action_date: e.target.value })} data-testid="next-action-date" />
            <div className="flex justify-end gap-2">
              <Button variant="ghost" onClick={() => setEditAction(false)}>Ακύρωση</Button>
              <Button onClick={saveNextAction} data-testid="next-action-save">Αποθήκευση</Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>

      <GrammatioFormDialog open={showGrammatio} onOpenChange={setShowGrammatio} onSaved={load} caseInfo={caseInfo} />
      <ParavoloFormDialog open={showParavolo} onOpenChange={setShowParavolo} onSaved={load} caseInfo={caseInfo} />
      <GrammatioIssueDialog grammatio={issuingG} open={!!issuingG} onOpenChange={(v) => !v && setIssuingG(null)} onIssued={load} />
      <ParavoloIssueDialog paravolo={issuingP} open={!!issuingP} onOpenChange={(v) => !v && setIssuingP(null)} onIssued={load} />
    </div>
  );
}
