/**
 * Τράπεζα — εισαγωγή αρχείου κινήσεων και συμφωνία με τιμολόγια.
 *
 * Το backend δεχόταν CSV και έκανε αυτόματη συμφωνία από τον γύρο 3, αλλά η εφαρμογή δεν είχε καμία οθόνη: ο χρήστης δεν
 * είχε πού να ανεβάσει αρχείο, να δει τις προτάσεις ή να συμφωνήσει μια κατάθεση. Εδώ ελέγχεται ότι η οθόνη καλεί τα endpoints
 * όπως τα περιμένει ο server και ότι δεν επιτρέπει σιωπηλή αποδοχή διαφοράς ποσού.
 */
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { describe, expect, it, vi } from "vitest";

import { Toaster } from "@/components/ui/sonner";
import Bank from "@/pages/Bank";
import { server } from "@/test/server";

const API = "http://localhost:8001/api";

const tx = (over) => ({
  id: "t0", date: "2026-10-09", description: "ΕΜΒΑΣΜΑ ΠΕΛΑΤΗ", amount: 124, reference: "", bank: "Πειραιώς",
  matched_invoice_id: null, matched_invoice_number: null, match_confidence: 0, match_status: "unmatched",
  match_ambiguous: false, amount_difference: null, ...over,
});

const invoice = (over) => ({
  id: "i0", number: "ΤΠΥ-2026-0001", client_id: "c1", client_name: "ΑΛΦΑ ΑΕ", date: "2026-10-01", status: "issued",
  total: 124, outstanding: 124, ...over,
});

const SUMMARY = { total: 6, unmatched: 3, auto_matched: 1, manual_matched: 1, auto_confirmed: 0, ignored: 1 };

const ROWS = {
  unmatched: [
    tx({ id: "t1", description: "ΕΜΒΑΣΜΑ ΑΛΦΑ", amount: 124 }),
    tx({ id: "t2", description: "ΕΜΒΑΣΜΑ ΧΩΡΙΣ ΑΝΤΙΣΤΟΙΧΙΑ", amount: 80, date: "2026-10-08" }),
    tx({ id: "t3", description: "ΕΝΟΙΚΙΟ", amount: -600, date: "2026-10-07" }),
  ],
  auto_matched: [
    tx({ id: "t4", description: "ΚΑΤΑΘΕΣΗ ΒΗΤΑ", amount: 248, date: "2026-10-06", match_status: "auto_matched",
         matched_invoice_id: "i2", matched_invoice_number: "ΤΠΥ-2026-0002", match_confidence: 92 }),
  ],
  manual_matched: [
    tx({ id: "t5", description: "ΕΞΟΦΛΗΣΗ", amount: 62, date: "2026-10-01", match_status: "manual_matched",
         matched_invoice_id: "i9", matched_invoice_number: "ΤΠΥ-2026-0009", amount_difference: -2 }),
  ],
  auto_confirmed: [],
  ignored: [tx({ id: "t6", description: "ΛΟΓΑΡΙΑΣΜΟΣ", amount: -20, date: "2026-09-30", match_status: "ignored" })],
};

const INVOICES = [
  invoice({ id: "i1", number: "ΤΠΥ-2026-0001", client_name: "ΑΛΦΑ ΑΕ", total: 124, outstanding: 124 }),
  invoice({ id: "i2", number: "ΤΠΥ-2026-0002", client_name: "ΒΗΤΑ ΑΕ", total: 248, outstanding: 248, date: "2026-10-02" }),
  invoice({ id: "i3", number: "ΤΠΥ-2026-0003", client_name: "ΓΑΜΜΑ ΕΠΕ", total: 82, outstanding: 82, date: "2026-10-03" }),
  invoice({ id: "i4", number: "ΤΠΥ-2026-0004", client_name: "ΑΛΦΑ ΑΕ", total: 100, outstanding: 0 }),            // καλύφθηκε εξ ολοκλήρου
  invoice({ id: "i5", number: "ΤΠΥ-2026-0005", client_name: "ΑΛΦΑ ΑΕ", total: 50, outstanding: 50, status: "paid" }),
  invoice({ id: "i6", number: "ΠΙΣ-2026-0001", client_name: "ΑΛΦΑ ΑΕ", total: -62, outstanding: -62 }),            // πιστωτικό
];

