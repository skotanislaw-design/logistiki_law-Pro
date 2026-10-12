/**
 * Τράπεζα — ευρήματα της ανασκόπησης του γύρου 5 (BK2–BK5) και κενά δοκιμών που άφησε η πρώτη έκδοση της οθόνης.
 *
 * Η οθόνη δεν πρέπει να λέει ψέματα: ούτε «δεν υπάρχουν κινήσεις» όταν απέτυχε η φόρτωση, ούτε «συμφωνήστε ή αγνοήστε
 * τις πρώτες» σε προβολή χωρίς ενέργειες, ούτε να δείχνει στον χρήστη τον τεχνικό όρο του API όταν το τιμολόγιο άλλαξε
 * ενώ ήταν ανοιχτό το παράθυρο. Και ό,τι γίνεται με χρήματα (αποδοχή διαφοράς, διπλό πάτημα, πιστωτικό) έχει τη δική του δοκιμή.
 */
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { afterEach, describe, expect, it, vi } from "vitest";

import { API, INVOICES, ROWS, SUMMARY, invoice, renderBank, serve, tx } from "@/test/bankFixtures";
import { server } from "@/test/server";

const EMPTY_ROWS = { unmatched: [], auto_matched: [], manual_matched: [], auto_confirmed: [], ignored: [] };
const summaryOf = (over = {}) => ({ total: 0, unmatched: 0, auto_matched: 0, manual_matched: 0, auto_confirmed: 0, ignored: 0, ...over });

/** n κινήσεις της ίδιας κατάστασης, με διαφορετικές ημερομηνίες και αριθμούς. */
const many = (status, n, prefix) => Array.from({ length: n }, (_, i) => tx({
  id: `${prefix}${i}`, match_status: status, description: `ΚΙΝΗΣΗ ${prefix}${i}`, amount: 10 + i,
  date: `2026-09-${String(1 + (i % 28)).padStart(2, "0")}`,
}));

const listed = () => screen.queryAllByTestId(/^bank-row-/);

describe("Τράπεζα — λίστα κινήσεων", () => {
  it("οι κινήσεις εμφανίζονται από τις πιο πρόσφατες· στην ίδια ημερομηνία, από το μεγαλύτερο id", async () => {
    serve({
      summary: summaryOf({ total: 4, unmatched: 4 }),
      rows: {
        ...EMPTY_ROWS,
        unmatched: [
          tx({ id: "a1", date: "2026-10-01", description: "ΠΑΛΙΑ" }),
          tx({ id: "b2", date: "2026-10-09", description: "ΝΕΑ Β" }),
          tx({ id: "b9", date: "2026-10-09", description: "ΝΕΑ Θ" }),
          tx({ id: "c3", date: "2026-10-05", description: "ΜΕΣΑΙΑ" }),
        ],
      },
    });
    renderBank();
    await screen.findByTestId("bank-row-a1");
    expect(listed().map((row) => row.dataset.testid)).toEqual(["bank-row-b9", "bank-row-b2", "bank-row-c3", "bank-row-a1"]);
  });

  it("οι ημερομηνίες γράφονται ΗΗ/ΜΜ/ΕΕΕΕ, όπως στο e-banking, και ό,τι δεν είναι ημερομηνία μένει όπως είναι", async () => {
    serve({
      summary: summaryOf({ total: 2, unmatched: 2 }),
      rows: { ...EMPTY_ROWS, unmatched: [tx({ id: "d1", date: "2026-03-05" }), tx({ id: "d2", date: "άγνωστη", amount: 5 })] },
    });
    renderBank();
    const first = await screen.findByTestId("bank-row-d1");
    expect(first).toHaveTextContent("05/03/2026");
    expect(first).not.toHaveTextContent("03/05/2026");
    expect(screen.getByTestId("bank-row-d2")).toHaveTextContent("άγνωστη");
  });

  it("οι χρεώσεις έχουν ετικέτα «Χρέωση» και ένα μηδενικό ποσό δεν προσφέρει συμφωνία", async () => {
    serve({
      summary: summaryOf({ total: 3, unmatched: 3 }),
      rows: {
        ...EMPTY_ROWS,
        unmatched: [
          tx({ id: "e1", amount: -45, date: "2026-10-03" }),
          tx({ id: "e2", amount: 0, date: "2026-10-02" }),
          tx({ id: "e3", amount: 30, date: "2026-10-01" }),
        ],
      },
    });
    renderBank();
    await screen.findByTestId("bank-row-e1");
    expect(screen.getByTestId("bank-row-e1")).toHaveTextContent("Χρέωση");
    expect(screen.getByTestId("bank-row-e3")).toHaveTextContent("Χωρίς αντιστοίχιση");
    expect(screen.queryByTestId("bank-reconcile-e1")).not.toBeInTheDocument();
    expect(screen.queryByTestId("bank-reconcile-e2")).not.toBeInTheDocument();
    expect(screen.getByTestId("bank-reconcile-e3")).toBeInTheDocument();
    expect(screen.getByTestId("bank-ignore-e2")).toBeInTheDocument();
  });

  it("οι συμφωνημένες και αγνοημένες κινήσεις δεν έχουν κουμπιά ενέργειας", async () => {
    const user = userEvent.setup();
    serve({
      summary: summaryOf({ total: 3, manual_matched: 1, auto_confirmed: 1, ignored: 1 }),
      rows: {
        ...EMPTY_ROWS,
        manual_matched: [tx({ id: "m1", match_status: "manual_matched", matched_invoice_number: "ΤΠΥ-1", matched_invoice_id: "i1" })],
        auto_confirmed: [tx({ id: "m2", match_status: "auto_confirmed", matched_invoice_number: "ΤΠΥ-2", matched_invoice_id: "i2", date: "2026-10-08" })],
        ignored: [tx({ id: "m3", match_status: "ignored", date: "2026-10-07" })],
      },
    });
    renderBank();
    await user.click(await screen.findByTestId("bank-filter-done"));
    await screen.findByTestId("bank-row-m1");
    for (const id of ["m1", "m2"]) {
      expect(screen.queryByTestId(`bank-reconcile-${id}`)).not.toBeInTheDocument();
      expect(screen.queryByTestId(`bank-ignore-${id}`)).not.toBeInTheDocument();
    }
    await user.click(screen.getByTestId("bank-filter-ignored"));
    await screen.findByTestId("bank-row-m3");
    expect(screen.queryByTestId("bank-ignore-m3")).not.toBeInTheDocument();
    expect(screen.queryByTestId("bank-reconcile-m3")).not.toBeInTheDocument();
  });

  it("η αποδεκτή διαφορά ποσού φαίνεται στη συμφωνημένη κίνηση, και η μηδενική όχι", async () => {
    const user = userEvent.setup();
    serve();                                                                    // t5: διαφορά −2
    renderBank();
    await user.click(await screen.findByTestId("bank-filter-done"));
    expect(await screen.findByTestId("bank-match-t5")).toHaveTextContent("Διαφορά ποσού που έγινε αποδεκτή: -2,00");
    serve({
      summary: summaryOf({ total: 1, manual_matched: 1 }),
      rows: { ...EMPTY_ROWS, manual_matched: [tx({ id: "z1", match_status: "manual_matched", matched_invoice_number: "ΤΠΥ-9", amount_difference: 0 })] },
    });
    await user.click(screen.getByTestId("bank-filter-ignored"));
    await user.click(screen.getByTestId("bank-filter-done"));
    expect(await screen.findByTestId("bank-match-z1")).not.toHaveTextContent("Διαφορά ποσού");
  });

  it("κενές προβολές: κάθε περίπτωση έχει το δικό της, πλήρες μήνυμα", async () => {
    const user = userEvent.setup();
    serve({ summary: summaryOf({ total: 0 }), rows: EMPTY_ROWS });
    renderBank();
    expect(await screen.findByTestId("bank-empty")).toHaveTextContent(
      "Δεν υπάρχουν κινήσεις. Εισάγετε το αρχείο κινήσεων από την τράπεζά σας για να ξεκινήσει η συμφωνία.");

    serve({ summary: summaryOf({ total: 5, manual_matched: 5 }), rows: EMPTY_ROWS });
    await user.click(screen.getByTestId("bank-filter-ignored"));
    await waitFor(() => expect(screen.getByTestId("bank-empty")).toHaveTextContent("Δεν υπάρχουν κινήσεις σε αυτή την προβολή."));
    await user.click(screen.getByTestId("bank-filter-action"));
    await waitFor(() => expect(screen.getByTestId("bank-empty")).toHaveTextContent(
      "Δεν υπάρχουν κινήσεις που χρειάζονται ενέργεια — όλες έχουν συμφωνηθεί ή αγνοηθεί."));
  });
});

