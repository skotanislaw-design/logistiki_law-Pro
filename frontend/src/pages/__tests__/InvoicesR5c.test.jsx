/**
 * Τιμολόγια — δεύτερος έλεγχος του γύρου 5 (δοκιμές μεταλλάξεων): τα σύνολα (KPI) και τα φίλτρα, η λίστα μετά από κάθε ενέργεια,
 * η ειδοποίηση για κομμένα εξοφλημένα και οι ημερομηνίες Ελλάδας στη λίστα. Κάθε αριθμός που βλέπει ο χρήστης είναι αυτός
 * που κρατά ο server, και κάθε ημερομηνία είναι ημερομηνία Ελλάδας όποια κι αν είναι η ζώνη της συσκευής.
 */
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { Toaster } from "@/components/ui/sonner";
import Invoices from "@/pages/Invoices";
import { MACHINE_ZONES, useMachineZone } from "@/test/machineZone";
import { server } from "@/test/server";

const API = "http://localhost:8001/api";

const row = (id, over = {}) => ({
  id, number: `ΤΠΥ-2026-${id}`, client_id: "c1", client_name: "ΑΛΦΑ ΑΕ", date: "2026-10-01", line_items: [], status: "issued",
  subtotal: 100, vat_amount: 24, total: 124, outstanding: 124, mydata_status: "not_sent", ...over,
});

function serveInvoices(rows, options) {
  server.use(
    http.get(`${API}/invoices`, () => HttpResponse.json(rows, options)),
    http.get(`${API}/clients`, () => HttpResponse.json([])),
  );
}

const shown = () => screen.queryAllByTestId(/^invoice-row-/).map((el) => el.dataset.testid.replace("invoice-row-", ""));

describe("Τιμολόγια — σύνολα (KPI) και φίλτρα", () => {
  const rows = [
    row("a", { status: "issued", total: 124, outstanding: 124 }),
    row("b", { status: "paid", total: 300, outstanding: 0 }),
    row("c", { status: "paid", total: 200, outstanding: 0 }),
  ];

  it("το «Εξοφλημένα» αθροίζει τα εξοφλημένα (όχι τα εκδοθέντα) και μετρά μόνο αυτά· το «Σύνολο» όλα", async () => {
    serveInvoices(rows);
    render(<><Invoices /><Toaster /></>);
    await screen.findByTestId("invoice-row-a");
    expect(screen.getByTestId("kpi-paid")).toHaveTextContent("500,00");
    expect(screen.getByTestId("kpi-paid")).toHaveTextContent("2 τιμολόγια");
    expect(screen.getByTestId("kpi-paid")).not.toHaveTextContent("124,00");
    expect(screen.getByTestId("kpi-all")).toHaveTextContent("624,00");
    expect(screen.getByTestId("kpi-all")).toHaveTextContent("3 τιμολόγια συνολικά");
    expect(screen.getByTestId("kpi-unpaid-total")).toHaveTextContent("124,00");
    expect(screen.getByTestId("kpi-unpaid-count")).toHaveTextContent("1");
  });

  it("κάθε KPI φιλτράρει τη δική του προβολή, και ο «Καθαρισμός φίλτρου» επαναφέρει όλα τα τιμολόγια", async () => {
    const user = userEvent.setup();
    serveInvoices(rows);
    render(<><Invoices /><Toaster /></>);
    await screen.findByTestId("invoice-row-a");
    expect(shown().sort()).toEqual(["a", "b", "c"]);
    expect(screen.getByTestId("invoices-filter-label")).toHaveTextContent("Όλα");
    expect(screen.queryByTestId("clear-filter-button")).not.toBeInTheDocument();

    await user.click(screen.getByTestId("kpi-unpaid"));
    expect(screen.getByTestId("invoices-filter-label")).toHaveTextContent("Ανεξόφλητα");
    expect(shown()).toEqual(["a"]);

    await user.click(screen.getByTestId("kpi-paid"));
    expect(screen.getByTestId("invoices-filter-label")).toHaveTextContent("Εξοφλημένα");
    expect(shown().sort()).toEqual(["b", "c"]);

    await user.click(screen.getByTestId("clear-filter-button"));
    expect(screen.getByTestId("invoices-filter-label")).toHaveTextContent("Όλα");
    expect(shown().sort()).toEqual(["a", "b", "c"]);
    expect(screen.queryByTestId("clear-filter-button")).not.toBeInTheDocument();
  });
});

