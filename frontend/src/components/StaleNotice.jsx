import { WarningCircle } from "@phosphor-icons/react";
import { Button } from "@/components/ui/button";
import { formatTime } from "@/lib/utils";

/**
 * Μόνιμη ένδειξη όταν η τελευταία ανανέωση «ζωντανών» στοιχείων απέτυχε. Η ειδοποίηση (toast) χάνεται σε λίγα δευτερόλεπτα·
 * ένας πίνακας που δείχνει ξεπερασμένο νούμερο δίχως καμία ένδειξη είναι χειρότερος από έναν που λέει ότι δεν ενημερώθηκε.
 */
export function StaleNotice({ failed, loaded, lastOkAt, onRetry, testId, subject = "τα στοιχεία" }) {
  if (!failed) return null;
  const since = lastOkAt ? formatTime(lastOkAt, { hour: "2-digit", minute: "2-digit", hourCycle: "h23" }) : "";
  return (
    <div role="alert" data-testid={testId}
      className="rounded-xl border border-[hsl(var(--destructive))]/40 bg-[hsl(var(--destructive))]/10 p-3 text-sm flex items-center gap-3">
      <WarningCircle size={20} className="shrink-0 text-[hsl(var(--destructive))]" />
      <div className="flex-1">
        {loaded
          ? `Δεν ενημερώθηκε από ${since} — ${subject} που βλέπετε μπορεί να έχουν αλλάξει.`
          : `Δεν ήταν δυνατή η φόρτωση: ${subject} δεν εμφανίζονται.`}
      </div>
      <Button size="sm" variant="outline" onClick={onRetry} data-testid={`${testId}-retry`}>Επανάληψη</Button>
    </div>
  );
}
