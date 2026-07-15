import { useEffect, useState } from "react";
import axios, { API, money, errMsg } from "@/lib/api";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Warning, ArrowSquareOut } from "@phosphor-icons/react";
import { toast } from "sonner";

export function ParavoloFormDialog({ open, onOpenChange, onSaved, caseInfo }) {
  const [categories, setCategories] = useState([]);
  const [form, setForm] = useState({ category: "", description: "", amount: "" });
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!open) return;
    axios.get(`${API}/paravola/categories/list`).then((r) => setCategories(r.data)).catch(() => {});
    setForm({ category: "", description: "", amount: "" });
  }, [open]);

  const save = async () => {
    if (!form.category) return toast.error("Επιλέξτε κατηγορία παραβόλου");
    if (!form.amount || Number(form.amount) <= 0) return toast.error("Συμπληρώστε το ποσό");
    setSaving(true);
    try {
      await axios.post(`${API}/paravola`, {
        category: form.category,
        description: form.description,
        amount: Number(form.amount),
        case_id: caseInfo?.id || "",
        case_title: caseInfo?.title || "",
      });
      toast.success("Το παράβολο δημιουργήθηκε (προσχέδιο)");
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
      <DialogContent className="max-w-md" aria-describedby={undefined}>
        <DialogHeader><DialogTitle>Νέο Παράβολο (e-Παράβολο)</DialogTitle></DialogHeader>
        <div className="space-y-4">
          {caseInfo && <p className="text-xs text-muted-foreground">Υπόθεση: <span className="text-[hsl(var(--gold))]">{caseInfo.number} — {caseInfo.title}</span></p>}
          <div>
            <Label>Κατηγορία</Label>
            <Select value={form.category} onValueChange={(v) => setForm({ ...form, category: v })}>
              <SelectTrigger className="mt-1.5" data-testid="paravolo-category-select"><SelectValue placeholder="Επιλέξτε κατηγορία" /></SelectTrigger>
              <SelectContent>{categories.map((c) => <SelectItem key={c} value={c}>{c}</SelectItem>)}</SelectContent>
            </Select>
          </div>
          <div>
            <Label>Περιγραφή</Label>
            <Input value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} className="mt-1.5" placeholder="π.χ. Έφεση κατά απόφασης 123/2026" data-testid="paravolo-description-input" />
          </div>
          <div>
            <Label>Ποσό (€)</Label>
            <Input type="number" step="0.01" value={form.amount} onChange={(e) => setForm({ ...form, amount: e.target.value })} className="mt-1.5" data-testid="paravolo-amount-input" />
          </div>
          <div className="flex justify-end gap-2">
            <Button variant="ghost" onClick={() => onOpenChange(false)}>Ακύρωση</Button>
            <Button onClick={save} disabled={saving} data-testid="paravolo-save-button">{saving ? "Αποθήκευση..." : "Αποθήκευση Προσχεδίου"}</Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}

export function ParavoloIssueDialog({ paravolo, open, onOpenChange, onIssued }) {
  const [code, setCode] = useState("");
  const [issuing, setIssuing] = useState(false);
  if (!paravolo) return null;

  const issue = async () => {
    setIssuing(true);
    try {
      await axios.post(`${API}/paravola/${paravolo.id}/issue`, { eparavolo_code: code });
      toast.success(`Το παράβολο ${paravolo.number} εκδόθηκε`);
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
      <DialogContent className="max-w-md" aria-describedby={undefined} data-testid="paravolo-issue-dialog">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Warning size={20} className="text-[hsl(var(--warning))]" /> Επιβεβαίωση Έκδοσης — Τελικό Βήμα
          </DialogTitle>
        </DialogHeader>
        <div className="space-y-4 text-sm">
          <div className="border border-[hsl(var(--gold))]/25 rounded-xl p-4 space-y-1">
            <p className="font-mono text-[hsl(var(--gold))]">{paravolo.number}</p>
            <p>{paravolo.category}</p>
            {paravolo.description && <p className="text-muted-foreground">{paravolo.description}</p>}
            <p className="font-mono text-lg">{money(paravolo.amount)}</p>
          </div>
          <p className="text-muted-foreground">
            Η επίσημη έκδοση γίνεται στο e-Παράβολο (ΓΓΠΣ). Εκδώστε εκεί το παράβολο και καταχωρήστε τον κωδικό του:
          </p>
          <a href="https://www1.gsis.gr/sgsisapps/eparavolo/public/welcome.htm" target="_blank" rel="noreferrer" className="flex items-center gap-1.5 text-[hsl(var(--gold))] hover:underline" data-testid="eparavolo-link">
            e-Παράβολο (gsis.gr) <ArrowSquareOut size={14} />
          </a>
          <Input value={code} onChange={(e) => setCode(e.target.value)} placeholder="Κωδικός e-Παραβόλου (προαιρετικό)" className="font-mono" data-testid="paravolo-code-input" />
          <div className="flex justify-end gap-2">
            <Button variant="ghost" onClick={() => onOpenChange(false)}>Ακύρωση</Button>
            <Button onClick={issue} disabled={issuing} data-testid="paravolo-confirm-issue">{issuing ? "Έκδοση..." : "Επιβεβαίωση Έκδοσης"}</Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}