describe("Τιμολόγια — η λίστα ξαναφορτώνει μετά από κάθε ενέργεια", () => {
  beforeEach(() => {
    // η λήψη PDF μετά την έκδοση χρειάζεται URL.createObjectURL και «κλικ» σε σύνδεσμο — δεν υπάρχουν στο jsdom
    URL.createObjectURL = vi.fn(() => "blob:test");
    URL.revokeObjectURL = vi.fn();
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
  });
  afterEach(() => vi.restoreAllMocks());

  it("μετά την έκδοση το πρόχειρο παίρνει αριθμό και χάνει το κουμπί «Έκδοση»", async () => {
    const user = userEvent.setup();
    let issued = false;
    let loads = 0;
    server.use(
      http.get(`${API}/invoices`, () => {
        loads += 1;
        return HttpResponse.json(issued ? [row("d1", { number: "ΤΠΥ-2026-0001", status: "issued", mydata_status: "synced", mydata_mark: "400001234567890" })]
          : [row("d1", { number: "", status: "draft", outstanding: 0 })]);
      }),
      http.get(`${API}/clients`, () => HttpResponse.json([])),
      http.post(`${API}/invoices/d1/issue`, () => {
        issued = true;
        return HttpResponse.json(row("d1", { number: "ΤΠΥ-2026-0001", status: "issued", mydata_status: "synced", mydata_mark: "400001234567890" }));
      }),
      http.get(`${API}/invoices/d1/pdf`, () => new HttpResponse(new Blob(["%PDF-1.4"]), { headers: { "Content-Type": "application/pdf" } })),
    );
    render(<><Invoices /><Toaster /></>);
    expect(within(await screen.findByTestId("invoice-row-d1")).getAllByRole("cell")[0]).toHaveTextContent("Πρόχειρο");
    await user.click(screen.getByTestId("issue-invoice-d1"));
    await waitFor(() => expect(screen.queryByTestId("issue-invoice-d1")).not.toBeInTheDocument());
    expect(within(screen.getByTestId("invoice-row-d1")).getAllByRole("cell")[0]).toHaveTextContent("ΤΠΥ-2026-0001");
    expect(screen.getByTestId("mark-paid-d1")).toBeInTheDocument();
    expect(loads).toBe(2);
  });

  it("η ειδοποίηση για τα κομμένα εξοφλημένα φεύγει όταν ο server δεν κόβει πια", async () => {
    const user = userEvent.setup();
    let truncated = true;
    server.use(
      http.get(`${API}/invoices`, () => HttpResponse.json([row("i1")], truncated ? { headers: { "X-Closed-Truncated": "1" } } : undefined)),
      http.get(`${API}/clients`, () => HttpResponse.json([])),
      http.post(`${API}/invoices/i1/mark-paid`, () => { truncated = false; return HttpResponse.json({ message: "ok" }); }),
    );
    render(<><Invoices /><Toaster /></>);
    expect(await screen.findByTestId("closed-truncated-notice")).toBeInTheDocument();
    await user.click(screen.getByTestId("mark-paid-i1"));
    await waitFor(() => expect(screen.queryByTestId("closed-truncated-notice")).not.toBeInTheDocument());
  });
});

describe.each(MACHINE_ZONES)("Τιμολόγια — ημερομηνίες Ελλάδας στη λίστα, μηχάνημα σε %s", (zone) => {
  useMachineZone(zone);

  it("η ημερομηνία του τιμολογίου δεν χάνει ημέρα, και η υπενθύμιση γράφει την ημερομηνία Ελλάδας της σφραγίδας του server", async () => {
    // 01:30 τα ξημερώματα της 12ης Οκτωβρίου στην Αθήνα = 22:30 της 11ης σε UTC
    serveInvoices([row("a", { date: "2026-10-12", reminder_sent_at: "2026-10-11T22:30:00+00:00", reminder_count: 2 })]);
    render(<><Invoices /><Toaster /></>);
    const line = await screen.findByTestId("invoice-row-a");
    expect(within(line).getAllByRole("cell")[2]).toHaveTextContent("12/10/2026");
    expect(within(line).getAllByRole("cell")[2]).not.toHaveTextContent("11/10/2026");
    expect(screen.getByTestId("reminder-invoice-a")).toHaveAttribute("title", "Τελευταία υπενθύμιση: 12/10/2026 (×2)");
  });
});
