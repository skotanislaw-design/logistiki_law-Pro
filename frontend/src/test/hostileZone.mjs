/**
 * Η ζώνη ώρας του «μηχανήματος» των tests: ΠΑΝΤΑ με διαφορετική ημερομηνία από την ελληνική.
 *
 * Η εφαρμογή υπολογίζει ημερομηνίες Ελλάδας (src/lib/utils.js)· ένας browser όμως τρέχει σε οποιαδήποτε ζώνη. Ένα test που
 * τρέχει σε ζώνη με την ΙΔΙΑ ημερομηνία με την Ελλάδα αποδεικνύει μόνο ότι ο κώδικας δουλεύει εκεί όπου το λάθος δεν φαίνεται.
 * Μία σταθερή ζώνη δεν αρκεί: η Pacific/Pago_Pago (UTC−11) έχει άλλη ημερομηνία από την Ελλάδα μόνο τις πρωινές ώρες
 * Ελλάδας· τις απογευματινές, ακριβώς όταν τρέχει το CI ή ο προγραμματιστής, έχει την ίδια. Η Pacific/Kiritimati (UTC+14)
 * καλύπτει το άλλο μισό του εικοσιτετραώρου. Σε κάθε στιγμή τουλάχιστον μία από τις δύο διαφέρει — εδώ επιλέγεται εκείνη.
 * (Ίδια λογική με το backend/tests/conftest.py.) `NOMOS_TEST_TZ=<ζώνη>` επιλέγει ρητά άλλη.
 */
export const HOSTILE_CANDIDATES = ["Pacific/Kiritimati", "Pacific/Pago_Pago"];

/** Η ημερομηνία ΕΕΕΕ-ΜΜ-ΗΗ της στιγμής `now` στη ζώνη `zone`. */
export const dayIn = (zone, now = new Date()) =>
  new Intl.DateTimeFormat("en-CA", { timeZone: zone, year: "numeric", month: "2-digit", day: "2-digit" }).format(now);

export function hostileZone(now = new Date(), explicit = process.env.NOMOS_TEST_TZ) {
  if (explicit) return explicit;
  const athens = dayIn("Europe/Athens", now);
  return HOSTILE_CANDIDATES.find((zone) => dayIn(zone, now) !== athens) || "Pacific/Pago_Pago";
}