describe("Τράπεζα — BK2: ο server επιστρέφει το πολύ 500 κινήσεις και η οθόνη το λέει σωστά", () => {
  it("με ακριβώς 500 κινήσεις δεν κόβεται τίποτα και δεν εμφανίζεται ειδοποίηση", async () => {
    const user = userEvent.setup();
    serve({ summary: summaryOf({ total: 500, ignored: 500 }), rows: { ...EMPTY_ROWS, ignored: many("ignored", 500, "g") } });
    renderBank();
    await user.click(await screen.findByTestId("bank-filter-ignored"));
    await screen.findByTestId("bank-row-g0");
    expect(listed()).toHaveLength(500);
    expect(screen.queryByTestId("bank-truncated")).not.toBeInTheDocument();
  });

  it("με 501 κινήσεις στην προβολή «Αγνοημένες» λέει πόσες λείπουν και δεν προτείνει ενέργειες που δεν υπάρχουν", async () => {
    const user = userEvent.setup();
    serve({ summary: summaryOf({ total: 501, ignored: 501 }), rows: { ...EMPTY_ROWS, ignored: many("ignored", 500, "g") } });
    renderBank();
    await user.click(await screen.findByTestId("bank-filter-ignored"));
    const notice = await screen.findByTestId("bank-truncated");
    expect(notice).toHaveTextContent("Εμφανίζονται οι 500 πιο πρόσφατες από 501 κινήσεις");
    expect(notice).toHaveTextContent("οι 1 παλαιότερες δεν εμφανίζονται σε αυτή την προβολή");
    expect(notice).not.toHaveTextContent("Συμφωνήστε");
    expect(notice).not.toHaveTextContent("αγνοήστε");
  });

  it("στις «Συμφωνημένες» το όριο ισχύει ανά κατάσταση και το σύνολο προκύπτει από τη σύνοψη", async () => {
    const user = userEvent.setup();
    serve({
      summary: summaryOf({ total: 650, manual_matched: 600, auto_confirmed: 50 }),
      rows: { ...EMPTY_ROWS, manual_matched: many("manual_matched", 500, "h"), auto_confirmed: many("auto_confirmed", 50, "k") },
    });
    renderBank();
    await user.click(await screen.findByTestId("bank-filter-done"));
    const notice = await screen.findByTestId("bank-truncated");
    expect(notice).toHaveTextContent("Εμφανίζονται οι 550 πιο πρόσφατες από 650 κινήσεις");
    expect(notice).toHaveTextContent("οι 100 παλαιότερες");
    expect(notice).not.toHaveTextContent("Συμφωνήστε");
  });

  it("στις «Όλες» το όριο είναι 500 συνολικά και η ειδοποίηση παραπέμπει στην προβολή των εκκρεμών", async () => {
    const user = userEvent.setup();
    serve({
      summary: summaryOf({ total: 900, unmatched: 400, ignored: 500 }),
      rows: { ...EMPTY_ROWS, unmatched: many("unmatched", 250, "u"), ignored: many("ignored", 250, "v") },
    });
    renderBank();
    await user.click(await screen.findByTestId("bank-filter-all"));
    const notice = await screen.findByTestId("bank-truncated");
    expect(notice).toHaveTextContent("Εμφανίζονται οι 500 πιο πρόσφατες από 900 κινήσεις");
    expect(notice).toHaveTextContent("οι 400 παλαιότερες");
    expect(notice).toHaveTextContent("χρησιμοποιήστε την προβολή «Χρειάζονται ενέργεια»");
  });

  it("στις «Χρειάζονται ενέργεια» προτείνει συμφωνία ή αγνόηση των πρώτων", async () => {
    serve({
      summary: summaryOf({ total: 1100, unmatched: 600, auto_matched: 500 }),
      rows: { ...EMPTY_ROWS, unmatched: many("unmatched", 500, "p"), auto_matched: many("auto_matched", 500, "q") },
    });
    renderBank();
    const notice = await screen.findByTestId("bank-truncated");
    expect(notice).toHaveTextContent("Εμφανίζονται 1000 από 1100 κινήσεις — οι 100 παλαιότερες δεν φορτώνονται (το πολύ 500 ανά κατάσταση)");
    expect(notice).toHaveTextContent("Συμφωνήστε ή αγνοήστε τις πρώτες για να εμφανιστούν οι υπόλοιπες.");
  });

  it("αν οι δύο καταστάσεις χωρούν (500 + 500), δεν υπάρχει ειδοποίηση", async () => {
    serve({
      summary: summaryOf({ total: 1000, unmatched: 500, auto_matched: 500 }),
      rows: { ...EMPTY_ROWS, unmatched: many("unmatched", 500, "p"), auto_matched: many("auto_matched", 500, "q") },
    });
    renderBank();
    await screen.findByTestId("bank-row-p0");
    expect(listed()).toHaveLength(1000);
    expect(screen.queryByTestId("bank-truncated")).not.toBeInTheDocument();
  });
});

