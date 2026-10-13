/**
 * Τράπεζα — δεύτερος έλεγχος του γύρου 5 (δοκιμές μεταλλάξεων): συμπεριφορές που καμία δοκιμή δεν «πρόσεχε».
 *
 * Οι ιδιότητες που προστατεύονται: μια κατάθεση δεν εφαρμόζεται ποτέ σε τιμολόγιο με ποσό που ο χρήστης δεν έχει δει·
 * κάθε αριθμός στην οθόνη είναι ο αριθμός που κρατά ο server· μια αποτυχημένη φόρτωση δεν παρουσιάζεται ως άδεια λίστα.
 * Τα ποσά στις δοκιμές επιλέγονται ώστε η κινητή υποδιαστολή να «πιάνει» κάθε εσφαλμένη στρογγυλοποίηση
 * (0,57 × 100 = 56,99999999999999 · 0,28 + 0,29 = 0,5700000000000001 · 19,99 × 100 = 1998,9999999999998).
 */
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { afterEach, describe, expect, it, vi } from "vitest";

import { API, INVOICES, ROWS, SUMMARY, invoice, renderBank, serve, tx } from "@/test/bankFixtures";
import { server } from "@/test/server";

const EMPTY_ROWS = { unmatched: [], auto_matched: [], manual_matched: [], auto_confirmed: [], ignored: [] };
const summaryOf = (over = {}) => ({ total: 0, unmatched: 0, auto_matched: 0, manual_matched: 0, auto_confirmed: 0, ignored: 0, ...over });
const rowsOf = (...unmatched) => ({ ...EMPTY_ROWS, unmatched });
const csv = () => new File(["Ημερομηνία;Αιτιολογία;Ποσό\n09/10/2026;ΕΜΒΑΣΜΑ;124,00\n"], "kiniseis.csv", { type: "text/csv" });

/** Ανοίγει τη συμφωνία της κίνησης `id` με τα δεδομένα που δίνονται (οι υπόλοιποι είναι οι προεπιλεγμένοι της οθόνης). */
async function openDialog(id, { rows, invoices, summary } = {}) {
  const user = userEvent.setup();
  serve({ rows, invoices, summary });
  renderBank();
  await screen.findByTestId(`bank-row-${id}`);
  await user.click(screen.getByTestId(`bank-reconcile-${id}`));
  await screen.findByTestId("reconcile-dialog");
  return user;
}

const pickIds = () => within(screen.getByTestId("reconcile-dialog")).getAllByTestId(/^pick-invoice-/).map((el) => el.dataset.testid);

/**
 * Η n-οστή ανάγνωση των τιμολογίων δίνει τη n-οστή λίστα της `sequence` (η τελευταία επαναλαμβάνεται)· κάθε POST συμφωνίας
 * περνά από το `post(searchParams)`. Επιστρέφει πόσες αναγνώσεις έγιναν.
 */
function serveReads(sequence, post) {
  let reads = 0;
  server.use(
    http.get(`${API}/invoices`, () => {
      const step = sequence[Math.min(reads, sequence.length - 1)];
      reads += 1;
      return HttpResponse.json(step.invoices, step.truncated ? { headers: { "X-Closed-Truncated": "1" } } : undefined);
    }),
    http.post(`${API}/bank/reconcile/:id`, ({ request }) => post(new URL(request.url).searchParams)),
  );
  return () => reads;
}

afterEach(() => vi.restoreAllMocks());

