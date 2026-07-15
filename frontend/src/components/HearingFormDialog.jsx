import { useEffect, useState } from "react";
import axios, { API, errMsg } from "@/lib/api";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { toast } from "sonner";

const EMPTY = { court: "", room: "", date: "", time: "", pinakio_number: "", notify_threshold: 5, case_id: "", case_title: "" };

export function HearingFormDialog({ open, onOpenChange, onSaved, caseInfo }) {
  const [form, setForm] = useState(EMPTY);
  const [cases, setCases] = useState([]);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!open) return;
    setForm({ ...EMPTY, case_id: caseInfo?.id || "", case_title: caseInfo?.title || "" });
    if (!caseInfo) axios.get(`${API}/cases`).then((r) => setCases(r.data)).catch(() => {});
  }, [open, caseInfo]);

  const save = async () => {
    if (!form.court.trim()) return toast.error("Συμπληρώστε το δικαστήριο");
    if (!form.date) return toast.error("Συμπληρώστε την ημερομηνία δικασίμου");
    if (!form.pinakio_number || Number(form.pinakio_number) <= 0) return toast.error("Συμπληρώστε τον αριθμό πινακίου");
    setSaving(true);
    try {
      await axios.post(`${API}/hearings`, {
        ...form,
        pinakio_number: Number(form.pinakio_number),
        notify_threshold: Number(form.notify_threshold || 5),
      });
      toast.success("Το πινάκιο καταχωρήθηκε");
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
        <DialogHeader><DialogTitle>Νέο Πινάκιο / Δικάσιμος</DialogTitle></DialogHeader>
        <div className="space-y-4">
          {caseInfo ? (
            <p className="text-xs text-muted-foreground">Υπόθεση: <span className="text-[hsl(var(--gold))]">{caseInfo.number} — {caseInfo.title}</span></p>
          ) : (
            <div>
              <Label>Υπόθεση</Label>
              <Select value={form.case_id} onValueChange={(v) => setForm({ ...form, case_id: v, case_title: cases.find((c) => c.id === v)?.title || "" })}>
                <SelectTrigger className="mt-1.5" data-testid="hearing-case-select"><SelectValue placeholder="Επιλέξτε υπόθεση (προαιρετικό)" /></SelectTrigger>
                <SelectContent>{cases.map((c) => <SelectItem key={c.id} value={c.id}>{c.number} — {c.title}</SelectItem>)}</SelectContent>
              </Select>
            </div>
          )}
          <div>
            <Label>Δικαστήριο *</Label>
            <Input value={form.court} onChange={(e) => setForm({ ...form, court: e.target.value })} className="mt-1.5" placeholder="π.χ. Μονομελές Πρωτοδικείο Αθηνών" data-testid="hearing-court-input" />
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <Label>Ημερομηνία *</Label>
              <Input type="date" value={form.date} onChange={(e) => setForm({ ...form, date: e.target.value })} className="mt-1.5" data-testid="hearing-date-input" />
            </div>
            <div>
              <Label>Ώρα</Label>
              <Input type="time" value={form.time} onChange={(e) => setForm({ ...form, time: e.target.value })} className="mt-1.5" />
            </div>
          </div>
          <div className="grid grid-cols-3 gap-3">
            <div>
              <Label>Αρ. Πινακίου *</Label>
              <Input type="number" value={form.pinakio_number} onChange={(e) => setForm({ ...form, pinakio_number: e.target.value })} className="mt-1.5" data-testid="hearing-number-input" />
            </div>
            <div>
              <Label>Αίθουσα</Label>
              <Input value={form.room} onChange={(e) => setForm({ ...form, room: e.target.value })} className="mt-1.5" />
            </div>
            <div>
              <Label>Ειδοπ. πριν από</Label>
              <Input type="number" value={form.notify_threshold} onChange={(e) => setForm({ ...form, notify_threshold: e.target.value })} className="mt-1.5" title="Νούμερα πριν το δικό σας" />
            </div>
          </div>
          <div className="flex justify-end gap-2">
            <Button variant="ghost" onClick={() => onOpenChange(false)}>Ακύρωση</Button>
            <Button onClick={save} disabled={saving} data-testid="hearing-save-button">{saving ? "Αποθήκευση..." : "Καταχώρηση"}</Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}
