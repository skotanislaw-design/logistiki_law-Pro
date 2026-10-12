/**
 * Το ποσοστό παρακράτησης που πληκτρολογεί ο χρήστης (Ρυθμίσεις → Παρακράτηση φόρου).
 *
 * Κενό ή μη αριθμητικό κείμενο δεν σημαίνει «κράτα την παλιά τιμή»: το πεδίο θα έδειχνε κενό ενώ θα αποθηκευόταν σιωπηλά
 * το προηγούμενο ποσοστό. Ο έλεγχος εδώ είναι ο ίδιος που κλειδώνει το κουμπί αποθήκευσης.
 */
import { describe, expect, it } from "vitest";

import { MAX_WITHHOLDING_PERCENT, WITHHOLDING_CATEGORIES, parseRatePercent, percentToRate } from "@/lib/withholding";

describe("parseRatePercent", () => {
  it.each([
    ["20", 20], ["0", 0], ["50", 50], ["12.5", 12.5], ["12,5", 12.5], [" 7.1 ", 7.1], ["0.01", 0.01], [".5", 0.5], ["5.", 5],
    ["007", 7], [20, 20], [12.5, 12.5],
  ])("δέχεται %j ως %j", (text, expected) => {
    expect(parseRatePercent(text)).toBe(expected);
  });

  it.each([
    [""], ["   "], [null], [undefined], ["abc"], ["12abc"], ["1,2,3"], ["1.2.3"], ["-1"], ["-0.01"], ["+5"], ["50.01"], ["51"], ["100"],
    ["1e1"], ["0x10"], ["NaN"], ["Infinity"], ["-Infinity"], ["."], [","], ["20%"],
  ])("απορρίπτει %j", (text) => {
    expect(parseRatePercent(text)).toBeNull();
  });

  it("το όριο ακολουθεί αυτό του server (50%)", () => {
    expect(MAX_WITHHOLDING_PERCENT).toBe(50);
    expect(parseRatePercent("50")).toBe(50);
    expect(parseRatePercent("50.0001")).toBeNull();
  });
});

describe("percentToRate", () => {
  it.each([[20, 0.2], [15, 0.15], [12.5, 0.125], [7.1, 0.071], [0, 0], [0.01, 0.0001], [33.33, 0.3333], [50, 0.5]])(
    "%j%% αποθηκεύεται ως %j", (percent, expected) => {
      expect(percentToRate(percent)).toBe(expected);
    });

  it("δεν αφήνει σφάλμα κινητής υποδιαστολής να φτάσει στον server", () => {
    // 0.29 * 100 = 28.999999999999996 σε IEEE-754· το ποσοστό ωστόσο είναι 29%
    expect(percentToRate(29)).toBe(0.29);
    expect(String(percentToRate(14.1))).toBe("0.141");
  });

  it("κάθε προτεινόμενο ποσοστό κατηγορίας είναι έγκυρο ποσοστό", () => {
    for (const category of WITHHOLDING_CATEGORIES) {
      const percent = Math.round(category.rate * 10000) / 100;
      expect(parseRatePercent(String(percent))).toBe(percent);
      expect(percentToRate(percent)).toBe(category.rate);
    }
  });
});