/** Βάζει handlers για όλα τα GET που κάνει η οθόνη· επιστρέφει τη λίστα των αιτημάτων transactions. */
function serve({ summary = SUMMARY, rows = ROWS, invoices = INVOICES } = {}) {
  const requested = [];
  server.use(
    http.get(`${API}/bank/summary`, () => HttpResponse.json(summary)),
    http.get(`${API}/bank/transactions`, ({ request }) => {
      const status = new URL(request.url).searchParams.get("status") || "all";
      requested.push(status);
      const list = status === "all" ? Object.values(rows).flat() : (rows[status] || []);
      return HttpResponse.json(list);
    }),
    http.get(`${API}/invoices`, () => HttpResponse.json(invoices)),
  );
  return requested;
}

const renderBank = () => render(<><Bank /><Toaster /></>);

describe("Τράπεζα — προβολή", () => {
  it("δείχνει τα σύνολα και, από προεπιλογή, μόνο ό,τι χρειάζεται ενέργεια", async () => {
    const requested = serve();
    renderBank();
    expect(await screen.findByTestId("bank-row-t1")).toBeInTheDocument();
    expect(screen.getByTestId("bank-row-t2")).toBeInTheDocument();
    expect(screen.getByTestId("bank-row-t4")).toBeInTheDocument();
    expect(screen.queryByTestId("bank-row-t5")).not.toBeInTheDocument();       // συμφωνημένη
    expect(screen.queryByTestId("bank-row-t6")).not.toBeInTheDocument();       // αγνοημένη
    expect(requested.sort()).toEqual(["auto_matched", "unmatched"]);
    expect(screen.getByTestId("bank-kpi-unmatched")).toHaveTextContent("3");
    expect(screen.getByTestId("bank-kpi-review")).toHaveTextContent("1");
    expect(screen.getByTestId("bank-kpi-done")).toHaveTextContent("1");
    expect(screen.getByTestId("bank-kpi-ignored")).toHaveTextContent("1");
  });

  it("η πρόταση δείχνει το προτεινόμενο τιμολόγιο και τη βεβαιότητα", async () => {
    serve();
    renderBank();
    const row = await screen.findByTestId("bank-row-t4");
    expect(within(row).getByTestId("bank-match-t4")).toHaveTextContent("ΤΠΥ-2026-0002");
    expect(within(row).getByTestId("bank-match-t4")).toHaveTextContent("92%");
  });

  it("η αμφίβολη πρόταση επισημαίνεται", async () => {
    serve({ rows: { ...ROWS, auto_matched: [{ ...ROWS.auto_matched[0], match_ambiguous: true }] } });
    renderBank();
    expect(await screen.findByTestId("bank-ambiguous-t4")).toBeInTheDocument();
  });

  it("οι χρεώσεις δεν συμφωνούνται με τιμολόγια — μόνο αγνοούνται", async () => {
    serve();
    renderBank();
    const row = await screen.findByTestId("bank-row-t3");
    expect(within(row).queryByTestId("bank-reconcile-t3")).not.toBeInTheDocument();
    expect(within(row).getByTestId("bank-ignore-t3")).toBeInTheDocument();
  });

  it("το φίλτρο «Συμφωνημένες» ζητά και τις δύο καταστάσεις και «Αγνοημένες» την αντίστοιχη", async () => {
    const requested = serve();
    const user = userEvent.setup();
    renderBank();
    await screen.findByTestId("bank-row-t1");

    requested.length = 0;
    await user.click(screen.getByTestId("bank-filter-done"));
    expect(await screen.findByTestId("bank-row-t5")).toBeInTheDocument();
    expect(requested.sort()).toEqual(["auto_confirmed", "manual_matched"]);
    expect(within(screen.getByTestId("bank-row-t5")).getByTestId("bank-match-t5")).toHaveTextContent("ΤΠΥ-2026-0009");
    expect(within(screen.getByTestId("bank-row-t5")).queryByTestId("bank-reconcile-t5")).not.toBeInTheDocument();
    expect(screen.queryByTestId("bank-row-t1")).not.toBeInTheDocument();

    requested.length = 0;
    await user.click(screen.getByTestId("bank-filter-ignored"));
    expect(await screen.findByTestId("bank-row-t6")).toBeInTheDocument();
    expect(requested).toEqual(["ignored"]);
  });

  it("χωρίς κινήσεις δείχνει σαφή οδηγία αντί για κενό πίνακα", async () => {
    serve({ summary: { total: 0, unmatched: 0, auto_matched: 0, manual_matched: 0, auto_confirmed: 0, ignored: 0 },
            rows: { unmatched: [], auto_matched: [], manual_matched: [], auto_confirmed: [], ignored: [] } });
    renderBank();
    expect(await screen.findByTestId("bank-empty")).toHaveTextContent("Δεν υπάρχουν κινήσεις");
  });
});