describe("Τράπεζα — κάρτες υποψήφιων τιμολογίων: ό,τι φαίνεται είναι ό,τι κρατά ο server", () => {
  it("η κάρτα δείχνει το οφειλόμενο (outstanding), όχι το μεικτό σύνολο, και τη διαφορά σε ευρώ, όχι σε λεπτά", async () => {
    await openDialog("a1", {
      rows: rowsOf(tx({ id: "a1", amount: 80 })), summary: summaryOf({ total: 1, unmatched: 1 }),
      // 24 € από το σύνολο έχουν ήδη εξοφληθεί από τον πελατολογικό λογαριασμό: μένουν 100 €
      invoices: [invoice({ id: "w1", number: "ΤΠΥ-W1", client_name: "ΕΨΙΛΟΝ ΑΕ", total: 124, outstanding: 100 })],
    });
    const card = screen.getByTestId("pick-invoice-w1");
    expect(card).toHaveTextContent(/100,00/);
    expect(card).not.toHaveTextContent(/124,00/);
    expect(card).toHaveTextContent(/διαφέρει\s*20,00/);                       // 100 − 80, όχι 2.000,00
    expect(card).not.toHaveTextContent("ακριβές ποσό");
  });

  it("η κάρτα ενός εξοφλημένου με το χέρι τιμολογίου δείχνει το ποσό σύνδεσης (bank_link_due), όχι το σύνολο", async () => {
    const user = await openDialog("a1", {
      rows: rowsOf(tx({ id: "a1", amount: 80 })), summary: summaryOf({ total: 1, unmatched: 1 }),
      invoices: [invoice({ id: "s1", number: "ΤΠΥ-S1", status: "paid", total: 124, outstanding: 0, bank_link_due: 90 })],
    });
    await user.click(screen.getByTestId("reconcile-settled-toggle"));
    const card = screen.getByTestId("pick-invoice-s1");
    expect(card).toHaveTextContent("ήδη εξοφλημένο");
    expect(card).toHaveTextContent(/90,00/);
    expect(card).not.toHaveTextContent(/124,00/);
    expect(card).toHaveTextContent(/διαφέρει\s*10,00/);
  });

  it("τα εξοφλημένα με το χέρι ταξινομούνται κι αυτά κατά εγγύτητα στο ποσό της κατάθεσης", async () => {
    const settled = (id, due) => invoice({ id, number: `ΤΠΥ-${id.toUpperCase()}`, status: "paid", total: due, outstanding: 0, bank_link_due: due });
    const user = await openDialog("t1", {                                       // κατάθεση 124 €
      invoices: [settled("s1", 90), settled("s2", 130), settled("s3", 124), settled("s4", 100)],
    });
    await user.click(screen.getByTestId("reconcile-settled-toggle"));
    expect(pickIds()).toEqual(["pick-invoice-s3", "pick-invoice-s2", "pick-invoice-s4", "pick-invoice-s1"]);   // διαφορές 0, 6, 24, 34
  });

  it("το «πρόταση συστήματος» μπαίνει μόνο στο τιμολόγιο που πρότεινε το σύστημα", async () => {
    await openDialog("t4");                                                    // πρόταση για το i2
    expect(screen.getByTestId("pick-invoice-i2")).toHaveTextContent("πρόταση συστήματος");
    expect(screen.getByTestId("pick-invoice-i1")).not.toHaveTextContent("πρόταση συστήματος");
    expect(screen.getByTestId("pick-invoice-i3")).not.toHaveTextContent("πρόταση συστήματος");
  });

  it("κίνηση χωρίς πρόταση δεν δείχνει «πρόταση συστήματος» πουθενά", async () => {
    await openDialog("t1");
    expect(within(screen.getByTestId("reconcile-dialog")).queryByText("πρόταση συστήματος")).not.toBeInTheDocument();
  });

  it("τα ποσά στρογγυλεύονται στο λεπτό: 0,57 € είναι ακριβώς 0,28 + 0,29 €", async () => {
    const user = await openDialog("k1", {
      rows: rowsOf(tx({ id: "k1", amount: 0.57 })), summary: summaryOf({ total: 1, unmatched: 1 }),
      invoices: [invoice({ id: "z1", number: "ΤΠΥ-Z1", total: 0.28 + 0.29, outstanding: 0.28 + 0.29 })],
    });
    expect(screen.getByTestId("pick-invoice-z1")).toHaveTextContent("ακριβές ποσό");
    await user.click(screen.getByTestId("pick-invoice-z1"));
    expect(screen.queryByTestId("accept-difference")).not.toBeInTheDocument();    // δεν ζητά αποδοχή ψεύτικης διαφοράς
    expect(screen.getByTestId("reconcile-confirm")).toBeEnabled();
  });

  it("κατάθεση 19,99 € για τιμολόγιο 20,00 € διαφέρει ένα λεπτό — όχι δύο", async () => {
    const user = await openDialog("k2", {
      rows: rowsOf(tx({ id: "k2", amount: 19.99 })), summary: summaryOf({ total: 1, unmatched: 1 }),
      invoices: [invoice({ id: "z2", number: "ΤΠΥ-Z2", total: 20, outstanding: 20 })],
    });
    expect(screen.getByTestId("pick-invoice-z2")).toHaveTextContent(/διαφέρει\s*0,01/);
    await user.click(screen.getByTestId("pick-invoice-z2"));
    expect(screen.getByTestId("reconcile-summary")).toHaveTextContent(/Διαφορά\s*-0,01/);
    expect(screen.getByTestId("accept-difference")).toBeInTheDocument();
  });
});

