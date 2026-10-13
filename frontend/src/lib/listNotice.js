import { toast } from "sonner";

/**
 * Ειδοποίηση «η λίστα είναι κομμένη».
 *
 * Οι λίστες της εφαρμογής δεν σελιδοποιούνται· ο server επιστρέφει το πολύ N εγγραφές και, όταν υπάρχουν και άλλες, προσθέτει
 * στην απάντηση την κεφαλίδα `X-List-Truncated: <N>`. Χωρίς αυτή την ειδοποίηση μια κομμένη λίστα μοιάζει πλήρης — ένα σύνολο
 * που δεν ισούται με αυτό που βλέπει ο χρήστης, ή ένα έγγραφο που «δεν υπάρχει» επειδή έμεινε εκτός. Η ειδοποίηση μπαίνει
 * σε ΕΝΑ σημείο (interceptor του axios, βλ. api.js), ώστε καμία οθόνη να μη χρειάζεται να το θυμηθεί, και εμφανίζεται μία
 * φορά ανά λίστα μέχρι να ξαναγίνει πλήρης (οι οθόνες που ανανεώνονται κάθε 15″ δεν πρέπει να τη ξαναβγάζουν συνεχώς).
 *
 * Οθόνη που έχει δικό της, ακριβέστερο μήνυμα ζητά `axios.get(url, { silentTruncation: true })`.
 */
export const TRUNCATED_HEADER = "x-list-truncated";

// [μοτίβο διαδρομής, τι είναι η λίστα]. Ό,τι δεν ταιριάζει παίρνει γενική διατύπωση.
const LABELS = [
  [/\/bank\/transactions$/, "Κινήσεις τράπεζας"],
  [/\/cases$/, "Υποθέσεις"],
  [/\/cases\/[^/]+$/, "Η υπόθεση"],
  [/\/expenses$/, "Έξοδα"],
  [/\/grammatia$/, "Γραμμάτια"],
  [/\/hearings$/, "Πινάκια"],
  [/\/kyc\/records$/, "Φάκελοι KYC"],
  [/\/paravola$/, "Παράβολα"],
  [/\/payroll\/employees$/, "Εργαζόμενοι"],
  [/\/payroll\/runs$/, "Μισθοδοσίες"],
  [/\/trust\/accounts$/, "Λογαριασμοί ξένων κεφαλαίων"],
  [/\/trust\/accounts\/[^/]+$/, "Κινήσεις λογαριασμού"],
  [/\/portal-chat\/messages\/[^/]+$/, "Συνομιλία"],
  [/\/portal\/[^/]+\/chat$/, "Συνομιλία"],
];

const announced = new Set();

/** Μόνο για tests: ξεχνά ποιες λίστες έχουν ήδη ανακοινωθεί. */
export function resetListNotices() {
  announced.clear();
}

function pathOf(config) {
  try {
    return new URL(config?.url || "", "http://x").pathname;
  } catch {
    return String(config?.url || "").split("?")[0];
  }
}

export function labelFor(path) {
  const hit = LABELS.find(([pattern]) => pattern.test(path));
  return hit ? hit[1] : "Η λίστα";
}

/** Interceptor απαντήσεων: ανακοινώνει (μία φορά) τις κομμένες λίστες και αφήνει την απάντηση αμετάβλητη. */
export function announceTruncation(response) {
  const config = response?.config;
  if (config?.silentTruncation) return response;
  const path = pathOf(config);
  const limit = response?.headers?.[TRUNCATED_HEADER];
  if (!limit) {
    announced.delete(path);                    // η λίστα ξαναέγινε πλήρης: μια μελλοντική κοπή θα ανακοινωθεί ξανά
    return response;
  }
  if (!announced.has(path)) {
    announced.add(path);
    toast.warning(`${labelFor(path)}: εμφανίζονται μόνο ${limit} εγγραφές — υπάρχουν και άλλες που δεν φαίνονται.`, {
      id: `list-truncated:${path}`,
      duration: 12000,
    });
  }
  return response;
}