describe("Τράπεζα — εισαγωγή αρχείου", () => {
  const csv = () => new File(["Ημερομηνία;Αιτιολογία;Ποσό\n09/10/2026;ΕΜΒΑΣΜΑ;124,00\n"], "kiniseis.csv", { type: "text/csv" });

  it("στέλνει το αρχείο με την επιλεγμένη τράπεζα και δείχνει το αποτέλεσμα", async () => {
    serve();
    let seen = null;
    server.use(http.post(`${API}/bank/import`, async ({ request }) => {
      const form = await request.formData();
      // Το jsdom δεν περνά το όνομα αρχείου στο multipart (έρχεται ως «blob»)· ελέγχεται το περιεχόμενο που στάλθηκε.
      seen = { bank: new URL(request.url).searchParams.get("bank"), content: await form.get("file")?.text() };
      return HttpResponse.json({ message: "Εισήχθησαν 5 κινήσεις. Αυτόματα συνδέθηκαν με τιμολόγια: 2. Από αυτές, 1 χρειάζονται έλεγχο (διαφορά ποσού ή περισσότερα από ένα πιθανά τιμολόγια).",
        created: 5, skipped: 1, auto_matched: 2, needs_review: 1 });
    }));
    const user = userEvent.setup({ applyAccept: false });
    renderBank();
    await screen.findByTestId("bank-row-t1");
    await user.click(screen.getByTestId("bank-name-select"));
    await user.click(await screen.findByRole("option", { name: "Eurobank" }));
    await user.upload(screen.getByTestId("bank-import-input"), csv());

    const result = await screen.findByTestId("bank-import-result");
    expect(seen.bank).toBe("Eurobank");
    expect(seen.content).toContain("09/10/2026;ΕΜΒΑΣΜΑ;124,00");
    expect(result).toHaveTextContent("Εισήχθησαν 5 κινήσεις");
    expect(result).toHaveTextContent("1 υπήρχαν ήδη");
    expect(result).toHaveTextContent("1 χρειάζονται έλεγχο");
  });

  it("το σφάλμα του server (μη αναγνώσιμο αρχείο) φτάνει στον χρήστη", async () => {
    serve();
    server.use(http.post(`${API}/bank/import`, () => HttpResponse.json({ detail: "Δεν βρέθηκαν εγκυρες κινήσεις." }, { status: 400 })));
    const user = userEvent.setup({ applyAccept: false });
    renderBank();
    await screen.findByTestId("bank-row-t1");
    await user.upload(screen.getByTestId("bank-import-input"), csv());
    expect(await screen.findByText("Δεν βρέθηκαν εγκυρες κινήσεις.")).toBeInTheDocument();
    expect(screen.queryByTestId("bank-import-result")).not.toBeInTheDocument();
  });

  it("μετά την εισαγωγή η λίστα ξαναφορτώνει", async () => {
    const requested = serve();
    server.use(http.post(`${API}/bank/import`, () => HttpResponse.json({ message: "ok", created: 1, skipped: 0, auto_matched: 0, needs_review: 0 })));
    const user = userEvent.setup({ applyAccept: false });
    renderBank();
    await screen.findByTestId("bank-row-t1");
    const before = requested.length;
    await user.upload(screen.getByTestId("bank-import-input"), csv());
    await screen.findByTestId("bank-import-result");
    await waitFor(() => expect(requested.length).toBeGreaterThan(before));
  });
});