describe("Τράπεζα — BK4: αποτυχία φόρτωσης", () => {
  afterEach(() => vi.restoreAllMocks());

  const failSummary = (status = 500) => http.get(`${API}/bank/summary`, () => HttpResponse.json({ detail: "Η βάση δεν είναι διαθέσιμη." }, { status }));

  it("όταν αποτύχει η σύνοψη δεν λέει «δεν υπάρχουν κινήσεις»: δείχνει το σφάλμα, «—» στα σύνολα και επανάληψη", async () => {
    const user = userEvent.setup();
    serve();
    server.use(failSummary());
    renderBank();
    const banner = await screen.findByTestId("bank-load-error");
    expect(banner).toHaveAttribute("role", "alert");
    expect(banner).toHaveTextContent("Δεν φορτώθηκαν οι κινήσεις: Η βάση δεν είναι διαθέσιμη.");
    expect(banner).not.toHaveTextContent("μπορεί να μην είναι ενημερωμένη");          // δεν υπάρχει λίστα που να είναι παλιά
    expect(screen.queryByTestId("bank-empty")).not.toBeInTheDocument();
    expect(screen.queryByText("Φόρτωση κινήσεων…")).not.toBeInTheDocument();
    for (const id of ["review", "unmatched", "done", "ignored"]) expect(screen.getByTestId(`bank-kpi-${id}`)).toHaveTextContent("—");
    expect(screen.queryByTestId("bank-kpi-unmatched")).not.toHaveTextContent("0");

    serve();                                                                           // ο server επανήλθε
    await user.click(screen.getByTestId("bank-retry"));
    expect(await screen.findByTestId("bank-row-t1")).toBeInTheDocument();
    expect(screen.queryByTestId("bank-load-error")).not.toBeInTheDocument();
    expect(screen.getByTestId("bank-kpi-unmatched")).toHaveTextContent("3");
  });

  it("αν αποτύχει η λίστα κινήσεων (η σύνοψη πέρασε), ισχύει το ίδιο", async () => {
    serve();
    server.use(http.get(`${API}/bank/transactions`, () => HttpResponse.json({ detail: "Λήξη χρόνου αναμονής." }, { status: 504 })));
    renderBank();
    expect(await screen.findByTestId("bank-load-error")).toHaveTextContent("Λήξη χρόνου αναμονής.");
    expect(screen.queryByTestId("bank-empty")).not.toBeInTheDocument();
    expect(screen.queryByTestId("bank-table")).not.toBeInTheDocument();
  });

  it("η αποτυχία φέρνει και ειδοποίηση, και η επανάληψη δείχνει «Φόρτωση…» αντί για κενή λίστα", async () => {
    const user = userEvent.setup();
    serve();
    server.use(failSummary(503));
    renderBank();
    await screen.findByTestId("bank-load-error");
    expect((await screen.findAllByText("Η βάση δεν είναι διαθέσιμη.")).length).toBeGreaterThanOrEqual(1);

    let release;
    const gate = new Promise((resolve) => { release = resolve; });
    serve();
    server.use(http.get(`${API}/bank/summary`, async () => { await gate; return HttpResponse.json(SUMMARY); }));
    await user.click(screen.getByTestId("bank-retry"));
    expect(await screen.findByText("Φόρτωση κινήσεων…")).toBeInTheDocument();
    expect(screen.queryByTestId("bank-load-error")).not.toBeInTheDocument();
    expect(screen.queryByTestId("bank-empty")).not.toBeInTheDocument();
    release();
    expect(await screen.findByTestId("bank-row-t1")).toBeInTheDocument();
  });

  it("αν αποτύχει η ανανέωση ενώ η λίστα φαίνεται, η λίστα μένει με προειδοποίηση ότι μπορεί να είναι παλιά", async () => {
    const user = userEvent.setup();
    serve();
    renderBank();
    await screen.findByTestId("bank-row-t1");
    server.use(failSummary());
    await user.click(screen.getByTestId("bank-filter-done"));                           // νέα φόρτωση που αποτυγχάνει
    expect(await screen.findByTestId("bank-load-error")).toHaveTextContent("Η βάση δεν είναι διαθέσιμη.");
    expect(screen.queryByTestId("bank-empty")).not.toBeInTheDocument();

    serve();
    await user.click(screen.getByTestId("bank-retry"));
    await screen.findByTestId("bank-row-t5");
    expect(screen.queryByTestId("bank-load-error")).not.toBeInTheDocument();
  });

  it("αν η ανανέωση αποτύχει ενώ υπάρχουν γραμμές στην ίδια προβολή, μένουν και η προειδοποίηση λέει ότι μπορεί να είναι παλιές", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    const user = userEvent.setup();
    serve();
    server.use(http.post(`${API}/bank/reconcile/:id`, () => HttpResponse.json({ message: "Η κίνηση σημειώθηκε ως αγνοητέα" })));
    renderBank();
    await screen.findByTestId("bank-row-t1");
    server.use(failSummary());
    await user.click(screen.getByTestId("bank-ignore-t3"));                            // μετά την αγνόηση ξαναφορτώνει — και αποτυγχάνει
    const banner = await screen.findByTestId("bank-load-error");
    expect(banner).toHaveTextContent("Η λίστα που βλέπετε μπορεί να μην είναι ενημερωμένη.");
    expect(screen.getByTestId("bank-row-t1")).toBeInTheDocument();
  });

  it("μια καθυστερημένη αποτυχία προηγούμενης προβολής δεν πειράζει την προβολή που βλέπει πια ο χρήστης", async () => {
    const user = userEvent.setup();
    let release;
    const gate = new Promise((resolve) => { release = resolve; });
    let firstSeen = false;
    serve();
    server.use(http.get(`${API}/bank/transactions`, async ({ request }) => {
      const status = new URL(request.url).searchParams.get("status");
      if (status === "unmatched" && !firstSeen) {
        firstSeen = true;
        await gate;
        return HttpResponse.json({ detail: "Παλιά αποτυχία." }, { status: 500 });
      }
      return HttpResponse.json(status === "ignored" ? ROWS.ignored : []);
    }));
    renderBank();
    await waitFor(() => expect(firstSeen).toBe(true));
    await user.click(screen.getByTestId("bank-filter-ignored"));
    await screen.findByTestId("bank-row-t6");
    release();
    await new Promise((resolve) => setTimeout(resolve, 50));                           // αφήνουμε την παλιά αποτυχία να φτάσει
    expect(screen.queryByTestId("bank-load-error")).not.toBeInTheDocument();
    expect(screen.queryByText("Παλιά αποτυχία.")).not.toBeInTheDocument();
    expect(screen.getByTestId("bank-row-t6")).toBeInTheDocument();
  });
});

