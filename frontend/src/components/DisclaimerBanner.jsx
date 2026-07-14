import { Warning } from "@phosphor-icons/react";

export function DisclaimerBanner({ compact = false }) {
  return (
    <div
      className={`flex items-start gap-2.5 rounded-sm border border-warning/40 bg-warning/10 text-foreground/80 ${compact ? "px-3 py-2 text-xs" : "px-4 py-3 text-sm"}`}
      data-testid="responsibility-disclaimer"
    >
      <Warning size={compact ? 16 : 18} className="text-warning shrink-0 mt-0.5" />
      <p>
        Οι υπολογισμοί ΦΠΑ, ΕΦΚΑ και μισθοδοσίας είναι εκτιμήσεις βάσει των καταχωρημένων παραστατικών και τρέχουσας νομοθεσίας.
        Η σύνδεση myDATA λειτουργεί σε <strong>προσομοίωση (mock)</strong> έως την καταχώρηση πραγματικών διαπιστευτηρίων ΑΑΔΕ.
        Την τελική ευθύνη ορθότητας, υποβολής και συμμόρφωσης φέρει ο πιστοποιημένος λογιστής/φοροτεχνικός της εταιρείας.
      </p>
    </div>
  );
}
