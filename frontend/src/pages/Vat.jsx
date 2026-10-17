import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import axios, { API, money, dateFmt, errMsg } from "@/lib/api";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { DisclaimerBanner } from "@/components/DisclaimerBanner";
import { toast } from "sonner";

export default function Vat() {
  const [data, setData] = useState(null);
  const [settings, setSettings] = useState(null);

  const load = () => {
    axios.get(`${API}/dashboard/summary`).then((r) => setData(r.data)).catch((e) => toast.error(errMsg(e)));
    axios.get(`${API}/settings`).then((r) => setSettings(r.data)).catch(() => {});
  };
  useEffect(load, []);

  const updateVatPeriod = async (value) => {
    try {
      // Μόνο το πεδίο που άλλαξε: ένα ολόκληρο PUT από τις ρυθμίσεις που φορτώθηκαν στη σελίδα θα έγραφε πάνω σε ό,τι
      // άλλαξε στο μεταξύ (π.χ. τον πίνακα εκπτωσιμότητας ΦΠΑ).
      await axios.patch(`${API}/settings`, { vat_period: value });
      toast.success("Η περίοδος ΦΠΑ ενημερώθηκε");
      load();
    } catch (e) {
      toast.error(errMsg(e));
    }
  };

  if (!data || !settings) return <div className="text-muted-foreground">Φόρτωση...</div>;

  const breakdown = [24, 13, 6, 0];

  return (
    <div className="space-y-8" data-testid="vat-page">
      <div className="flex items-center justify-between">
        <div>
          <div className="lux-overline mb-1.5">Nomos One · Οικονομικά</div>
          <h1 className="font-brand text-4xl font-bold tracking-wide">ΦΠΑ</h1>
          <p className="text-sm text-muted-foreground mt-1">Εκκροές - Εισροές τρέχοντος μήνα & προθεσμία υποβολής</p>
        </div>
        <div className="w-48">
          <Select value={settings.vat_period} onValueChange={updateVatPeriod}>
            <SelectTrigger data-testid="vat-period-select"><SelectValue /></SelectTrigger>
            <SelectContent>
              <SelectItem value="monthly">Μηνιαία Δήλωση</SelectItem>
              <SelectItem value="quarterly">Τριμηνιαία Δήλωση</SelectItem>
            </SelectContent>
          </Select>
        </div>
      </div>

      <DisclaimerBanner />

      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <div className="lux-card p-6">
          <span className="text-xs uppercase tracking-[0.15em] font-semibold text-muted-foreground">ΦΠΑ Εκροών (Πωλήσεις)</span>
          <p className="font-mono text-2xl mt-2" data-testid="output-vat">{money(data.output_vat)}</p>
        </div>
        <div className="lux-card p-6">
          <span className="text-xs uppercase tracking-[0.15em] font-semibold text-muted-foreground">ΦΠΑ Εισροών (Έξοδα)</span>
          <p className="font-mono text-2xl mt-2" data-testid="input-vat">{money(data.input_vat)}</p>
          {data.input_vat_nondeductible > 0 && (
            <p className="text-xs text-muted-foreground mt-2" data-testid="input-vat-nondeductible">
              Εκτός του ποσού: {money(data.input_vat_nondeductible)} ΦΠΑ που δεν εκπίπτει (έχει γίνει κόστος).
            </p>
          )}
          {data.input_vat_unruled > 0 && (
            <p className="text-xs text-[hsl(var(--warning))] mt-2" data-testid="input-vat-unruled">
              {money(data.input_vat_unruled)} εκπίπτουν 100% χωρίς να έχει οριστεί κανόνας εκπτωσιμότητας.{" "}
              <Link to="/settings" className="underline">Ορίστε τους κανόνες στις Ρυθμίσεις</Link> με τον λογιστή σας.
            </p>
          )}
        </div>
        <div className="lux-card p-6 bg-destructive/5">
          <span className="text-xs uppercase tracking-[0.15em] font-semibold text-muted-foreground">ΦΠΑ προς Απόδοση</span>
          <p className="font-mono text-2xl mt-2 text-destructive" data-testid="vat-net-due">{money(data.vat_due)}</p>
        </div>
      </div>

      <div className="lux-card p-6">
        <h3 className="font-heading text-lg font-medium mb-4">Προθεσμία Υποβολής/Πληρωμής</h3>
        <p className="text-sm text-muted-foreground">
          Η επόμενη {settings.vat_period === "monthly" ? "μηνιαία" : "τριμηνιαία"} δήλωση ΦΠΑ πρέπει να υποβληθεί και πληρωθεί έως:
        </p>
        <p className="font-mono text-3xl mt-3" data-testid="vat-deadline-date">{dateFmt(data.next_vat_deadline)}</p>
      </div>

      <div className="lux-card p-6">
        <h3 className="font-heading text-lg font-medium mb-4">Συντελεστές ΦΠΑ Ελλάδας</h3>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
          {breakdown.map((r) => (
            <div key={r} className="border border-border rounded-sm p-3 text-center">
              <p className="font-mono text-xl font-semibold">{r}%</p>
              <p className="text-xs text-muted-foreground mt-1">
                {r === 24 ? "Κανονικός" : r === 13 ? "Μειωμένος" : r === 6 ? "Υπερμειωμένος" : "Απαλλαγή"}
              </p>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
