import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { NONE, SCOPE_LABEL, caseLabel, lawyerAfterCaseChange, withCurrent } from "@/lib/matters";

/**
 * Σε ποιον ανήκει ένα έξοδο και πόσο από το ΦΠΑ του εκπίπτει (Φάση 1 του «ψηφιακού λογιστή»).
 *
 * Ο server είναι η πηγή της αλήθειας (backend/dimensions.py): ο πελάτης ενός εξόδου υπόθεσης προκύπτει πάντα από την
 * υπόθεση, και αντιφατικοί συνδυασμοί απορρίπτονται. Η φόρμα απλώς δεν επιτρέπει να φτιαχτούν — αλλάζοντας εύρος καθαρίζει
 * ό,τι δεν ισχύει πια.
 */
export function ExpenseMatterFields({ form, onChange, lookups }) {
  const { cases, clients, lawyers, failed } = lookups;
  const scope = form.scope || "unassigned";
  const ownedByClient = scope === "client" || scope === "case";

  const setScope = (value) => onChange({
    scope: value,
    case_id: value === "case" ? form.case_id || "" : "",
    client_id: value === "client" ? form.client_id || "" : "",
    rebillable: value === "client" || value === "case" ? !!form.rebillable : false,
  });

  const caseOptions = withCurrent(
    cases.map((c) => ({ value: c.id, label: caseLabel(c) + (c.client_name ? ` (${c.client_name})` : "") })),
    scope === "case" ? form.case_id : "", "(άγνωστη υπόθεση)");
  const clientOptions = withCurrent(
    clients.map((c) => ({ value: c.id, label: c.name })),
    scope === "client" ? form.client_id : "", "(άγνωστος πελάτης)");
  const lawyerOptions = withCurrent(
    lawyers.map((u) => ({ value: u.id, label: u.name || u.email })),
    form.lawyer_id, "(άγνωστος χρήστης)");
  const selectedCase = cases.find((c) => c.id === form.case_id);

  return (
    <div className="space-y-3 rounded-xl border border-border/60 p-3" data-testid="expense-matter-fields">
      <div className="lux-overline">Αντιστοίχιση & ΦΠΑ</div>

      <div className="grid grid-cols-2 gap-3">
        <div>
          <Label>Σε ποιον ανήκει</Label>
          <Select value={scope} onValueChange={setScope}>
            <SelectTrigger className="mt-1.5" data-testid="expense-scope-select"><SelectValue /></SelectTrigger>
            <SelectContent>
              {Object.entries(SCOPE_LABEL).map(([value, label]) => <SelectItem key={value} value={value}>{label}</SelectItem>)}
            </SelectContent>
          </Select>
        </div>

        {scope === "case" && (
          <div>
            <Label>Υπόθεση</Label>
            <Select value={form.case_id || ""}
              onValueChange={(v) => onChange({ case_id: v, lawyer_id: lawyerAfterCaseChange(cases, form.case_id, v, form.lawyer_id) })}>
              <SelectTrigger className="mt-1.5" data-testid="expense-case-select"><SelectValue placeholder="Επιλέξτε υπόθεση" /></SelectTrigger>
              <SelectContent>{caseOptions.map((o) => <SelectItem key={o.value} value={o.value}>{o.label}</SelectItem>)}</SelectContent>
            </Select>
            {failed.cases && <p className="text-[11px] text-destructive mt-1" role="alert">Η λίστα υποθέσεων δεν φορτώθηκε.</p>}
          </div>
        )}

        {scope === "client" && (
          <div>
            <Label>Πελάτης</Label>
            <Select value={form.client_id || ""} onValueChange={(v) => onChange({ client_id: v })}>
              <SelectTrigger className="mt-1.5" data-testid="expense-client-select"><SelectValue placeholder="Επιλέξτε πελάτη" /></SelectTrigger>
              <SelectContent>{clientOptions.map((o) => <SelectItem key={o.value} value={o.value}>{o.label}</SelectItem>)}</SelectContent>
            </Select>
            {failed.clients && <p className="text-[11px] text-destructive mt-1" role="alert">Η λίστα πελατών δεν φορτώθηκε.</p>}
          </div>
        )}
      </div>

      {scope === "case" && selectedCase?.client_name && (
        <p className="text-xs text-muted-foreground" data-testid="expense-derived-client">Πελάτης υπόθεσης: {selectedCase.client_name}</p>
      )}

      <div>
        <Label>Δικηγόρος (προαιρετικό)</Label>
        <Select value={form.lawyer_id || NONE} onValueChange={(v) => onChange({ lawyer_id: v === NONE ? "" : v })}>
          <SelectTrigger className="mt-1.5" data-testid="expense-lawyer-select"><SelectValue /></SelectTrigger>
          <SelectContent>
            <SelectItem value={NONE}>{scope === "case" ? "Ο υπεύθυνος της υπόθεσης" : "Κανένας"}</SelectItem>
            {lawyerOptions.map((o) => <SelectItem key={o.value} value={o.value}>{o.label}</SelectItem>)}
          </SelectContent>
        </Select>
        {failed.lawyers && <p className="text-[11px] text-destructive mt-1" role="alert">Η λίστα δικηγόρων δεν φορτώθηκε.</p>}
      </div>

      {ownedByClient && (
        <div className="flex items-center gap-2">
          <Checkbox id="expense-rebillable" checked={!!form.rebillable} onCheckedChange={(v) => onChange({ rebillable: v === true })}
            data-testid="expense-rebillable" />
          <Label htmlFor="expense-rebillable" className="font-normal">Θα χρεωθεί στον πελάτη</Label>
        </div>
      )}

      <div>
        <Label htmlFor="expense-vat-deductible">Εκπτωσιμότητα ΦΠΑ (%)</Label>
        <Input id="expense-vat-deductible" type="number" min={0} max={100} step="any" className="mt-1.5"
          value={form.vat_deductible_pct ?? ""} placeholder="κανόνας κατηγορίας"
          onChange={(e) => onChange({ vat_deductible_pct: e.target.value === "" ? null : Number(e.target.value) })}
          data-testid="expense-vat-deductible-input" />
        <p className="text-[11px] text-muted-foreground mt-1">
          Κενό = ακολουθεί τον κανόνα της κατηγορίας στις Ρυθμίσεις· αν δεν υπάρχει κανόνας, εκπίπτει το 100% του ΦΠΑ.
          Συμπληρώστε το μόνο όταν ο λογιστής σας έχει ορίσει άλλο ποσοστό για αυτό το παραστατικό.
        </p>
      </div>
    </div>
  );
}