describe("Τράπεζα — το τιμολόγιο άλλαξε ενώ ήταν ανοιχτό το παράθυρο (συμπληρωματικές περιπτώσεις)", () => {
  const settled = (due) => invoice({ id: "s1", number: "ΤΠΥ-2026-0010", client_name: "ΕΨΙΛΟΝ ΑΕ", status: "paid", total: 124, outstanding: 0, bank_link_due: due });
  const refuse = (detail) => () => HttpResponse.json({ detail }, { status: 409 });

  it("η επιλογή άλλου τιμολογίου σβήνει το μήνυμα σφάλματος του προηγούμενου", async () => {
    serve();
    server.use(http.post(`${API}/bank/reconcile/:id`, refuse("Το τιμολόγιο έχει ήδη εξοφληθεί από άλλη κίνηση.")));
    const user = await openDialog("t1");
    await user.click(screen.getByTestId("pick-invoice-i1"));
    await user.click(screen.getByTestId("reconcile-confirm"));
    expect(await screen.findByTestId("reconcile-error")).toHaveTextContent("έχει ήδη εξοφληθεί από άλλη κίνηση");
    await user.click(screen.getByTestId("pick-invoice-i3"));
    expect(screen.queryByTestId("reconcile-error")).not.toBeInTheDocument();
  });

  it("διαφορά μόνο στο σφάλμα κινητής υποδιαστολής (0,3 → 0,1 + 0,2) δεν θεωρείται αλλαγή του οφειλόμενου", async () => {
    const user = userEvent.setup();
    serve({ rows: rowsOf(tx({ id: "f1", amount: 0.3 })), summary: summaryOf({ total: 1, unmatched: 1 }) });
    const reads = serveReads([
      { invoices: [invoice({ id: "y1", number: "ΤΠΥ-Y1", total: 0.3, outstanding: 0.3 })] },
      { invoices: [invoice({ id: "y1", number: "ΤΠΥ-Y1", total: 0.1 + 0.2, outstanding: 0.1 + 0.2 })] },
    ], () => HttpResponse.json({ detail: "Προσωρινό σφάλμα." }, { status: 500 }));
    renderBank();
    await screen.findByTestId("bank-row-f1");
    await user.click(screen.getByTestId("bank-reconcile-f1"));
    await user.click(await screen.findByTestId("pick-invoice-y1"));
    expect(screen.getByTestId("reconcile-confirm")).toBeEnabled();              // ακριβές: 0,30 = 0,30
    await user.click(screen.getByTestId("reconcile-confirm"));
    const error = await screen.findByTestId("reconcile-error");
    expect(error).toHaveTextContent("Προσωρινό σφάλμα.");
    expect(error).not.toHaveTextContent("άλλαξε από");
    expect(reads()).toBe(2);
  });

  it("αν άλλαξε το ποσό σύνδεσης ενός εξοφλημένου με το χέρι τιμολογίου, ο χρήστης το μαθαίνει και ξαναδίνει αποδοχή", async () => {
    const posts = [];
    serve();
    serveReads([{ invoices: [settled(124)] }, { invoices: [settled(130)] }], (q) => {
      posts.push(q.get("accept_difference"));
      return refuse("Η κατάθεση δεν ταιριάζει με το ποσό σύνδεσης.")();
    });
    const user = userEvent.setup();
    renderBank();
    await screen.findByTestId("bank-row-t1");
    await user.click(screen.getByTestId("bank-reconcile-t1"));
    await user.click(await screen.findByTestId("reconcile-settled-toggle"));
    await user.click(screen.getByTestId("pick-invoice-s1"));
    expect(screen.queryByTestId("accept-difference")).not.toBeInTheDocument();    // 124 = 124
    await user.click(screen.getByTestId("reconcile-confirm"));

    const error = await screen.findByTestId("reconcile-error");
    expect(error).toHaveTextContent("Το οφειλόμενο του ΤΠΥ-2026-0010 άλλαξε από 124,00 € σε 130,00 € ενώ το παράθυρο ήταν ανοιχτό.");
    expect(screen.getByTestId("reconcile-summary")).toHaveTextContent("130,00");
    expect(screen.getByTestId("accept-difference")).not.toBeChecked();
    expect(screen.getByTestId("reconcile-confirm")).toBeDisabled();
    expect(posts).toEqual([null]);
  });

  it("αν το οφειλόμενο άλλαξε, η αποδοχή διαφοράς που δόθηκε για το παλιό ποσό ακυρώνεται: ούτε το νέο ποσό εξοφλείται χωρίς να το δει", async () => {
    const posts = [];
    serve();
    serveReads([
      { invoices: INVOICES },
      { invoices: INVOICES.map((inv) => (inv.id === "i1" ? { ...inv, total: 100, outstanding: 100 } : inv)) },
    ], (q) => {
      posts.push(q.get("accept_difference"));
      return refuse("Η κατάθεση (80,00 €) δεν ταιριάζει με το οφειλόμενο του ΤΠΥ-2026-0001 (100,00 €).")();
    });
    const user = userEvent.setup();
    renderBank();
    await screen.findByTestId("bank-row-t2");                                    // κατάθεση 80 €
    await user.click(screen.getByTestId("bank-reconcile-t2"));
    await user.click(await screen.findByTestId("pick-invoice-i1"));              // οφείλει 124: διαφορά −44
    await user.click(screen.getByTestId("accept-difference"));
    expect(screen.getByTestId("reconcile-confirm")).toBeEnabled();
    await user.click(screen.getByTestId("reconcile-confirm"));

    expect(await screen.findByTestId("reconcile-error")).toHaveTextContent("άλλαξε από 124,00 € σε 100,00 €");
    expect(screen.getByTestId("accept-difference")).not.toBeChecked();
    expect(screen.getByTestId("reconcile-confirm")).toBeDisabled();
    expect(posts).toEqual(["true"]);                                             // τίποτα δεν στάλθηκε για το νέο ποσό
  });

  it("η ένδειξη «μόνο τα πιο πρόσφατα εξοφλημένα» ακολουθεί τα φρέσκα δεδομένα μετά από αποτυχημένη συμφωνία", async () => {
    serve();
    serveReads([{ invoices: [settled(124)] }, { invoices: [settled(124)], truncated: true }], refuse("Προσωρινό σφάλμα."));
    const user = userEvent.setup();
    renderBank();
    await screen.findByTestId("bank-row-t1");
    await user.click(screen.getByTestId("bank-reconcile-t1"));
    await user.click(await screen.findByTestId("reconcile-settled-toggle"));
    expect(screen.getByTestId("reconcile-settled-section")).not.toHaveTextContent("μόνο τα πιο πρόσφατα");
    await user.click(screen.getByTestId("pick-invoice-s1"));
    await user.click(screen.getByTestId("reconcile-confirm"));
    await screen.findByTestId("reconcile-error");
    expect(screen.getByTestId("reconcile-settled-section")).toHaveTextContent("Εμφανίζονται μόνο τα πιο πρόσφατα εξοφλημένα τιμολόγια.");
  });
});

