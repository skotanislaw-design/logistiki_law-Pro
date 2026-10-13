/**
 * ΔΙΟΡΘΩΘΗΚΕ (frontend/src/pages/Invoices.jsx): μια αποτυχημένη φόρτωση τιμολογίων παρουσιαζόταν ως άδεια λίστα.
 *
 * Το `load()` έπιανε το σφάλμα μόνο με ένα toast που χάνεται σε λίγα δευτερόλεπτα· η σελίδα έμενε με `invoices = []` και έδειχνε
 * μόνιμα «Δεν υπάρχουν τιμολόγια ακόμα.», «Ανείσπρακτα 0,00 €», «0 ανεξόφλητα τιμολόγια» και «0 τιμολόγια συνολικά» — δηλαδή
 * ότι ο λογαριασμός είναι καθαρός ενώ απλώς δεν είχε φορτώσει. Η οθόνη «Τράπεζα» είχε ήδη τη σωστή συμπεριφορά (μήνυμα σφάλματος
 * με «Επανάληψη» και «—» στα σύνολα)· τώρα την έχει και η λίστα τιμολογίων. Ιδιότητα: «μια αποτυχημένη φόρτωση δεν
 * παρουσιάζεται ποτέ ως άδεια λίστα».
 *
 * Ισχύει: ούτε το κείμενο της άδειας λίστας ούτε σύνολα «0,00 €» όσο η λίστα δεν έχει φορτώσει.
 */
import { render, screen } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";

import { Toaster } from "@/components/ui/sonner";
import Invoices from "@/pages/Invoices";
import { server } from "@/test/server";

const API = "http://localhost:8001/api";

async function renderWithFailedLoad() {
  server.use(
    http.get(`${API}/invoices`, () => HttpResponse.json({ detail: "Η βάση δεν είναι διαθέσιμη." }, { status: 503 })),
    http.get(`${API}/clients`, () => HttpResponse.json([])),
  );
  render(<><Invoices /><Toaster /></>);
  expect(await screen.findByText("Η βάση δεν είναι διαθέσιμη.")).toBeInTheDocument();       // το (προσωρινό) toast
}

describe("Τιμολόγια — αποτυχημένη φόρτωση", () => {
  it("η λίστα δεν δηλώνει «Δεν υπάρχουν τιμολόγια ακόμα» όταν απλώς δεν φόρτωσε", async () => {
    await renderWithFailedLoad();
    expect(screen.queryByText("Δεν υπάρχουν τιμολόγια ακόμα.")).not.toBeInTheDocument();
  });

  it("τα σύνολα δεν δείχνουν «0,00 €» και «0 τιμολόγια» όταν η λίστα δεν φόρτωσε", async () => {
    await renderWithFailedLoad();
    expect(screen.getByTestId("kpi-unpaid-total")).not.toHaveTextContent("0,00");
    expect(screen.getByTestId("kpi-all")).not.toHaveTextContent("0 τιμολόγια συνολικά");
  });
});
