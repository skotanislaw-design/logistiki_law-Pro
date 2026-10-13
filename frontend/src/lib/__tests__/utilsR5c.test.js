/**
 * Ποσά — δεύτερος έλεγχος του γύρου 5 (δοκιμές μεταλλάξεων): `unpaidTotal` (lib/utils.js) και `money` (lib/api.js).
 *
 * Κάθε ποσό που βλέπει ο χρήστης πρέπει να είναι ακριβώς στα λεπτά και ποτέ «NaN €».
 */
import { describe, expect, it } from "vitest";

import { money } from "@/lib/api";
import { unpaidTotal } from "@/lib/utils";

const row = (client, total, outstanding = total, status = "issued") => ({ client_id: client, total, outstanding, status });

describe("unpaidTotal — το τελικό άθροισμα στρογγυλεύεται και μεταξύ πελατών", () => {
  it("0,1 € ενός πελάτη + 0,2 € άλλου = 0,3 € (όχι 0,30000000000000004)", () => {
    expect(unpaidTotal([row("a", 0.1), row("b", 0.2)])).toBe(0.3);
  });

  it("τρεις πελάτες με 0,1 + 0,2 + 0,3 € δίνουν 0,6 €", () => {
    expect(unpaidTotal([row("a", 0.1), row("b", 0.2), row("c", 0.3)])).toBe(0.6);
  });

  it("οι δεκάδες πελάτες δεν συσσωρεύουν σφάλμα κινητής υποδιαστολής: 30 × 0,1 € = 3 €", () => {
    const rows = Array.from({ length: 30 }, (_, i) => row(`c${i}`, 0.1));
    expect(unpaidTotal(rows)).toBe(3);
  });

  it("το πιστωτικό ενός πελάτη συμψηφίζεται με τα υπόλοιπα του ίδιου πελάτη και όχι των άλλων", () => {
    expect(unpaidTotal([row("a", 100), row("a", -30), row("b", 20), row("c", -50)])).toBe(90);
  });
});

describe("money — ποσό σε ευρώ, ελληνική μορφή", () => {
  it.each([["άγνωστο (undefined)", undefined], ["κενό (null)", null], ["κενό κείμενο", ""], ["NaN", NaN], ["μηδέν", 0]])(
    "το %s γράφεται 0,00 € και ποτέ «NaN €»", (_label, value) => {
      expect(money(value)).toMatch(/^0,00\s€$/);
      expect(money(value)).not.toMatch(/NaN/);
    });

  it.each([[1240, "1.240,00"], [124.5, "124,50"], [-2, "-2,00"], [0.01, "0,01"]])("το %s γράφεται %s €", (value, text) => {
    expect(money(value)).toMatch(new RegExp(`^${text.replace(/[.\\]/g, "\\$&")}\\s€$`));
  });
});