describe("Τράπεζα — άνοιγμα της συμφωνίας", () => {
  it("αν δεν διαβαστούν τα τιμολόγια, δεν ανοίγει διάλογος με «δεν υπάρχει εκδοθέν τιμολόγιο»: φαίνεται το σφάλμα και το κουμπί ξαναδουλεύει", async () => {
    const user = userEvent.setup();
    serve();
    server.use(http.get(`${API}/invoices`, () => HttpResponse.json({ detail: "Η βάση δεν είναι διαθέσιμη." }, { status: 503 })));
    renderBank();
    await screen.findByTestId("bank-row-t1");
    await user.click(screen.getByTestId("bank-reconcile-t1"));
    expect(await screen.findByText("Η βάση δεν είναι διαθέσιμη.")).toBeInTheDocument();
    expect(screen.queryByTestId("reconcile-dialog")).not.toBeInTheDocument();
    expect(screen.queryByTestId("reconcile-no-invoices")).not.toBeInTheDocument();
    await waitFor(() => expect(screen.getByTestId("bank-reconcile-t1")).toBeEnabled());

    serve();                                                                      // ο server επανήλθε
    await user.click(screen.getByTestId("bank-reconcile-t1"));
    expect(await screen.findByTestId("reconcile-dialog")).toBeInTheDocument();
    expect(screen.getByTestId("pick-invoice-i1")).toBeInTheDocument();
  });
});

