import { useEffect, useState } from "react";
import axios, { API, money, errMsg } from "@/lib/api";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Warning, ArrowSquareOut } from "@phosphor-icons/react";
import { toast } from "sonner";

const EMPTY = { court_type: "", procedure: "", reference_amount: 0, contributions_amount: 0, vat_amount: 0, client_id: "", client_name: "", notes: "" };

export function GrammatioFormDialog({ open, onOpenChange, onSaved, caseInfo }) {
  const [reference, setReference] = useState([]);
  const [clients, setClients] = useState([]);
  const [form, setForm] = useState(EMPTY);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!open) return;
    axios.get(`${API}/grammatia/reference/table`).then((r) => setReference(r.data)).catch(() => {});
    axios.get(`${API}/clients`).then((r) => setClients(r.data)).catch(() => {});
    setForm({
      ...EMPTY,
      client_id: caseInfo?.client_id || "",
      client_name: caseInfo?.client_name || "",
    });
  }, [open, caseInfo]);

  const pickReference = (key) => {
    const ref = reference[Number(key)];
    if (!ref) return;
    const vat = Math.round(ref.amount * 0.24 * 100) / 100;
    const contrib = Math.round(ref.amount * 0.12 * 100) / 100;
    setForm({ ...form, court_type: ref.court_type, procedure: ref.procedure, reference_amount: ref.amount, vat_amount: vat, contributions_amount: contrib });
  };

  const total = Math.round((Number(form.reference_amount || 0) + Number(form.contributions_amount || 0) + Number(form.vat_amount || 0)) * 100) / 100;

  const save = async () => {
    if (!form.court_type) return toast.error("Επιλέξτε δικαστήριο/διαδικασία");
    if (!form.reference_amount) return toast.error("Συμπληρώστε την αμοιβή αναφοράς");
    setSaving(true);
    try {
      const payload = {
        ...form,
        reference_amount: Number(form.reference_amount),
        contributions_amount: Number(form.contributions_amount || 0),
        vat_amount: Number(form.vat_amount || 0),
        case_id: caseInfo?.id || "",
        case_title: caseInfo?.title || "",
      };
      await axios.post(`${API}/grammatia`, payload);
      toast.success("Το γραμμάτιο δημιουργήθηκε (προσχέδιο)");
      onOpenChange(false);
      onSaved?.();
    } catch (e) {
      toast.error(errMsg(e));
    } finally {
      setSaving(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-lg max-h-[85vh] overflow-y-auto" aria-describedby={undefined}>
        <DialogHeader><DialogTitle>Νέο Γραμμάτιο Προείσπραξης</DialogTitle></DialogHeader>
        <div className="space-y-4">
          {caseInfo && <p className="text-xs text-muted-foreground">Υπόθεση: <span className="text-[hsl(var(--gold))]">{caseInfo.number} — {caseInfo.title}</span></p>}
          <div>
            <Label>Δικαστήριο / Διαδικασία (ποσά αναφοράς Ν.4194/2013)</Label>
            <Select onValueChange={pickReference}>
              <SelectTrigger className="mt-1.5" data-testid="grammatio-reference-select"><SelectValue placeholder="Επιλέξτε από πίνακα αναφοράς" /></SelectTrigger>
              <SelectContent>
                {reference.map((r, i) => (
                  <SelectItem key={i} value={String(i)}>{r.court_type} — {r.procedure} ({money(r.amount)})</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          {!caseInfo && (
            <div>
              <Label>Πελάτης / Εντολέας</Label>
              <Select value={form.client_id} onValueChange={(v) => setForm({ ...form, client_id: v, client_name: clients.find((c) => c.id === v)?.name || "" })}>
                <SelectTrigger className="mt-1.5" data-testid="grammatio-client-select"><SelectValue placeholder="Επιλέξτε πελάτη (προαιρετικό)" /></SelectTrigger>
                <SelectContent>{clients.map((c) => <SelectItem key={c.id} value={c.id}>{c.name}</SelectItem>)}</SelectContent>
              </Select>
            </div>
          )}
          <div className="grid grid-cols-3 gap-3">
            <div>
              <Label>Αμοιβή αναφοράς (€)</Label>
              <Input type="number" step="0.01" value={form.reference_amount} onChange={(e) => {
                const v = Number(e.target.value);
                setForm({ ...form, reference_amount: v, vat_amount: Math.round(v * 24) / 100, contributions_amount: Math.round(v * 12) / 100 });
              }} className="mt-1.5" data-testid="grammatio-amount-input" />
            </div>
            <div>
              <Label>Εισφορές (€)</Label>
              <Input type="number" step="0.01" value={form.contributions_amount} onChange={(e) => setForm({ ...form, contributions_amount: e.target.value })} className="mt-1.5" />
            </div>
            <div>
              <Label>ΦΠΑ 24% (€)</Label>
              <Input type="number" step="0.01" value={form.vat_amount} onChange={(e) => setForm({ ...form, vat_amount: e.target.value })} className="mt-1.5" />
            </div>
          </div>
          <div>
            <Label>Σημειώσεις</Label>
            <Input value={form.notes} onChange={(e) => setForm({ ...form, notes: e.target.value })} className="mt-1.5" placeholder="π.χ. αρ. πινακίου, δικάσιμος..." />
          </div>
          <div className="flex items-center justify-between border-t border-[hsl(var(--gold))]/15 pt-3">
            <span className="text-sm text-muted-foreground">Σύνολο γραμματίου</span>
            <span className="font-mono text-lg text-[hsl(var(--gold))]" data-testid="grammatio-total">{money(total)}</span>
          </div>
          <div className="flex justify-end gap-2">
            <Button variant="ghost" onClick={() => onOpenChange(false)}>Ακύρωση</Button>
            <Button onClick={save} disabled={saving} data-testid="grammatio-save-button">{saving ? "Αποθήκευση..." : "Αποθήκευση Προσχεδίου"}</Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}

export function GrammatioIssueDialog({ grammatio, open, onOpenChange, onIssued }) {
  const [portalRef, setPortalRef] = useState("");
  const [issuing, setIssuing] = useState(false);
  if (!grammatio) return null;

  const issue = async () => {
    setIssuing(true);
    try {
      await axios.post(`${API}/grammatia/${grammatio.id}/issue`, { portal_ref: portalRef });
      toast.success(`Το γραμμάτιο ${grammatio.number} εκδόθηκε`);
      onOpenChange(false);
      onIssued?.();
    } catch (e) {
      toast.error(errMsg(e));
    } finally {
      setIssuing(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-md" aria-describedby={undefined} data-testid="grammatio-issue-dialog">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Warning size={20} className="text-[hsl(var(--warning))]" /> Επιβεβαίωση Έκδοσης — Τελικό Βήμα
          </DialogTitle>
        </DialogHeader>
        <div className="space-y-4 text-sm">
          <div className="border border-[hsl(var(--gold))]/25 rounded-xl p-4 space-y-1">
            <p className="font-mono text-[hsl(var(--gold))]">{grammatio.number}</p>
            <p>{grammatio.court_type} — {grammatio.procedure}</p>
            <p className="text-muted-foreground">{grammatio.client_name || "Χωρίς πελάτη"}</p>
            <p className="font-mono text-lg">{money(grammatio.total)}</p>
          </div>
          <p className="text-muted-foreground">
            Η επίσημη έκδοση γίνεται στο portal της Ολομέλειας. Εκδώστε εκεί το γραμμάτιο και προαιρετικά καταχωρήστε τον αριθμό του:
          </p>
          <a href="https://portal.olomeleia.gr" target="_blank" rel="noreferrer" className="flex items-center gap-1.5 text-[hsl(var(--gold))] hover:underline" data-testid="olomeleia-link">
            portal.olomeleia.gr <ArrowSquareOut size={14} />
          </a>
          <Input value={portalRef} onChange={(e) => setPortalRef(e.target.value)} placeholder="Αρ. γραμματίου από το portal (προαιρετικό)" data-testid="grammatio-portal-ref" />
          <div className="flex justify-end gap-2">
            <Button variant="ghost" onClick={() => onOpenChange(false)}>Ακύρωση</Button>
            <Button onClick={issue} disabled={issuing} data-testid="grammatio-confirm-issue">{issuing ? "Έκδοση..." : "Επιβεβαίωση Έκδοσης"}</Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}