describe("Τράπεζα — μαζική επιβεβαίωση", () => {
  it("επιβεβαιώνει τις ασφαλείς προτάσεις και απαριθμεί όσες θέλουν ατομική απόφαση", async () => {
    serve();
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    server.use(http.post(`${API}/bank/confirm-auto-matches`, () => HttpResponse.json({
      message: "Επιβεβαιώθηκαν 2 συνδέσεις. Τα αντίστοιχα τιμολόγια εξοφλήθηκαν. 1 χρειάζονται ατομικό έλεγχο.",
      confirmed: 2, skipped: 1,
      skipped_items: [{ id: "t4", reason: "ambiguous", message: "Ταιριάζουν περισσότερα από ένα τιμολόγια — επιλέξτε ατομικά." }],
    })));
    const user = userEvent.setup();
    renderBank();
    await screen.findByTestId("bank-row-t4");
    await user.click(screen.getByTestId("bank-confirm-all"));
    const result = await screen.findByTestId("bank-confirm-result");
    expect(confirm.mock.calls[0][0]).toMatch(/τιμολόγια θα εξοφληθούν/);       // η εξόφληση δεν γίνεται με μία απρόσεκτη κίνηση
    expect(result).toHaveTextContent("Επιβεβαιώθηκαν 2 συνδέσεις");
    expect(result).toHaveTextContent("Ταιριάζουν περισσότερα από ένα τιμολόγια");
    expect(result).toHaveTextContent("ΚΑΤΑΘΕΣΗ ΒΗΤΑ");                          // η κίνηση που θέλει απόφαση αναγνωρίζεται
    vi.restoreAllMocks();
  });

  it("αν ο χρήστης αρνηθεί την επιβεβαίωση, δεν εξοφλείται τίποτα", async () => {
    serve();
    vi.spyOn(window, "confirm").mockReturnValue(false);
    let called = false;
    server.use(http.post(`${API}/bank/confirm-auto-matches`, () => { called = true; return HttpResponse.json({}); }));
    const user = userEvent.setup();
    renderBank();
    await screen.findByTestId("bank-row-t4");
    await user.click(screen.getByTestId("bank-confirm-all"));
    expect(called).toBe(false);
    expect(screen.queryByTestId("bank-confirm-result")).not.toBeInTheDocument();
    vi.restoreAllMocks();
  });

  it("δεν προσφέρεται όταν δεν υπάρχουν προτάσεις", async () => {
    serve({ summary: { ...SUMMARY, auto_matched: 0 }, rows: { ...ROWS, auto_matched: [] } });
    renderBank();
    await screen.findByTestId("bank-row-t1");
    expect(screen.queryByTestId("bank-confirm-all")).not.toBeInTheDocument();
  });
});