describe("Τράπεζα — φίλτρα και φόρτωση", () => {
  it("το κλικ στο ήδη ενεργό φίλτρο δεν καθαρίζει τη λίστα ούτε την αφήνει να φορτώνει για πάντα", async () => {
    const user = userEvent.setup();
    serve();
    renderBank();
    await screen.findByTestId("bank-row-t1");
    await user.click(screen.getByTestId("bank-filter-action"));
    expect(screen.getByTestId("bank-row-t1")).toBeInTheDocument();
    expect(screen.queryByText("Φόρτωση κινήσεων…")).not.toBeInTheDocument();
  });

  it("όσο φορτώνει η νέα προβολή δεν μένουν στην οθόνη οι γραμμές της προηγούμενης", async () => {
    const user = userEvent.setup();
    serve();
    renderBank();
    await screen.findByTestId("bank-row-t1");
    let release;
    const gate = new Promise((resolve) => { release = resolve; });
    server.use(http.get(`${API}/bank/transactions`, async ({ request }) => {
      if (new URL(request.url).searchParams.get("status") === "ignored") { await gate; return HttpResponse.json(ROWS.ignored); }
      return HttpResponse.json([]);
    }));
    await user.click(screen.getByTestId("bank-filter-ignored"));
    expect(await screen.findByText("Φόρτωση κινήσεων…")).toBeInTheDocument();
    expect(screen.queryByTestId("bank-row-t1")).not.toBeInTheDocument();
    release();
    expect(await screen.findByTestId("bank-row-t6")).toBeInTheDocument();
    expect(screen.queryByText("Φόρτωση κινήσεων…")).not.toBeInTheDocument();
  });

  it("άδεια λίστα και αποτυχία της ανανέωσης: μόνο το σφάλμα φόρτωσης, όχι «Δεν υπάρχουν κινήσεις»", async () => {
    const user = userEvent.setup({ applyAccept: false });
    serve({ summary: summaryOf({ total: 0 }), rows: EMPTY_ROWS });
    renderBank();
    expect(await screen.findByTestId("bank-empty")).toBeInTheDocument();
    server.use(
      http.post(`${API}/bank/import`, () => HttpResponse.json({ message: "ok", created: 1, skipped: 0 })),
      http.get(`${API}/bank/summary`, () => HttpResponse.json({ detail: "Η βάση δεν είναι διαθέσιμη." }, { status: 503 })),
    );
    await user.upload(screen.getByTestId("bank-import-input"), csv());           // μετά την εισαγωγή η λίστα ανανεώνεται — και αποτυγχάνει
    const banner = await screen.findByTestId("bank-load-error");
    expect(banner).toHaveTextContent("Δεν φορτώθηκαν οι κινήσεις: Η βάση δεν είναι διαθέσιμη.");
    expect(screen.queryByTestId("bank-empty")).not.toBeInTheDocument();
    expect(screen.queryByTestId("bank-table")).not.toBeInTheDocument();
  });

  it("οι ειδοποιήσεις περικοπής των «Αγνοημένων» και «Συμφωνημένων» δεν παραπέμπουν στις «Χρειάζονται ενέργεια»", async () => {
    const user = userEvent.setup();
    const some = (status, n, prefix) => Array.from({ length: n }, (_, i) => tx({ id: `${prefix}${i}`, match_status: status, date: `2026-09-${10 + i}` }));
    serve({
      summary: summaryOf({ total: 12, ignored: 7, manual_matched: 5 }),
      rows: { ...EMPTY_ROWS, ignored: some("ignored", 5, "g"), manual_matched: some("manual_matched", 3, "h") },
    });
    renderBank();
    await user.click(await screen.findByTestId("bank-filter-ignored"));
    let notice = await screen.findByTestId("bank-truncated");
    expect(notice).toHaveTextContent("Εμφανίζονται οι 5 πιο πρόσφατες από 7 κινήσεις — οι 2 παλαιότερες δεν εμφανίζονται σε αυτή την προβολή");
    expect(notice).not.toHaveTextContent("χρησιμοποιήστε την προβολή");
    await user.click(screen.getByTestId("bank-filter-done"));
    await screen.findByTestId("bank-row-h0");
    notice = await screen.findByTestId("bank-truncated");
    expect(notice).toHaveTextContent("Εμφανίζονται οι 3 πιο πρόσφατες από 5 κινήσεις — οι 2 παλαιότερες");
    expect(notice).not.toHaveTextContent("χρησιμοποιήστε την προβολή");
    expect(notice).not.toHaveTextContent("Χρειάζονται ενέργεια");
  });
});

