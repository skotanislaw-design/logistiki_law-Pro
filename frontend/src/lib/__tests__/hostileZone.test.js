/**
 * Η ζώνη του μηχανήματος των tests πρέπει να έχει, σε ΚΑΘΕ στιγμή, άλλη ημερομηνία από την Ελλάδα.
 * (Πριν ήταν σταθερή: η Pacific/Pago_Pago συμφωνεί με την Ελλάδα στις απογευματινές ώρες, όταν τρέχουν συνήθως τα tests.)
 */
import { describe, expect, it } from "vitest";

import { HOSTILE_CANDIDATES, dayIn, hostileZone } from "@/test/hostileZone.mjs";

const HOUR = 3_600_000;
// Προσοχή: `undefined` ως δεύτερο όρισμα ενεργοποιεί την προεπιλογή του (`process.env.NOMOS_TEST_TZ`), άρα με ρητή ζώνη στο
// περιβάλλον τα tests θα έβλεπαν αυτήν και όχι την προσαρμοζόμενη επιλογή. Το `null` δηλώνει «καμία ρητή ζώνη».
const instants = (from, hours) => Array.from({ length: hours }, (_, i) => new Date(Date.parse(from) + i * HOUR));

describe("hostileZone", () => {
  it("σε κάθε ώρα του εικοσιτετραώρου επιλέγει ζώνη με ΑΛΛΗ ημερομηνία από την Ελλάδα", () => {
    for (const now of instants("2026-10-14T00:00:00Z", 24)) {
      const zone = hostileZone(now, null);
      expect(dayIn(zone, now), `στις ${now.toISOString()} επιλέχθηκε ${zone}`).not.toBe(dayIn("Europe/Athens", now));
    }
  });

  it("το ίδιο ισχύει γύρω από τις αλλαγές θερινής/χειμερινής ώρας και την αλλαγή του έτους", () => {
    for (const from of ["2026-03-28T00:00:00Z", "2026-10-24T00:00:00Z", "2026-12-31T00:00:00Z", "2027-03-27T00:00:00Z"]) {
      for (const now of instants(from, 72)) {
        const zone = hostileZone(now, null);
        expect(dayIn(zone, now), `στις ${now.toISOString()} επιλέχθηκε ${zone}`).not.toBe(dayIn("Europe/Athens", now));
      }
    }
  });

  it("αλλάζει ζώνη κατά τη διάρκεια της ημέρας — μία σταθερή ζώνη δεν αρκεί", () => {
    const used = new Set(instants("2026-10-14T00:00:00Z", 24).map((now) => hostileZone(now, null)));
    expect(used.size).toBeGreaterThan(1);
    expect([...used].every((zone) => HOSTILE_CANDIDATES.includes(zone))).toBe(true);
  });

  it("η ρητή επιλογή (NOMOS_TEST_TZ) υπερισχύει", () => {
    expect(hostileZone(new Date("2026-10-14T12:00:00Z"), "America/Los_Angeles")).toBe("America/Los_Angeles");
  });

  it("η ημερομηνία υπολογίζεται σε μορφή ΕΕΕΕ-ΜΜ-ΗΗ", () => {
    expect(dayIn("Europe/Athens", new Date("2026-10-14T22:30:00Z"))).toBe("2026-10-15");
    expect(dayIn("Pacific/Pago_Pago", new Date("2026-10-14T05:00:00Z"))).toBe("2026-10-13");
  });
});