describe("Τράπεζα — ανταγωνισμός αιτημάτων και ανανεώσεις", () => {
  it("η καθυστερημένη απάντηση προηγούμενου φίλτρου δεν γράφει πάνω στη λίστα που βλέπει ο χρήστης", async () => {
    const user = userEvent.setup();
    let release;
    const gate = new Promise((resolve) => { release = resolve; });
    let firstSeen = false;
    serve();
    server.use(http.get(`${API}/bank/transactions`, async ({ request }) => {
      const status = new URL(request.url).searchParams.get("status");
      if (status === "unmatched" && !firstSeen) {
        firstSeen = true;
        await gate;
        return HttpResponse.json([tx({ id: "OLD", description: "ΠΑΛΙΑ ΑΠΑΝΤΗΣΗ" })]);
      }
      return HttpResponse.json(status === "ignored" ? ROWS.ignored : []);
    }));
    renderBank();
    await waitFor(() => expect(firstSeen).toBe(true));
    await user.click(screen.getByTestId("bank-filter-ignored"));
    await screen.findByTestId("bank-row-t6");
    release();
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(screen.queryByTestId("bank-row-OLD")).not.toBeInTheDocument();
    expect(screen.getByTestId("bank-row-t6")).toBeInTheDocument();
  });

  it("η εισαγωγή από τις «Συμφωνημένες» πηγαίνει στις «Χρειάζονται ενέργεια», όπου θα φανούν οι νέες κινήσεις", async () => {
    const user = userEvent.setup({ applyAccept: false });
    const requested = serve();
    server.use(http.post(`${API}/bank/import`, () => HttpResponse.json({ message: "ok", created: 1, skipped: 0, auto_matched: 0, needs_review: 0 })));
    renderBank();
    await user.click(await screen.findByTestId("bank-filter-done"));
    await screen.findByTestId("bank-row-t5");
    requested.length = 0;
    await user.upload(screen.getByTestId("bank-import-input"), new File(["x"], "k.csv", { type: "text/csv" }));
    await screen.findByTestId("bank-import-result");
    await waitFor(() => expect(requested.sort()).toEqual(["auto_matched", "unmatched"]));
    expect(screen.getByTestId("bank-filter-action")).toHaveAttribute("aria-pressed", "true");
  });

  it("η εισαγωγή στις «Όλες» ξαναφορτώνει την ίδια προβολή", async () => {
    const user = userEvent.setup({ applyAccept: false });
    const requested = serve();
    server.use(http.post(`${API}/bank/import`, () => HttpResponse.json({ message: "ok", created: 1, skipped: 0, auto_matched: 0, needs_review: 0 })));
    renderBank();
    await user.click(await screen.findByTestId("bank-filter-all"));
    await screen.findByTestId("bank-row-t5");
    requested.length = 0;
    await user.upload(screen.getByTestId("bank-import-input"), new File(["x"], "k.csv", { type: "text/csv" }));
    await screen.findByTestId("bank-import-result");
    await waitFor(() => expect(requested).toEqual(["all"]));
    expect(screen.getByTestId("bank-filter-all")).toHaveAttribute("aria-pressed", "true");
  });

  it("μετά από επιτυχή συμφωνία ο διάλογος κλείνει και η λίστα ξαναφορτώνει", async () => {
    const user = userEvent.setup();
    const requested = serve();
    server.use(http.post(`${API}/bank/reconcile/:id`, () => HttpResponse.json({ message: "Συνδέθηκε.", status: "paid", difference: 0 })));
    renderBank();
    await screen.findByTestId("bank-row-t1");
    await user.click(screen.getByTestId("bank-reconcile-t1"));
    await user.click(await screen.findByTestId("pick-invoice-i1"));
    const before = requested.length;
    await user.click(screen.getByTestId("reconcile-confirm"));
    await waitFor(() => expect(screen.queryByTestId("reconcile-dialog")).not.toBeInTheDocument());
    await waitFor(() => expect(requested.length).toBeGreaterThan(before));
  });
});