describe("Τράπεζα — σύνολα (KPI) και μαζική επιβεβαίωση", () => {
  const SUMMARY_MIXED = summaryOf({ total: 20, unmatched: 4, auto_matched: 2, manual_matched: 5, auto_confirmed: 3, ignored: 6 });
  const kpi = (id) => screen.getByTestId(`bank-kpi-${id}`);

  it("κάθε KPI διαβάζει το δικό του πεδίο της σύνοψης: οι «Συμφωνημένες» είναι χειροκίνητες + αυτόματες", async () => {
    serve({ summary: SUMMARY_MIXED });
    renderBank();
    await screen.findByTestId("bank-row-t1");
    expect(within(kpi("review")).getByText("2")).toBeInTheDocument();
    expect(within(kpi("unmatched")).getByText("4")).toBeInTheDocument();
    expect(within(kpi("done")).getByText("8")).toBeInTheDocument();             // 5 + 3
    expect(within(kpi("ignored")).getByText("6")).toBeInTheDocument();          // όχι οι 2 προτάσεις
  });

  it("το κουμπί μαζικής επιβεβαίωσης δείχνει τις προτάσεις της σύνοψης του server, όχι όσες γραμμές φορτώθηκαν", async () => {
    serve({ summary: SUMMARY_MIXED });                                           // 2 προτάσεις· στη λίστα φαίνεται μία (t4) και 4 γραμμές συνολικά
    renderBank();
    await screen.findByTestId("bank-row-t1");
    expect(screen.getByTestId("bank-confirm-all")).toHaveTextContent("Επιβεβαίωση ασφαλών προτάσεων (2)");
  });

  it("μετά τη μαζική επιβεβαίωση η λίστα ξαναφορτώνει: οι επιβεβαιωμένες προτάσεις φεύγουν και το κουμπί εξαφανίζεται", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    const user = userEvent.setup();
    serve();
    server.use(http.post(`${API}/bank/confirm-auto-matches`, () => {
      serve({
        summary: { ...SUMMARY, auto_matched: 0, auto_confirmed: 1 },
        rows: { ...ROWS, auto_matched: [], auto_confirmed: [tx({ id: "t4", match_status: "auto_confirmed", matched_invoice_id: "i2", matched_invoice_number: "ΤΠΥ-2026-0002", amount: 248 })] },
      });
      return HttpResponse.json({ message: "Επιβεβαιώθηκε 1 σύνδεση. Το αντίστοιχο τιμολόγιο εξοφλήθηκε.", confirmed: 1, skipped: 0, skipped_items: [] });
    }));
    renderBank();
    await screen.findByTestId("bank-row-t4");
    expect(screen.getByTestId("bank-row-t4")).toHaveTextContent("Πρόταση");
    await user.click(screen.getByTestId("bank-confirm-all"));
    expect(await screen.findByTestId("bank-confirm-result")).toHaveTextContent("Επιβεβαιώθηκε 1 σύνδεση");
    await waitFor(() => expect(screen.queryByTestId("bank-row-t4")).not.toBeInTheDocument());   // δεν μένει ως «Πρόταση»
    expect(screen.queryByTestId("bank-confirm-all")).not.toBeInTheDocument();
    expect(screen.getByTestId("bank-row-t1")).toBeInTheDocument();
  });
});