describe("Τράπεζα — χειροκίνητη συμφωνία", () => {
  async function openReconcile(id) {
    const user = userEvent.setup();
    renderBank();
    await screen.findByTestId(`bank-row-${id}`);
    await user.click(screen.getByTestId(`bank-reconcile-${id}`));
    await screen.findByTestId("reconcile-dialog");
    return user;
  }

  it("προτείνει πρώτα τα τιμολόγια με το ακριβές ποσό και κρύβει όσα δεν εισπράττονται", async () => {
    serve();
    await openReconcile("t1");                                                  // 124 €
    const dialog = screen.getByTestId("reconcile-dialog");
    const order = within(dialog).getAllByTestId(/^pick-invoice-/).map((el) => el.dataset.testid);
    expect(order[0]).toBe("pick-invoice-i1");                                   // 124 = ακριβές
    expect(within(dialog).getByTestId("pick-invoice-i1")).toHaveTextContent("ακριβές ποσό");
    expect(order).not.toContain("pick-invoice-i4");                             // τίποτα δεν οφείλεται
    expect(order).not.toContain("pick-invoice-i5");                             // εξοφλημένο
    expect(order).not.toContain("pick-invoice-i6");                             // πιστωτικό
  });

  it("ακριβές ποσό: συμφωνεί χωρίς αποδοχή διαφοράς", async () => {
    serve();
    let call = null;
    server.use(http.post(`${API}/bank/reconcile/:id`, ({ request, params }) => {
      const q = new URL(request.url).searchParams;
      call = { id: params.id, invoice_id: q.get("invoice_id"), accept: q.get("accept_difference"), ignore: q.get("ignore") };
      return HttpResponse.json({ message: "Συνδέθηκε και το τιμολόγιο σημειώθηκε ως εξοφλημένο.", status: "paid", difference: 0 });
    }));
    const user = await openReconcile("t1");
    await user.click(screen.getByTestId("pick-invoice-i1"));
    expect(screen.queryByTestId("accept-difference")).not.toBeInTheDocument();
    await user.click(screen.getByTestId("reconcile-confirm"));
    await waitFor(() => expect(call).not.toBeNull());
    expect(call).toEqual({ id: "t1", invoice_id: "i1", accept: null, ignore: null });
    expect(await screen.findByText(/σημειώθηκε ως εξοφλημένο/)).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByTestId("reconcile-dialog")).not.toBeInTheDocument());
  });

  it("διαφορά ποσού: δείχνει τη διαφορά και δεν συνεχίζει αν δεν γίνει ρητή αποδοχή", async () => {
    serve();
    let call = null;
    server.use(http.post(`${API}/bank/reconcile/:id`, ({ request }) => {
      const q = new URL(request.url).searchParams;
      call = { invoice_id: q.get("invoice_id"), accept: q.get("accept_difference") };
      return HttpResponse.json({ message: "Συνδέθηκε. Καταγράφηκε διαφορά -2.00 €.", status: "paid", difference: -2 });
    }));
    const user = await openReconcile("t2");                                     // κατάθεση 80, το τιμολόγιο οφείλει 82
    await user.click(screen.getByTestId("pick-invoice-i3"));
    expect(screen.getByTestId("reconcile-summary")).toHaveTextContent(/Διαφορά/);
    expect(screen.getByTestId("reconcile-summary")).toHaveTextContent(/-2,00/);
    expect(screen.getByTestId("reconcile-confirm")).toBeDisabled();
    await user.click(screen.getByTestId("accept-difference"));
    expect(screen.getByTestId("reconcile-confirm")).toBeEnabled();
    await user.click(screen.getByTestId("reconcile-confirm"));
    await waitFor(() => expect(call).not.toBeNull());
    expect(call).toEqual({ invoice_id: "i3", accept: "true" });
  });

  it("η πρόταση προεπιλέγεται όταν ανοίγει η συμφωνία μιας αυτόματης αντιστοίχισης", async () => {
    serve();
    await openReconcile("t4");
    const picked = screen.getByTestId("pick-invoice-i2");
    expect(picked).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByTestId("reconcile-confirm")).toBeEnabled();              // 248 = 248
  });

  it("η άρνηση του server εμφανίζεται και ο διάλογος μένει ανοιχτός", async () => {
    serve();
    server.use(http.post(`${API}/bank/reconcile/:id`, () => HttpResponse.json(
      { detail: "Το τιμολόγιο έχει ήδη εξοφληθεί από άλλη κίνηση." }, { status: 409 })));
    const user = await openReconcile("t1");
    await user.click(screen.getByTestId("pick-invoice-i1"));
    await user.click(screen.getByTestId("reconcile-confirm"));
    expect(await screen.findByTestId("reconcile-error")).toHaveTextContent("έχει ήδη εξοφληθεί από άλλη κίνηση");
    expect(screen.getByTestId("reconcile-dialog")).toBeInTheDocument();
  });

  it("η αναζήτηση φιλτράρει τα τιμολόγια ανά πελάτη ή αριθμό", async () => {
    serve();
    const user = await openReconcile("t1");
    await user.type(screen.getByTestId("reconcile-search"), "ΓΑΜΜΑ");
    const dialog = screen.getByTestId("reconcile-dialog");
    expect(within(dialog).getAllByTestId(/^pick-invoice-/).map((el) => el.dataset.testid)).toEqual(["pick-invoice-i3"]);
  });
});

describe("Τράπεζα — αγνόηση", () => {
  it("η αγνόηση ζητά επιβεβαίωση, καλεί το endpoint και ξαναφορτώνει", async () => {
    const requested = serve();
    vi.spyOn(window, "confirm").mockReturnValue(true);
    let call = null;
    server.use(http.post(`${API}/bank/reconcile/:id`, ({ request, params }) => {
      call = { id: params.id, ignore: new URL(request.url).searchParams.get("ignore") };
      return HttpResponse.json({ message: "Η κίνηση σημειώθηκε ως αγνοητέα" });
    }));
    const user = userEvent.setup();
    renderBank();
    await screen.findByTestId("bank-row-t3");
    const before = requested.length;
    await user.click(screen.getByTestId("bank-ignore-t3"));
    await waitFor(() => expect(call).toEqual({ id: "t3", ignore: "true" }));
    await waitFor(() => expect(requested.length).toBeGreaterThan(before));
    vi.restoreAllMocks();
  });

  it("αν ο χρήστης αρνηθεί, δεν στέλνεται τίποτα", async () => {
    serve();
    vi.spyOn(window, "confirm").mockReturnValue(false);
    let called = false;
    server.use(http.post(`${API}/bank/reconcile/:id`, () => { called = true; return HttpResponse.json({}); }));
    const user = userEvent.setup();
    renderBank();
    await screen.findByTestId("bank-row-t3");
    await user.click(screen.getByTestId("bank-ignore-t3"));
    expect(called).toBe(false);
    vi.restoreAllMocks();
  });
});