describe("Τράπεζα — διάλογος συμφωνίας: ασφάλεια των χρημάτων", () => {
  async function openDialog(id, { rows, invoices, summary } = {}) {
    const user = userEvent.setup();
    serve({ rows, invoices, summary });
    renderBank();
    await screen.findByTestId(`bank-row-${id}`);
    await user.click(screen.getByTestId(`bank-reconcile-${id}`));
    await screen.findByTestId("reconcile-dialog");
    return user;
  }

  it("η αποδοχή διαφοράς δεν περνά από το ένα τιμολόγιο στο άλλο", async () => {
    const user = await openDialog("t2");                                      // κατάθεση 80 · i1=124, i2=248, i3=82
    await user.click(screen.getByTestId("pick-invoice-i3"));                  // διαφορά −2
    await user.click(screen.getByTestId("accept-difference"));
    expect(screen.getByTestId("reconcile-confirm")).toBeEnabled();
    await user.click(screen.getByTestId("pick-invoice-i1"));                  // άλλο τιμολόγιο, άλλη διαφορά
    expect(screen.getByTestId("accept-difference")).not.toBeChecked();
    expect(screen.getByTestId("reconcile-confirm")).toBeDisabled();
  });

  it("τιμολόγιο με πιστωτικό φαίνεται αλλά δεν επιλέγεται", async () => {
    const user = await openDialog("t1", {
      invoices: [...INVOICES, invoice({ id: "i7", number: "ΤΠΥ-2026-0007", client_name: "ΔΕΛΤΑ ΑΕ", total: 124, outstanding: 124, credit_note_id: "pis1" })],
    });
    const blocked = screen.getByTestId("pick-invoice-i7");
    expect(blocked).toBeDisabled();
    expect(blocked).toHaveTextContent("Έχει πιστωτικό — ο συμψηφισμός γίνεται από τον λογιστή");
    await user.click(blocked);
    expect(screen.queryByTestId("reconcile-summary")).not.toBeInTheDocument();
    expect(screen.getByTestId("reconcile-confirm")).toBeDisabled();
  });

  it("το διπλό πάτημα στη συμφωνία στέλνει ένα μόνο αίτημα", async () => {
    let posts = 0;
    let release;
    const gate = new Promise((resolve) => { release = resolve; });
    const user = await openDialog("t1");
    server.use(http.post(`${API}/bank/reconcile/:id`, async () => {
      posts += 1;
      await gate;
      return HttpResponse.json({ message: "Συνδέθηκε.", status: "paid", difference: 0 });
    }));
    await user.click(screen.getByTestId("pick-invoice-i1"));
    await user.dblClick(screen.getByTestId("reconcile-confirm"));
    await waitFor(() => expect(posts).toBe(1));
    expect(screen.getByTestId("reconcile-confirm")).toBeDisabled();
    expect(screen.getByTestId("reconcile-confirm")).toHaveTextContent("Συμφωνία…");
    release();
    await waitFor(() => expect(screen.queryByTestId("reconcile-dialog")).not.toBeInTheDocument());
    expect(posts).toBe(1);
  });

  it("το ποσό συγκρίνεται σε λεπτά: ίδιο μέχρι το λεπτό είναι ακριβές, διαφορά ενός λεπτού όχι", async () => {
    const rows = { ...EMPTY_ROWS, unmatched: [tx({ id: "c1", amount: 123.45 })] };
    const user = await openDialog("c1", {
      rows, summary: summaryOf({ total: 1, unmatched: 1 }),
      invoices: [
        invoice({ id: "x1", number: "ΤΠΥ-X1", outstanding: 123.45, total: 123.45 }),
        invoice({ id: "x2", number: "ΤΠΥ-X2", outstanding: 123.46, total: 123.46, date: "2026-10-02" }),
        invoice({ id: "x3", number: "ΤΠΥ-X3", outstanding: 123.44, total: 123.44, date: "2026-10-03" }),
      ],
    });
    await user.click(screen.getByTestId("pick-invoice-x1"));
    expect(screen.queryByTestId("accept-difference")).not.toBeInTheDocument();
    expect(screen.getByTestId("reconcile-confirm")).toBeEnabled();
    await user.click(screen.getByTestId("pick-invoice-x2"));
    expect(screen.getByTestId("accept-difference")).toBeInTheDocument();
    expect(screen.getByTestId("reconcile-summary")).toHaveTextContent("-0,01");
    expect(screen.getByTestId("reconcile-confirm")).toBeDisabled();
    await user.click(screen.getByTestId("pick-invoice-x3"));
    expect(screen.getByTestId("reconcile-summary")).toHaveTextContent("0,01");
    expect(screen.getByTestId("accept-difference")).toBeInTheDocument();
  });

  it("το σφάλμα κινητής υποδιαστολής δεν δημιουργεί ψεύτικη διαφορά (0,1 + 0,2 = 0,3)", async () => {
    const rows = { ...EMPTY_ROWS, unmatched: [tx({ id: "f1", amount: 0.3 })] };
    const user = await openDialog("f1", {
      rows, summary: summaryOf({ total: 1, unmatched: 1 }),
      invoices: [invoice({ id: "y1", number: "ΤΠΥ-Y1", outstanding: 0.1 + 0.2, total: 0.1 + 0.2 })],
    });
    await user.click(screen.getByTestId("pick-invoice-y1"));
    expect(screen.queryByTestId("accept-difference")).not.toBeInTheDocument();
    expect(screen.getByTestId("reconcile-confirm")).toBeEnabled();
  });

  it("το κείμενο της διαφοράς εξηγεί διαφορετικά τη μικρότερη και τη μεγαλύτερη κατάθεση", async () => {
    const user = await openDialog("t2");                                      // 80 €
    await user.click(screen.getByTestId("pick-invoice-i3"));                  // οφείλει 82 → κατάθεση μικρότερη
    expect(screen.getByTestId("reconcile-summary")).toHaveTextContent("Η κατάθεση είναι μικρότερη από το οφειλόμενο (π.χ. προμήθεια τράπεζας ή παρακράτηση)");
    expect(screen.getByTestId("reconcile-summary")).not.toHaveTextContent("μεγαλύτερη");
    expect(screen.getByTestId("reconcile-summary")).toHaveTextContent(/Διαφορά\D*2,00/);   // 80 − 82
    await user.click(screen.getByTestId("pick-invoice-i1"));                  // οφείλει 124 → κατάθεση μικρότερη (80)
    expect(screen.getByTestId("reconcile-summary")).toHaveTextContent("Η κατάθεση είναι μικρότερη από το οφειλόμενο");
    expect(screen.getByTestId("reconcile-summary")).toHaveTextContent(/Διαφορά\D*44,00/);  // 80 − 124
    expect(screen.getByTestId("reconcile-summary")).not.toHaveTextContent("μεγαλύτερη");
  });

  it("όταν η κατάθεση είναι μεγαλύτερη από το οφειλόμενο, το λέει και προειδοποιεί για πιθανή υπερπληρωμή", async () => {
    const rows = { ...EMPTY_ROWS, unmatched: [tx({ id: "g1", amount: 130 })] };
    const user = await openDialog("g1", { rows, summary: summaryOf({ total: 1, unmatched: 1 }) });
    await user.click(screen.getByTestId("pick-invoice-i1"));                  // οφείλει 124
    const summary = screen.getByTestId("reconcile-summary");
    expect(summary).toHaveTextContent("Η κατάθεση είναι μεγαλύτερη από το οφειλόμενο");
    expect(summary).toHaveTextContent("πιθανή προκαταβολή ή υπερπληρωμή");
    expect(summary).not.toHaveTextContent("μικρότερη");
    expect(summary).toHaveTextContent(/Διαφορά\D*6,00/);                       // 130 − 124
  });

  it("η αναζήτηση δεν επηρεάζεται από τόνους, κεφαλαία ή τελικό σίγμα", async () => {
    const user = await openDialog("t1", {
      invoices: [
        invoice({ id: "s1", number: "ΤΠΥ-S1", client_name: "ΠΑΠΑΔΟΠΟΥΛΟΣ ΓΙΩΡΓΟΣ", outstanding: 50, total: 50 }),
        invoice({ id: "s2", number: "ΤΠΥ-S2", client_name: "ΒΗΤΑ ΑΕ", outstanding: 60, total: 60, date: "2026-10-02" }),
      ],
    });
    const search = screen.getByTestId("reconcile-search");
    for (const typed of ["παπαδόπουλος", "ΠΑΠΑΔΟΠΟΥΛΟΣ", "παπαδοπουλοσ", "Γιώργος", "γιωργοσ"]) {
      await user.clear(search);
      await user.type(search, typed);
      expect(within(screen.getByTestId("reconcile-dialog")).getAllByTestId(/^pick-invoice-/).map((el) => el.dataset.testid)).toEqual(["pick-invoice-s1"]);
    }
    await user.clear(search);
    await user.type(search, "ΤΠΥ-S2");
    expect(within(screen.getByTestId("reconcile-dialog")).getAllByTestId(/^pick-invoice-/).map((el) => el.dataset.testid)).toEqual(["pick-invoice-s2"]);
    await user.clear(search);
    await user.type(search, "ΤΕΛΕΙΩΣ ΑΛΛΟ");
    expect(screen.getByText("Κανένα τιμολόγιο δεν ταιριάζει με την αναζήτηση.")).toBeInTheDocument();
  });

  it("με ίδια απόσταση από το ποσό, πρώτο το παλαιότερο τιμολόγιο και μετά ο μικρότερος αριθμός", async () => {
    await openDialog("t1", {
      invoices: [
        invoice({ id: "o3", number: "ΤΠΥ-0003", outstanding: 124, total: 124, date: "2026-10-05" }),
        invoice({ id: "o2", number: "ΤΠΥ-0002", outstanding: 124, total: 124, date: "2026-10-05" }),
        invoice({ id: "o1", number: "ΤΠΥ-0009", outstanding: 124, total: 124, date: "2026-10-01" }),
        invoice({ id: "o4", number: "ΤΠΥ-0004", outstanding: 130, total: 130, date: "2026-09-01" }),
      ],
    });
    expect(within(screen.getByTestId("reconcile-dialog")).getAllByTestId(/^pick-invoice-/).map((el) => el.dataset.testid))
      .toEqual(["pick-invoice-o1", "pick-invoice-o2", "pick-invoice-o3", "pick-invoice-o4"]);
  });

  it("αν δεν υπάρχει κανένα εκδοθέν τιμολόγιο με υπόλοιπο, το λέει καθαρά", async () => {
    await openDialog("t1", { invoices: [invoice({ id: "p1", status: "paid", outstanding: 0 })] });
    expect(screen.getByTestId("reconcile-no-invoices")).toHaveTextContent("Δεν υπάρχει εκδοθέν τιμολόγιο με υπόλοιπο προς είσπραξη.");
    expect(screen.queryByTestId("reconcile-settled-toggle")).not.toBeInTheDocument();
  });

  it("ένα πρόχειρο δεν προσφέρεται ποτέ για συμφωνία", async () => {
    await openDialog("t1", { invoices: [...INVOICES, invoice({ id: "dr", number: "", status: "draft", outstanding: 0, total: 124 })] });
    expect(screen.queryByTestId("pick-invoice-dr")).not.toBeInTheDocument();
  });

  it("το παράθυρο δεν κλείνει ενώ γίνεται η συμφωνία", async () => {
    let release;
    const gate = new Promise((resolve) => { release = resolve; });
    const user = await openDialog("t1");
    server.use(http.post(`${API}/bank/reconcile/:id`, async () => {
      await gate;
      return HttpResponse.json({ message: "ok", status: "paid", difference: 0 });
    }));
    await user.click(screen.getByTestId("pick-invoice-i1"));
    await user.click(screen.getByTestId("reconcile-confirm"));
    expect(screen.getByTestId("reconcile-cancel")).toBeDisabled();
    await user.keyboard("{Escape}");
    expect(screen.getByTestId("reconcile-dialog")).toBeInTheDocument();
    release();
    await waitFor(() => expect(screen.queryByTestId("reconcile-dialog")).not.toBeInTheDocument());
  });
});

