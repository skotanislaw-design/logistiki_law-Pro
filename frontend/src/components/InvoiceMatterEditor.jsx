import { useState } from "react";
import axios, { API, errMsg } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { NONE, caseLabel, lawyerAfterCaseChange, useMatterLookups, withCurrent } from "@/lib/matters";
import { PencilSimple } from "@phosphor-icons/react";
import { toast } from "sonner";

/**
 * Σε ποια υπόθεση και σε ποιον δικηγόρο ανήκει ένα τιμολόγιο — και μετά την έκδοσή του.
 *
 * Ένα εκδοθέν τιμολόγιο δεν τροποποιείται (είναι το παραστατικό που διαβιβάστηκε στην ΑΑΔΕ), η υπόθεση όμως και ο
 * δικηγόρος είναι μεταδεδομένα οργάνωσης που δεν το αγγίζουν. Γι' αυτό υπάρχει χωριστό endpoint (`PATCH /invoices/:id/matter`)
 * που αλλάζει μόνο αυτά τα δύο πεδία. Οι λίστες φορτώνουν μόνο όταν χρειάζονται: όταν το τιμολόγιο έχει ήδη σύνδεση (για να
 * φανούν ονόματα) ή όταν ο χρήστης πατήσει «Αλλαγή».
 */
export function InvoiceMatterEditor({ invoice, onSaved }) {
  const [matter, setMatter] = useState({ case_id: invoice.case_id || "", lawyer_id: invoice.lawyer_id || "" });
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(matter);
  const [saving, setSaving] = useState(false);
  const tagged = !!(matter.case_id || matter.lawyer_id);
  const lookups = useMatterLookups(editing || tagged, { clients: false });

  const caseOptions = withCurrent(
    lookups.cases.map((c) => ({ value: c.id, label: caseLabel(c) + (c.client_name ? ` (${c.client_name})` : "") })),
    draft.case_id, "(άγνωστη υπόθεση)");
  const lawyerOptions = withCurrent(
    lookups.lawyers.map((u) => ({ value: u.id, label: u.name || u.email })),
    draft.lawyer_id, "(άγνωστος χρήστης)");

  const caseRow = lookups.cases.find((c) => c.id === matter.case_id);
  const lawyerRow = lookups.lawyers.find((u) => u.id === matter.lawyer_id);
  const caseText = !matter.case_id ? "—" : caseRow ? caseLabel(caseRow) : lookups.loaded ? "(άγνωστη υπόθεση)" : "…";
  const lawyerText = !matter.lawyer_id ? "—" : lawyerRow ? (lawyerRow.name || lawyerRow.email) : lookups.loaded ? "(άγνωστος χρήστης)" : "…";

  const save = async () => {
    setSaving(true);
    try {
      const { data } = await axios.patch(`${API}/invoices/${invoice.id}/matter`, draft);
      setMatter({ case_id: data.case_id || "", lawyer_id: data.lawyer_id || "" });
      setEditing(false);
      toast.success("Η σύνδεση του τιμολογίου ενημερώθηκε");
      onSaved?.();
    } catch (e) {
      toast.error(errMsg(e));
    } finally {
      setSaving(false);
    }
  };

  if (!editing) {
    return (
      <div className="flex items-center justify-between gap-3 text-sm" data-testid="invoice-matter-summary">
        <div className="min-w-0">
          <span className="text-muted-foreground">Υπόθεση: </span><span data-testid="invoice-matter-case">{caseText}</span>
          <span className="text-muted-foreground ml-3">Δικηγόρος: </span><span data-testid="invoice-matter-lawyer">{lawyerText}</span>
        </div>
        <Button size="sm" variant="ghost" onClick={() => { setDraft(matter); setEditing(true); }} data-testid="invoice-matter-edit">
          <PencilSimple size={14} className="mr-1" /> Αλλαγή
        </Button>
      </div>
    );
  }

  return (
    <div className="space-y-3 rounded-xl border border-border/60 p-3" data-testid="invoice-matter-form">
      <div className="grid grid-cols-2 gap-3">
        <div>
          <Label>Υπόθεση</Label>
          <Select value={draft.case_id || NONE} onValueChange={(v) => {
            const next = v === NONE ? "" : v;
            setDraft({ ...draft, case_id: next, lawyer_id: lawyerAfterCaseChange(lookups.cases, draft.case_id, next, draft.lawyer_id) });
          }}>
            <SelectTrigger className="mt-1.5" data-testid="invoice-matter-case-select"><SelectValue /></SelectTrigger>
            <SelectContent>
              <SelectItem value={NONE}>Καμία</SelectItem>
              {caseOptions.map((o) => <SelectItem key={o.value} value={o.value}>{o.label}</SelectItem>)}
            </SelectContent>
          </Select>
          {lookups.failed.cases && <p className="text-[11px] text-destructive mt-1" role="alert">Η λίστα υποθέσεων δεν φορτώθηκε.</p>}
        </div>
        <div>
          <Label>Δικηγόρος</Label>
          <Select value={draft.lawyer_id || NONE} onValueChange={(v) => setDraft({ ...draft, lawyer_id: v === NONE ? "" : v })}>
            <SelectTrigger className="mt-1.5" data-testid="invoice-matter-lawyer-select"><SelectValue /></SelectTrigger>
            <SelectContent>
              <SelectItem value={NONE}>{draft.case_id ? "Ο υπεύθυνος της υπόθεσης" : "Κανένας"}</SelectItem>
              {lawyerOptions.map((o) => <SelectItem key={o.value} value={o.value}>{o.label}</SelectItem>)}
            </SelectContent>
          </Select>
          {lookups.failed.lawyers && <p className="text-[11px] text-destructive mt-1" role="alert">Η λίστα δικηγόρων δεν φορτώθηκε.</p>}
        </div>
      </div>
      <div className="flex justify-end gap-2">
        <Button size="sm" variant="ghost" onClick={() => setEditing(false)}>Ακύρωση</Button>
        <Button size="sm" onClick={save} disabled={saving} data-testid="invoice-matter-save">{saving ? "Αποθήκευση…" : "Αποθήκευση"}</Button>
      </div>
    </div>
  );
}
