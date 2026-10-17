import { useEffect, useState } from "react";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { EXPENSE_CATEGORIES, parsePercent, rulesSignature } from "@/lib/vatRules";

/**
 * Πίνακας εκπτωσιμότητας ΦΠΑ εισροών ανά κατηγορία εξόδου.
 *
 * Το Nomos One δεν αποφασίζει τι εκπίπτει: ο πίνακας είναι δεδομένα που ορίζει ο λογιστής του γραφείου. Ένα κενό πεδίο
 * σημαίνει «δεν υπάρχει κανόνας» — όχι 100% και όχι 0 — και το σύστημα το δηλώνει στη δήλωση Φ2 («χωρίς κανόνα»). Το 100 είναι
 * έγκυρος κανόνας: «επιβεβαιώθηκε ότι εκπίπτει πλήρως».
 *
 * Το κείμενο κάθε πεδίου κρατιέται εδώ ώστε να πληκτρολογείται «33,5» χωρίς να χάνεται η υποδιαστολή, και ένα άκυρο κείμενο
 * δεν πάει ποτέ σιωπηλά στον server: ο γονέας ενημερώνεται (`onInvalidChange`) για να απενεργοποιήσει την αποθήκευση.
 * Ο γονέας δίνει `key` που αλλάζει όταν η φόρμα αντικαθίσταται απ' έξω (αποθήκευση, επαναφορά), ώστε το κείμενο να μηδενίζεται.
 */
export function VatDeductibilityRules({ rules, confirmedBy, onChange, onInvalidChange }) {
  const current = rules || {};
  const [texts, setTexts] = useState({});
  // Ο πίνακας όπως φορτώθηκε ή αποθηκεύτηκε (ο γονέας αλλάζει το `key` μετά από κάθε αποθήκευση): ό,τι διαφέρει από αυτόν
  // δεν έχει δει ακόμη ο λογιστής που ονομάζεται παρακάτω ως επιβεβαιώσας.
  const [savedSignature] = useState(() => rulesSignature(rules));
  const changedAfterConfirmation = !!(confirmedBy || "").trim() && rulesSignature(current) !== savedSignature;

  const categories = [...EXPENSE_CATEGORIES, ...Object.keys(current).filter((c) => !EXPENSE_CATEGORIES.includes(c))];
  const invalid = Object.values(texts).some((t) => parsePercent(t) === null);
  useEffect(() => {
    onInvalidChange?.(invalid);
    return () => onInvalidChange?.(false);
  }, [invalid]); // eslint-disable-line react-hooks/exhaustive-deps

  const shown = (category) => {
    const text = texts[category];
    if (text !== undefined) {
      const parsed = parsePercent(text);
      if (parsed === null || parsed === current[category]) return text;     // άκυρο ή συνεπές με την τιμή: μένει όπως πληκτρολογήθηκε
    }
    return current[category] === undefined ? "" : String(current[category]);
  };

  const edit = (category, text) => {
    setTexts((prev) => ({ ...prev, [category]: text }));
    const parsed = parsePercent(text);
    if (parsed === null) return;                                              // άκυρο: δεν αλλάζει τίποτα στη φόρμα
    const next = { ...current };
    if (parsed === undefined) delete next[category]; else next[category] = parsed;
    onChange({ vat_deductibility_rules: next });
  };

  const ruled = categories.filter((c) => current[c] !== undefined).length;

  return (
    <div className="space-y-3" data-testid="settings-vat-rules">
      <p className="text-sm text-muted-foreground">
        Ορίστε για κάθε κατηγορία εξόδου ποιο ποσοστό του ΦΠΑ εκπίπτει (0–100). Το ΦΠΑ που δεν εκπίπτει δεν μπαίνει στη δήλωση Φ2,
        γίνεται κόστος του εξόδου. <strong>Κενό = δεν υπάρχει κανόνας</strong>: το ΦΠΑ εκπίπτει 100% από υπόθεση και εμφανίζεται ως
        «χωρίς κανόνα» στη δήλωση. Τα ποσοστά ορίζονται σε συνεννόηση με τον λογιστή σας — το Nomos One δεν αποφασίζει τι εκπίπτει.
      </p>
      <div className="rounded-xl border border-border/60 divide-y divide-border/50">
        {categories.map((category) => {
          const text = texts[category];
          const bad = text !== undefined && parsePercent(text) === null;
          return (
            <div key={category} className="flex items-center justify-between gap-3 px-3 py-2">
              <Label htmlFor={`vat-rule-${category}`} className="font-normal">{category}</Label>
              <div className="flex items-center gap-2">
                <Input id={`vat-rule-${category}`} className="w-28 text-right" inputMode="decimal" placeholder="χωρίς κανόνα"
                  value={shown(category)} onChange={(e) => edit(category, e.target.value)}
                  aria-invalid={bad || undefined} data-testid={`vat-rule-${category}`} />
                <span className="text-xs text-muted-foreground w-3">%</span>
              </div>
            </div>
          );
        })}
      </div>
      {invalid && (
        <p role="alert" className="text-xs text-[hsl(var(--destructive))]" data-testid="vat-rules-error">
          Δώστε ποσοστό από 0 έως 100 (π.χ. 50 ή 33,5) ή αφήστε το πεδίο κενό. Οι ρυθμίσεις δεν αποθηκεύονται μέχρι να διορθωθεί.
        </p>
      )}
      <p className="text-xs text-muted-foreground" data-testid="vat-rules-count">
        {ruled === 0 ? "Δεν έχει οριστεί κανένας κανόνας." : `Ορισμένοι κανόνες: ${ruled} από ${categories.length} κατηγορίες.`}
      </p>
      <div>
        <Label htmlFor="vat-rules-confirmed-by">Επιβεβαιώθηκε από τον λογιστή (όνομα, ημερομηνία)</Label>
        <Input id="vat-rules-confirmed-by" className="mt-1.5" maxLength={120} placeholder="π.χ. Χριστόφορος, 20/10/2026"
          value={confirmedBy || ""} onChange={(e) => onChange({ vat_rules_confirmed_by: e.target.value })}
          data-testid="vat-rules-confirmed-by" />
        <p className="text-[11px] text-muted-foreground mt-1">
          Ενημερωτικό: εμφανίζεται στη δήλωση Φ2. Κενό σημαίνει ότι ο πίνακας δεν έχει επιβεβαιωθεί από λογιστή.
        </p>
        {changedAfterConfirmation && (
          <p role="status" className="text-xs mt-2 rounded-lg border border-[hsl(var(--warning))]/40 bg-[hsl(var(--warning))]/10 p-2"
            data-testid="vat-rules-changed-note">
            Αλλάξατε τον πίνακα μετά την επιβεβαίωση. Με την αποθήκευση η επιβεβαίωση του λογιστή αποσύρεται — η δήλωση Φ2 δεν
            θα γράφει «επιβεβαιωμένος» έναν πίνακα που δεν έχει δει. Αν τις αλλαγές τις ενέκρινε ο λογιστής, γράψτε νέο όνομα
            και ημερομηνία (π.χ. «Χριστόφορος, 27/10/2026») πριν αποθηκεύσετε.
          </p>
        )}
      </div>
    </div>
  );
}