describe("Τράπεζα — BK3: το τιμολόγιο άλλαξε ενώ ήταν ανοιχτό το παράθυρο", () => {
  const mismatch = "Η κατάθεση (124,00 €) δεν ταιριάζει με το οφειλόμενο του ΤΠΥ-2026-0001 (123,50 €): διαφορά +0,50 €. "
    + "Αν η διαφορά είναι αποδεκτή (π.χ. προμήθεια τράπεζας), πρέπει να την αποδεχτείτε ρητά (παράμετρος accept_difference).";

  /** Τα υπόλοιπα που επιστρέφει ο server στην πρώτη και στη δεύτερη ανάγνωση της λίστας τιμολογίων. */
  function serveRace({ first = 124, second = 123.5, posts }) {
    serve();
    let reads = 0;
    server.use(
      http.get(`${API}/invoices`, () => {
        reads += 1;
        const owed = reads === 1 ? first : second;
        return HttpResponse.json(second === null && reads > 1
          ? INVOICES.filter((inv) => inv.id !== "i1")
          : INVOICES.map((inv) => (inv.id === "i1" ? { ...inv, total: owed, outstanding: owed } : inv)));
      }),
      http.post(`${API}/bank/reconcile/:id`, async ({ request }) => {
        const q = new URL(request.url).searchParams;
        posts.push({ invoice_id: q.get("invoice_id"), accept: q.get("accept_difference") });
        if (q.get("accept_difference") === "true") return HttpResponse.json({ message: "Συνδέθηκε. Καταγράφηκε διαφορά +0.50 €.", status: "paid", difference: 0.5 });
        return HttpResponse.json({ detail: mismatch }, { status: 409 });
      }),
    );
    return () => reads;
  }

  async function pickExact() {
    const user = userEvent.setup();
    renderBank();
    await screen.findByTestId("bank-row-t1");
    await user.click(screen.getByTestId("bank-reconcile-t1"));
    await user.click(await screen.findByTestId("pick-invoice-i1"));
    return user;
  }

  it("ξαναδιαβάζει τα υπόλοιπα, δείχνει τη νέα διαφορά και ζητά ρητή αποδοχή — χωρίς τεχνικούς όρους", async () => {
    const posts = [];
    const reads = serveRace({ posts });
    const user = await pickExact();
    expect(screen.queryByTestId("accept-difference")).not.toBeInTheDocument();         // 124 = 124: ακριβές
    await user.click(screen.getByTestId("reconcile-confirm"));

    const error = await screen.findByTestId("reconcile-error");
    expect(error).toHaveTextContent("Το οφειλόμενο του ΤΠΥ-2026-0001 άλλαξε από 124,00 € σε 123,50 € ενώ το παράθυρο ήταν ανοιχτό.");
    expect(error).toHaveTextContent("Ελέγξτε τη νέα διαφορά και επιβεβαιώστε ξανά.");
    expect(error).not.toHaveTextContent("accept_difference");
    expect(error).not.toHaveTextContent("=true");
    expect(reads()).toBe(2);
    expect(screen.getByTestId("reconcile-summary")).toHaveTextContent("123,50");
    expect(screen.getByTestId("accept-difference")).toBeInTheDocument();
    expect(screen.getByTestId("accept-difference")).not.toBeChecked();
    expect(screen.getByTestId("reconcile-confirm")).toBeDisabled();
    expect(screen.getByTestId("reconcile-confirm")).toHaveTextContent("Συμφωνία και εξόφληση");
    expect(posts).toEqual([{ invoice_id: "i1", accept: null }]);

    await user.click(screen.getByTestId("accept-difference"));
    await user.click(screen.getByTestId("reconcile-confirm"));
    await waitFor(() => expect(posts).toHaveLength(2));
    expect(posts[1]).toEqual({ invoice_id: "i1", accept: "true" });
    await waitFor(() => expect(screen.queryByTestId("reconcile-dialog")).not.toBeInTheDocument());
  });

  it("αν το οφειλόμενο δεν άλλαξε, μένει το μήνυμα του server", async () => {
    const posts = [];
    serveRace({ first: 124, second: 124, posts });
    const user = await pickExact();
    await user.click(screen.getByTestId("reconcile-confirm"));
    const error = await screen.findByTestId("reconcile-error");
    expect(error).toHaveTextContent("δεν ταιριάζει με το οφειλόμενο");
    expect(error).not.toHaveTextContent("άλλαξε από");
    expect(screen.queryByTestId("accept-difference")).not.toBeInTheDocument();
  });

  it("αν το τιμολόγιο δεν είναι πια ανοιχτό, φεύγει από τη λίστα και μένει το μήνυμα του server", async () => {
    const posts = [];
    serveRace({ first: 124, second: null, posts });
    const user = await pickExact();
    await user.click(screen.getByTestId("reconcile-confirm"));
    expect(await screen.findByTestId("reconcile-error")).toHaveTextContent("δεν ταιριάζει με το οφειλόμενο");
    expect(screen.queryByTestId("pick-invoice-i1")).not.toBeInTheDocument();
    expect(screen.queryByTestId("reconcile-summary")).not.toBeInTheDocument();
    expect(screen.getByTestId("reconcile-confirm")).toBeDisabled();
  });

  it("αν η ανάγνωση των υπολοίπων αποτύχει, μένει το μήνυμα του server και ο διάλογος συνεχίζει να δουλεύει", async () => {
    serve();
    let reads = 0;
    server.use(
      http.get(`${API}/invoices`, () => {
        reads += 1;
        return reads === 1 ? HttpResponse.json(INVOICES) : HttpResponse.json({ detail: "αποτυχία" }, { status: 500 });
      }),
      http.post(`${API}/bank/reconcile/:id`, () => HttpResponse.json({ detail: "Το τιμολόγιο έχει ήδη εξοφληθεί από άλλη κίνηση." }, { status: 409 })),
    );
    const user = await pickExact();
    await user.click(screen.getByTestId("reconcile-confirm"));
    expect(await screen.findByTestId("reconcile-error")).toHaveTextContent("έχει ήδη εξοφληθεί από άλλη κίνηση");
    expect(screen.getByTestId("reconcile-dialog")).toBeInTheDocument();
    expect(screen.getByTestId("pick-invoice-i1")).toBeInTheDocument();
    expect(screen.getByTestId("reconcile-confirm")).toBeEnabled();
    expect(screen.getByTestId("reconcile-cancel")).toBeEnabled();
  });

  it("αν το τιμολόγιο εμφανίζεται πια ως εξοφλημένο με το ίδιο ποσό, δεν ισχυρίζεται ότι «άλλαξε»", async () => {
    serve();
    let reads = 0;
    server.use(
      http.get(`${API}/invoices`, () => {
        reads += 1;
        return HttpResponse.json(reads === 1 ? INVOICES : INVOICES.map((inv) => (inv.id === "i1"
          ? { ...inv, status: "paid", outstanding: 0, bank_link_due: 124 } : inv)));
      }),
      http.post(`${API}/bank/reconcile/:id`, () => HttpResponse.json({ detail: "Προσωρινό σφάλμα." }, { status: 500 })),
    );
    const user = await pickExact();
    await user.click(screen.getByTestId("reconcile-confirm"));
    const error = await screen.findByTestId("reconcile-error");
    expect(error).toHaveTextContent("Προσωρινό σφάλμα.");
    expect(error).not.toHaveTextContent("άλλαξε από");
  });
});