describe("Τράπεζα — εισαγωγή αρχείου: κατάσταση του κουμπιού και προβολή", () => {
  it("μετά την εισαγωγή το κουμπί ξεκλειδώνει — και το ίδιο μετά από αποτυχία", async () => {
    const user = userEvent.setup({ applyAccept: false });
    serve();
    let fail = false;
    server.use(http.post(`${API}/bank/import`, () => (fail
      ? HttpResponse.json({ detail: "Δεν βρέθηκαν εγκυρες κινήσεις." }, { status: 400 })
      : HttpResponse.json({ message: "ok", created: 1, skipped: 0 }))));
    renderBank();
    await screen.findByTestId("bank-row-t1");
    await user.upload(screen.getByTestId("bank-import-input"), csv());
    await screen.findByTestId("bank-import-result");
    await waitFor(() => expect(screen.getByTestId("bank-import-button")).toBeEnabled());
    expect(screen.getByTestId("bank-import-button")).toHaveTextContent("Εισαγωγή αρχείου κινήσεων (CSV)");

    fail = true;
    await user.upload(screen.getByTestId("bank-import-input"), csv());
    expect(await screen.findByText("Δεν βρέθηκαν εγκυρες κινήσεις.")).toBeInTheDocument();
    await waitFor(() => expect(screen.getByTestId("bank-import-button")).toBeEnabled());
    expect(screen.getByTestId("bank-import-button")).toHaveTextContent("Εισαγωγή αρχείου κινήσεων (CSV)");
  });

  it("η εισαγωγή από την προβολή «Αγνοημένες» πηγαίνει στις «Χρειάζονται ενέργεια», όπου θα φανούν οι νέες κινήσεις", async () => {
    const user = userEvent.setup({ applyAccept: false });
    const requested = serve();
    server.use(http.post(`${API}/bank/import`, () => HttpResponse.json({ message: "ok", created: 1, skipped: 0, auto_matched: 0, needs_review: 0 })));
    renderBank();
    await user.click(await screen.findByTestId("bank-filter-ignored"));
    await screen.findByTestId("bank-row-t6");
    requested.length = 0;
    await user.upload(screen.getByTestId("bank-import-input"), csv());
    await screen.findByTestId("bank-import-result");
    await waitFor(() => expect(requested.sort()).toEqual(["auto_matched", "unmatched"]));
    expect(screen.getByTestId("bank-filter-action")).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByTestId("bank-filter-ignored")).toHaveAttribute("aria-pressed", "false");
  });

  it("το ίδιο αρχείο μπορεί να ξαναεπιλεγεί (π.χ. αφού ο server απέρριψε την πρώτη εισαγωγή)", async () => {
    const user = userEvent.setup({ applyAccept: false });
    serve();
    let posts = 0;
    server.use(http.post(`${API}/bank/import`, () => {
      posts += 1;
      return posts === 1
        ? HttpResponse.json({ detail: "Προσωρινό σφάλμα." }, { status: 503 })
        : HttpResponse.json({ message: "Εισήχθησαν 1 κινήσεις.", created: 1, skipped: 0 });
    }));
    renderBank();
    await screen.findByTestId("bank-row-t1");
    const file = csv();
    const input = screen.getByTestId("bank-import-input");
    await user.upload(input, file);
    await waitFor(() => expect(posts).toBe(1));
    await waitFor(() => expect(screen.getByTestId("bank-import-button")).toBeEnabled());
    await user.upload(input, file);                                              // το ΙΔΙΟ αντικείμενο αρχείου, όπως σε πραγματικό browser
    expect(await screen.findByTestId("bank-import-result")).toHaveTextContent("Εισήχθησαν 1 κινήσεις.");
    expect(posts).toBe(2);
  });
});

