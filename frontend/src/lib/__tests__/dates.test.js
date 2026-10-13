/**
 * Ημερομηνίες Ελλάδας στο frontend — οι βοηθοί του src/lib/utils.js.
 *
 * Ο server υπολογίζει προθεσμίες, αρίθμηση και αναφορές με ημερομηνίες Ελλάδας. Ο browser έχει τη ζώνη της συσκευής,
 * και το `toISOString()` δίνει πάντα UTC: μετά τα μεσάνυχτα Ελλάδας (21:00/22:00 UTC) ήταν ακόμη «χθες». Κάθε test εδώ
 * τρέχει σε πέντε ζώνες μηχανήματος και πρέπει να δίνει το ίδιο αποτέλεσμα σε όλες.
 */
import { afterEach, describe, expect, it, vi } from "vitest";

import { dateFmt } from "@/lib/api";
import { daysSince, formatDate, formatDateTime, formatTime, monthAthens, todayAthens, yearAthens } from "@/lib/utils";
import { MACHINE_ZONES, freezeAt, useMachineZone } from "@/test/machineZone";

describe.each(MACHINE_ZONES)("ώρα Ελλάδας — μηχάνημα σε %s", (zone) => {
  useMachineZone(zone);
  afterEach(() => { vi.unstubAllGlobals(); });

  describe("σήμερα", () => {
    it.each([
      // [στιγμή UTC, ημερομηνία Ελλάδας, περιγραφή]
      ["2026-10-11T22:30:00Z", "2026-10-12", "01:30 Ελλάδας (θερινή ώρα, UTC+3)"],
      ["2026-10-11T20:59:00Z", "2026-10-11", "23:59 Ελλάδας, ένα λεπτό πριν τα μεσάνυχτα"],
      ["2026-10-11T21:00:00Z", "2026-10-12", "00:00 Ελλάδας ακριβώς"],
      ["2026-12-31T21:59:00Z", "2026-12-31", "23:59 της Πρωτοχρονιάς (χειμερινή ώρα, UTC+2)"],
      ["2026-12-31T22:00:00Z", "2027-01-01", "00:00 της 1ης Ιανουαρίου"],
      ["2026-06-30T21:00:00Z", "2026-07-01", "00:00 της 1ης Ιουλίου (θερινή ώρα)"],
      ["2026-06-30T20:59:00Z", "2026-06-30", "23:59 της 30ής Ιουνίου"],
    ])("στις %s είναι %s (%s)", (instant, expected) => {
      freezeAt(instant);
      expect(todayAthens()).toBe(expected);
    });

    it("ο μήνας και το έτος ακολουθούν την ημερομηνία Ελλάδας", () => {
      freezeAt("2026-12-31T22:30:00Z");                       // 00:30 της 1ης Ιανουαρίου 2027 στην Αθήνα
      expect(monthAthens()).toBe("2027-01");
      expect(yearAthens()).toBe(2027);
      freezeAt("2026-12-31T21:30:00Z");                       // 23:30 της 31ης Δεκεμβρίου
      expect(monthAthens()).toBe("2026-12");
      expect(yearAthens()).toBe(2026);
    });

    it("ο μήνας έχει πάντα δύο ψηφία και το έτος είναι αριθμός", () => {
      freezeAt("2026-03-05T10:00:00Z");
      expect(monthAthens()).toBe("2026-03");
      expect(yearAthens()).toBe(2026);
    });
  });

  describe("προβολή ημερομηνίας", () => {
    it("μια απλή ημερομηνία δεν αλλάζει ημέρα σε καμία ζώνη", () => {
      expect(formatDate("2026-10-12")).toBe("12/10/2026");
      expect(formatDate("2026-01-01")).toBe("1/1/2026");
      expect(formatDate("2026-12-31")).toBe("31/12/2026");
    });

    it("η σφραγίδα UTC του server προβάλλεται με την ημερομηνία Ελλάδας", () => {
      expect(formatDate("2026-12-31T22:30:00+00:00")).toBe("1/1/2027");        // 00:30 της 1ης Ιανουαρίου στην Αθήνα
      expect(formatDate("2026-12-31T21:30:00+00:00")).toBe("31/12/2026");
      expect(formatDate("2026-10-11T21:00:00Z")).toBe("12/10/2026");
      expect(formatDate("2026-10-11T20:59:59Z")).toBe("11/10/2026");
    });

    it("τα κενά δείχνουν παύλα και ό,τι δεν είναι ημερομηνία επιστρέφεται όπως είναι", () => {
      for (const empty of [null, undefined, ""]) expect(formatDate(empty)).toBe("-");
      expect(formatDate("σκουπίδι")).toBe("σκουπίδι");
      expect(formatDate("σκουπίδι")).not.toMatch(/Invalid/);
    });

    it("το dateFmt της εφαρμογής είναι αυτός ο βοηθός", () => {
      expect(dateFmt("2026-12-31T22:30:00+00:00")).toBe("1/1/2027");
      expect(dateFmt(null)).toBe("-");
    });
  });

  describe("προβολή ώρας", () => {
    it("η ώρα μιας σφραγίδας UTC προβάλλεται σε ώρα Ελλάδας (θερινή και χειμερινή)", () => {
      expect(formatTime("2026-10-11T22:30:00+00:00")).toBe("1:30:00 π.μ.");         // UTC+3
      expect(formatTime("2026-12-31T22:30:00+00:00")).toBe("12:30:00 π.μ.");        // UTC+2
      expect(formatTime("2026-07-01T09:05:07Z")).toBe("12:05:07 μ.μ.");
    });

    it("δέχεται τις επιλογές μορφής του toLocaleTimeString, χωρίς να χάνει τη ζώνη", () => {
      expect(formatTime("2026-10-11T22:30:00+00:00", { hour: "2-digit", minute: "2-digit" })).toBe("01:30 π.μ.");
    });

    it("η ημερομηνία με ώρα έχει την ημερομηνία ΚΑΙ την ώρα Ελλάδας", () => {
      expect(formatDateTime("2026-10-11T22:30:00+00:00")).toBe("12/10/2026, 1:30:00 π.μ.");
      expect(formatDateTime("2026-12-31T21:59:59+00:00")).toBe("31/12/2026, 11:59:59 μ.μ.");
      expect(formatDateTime("2026-12-31T22:00:00+00:00")).toBe("1/1/2027, 12:00:00 π.μ.");
    });

    it("τα κενά δείχνουν παύλα και ό,τι δεν διαβάζεται επιστρέφεται όπως είναι", () => {
      for (const empty of [null, undefined, ""]) {
        expect(formatTime(empty)).toBe("-");
        expect(formatDateTime(empty)).toBe("-");
      }
      expect(formatTime("σκουπίδι")).toBe("σκουπίδι");
      expect(formatDateTime("σκουπίδι")).toBe("σκουπίδι");
    });
  });

  describe("σφραγίδα χωρίς ζώνη (παλιά ή εισαγόμενα δεδομένα)", () => {
    it("διαβάζεται ως UTC, όπως κάνει το backend — η ώρα δεν εξαρτάται από τη συσκευή", () => {
      expect(formatDate("2026-10-11T22:30:00")).toBe("12/10/2026");               // 01:30 της 12ης στην Αθήνα
      expect(formatTime("2026-10-11T22:30:00")).toBe("1:30:00 π.μ.");
      expect(formatDateTime("2026-10-11T22:30:00")).toBe("12/10/2026, 1:30:00 π.μ.");
    });

    it("είναι ίδια με την αντίστοιχη σφραγίδα που δηλώνει ρητά UTC", () => {
      for (const stamp of ["2026-10-11T22:30:00", "2026-12-31T22:30:00", "2026-06-30T21:00:00"]) {
        expect(formatDateTime(stamp)).toBe(formatDateTime(`${stamp}Z`));
        expect(formatDateTime(stamp)).toBe(formatDateTime(`${stamp}+00:00`));
      }
    });

    it("δέχεται κενό αντί για T, κλάσμα δευτερολέπτου και σφραγίδα χωρίς δευτερόλεπτα", () => {
      expect(formatDateTime("2026-10-11 22:30:00")).toBe("12/10/2026, 1:30:00 π.μ.");
      expect(formatDateTime("2026-10-11T22:30:00.250")).toBe("12/10/2026, 1:30:00 π.μ.");
      expect(formatDateTime("2026-10-11T22:30")).toBe("12/10/2026, 1:30:00 π.μ.");
      expect(formatDateTime("  2026-10-11T22:30:00  ")).toBe("12/10/2026, 1:30:00 π.μ.");
    });

    it("περνά στον αναλυτή ημερομηνιών μορφή ISO με «T» — οι αυστηροί αναλυτές (Safari) δεν διαβάζουν κενό αντί για «T»", () => {
      // Η V8 (Node, Chrome) δέχεται «2026-10-11 22:30:00Z» και θα έκρυβε το σφάλμα· ο Safari το διάβαζε ως «Invalid Date».
      const RealDate = Date;
      class StrictDate extends RealDate {
        constructor(...args) {
          if (typeof args[0] === "string" && /^\d{4}-\d{2}-\d{2} /.test(args[0])) super(NaN);
          else super(...args);
        }
      }
      vi.stubGlobal("Date", StrictDate);
      expect(formatDateTime("2026-10-11 22:30:00")).toBe("12/10/2026, 1:30:00 π.μ.");
      expect(formatDate("2026-10-11 22:30")).toBe("12/10/2026");
    });

    it("σφραγίδα με ρητή ζώνη και απλή ημερομηνία δεν επηρεάζονται", () => {
      expect(formatDateTime("2026-10-11T22:30:00+00:00")).toBe("12/10/2026, 1:30:00 π.μ.");
      expect(formatDateTime("2026-10-11T22:30:00Z")).toBe("12/10/2026, 1:30:00 π.μ.");
      expect(formatDateTime("2026-10-12T01:30:00+03:00")).toBe("12/10/2026, 1:30:00 π.μ.");
      expect(formatDate("2026-10-12")).toBe("12/10/2026");
    });

    it("ό,τι δεν είναι σφραγίδα επιστρέφεται όπως είναι", () => {
      for (const junk of ["2026-10-11T25:61:00", "T22:30:00", "22:30:00", "σκουπίδι"]) expect(formatDateTime(junk)).toBe(junk);
    });

    it("δεν αγγίζει τιμές που δεν είναι κείμενο", () => {
      expect(formatDateTime(Date.UTC(2026, 9, 11, 22, 30))).toBe("12/10/2026, 1:30:00 π.μ.");
      expect(formatDateTime(new Date(Date.UTC(2026, 9, 11, 22, 30)))).toBe("12/10/2026, 1:30:00 π.μ.");
    });
  });

  describe("ημέρες από μια ημερομηνία", () => {
    it("μετρά ημερολογιακές ημέρες Ελλάδας, όχι ώρες", () => {
      freezeAt("2026-10-11T22:30:00Z");                                          // 12 Οκτωβρίου, 01:30
      expect(daysSince("2026-09-12")).toBe(30);                                  // το όριο των «30+ ημερών»
      expect(daysSince("2026-09-13")).toBe(29);
      expect(daysSince("2026-10-12")).toBe(0);
      expect(daysSince("2026-10-11")).toBe(1);
    });

    it("η ημέρα αλλάζει στα μεσάνυχτα Ελλάδας, όχι του μηχανήματος", () => {
      freezeAt("2026-10-11T20:59:00Z");                                          // 23:59 της 11ης
      expect(daysSince("2026-09-12")).toBe(29);
      freezeAt("2026-10-11T21:00:00Z");                                          // 00:00 της 12ης
      expect(daysSince("2026-09-12")).toBe(30);
    });

    it("η αλλαγή θερινής/χειμερινής ώρας δεν χαλά το μέτρημα", () => {
      freezeAt("2026-10-27T10:00:00Z");                                          // μετά τις 25 Οκτωβρίου (τέλος θερινής ώρας)
      expect(daysSince("2026-10-20")).toBe(7);
      expect(daysSince("2026-10-25")).toBe(2);
      freezeAt("2026-03-30T10:00:00Z");                                          // μετά τις 29 Μαρτίου (έναρξη θερινής ώρας)
      expect(daysSince("2026-03-22")).toBe(8);
    });

    it("μελλοντική ημερομηνία δίνει αρνητικό αριθμό και μια άκυρη ή κενή δίνει 0", () => {
      freezeAt("2026-10-12T10:00:00Z");
      expect(daysSince("2026-10-15")).toBe(-3);
      for (const bad of ["", null, undefined, "σκουπίδι", "12/10/2026"]) expect(daysSince(bad)).toBe(0);
    });

    it("δέχεται και πλήρη σφραγίδα, μετρώντας από την ημερομηνία της", () => {
      freezeAt("2026-10-12T10:00:00Z");
      expect(daysSince("2026-10-02T23:59:00+00:00")).toBe(10);
    });
  });
});