describe("Τράπεζα — BK5: κατάθεση για τιμολόγιο που είχε σημειωθεί εξοφλημένο με το χέρι", () => {
  const settledInvoices = (over = {}) => [
    ...INVOICES,
    invoice({ id: "s1", number: "ΤΠΥ-2026-0010", client_name: "ΕΨΙΛΟΝ ΑΕ", status: "paid", total: 124, outstanding: 0, bank_link_due: 124, date: "2026-09-20", ...over }),
    invoice({ id: "s2", number: "ΤΠΥ-2026-0011", client_name: "ΖΗΤΑ ΑΕ", status: "paid", total: 90, outstanding: 0, bank_link_due: 90, date: "2026-09-21" }),
  ];

  async function open(id = "t1", { invoices = settledInvoices(), replyHeaders } = {}) {
    const user = userEvent.setup();
    serve({ invoices });
    if (replyHeaders) server.use(http.get(`${API}/invoices`, () => HttpResponse.json(invoices, { headers: replyHeaders })));
    renderBank();
    await screen.findByTestId(`bank-row-${id}`);
    await user.click(screen.getByTestId(`bank-reconcile-${id}`));
    await screen.findByTestId("reconcile-dialog");
    return user;
  }

  it("τα εξοφλημένα με το χέρι είναι κρυμμένα μέχρι να τα ζητήσει ο χρήστης, και τα εκδοθέντα μένουν πρώτα", async () => {
    const user = await open();
    const toggle = screen.getByTestId("reconcile-settled-toggle");
    expect(toggle).toHaveTextContent("Εμφάνιση εξοφλημένων με το χέρι (2)");
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByTestId("pick-invoice-s1")).not.toBeInTheDocument();
    expect(screen.getByTestId("pick-invoice-i1")).toBeInTheDocument();
    await user.click(toggle);
    expect(screen.getByTestId("reconcile-settled-toggle")).toHaveTextContent("Απόκρυψη εξοφλημένων με το χέρι (2)");
    expect(screen.getByTestId("reconcile-settled-toggle")).toHaveAttribute("aria-expanded", "true");
    const order = within(screen.getByTestId("reconcile-dialog")).getAllByTestId(/^pick-invoice-/).map((el) => el.dataset.testid);
    expect(order.indexOf("pick-invoice-i1")).toBeLessThan(order.indexOf("pick-invoice-s1"));
    expect(screen.getByTestId("pick-invoice-s1")).toHaveTextContent("ήδη εξοφλημένο");
    expect(screen.getByTestId("pick-invoice-s1")).toHaveTextContent("ακριβές ποσό");                     // κατάθεση 124 = 124
    expect(screen.getByTestId("pick-invoice-s2")).not.toHaveTextContent("ακριβές ποσό");
    await user.click(screen.getByTestId("reconcile-settled-toggle"));
    expect(screen.queryByTestId("pick-invoice-s1")).not.toBeInTheDocument();
  });

  it("η σύνδεση: εξηγεί ότι η κατάσταση του τιμολογίου δεν αλλάζει και στέλνει το ίδιο αίτημα με κάθε συμφωνία", async () => {
    let call = null;
    const user = await open();
    server.use(http.post(`${API}/bank/reconcile/:id`, ({ request, params }) => {
      const q = new URL(request.url).searchParams;
      call = { id: params.id, invoice_id: q.get("invoice_id"), accept: q.get("accept_difference") };
      return HttpResponse.json({ message: "Η κατάθεση συνδέθηκε με το τιμολόγιο, που ήταν ήδη σημειωμένο ως εξοφλημένο.", status: "linked", difference: 0 });
    }));
    await user.click(screen.getByTestId("reconcile-settled-toggle"));
    await user.click(screen.getByTestId("pick-invoice-s1"));
    expect(screen.getByTestId("reconcile-settled-note")).toHaveTextContent("ήδη σημειωμένο ως εξοφλημένο");
    expect(screen.getByTestId("reconcile-settled-note")).toHaveTextContent("η κατάσταση του τιμολογίου δεν αλλάζει");
    expect(screen.queryByTestId("accept-difference")).not.toBeInTheDocument();
    expect(screen.getByTestId("reconcile-confirm")).toHaveTextContent("Σύνδεση κατάθεσης");
    expect(screen.getByTestId("reconcile-confirm")).toBeEnabled();
    await user.click(screen.getByTestId("reconcile-confirm"));
    await waitFor(() => expect(call).not.toBeNull());
    expect(call).toEqual({ id: "t1", invoice_id: "s1", accept: null });
    expect(await screen.findByText(/που ήταν ήδη σημειωμένο ως εξοφλημένο/)).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByTestId("reconcile-dialog")).not.toBeInTheDocument());
  });

  it("με διαφορά ποσού χρειάζεται ρητή αποδοχή, με κείμενο που μιλά για σύνδεση και όχι για εξόφληση", async () => {
    let call = null;
    const user = await open();                                                         // κατάθεση 124
    server.use(http.post(`${API}/bank/reconcile/:id`, ({ request }) => {
      call = { accept: new URL(request.url).searchParams.get("accept_difference") };
      return HttpResponse.json({ message: "ok", status: "linked", difference: 34 });
    }));
    await user.click(screen.getByTestId("reconcile-settled-toggle"));
    await user.click(screen.getByTestId("pick-invoice-s2"));                           // οφείλει 90
    expect(screen.getByTestId("accept-difference").closest("label")).toHaveTextContent("Αποδέχομαι τη διαφορά και θέλω να συνδεθεί η κατάθεση με το τιμολόγιο.");
    expect(screen.getByTestId("accept-difference").closest("label")).not.toHaveTextContent("να εξοφληθεί");
    expect(screen.getByTestId("reconcile-confirm")).toBeDisabled();
    await user.click(screen.getByTestId("accept-difference"));
    await user.click(screen.getByTestId("reconcile-confirm"));
    await waitFor(() => expect(call).toEqual({ accept: "true" }));
  });

  it("η συμφωνία ενός εκδοθέντος τιμολογίου συνεχίζει να μιλά για εξόφληση", async () => {
    const user = await open();
    await user.click(screen.getByTestId("pick-invoice-i3"));                           // οφείλει 82 → διαφορά
    expect(screen.getByTestId("accept-difference").closest("label")).toHaveTextContent("Αποδέχομαι τη διαφορά και θέλω να εξοφληθεί το τιμολόγιο.");
    expect(screen.queryByTestId("reconcile-settled-note")).not.toBeInTheDocument();
    expect(screen.getByTestId("reconcile-confirm")).toHaveTextContent("Συμφωνία και εξόφληση");
  });

  it("η αναζήτηση φιλτράρει και τα εξοφλημένα, και ο αριθμός στο κουμπί ακολουθεί την αναζήτηση", async () => {
    const user = await open();
    await user.type(screen.getByTestId("reconcile-search"), "ΕΨΙΛΟΝ");
    expect(screen.getByTestId("reconcile-settled-toggle")).toHaveTextContent("(1)");
    await user.click(screen.getByTestId("reconcile-settled-toggle"));
    expect(screen.getByTestId("pick-invoice-s1")).toBeInTheDocument();
    expect(screen.queryByTestId("pick-invoice-s2")).not.toBeInTheDocument();
    await user.clear(screen.getByTestId("reconcile-search"));
    await user.type(screen.getByTestId("reconcile-search"), "ΚΑΝΕΝΑ ΤΕΤΟΙΟ");
    expect(screen.getByText("Κανένα εξοφλημένο τιμολόγιο δεν ταιριάζει με την αναζήτηση.")).toBeInTheDocument();
  });

  it("δεν προσφέρονται εξοφλημένα χωρίς ποσό σύνδεσης, με μηδενικό ποσό ή με πιστωτικό", async () => {
    await open("t1", {
      invoices: [
        ...INVOICES,
        invoice({ id: "n1", status: "paid", outstanding: 0 }),                                          // τιμολόγιο Stripe/πελατολογικού: δεν έχει bank_link_due
        invoice({ id: "n2", status: "paid", outstanding: 0, bank_link_due: 0 }),
        invoice({ id: "n3", status: "paid", outstanding: 0, bank_link_due: 124, credit_note_id: "pis" }),
        invoice({ id: "n4", status: "cancelled", outstanding: 0, bank_link_due: 124 }),
        invoice({ id: "n5", status: "draft", outstanding: 0, bank_link_due: 124 }),
      ],
    });
    expect(screen.queryByTestId("reconcile-settled-toggle")).not.toBeInTheDocument();
    for (const id of ["n1", "n2", "n3", "n4", "n5"]) expect(screen.queryByTestId(`pick-invoice-${id}`)).not.toBeInTheDocument();
  });

  it("όταν δεν υπάρχει ανοιχτό τιμολόγιο αλλά υπάρχουν εξοφλημένα, το μήνυμα το υποδεικνύει", async () => {
    await open("t1", { invoices: [invoice({ id: "s9", status: "paid", outstanding: 0, bank_link_due: 124 })] });
    const message = screen.getByTestId("reconcile-no-invoices");
    expect(message).toHaveTextContent("Δεν υπάρχει εκδοθέν τιμολόγιο με υπόλοιπο προς είσπραξη.");
    expect(message).toHaveTextContent("Αν το τιμολόγιο είχε σημειωθεί εξοφλημένο με το χέρι, εμφανίστε τα εξοφλημένα παρακάτω.");
  });

  it("αν υπάρχουν και παλαιότερα εξοφλημένα που δεν φορτώθηκαν, το λέει", async () => {
    const user = await open("t1", { replyHeaders: { "X-Closed-Truncated": "1" } });
    await user.click(screen.getByTestId("reconcile-settled-toggle"));
    expect(screen.getByTestId("reconcile-settled-section")).toHaveTextContent("Εμφανίζονται μόνο τα πιο πρόσφατα εξοφλημένα τιμολόγια.");
  });

  it("χωρίς περικοπή δεν υπάρχει τέτοια σημείωση", async () => {
    const user = await open();
    await user.click(screen.getByTestId("reconcile-settled-toggle"));
    expect(screen.getByTestId("reconcile-settled-section")).not.toHaveTextContent("μόνο τα πιο πρόσφατα");
  });

  it("η άρνηση του server (π.χ. τη συνέδεσε στο μεταξύ άλλη κατάθεση) εμφανίζεται και ο διάλογος μένει ανοιχτός", async () => {
    const user = await open();
    server.use(http.post(`${API}/bank/reconcile/:id`, () => HttpResponse.json(
      { detail: "Το τιμολόγιο ΤΠΥ-2026-0010 έχει ήδη εξοφληθεί. Η κατάθεση 124,00 € μπορεί να είναι διπλοπληρωμή." }, { status: 409 })));
    await user.click(screen.getByTestId("reconcile-settled-toggle"));
    await user.click(screen.getByTestId("pick-invoice-s1"));
    await user.click(screen.getByTestId("reconcile-confirm"));
    expect(await screen.findByTestId("reconcile-error")).toHaveTextContent("μπορεί να είναι διπλοπληρωμή");
    expect(screen.getByTestId("reconcile-dialog")).toBeInTheDocument();
  });
});
