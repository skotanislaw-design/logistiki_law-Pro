/**
 * Βοηθός δοκιμών: μετακινεί το «μηχάνημα» σε άλλη ζώνη ώρας και παγώνει το ρολόι.
 *
 * Η εφαρμογή δείχνει και υπολογίζει ημερομηνίες Ελλάδας (src/lib/utils.js). Ένας browser όμως τρέχει σε οποιαδήποτε ζώνη —
 * ο δικηγόρος στο εξωτερικό, ο υπολογιστής του CI σε UTC. Ένα test που τρέχει μόνο στη ζώνη του μηχανήματος που το
 * εκτελεί δεν αποδεικνύει τίποτα για τα υπόλοιπα, γι' αυτό τα tests ώρας επαναλαμβάνονται σε ζώνες πριν, μετά και
 * μακριά από την Ελλάδα. (Η προεπιλεγμένη ζώνη όλης της suite ορίζεται στο vitest.config.mjs.)
 */
import { afterEach, beforeEach, vi } from "vitest";

export const MACHINE_ZONES = ["UTC", "Europe/Athens", "Pacific/Kiritimati", "Pacific/Pago_Pago", "America/Los_Angeles"];

export function setMachineZone(zone) {
  const before = process.env.TZ;
  process.env.TZ = zone;
  return () => {
    if (before === undefined) delete process.env.TZ;
    else process.env.TZ = before;
  };
}

/** Παγώνει το `Date` (όχι τους χρονομετρητές) στη δοσμένη στιγμή ISO, π.χ. "2026-10-11T22:30:00Z". */
export function freezeAt(iso) {
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(new Date(iso));
}

/** Μέσα σε describe: το μηχάνημα μπαίνει στη ζώνη πριν από κάθε test και το ρολόι ξεπαγώνει μετά από αυτό. */
export function useMachineZone(zone) {
  let restore = () => {};
  beforeEach(() => { restore = setMachineZone(zone); });
  afterEach(() => { vi.useRealTimers(); restore(); });
}
