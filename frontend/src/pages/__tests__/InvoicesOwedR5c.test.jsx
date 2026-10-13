/**
 * Τιμολόγια — συμπληρωματικός έλεγχος του γύρου 5 (δοκιμές μεταλλάξεων): ποια τιμολόγια θεωρούνται «ανεξόφλητα» / «σε καθυστέρηση»
 * και πότε η γραμμή δείχνει «Οφείλεται».
 *
 * Ένα πρόχειρο ή ένα ακυρωμένο τιμολόγιο δεν οφείλεται από κανέναν: δεν πρέπει να φέρει σήμα καθυστέρησης ούτε να φουσκώνει τα
 * «Ανείσπρακτα». Και η γραμμή «Οφείλεται» πρέπει να δείχνει κάθε πραγματική διαφορά (έστω και 0,01 €) αλλά να αγνοεί το
 * σφάλμα της κινητής υποδιαστολής, ώστε ό,τι βλέπει ο χρήστης να είναι ακριβώς ό,τι κρατά ο server.
 */
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";

import { Toaster } from "@/components/ui/sonner";
import Invoices from "@/pages/Invoices";
import { MACHINE_ZONES, freezeAt, useMachineZone } from "@/test/machineZone";
import { server } from "@/test/server";

const API = "http://localhost:8001/api";
const NIGHT = "2026-10-11T22:30:00Z";                // Δευτέρα 12/10/2026 01:30 στην Αθήνα · στο UTC ακόμη 11/10

const invoice = (id, date, over = {}) => ({
  id, number: `ΤΠΥ-${id}`, client_id: "c1", client_name: "ΑΛΦΑ ΑΕ", date, status: "issued", line_items: [],
  subtotal: 100, vat_amount: 24, total: 124, outstanding: 124, mydata_status: "synced", ...over,
});

const draft = (id, date) => invoice(id, date, { status: "draft", number: "", outstanding: undefined, mydata_status: "not_sent" });
const cancelled = (id, date) => invoice(id, date, { status: "cancelled", outstanding: 0 });

function serve(rows) {
  server.use(
    http.get(`${API}/invoices`, () => HttpResponse.json(rows)),
    http.get(`${API}/clients`, () => HttpResponse.json([])),
  );
}

describe.each(MACHINE_ZONES)("Τιμολόγια — καθυστέρηση μόνο στα εκδοθέντα — μηχάνημα σε %s", (zone) => {
  useMachineZone(zone);

  it("πρόχειρο και ακυρωμένο με παλιά ημερομηνία δεν φέρουν σήμα καθυστέρησης ούτε μετρούν στα «30+ ημερών»", async () => {
    freezeAt(NIGHT);
    serve([invoice("a", "2026-08-01"), draft("d", "2026-08-01"), cancelled("x", "2026-08-01")]);
    render(<><Invoices /><Toaster /></>);

    const rowA = await screen.findByTestId("invoice-row-a");
    expect(within(rowA).getByText(/72η/)).toBeInTheDocument();                       // 1/8 → 12/10 = 72 ημέρες
    for (const id of ["d", "x"]) {
      expect(within(screen.getByTestId(`invoice-row-${id}`)).queryByText(/\d+η$/)).not.toBeInTheDocument();
    }
    expect(screen.getByTestId("kpi-unpaid")).toHaveTextContent("· 1 με καθυστέρηση 30+ ημερών");
  });
});

describe("Τιμολόγια — «Ανείσπρακτα» είναι μόνο τα εκδοθέντα", () => {
  it("το πλήθος και η λίστα του φίλτρου δεν περιλαμβάνουν πρόχειρα και ακυρωμένα", async () => {
    serve([
      invoice("a", "2026-10-01"),
      invoice("b", "2026-10-02", { subtotal: 50, vat_amount: 12, total: 62, outstanding: 62 }),
      draft("d", "2026-10-03"),
      cancelled("x", "2026-10-04"),
    ]);
    const user = userEvent.setup();
    render(<><Invoices /><Toaster /></>);
    await screen.findByTestId("invoice-row-a");

    expect(screen.getByTestId("kpi-unpaid-count")).toHaveTextContent(/^2$/);
    expect(screen.getByTestId("kpi-unpaid-total")).toHaveTextContent(/^186,00\s€$/);

    await user.click(screen.getByTestId("kpi-unpaid"));
    expect(screen.getByTestId("invoice-row-a")).toBeInTheDocument();
    expect(screen.getByTestId("invoice-row-b")).toBeInTheDocument();
    expect(screen.queryByTestId("invoice-row-d")).not.toBeInTheDocument();
    expect(screen.queryByTestId("invoice-row-x")).not.toBeInTheDocument();
  });
});

describe("Τιμολόγια — γραμμή «Οφείλεται»", () => {
  it("δεν εμφανίζεται όταν το υπόλοιπο διαφέρει από το σύνολο μόνο κατά το σφάλμα κινητής υποδιαστολής (0,3 € έναντι 0,1 + 0,2)", async () => {
    serve([invoice("a", "2026-10-01", { subtotal: 0.24, vat_amount: 0.06, total: 0.3, outstanding: 0.1 + 0.2 })]);
    render(<><Invoices /><Toaster /></>);
    const row = await screen.findByTestId("invoice-row-a");
    expect(row).toHaveTextContent(/0,30\s€/);                                         // το σύνολο φαίνεται
    expect(screen.queryByTestId("due-a")).not.toBeInTheDocument();                   // αλλά όχι «Οφείλεται: 0,30 €»
  });

  it.each([[99.7, "99,70"], [99.99, "99,99"]])(
    "μερική είσπραξη: υπόλοιπο %s € από σύνολο 100 € δείχνει «Οφείλεται: %s €», έστω και για λίγα λεπτά",
    async (outstanding, text) => {
      serve([invoice("a", "2026-10-01", { subtotal: 80.65, vat_amount: 19.35, total: 100, outstanding })]);
      render(<><Invoices /><Toaster /></>);
      expect(await screen.findByTestId("due-a")).toHaveTextContent(new RegExp(`^Οφείλεται: ${text}\\s€$`));
    },
  );
});