describe("Τράπεζα — αγνόηση και στήλη αντιστοίχισης", () => {
  it("η αγνόηση κλειδώνει το κουμπί όσο τρέχει το αίτημα: το διπλό κλικ στέλνει ένα μόνο αίτημα", async () => {
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    const user = userEvent.setup();
    serve();
    let posts = 0;
    let release;
    const gate = new Promise((resolve) => { release = resolve; });
    server.use(http.post(`${API}/bank/reconcile/:id`, async () => {
      posts += 1;
      await gate;
      return HttpResponse.json({ message: "Η κίνηση σημειώθηκε ως αγνοητέα" });
    }));
    renderBank();
    await screen.findByTestId("bank-row-t3");
    await user.dblClick(screen.getByTestId("bank-ignore-t3"));
    await waitFor(() => expect(posts).toBe(1));
    expect(screen.getByTestId("bank-ignore-t3")).toBeDisabled();
    expect(confirm).toHaveBeenCalledTimes(1);
    release();
    await waitFor(() => expect(screen.getByTestId("bank-ignore-t3")).toBeEnabled());
    expect(posts).toBe(1);
  });

  it("η ερώτηση αγνόησης γράφει την ημερομηνία ΗΗ/ΜΜ/ΕΕΕΕ και το ποσό της κίνησης", async () => {
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    const user = userEvent.setup();
    serve();
    renderBank();
    await screen.findByTestId("bank-row-t3");
    await user.click(screen.getByTestId("bank-ignore-t3"));                      // ΕΝΟΙΚΙΟ, −600 €, 2026-10-07
    expect(confirm).toHaveBeenCalledTimes(1);
    const question = confirm.mock.calls[0][0];
    expect(question).toContain("«ΕΝΟΙΚΙΟ»");
    expect(question).toMatch(/-600,00\s€\) της 07\/10\/2026;/);
    expect(question).not.toContain("2026-10-07");
  });

  it("συμφωνημένη κίνηση χωρίς πεδίο διαφοράς ποσού δεν δείχνει «Διαφορά ποσού που έγινε αποδεκτή»", async () => {
    const user = userEvent.setup();
    serve({
      summary: summaryOf({ total: 2, manual_matched: 2 }),
      rows: {
        ...EMPTY_ROWS,
        manual_matched: [
          tx({ id: "m1", match_status: "manual_matched", matched_invoice_number: "ΤΠΥ-1", amount_difference: undefined }),
          tx({ id: "m2", match_status: "manual_matched", matched_invoice_number: "ΤΠΥ-2", amount_difference: null, date: "2026-10-08" }),
        ],
      },
    });
    renderBank();
    await user.click(await screen.findByTestId("bank-filter-done"));
    expect(await screen.findByTestId("bank-match-m1")).toHaveTextContent("ΤΠΥ-1");
    expect(screen.getByTestId("bank-match-m1")).not.toHaveTextContent("Διαφορά ποσού");
    expect(screen.getByTestId("bank-match-m2")).not.toHaveTextContent("Διαφορά ποσού");
  });
});
