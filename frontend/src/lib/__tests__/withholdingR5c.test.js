/**
 * Κατηγορίες παρακράτησης — δεύτερος έλεγχος του γύρου 5 (δοκιμές μεταλλάξεων).
 *
 * Οι τρεις κατηγορίες του myDATA και το συνήθως ισχύον ποσοστό τους είναι νομικό δεδομένο: ένα λάθος εδώ (π.χ. 25% αντί για 20%
 * στην «3») θα προτεινόταν σιωπηλά σε κάθε γραφείο που αλλάζει κατηγορία. Ο συγχρονισμός με τον server ελέγχεται και από το
 * backend/tests/test_unit_payroll_settings_r5.py· εδώ ελέγχεται ο πίνακας όπως τον βλέπει η οθόνη.
 */
import { describe, expect, it } from "vitest";

import { WITHHOLDING_CATEGORIES, parseRatePercent, percentToRate } from "@/lib/withholding";

describe("WITHHOLDING_CATEGORIES", () => {
  it("έχει ακριβώς τις κατηγορίες 2, 3 και 10, με ποσοστά 20%, 20% και 15%", () => {
    expect(WITHHOLDING_CATEGORIES.map(({ value, rate }) => [value, rate])).toEqual([["2", 0.2], ["3", 0.2], ["10", 0.15]]);
  });

  it("κάθε κατηγορία έχει ετικέτα που ξεκινά με τον αριθμό της και τα ποσοστά περνούν ανέγγιχτα από το πεδίο στον server", () => {
    for (const { value, label, rate } of WITHHOLDING_CATEGORIES) {
      expect(label.startsWith(`${value} — `)).toBe(true);
      const typed = String(Math.round(rate * 10000) / 100);                       // ό,τι δείχνει το πεδίο
      expect(percentToRate(parseRatePercent(typed))).toBe(rate);                 // ό,τι στέλνεται
    }
  });
});
