/**
 * Τράπεζα — ευρήματα D-05, D-06, D-09 του δεύτερου ελέγχου του γύρου 5 (ελεγκτής D).
 *
 * D-05  Η αποδοχή διαφοράς ήταν λευκή επιταγή: ο server δεχόταν ΟΠΟΙΑΔΗΠΟΤΕ διαφορά. Αν στο μεταξύ άλλαζε το οφειλόμενο
 *       (δεύτερη καρτέλα, συνάδελφος, εξόφληση από πελατολογικό), καταγραφόταν διαφορά που ο χρήστης δεν είδε ποτέ.
 *       Τώρα η αίτηση φέρνει τη διαφορά που είδε (`expected_difference`)· ο server αρνείται με 409 αν έχει αλλάξει.
 * D-06  Δύο γρήγορα «Συμφωνία» σε διαφορετικές γραμμές άφηναν το ανοιχτό παράθυρο να «γυρίσει» σε άλλη κατάθεση, κρατώντας
 *       το τσεκαρισμένο «Αποδέχομαι τη διαφορά» της πρώτης. Τώρα: μία ενέργεια τη φορά.
 * D-09  Μετά από άρνηση του server (η κίνηση συμφωνήθηκε στο μεταξύ) η γραμμή έμενε στη λίστα με κουμπιά που θα αρνούνταν ξανά.
 */
import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { afterEach, describe, expect, it, vi } from "vitest";

import { API, INVOICES, renderBank, serve } from "@/test/bankFixtures";
import { server } from "@/test/server";

afterEach(() => { vi.restoreAllMocks(); });

const gate = () => {
  let release;
  const opened = new Promise((resolve) => { release = resolve; });
  return { opened, release };
};

async function openDialog(id) {
  const user = userEvent.setup();
  const requested = serve();
  renderBank();
  await screen.findByTestId(`bank-row-${id}`);
  await user.click(screen.getByTestId(`bank-reconcile-${id}`));
  await screen.findByTestId("reconcile-dialog");
  return { user, requested };
}

const queryOf = (request) => Object.fromEntries(new URL(request.url).searchParams);

// ============================================================================ D-05 ====

describe("D-05 — η αποδοχή διαφοράς δένεται με τη διαφορά που είδε ο χρήστης", () => {
  /** Καταγράφει τις παραμέτρους κάθε συμφωνίας και απαντά επιτυχία. */
  function recordPosts() {
    const posts = [];
    server.use(http.post(`${API}/bank/reconcile/:id`, ({ request }) => {
      posts.push(queryOf(request));
      return HttpResponse.json({ message: "Συνδέθηκε.", status: "paid", difference: 0 });
    }));
    return posts;
  }

  it("στέλνει μαζί με την αποδοχή και τη διαφορά (κατάθεση − οφειλόμενο): μικρότερη κατάθεση, αρνητικό πρόσημο", async () => {
    const { user } = await openDialog("t2");                                  // κατάθεση 80 €
    const posts = recordPosts();
    await user.click(screen.getByTestId("pick-invoice-i1"));                  // οφείλει 124 → −44
    await user.click(screen.getByTestId("accept-difference"));
    await user.click(screen.getByTestId("reconcile-confirm"));

    await waitFor(() => expect(posts).toHaveLength(1));
    expect(posts[0]).toEqual({ invoice_id: "i1", accept_difference: "true", expected_difference: "-44" });
  });

  it("μεγαλύτερη κατάθεση, θετικό πρόσημο", async () => {
    const { user } = await openDialog("t1");                                  // κατάθεση 124 €
    const posts = recordPosts();
    await user.click(screen.getByTestId("pick-invoice-i3"));                  // οφείλει 82 → +42
    await user.click(screen.getByTestId("accept-difference"));
    await user.click(screen.getByTestId("reconcile-confirm"));

    await waitFor(() => expect(posts).toHaveLength(1));
    expect(posts[0]).toEqual({ invoice_id: "i3", accept_difference: "true", expected_difference: "42" });
  });

  it("η διαφορά στέλνεται σε ευρώ με ακρίβεια λεπτού, χωρίς σφάλμα κινητής υποδιαστολής", async () => {
    const user = userEvent.setup();
    serve({
      summary: { total: 1, unmatched: 1, auto_matched: 0, manual_matched: 0, auto_confirmed: 0, ignored: 0 },
      rows: { unmatched: [{ id: "c1", date: "2026-10-09", description: "ΚΑΤΑΘΕΣΗ", amount: 1240, match_status: "unmatched" }], auto_matched: [], manual_matched: [], auto_confirmed: [], ignored: [] },
      invoices: [{ id: "x1", number: "ΤΠΥ-X1", client_name: "ΑΛΦΑ ΑΕ", date: "2026-10-01", status: "issued", total: 1240.07, outstanding: 1240.07 }],
    });
    const posts = recordPosts();
    renderBank();
    await user.click(await screen.findByTestId("bank-reconcile-c1"));
    await user.click(await screen.findByTestId("pick-invoice-x1"));
    await user.click(screen.getByTestId("accept-difference"));
    await user.click(screen.getByTestId("reconcile-confirm"));

    await waitFor(() => expect(posts).toHaveLength(1));
    expect(posts[0].expected_difference).toBe("-0.07");
  });

  it("σε ακριβές ποσό δεν στέλνεται ούτε αποδοχή ούτε διαφορά", async () => {
    const { user } = await openDialog("t1");
    const posts = recordPosts();
    await user.click(screen.getByTestId("pick-invoice-i1"));                  // 124 = 124
    await user.click(screen.getByTestId("reconcile-confirm"));

    await waitFor(() => expect(posts).toHaveLength(1));
    expect(posts[0]).toEqual({ invoice_id: "i1" });
  });

  describe("όταν ο server αρνείται επειδή η διαφορά άλλαξε (409 amount_changed)", () => {
    const refusal = "Το οφειλόμενο του ΤΠΥ-2026-0001 άλλαξε από τότε που είδατε τη διαφορά: τώρα η κατάθεση (80,00 €) απέχει +56,00 € "
      + "ενώ είχατε αποδεχτεί −44,00 €. Ελέγξτε το ποσό και επιβεβαιώστε ξανά.";

    /** Το i1 οφείλει 124 στην πρώτη ανάγνωση και 24 από τη δεύτερη (μπήκε εξόφληση 100 € από πελατολογικό). */
    function serveChangedInvoice(posts) {
      serve();
      let reads = 0;
      server.use(
        http.get(`${API}/invoices`, () => {
          reads += 1;
          const owed = reads === 1 ? 124 : 24;
          return HttpResponse.json(INVOICES.map((inv) => (inv.id === "i1" ? { ...inv, total: 124, outstanding: owed } : inv)));
        }),
        http.post(`${API}/bank/reconcile/:id`, ({ request }) => {
          const q = queryOf(request);
          posts.push(q);
          if (q.expected_difference === "56") return HttpResponse.json({ message: "Συνδέθηκε. Καταγράφηκε διαφορά +56.00 €.", status: "paid", difference: 56 });
          return HttpResponse.json({ detail: refusal }, { status: 409 });
        }),
      );
    }

    async function acceptStaleDifference() {
      const posts = [];
      serveChangedInvoice(posts);
      const user = userEvent.setup();
      renderBank();
      await user.click(await screen.findByTestId("bank-reconcile-t2"));      // κατάθεση 80 €
      await user.click(await screen.findByTestId("pick-invoice-i1"));        // οφείλει 124 → −44
      await user.click(screen.getByTestId("accept-difference"));
      await user.click(screen.getByTestId("reconcile-confirm"));
      return { user, posts };
    }

    it("δεν καταγράφεται τίποτα, δείχνεται η νέα διαφορά και το κουτί αποδοχής ξετσεκάρεται", async () => {
      const { posts } = await acceptStaleDifference();

      const error = await screen.findByTestId("reconcile-error");
      expect(error).toHaveTextContent("Το οφειλόμενο του ΤΠΥ-2026-0001 άλλαξε από 124,00 € σε 24,00 €");
      expect(posts).toEqual([{ invoice_id: "i1", accept_difference: "true", expected_difference: "-44" }]);
      expect(screen.getByTestId("reconcile-dialog")).toBeInTheDocument();
      expect(screen.getByTestId("reconcile-summary")).toHaveTextContent("24,00");
      expect(screen.getByTestId("reconcile-summary")).toHaveTextContent(/Διαφορά\D*56,00/);
      expect(screen.getByTestId("accept-difference")).not.toBeChecked();
      expect(screen.getByTestId("reconcile-confirm")).toBeDisabled();
    });

    it("η νέα αποδοχή φέρνει τη νέα διαφορά και τότε ολοκληρώνεται", async () => {
      const { user, posts } = await acceptStaleDifference();
      await screen.findByTestId("reconcile-error");

      await user.click(screen.getByTestId("accept-difference"));
      await user.click(screen.getByTestId("reconcile-confirm"));

      await waitFor(() => expect(posts).toHaveLength(2));
      expect(posts[1]).toEqual({ invoice_id: "i1", accept_difference: "true", expected_difference: "56" });
      await waitFor(() => expect(screen.queryByTestId("reconcile-dialog")).not.toBeInTheDocument());
    });
  });

  it("οποιαδήποτε άρνηση ξετσεκάρει την αποδοχή: αυτή αφορούσε την κατάσταση που έβλεπε ο χρήστης", async () => {
    const { user } = await openDialog("t2");
    server.use(http.post(`${API}/bank/reconcile/:id`, () => HttpResponse.json({ detail: "Προσωρινό σφάλμα." }, { status: 500 })));
    await user.click(screen.getByTestId("pick-invoice-i1"));
    await user.click(screen.getByTestId("accept-difference"));
    await user.click(screen.getByTestId("reconcile-confirm"));

    expect(await screen.findByTestId("reconcile-error")).toHaveTextContent("Προσωρινό σφάλμα.");
    expect(screen.getByTestId("accept-difference")).not.toBeChecked();
    expect(screen.getByTestId("reconcile-confirm")).toBeDisabled();
  });

  it("το ίδιο ισχύει όταν δεν έφτασε απάντηση (πτώση σύνδεσης): δεν μένει αποδοχή που ίσως δεν ισχύει πια", async () => {
    const { user, requested } = await openDialog("t2");
    server.use(http.post(`${API}/bank/reconcile/:id`, () => HttpResponse.error()));
    const loads = requested.length;
    await user.click(screen.getByTestId("pick-invoice-i1"));
    await user.click(screen.getByTestId("accept-difference"));
    await user.click(screen.getByTestId("reconcile-confirm"));

    await screen.findByTestId("reconcile-error");
    expect(screen.getByTestId("accept-difference")).not.toBeChecked();
    expect(requested.length).toBe(loads);                                       // καμία άρνηση του server: η λίστα δεν ξαναφορτώνεται
  });
});

// ============================================================================ D-06 ====

describe("D-06 — μία ενέργεια τη φορά", () => {
  /** Ανοίγει την οθόνη και καθυστερεί την ανάγνωση των τιμολογίων (αυτή που προηγείται του παραθύρου συμφωνίας). */
  async function withSlowInvoices() {
    const user = userEvent.setup();
    const requested = serve();
    const door = gate();
    const reads = { count: 0 };
    server.use(http.get(`${API}/invoices`, async () => {
      reads.count += 1;
      await door.opened;
      return HttpResponse.json(INVOICES);
    }));
    renderBank();
    await screen.findByTestId("bank-row-t1");
    return { user, requested, reads, release: door.release };
  }

  const allActionButtons = () => screen.getAllByTestId(/^bank-(reconcile|ignore)-/);

  it("όσο φορτώνει το παράθυρο μιας κατάθεσης, τα κουμπιά ΟΛΩΝ των γραμμών περιμένουν", async () => {
    const { user, reads, release } = await withSlowInvoices();
    expect(allActionButtons().length).toBeGreaterThanOrEqual(5);
    allActionButtons().forEach((button) => expect(button).toBeEnabled());

    await user.click(screen.getByTestId("bank-reconcile-t1"));

    allActionButtons().forEach((button) => expect(button).toBeDisabled());
    await user.click(screen.getByTestId("bank-reconcile-t2"));               // άλλη γραμμή: δεν κάνει τίποτα
    expect(reads.count).toBe(1);

    release();
    const dialog = await screen.findByTestId("reconcile-dialog");
    expect(dialog).toHaveTextContent("ΕΜΒΑΣΜΑ ΑΛΦΑ");                       // της πρώτης κατάθεσης, όχι της δεύτερης
    expect(dialog).not.toHaveTextContent("ΧΩΡΙΣ ΑΝΤΙΣΤΟΙΧΙΑ");
    expect(reads.count).toBe(1);
  });

  it("το διπλό κλικ στο ίδιο κουμπί ανοίγει ένα παράθυρο και ζητά μία φορά τα τιμολόγια", async () => {
    const { user, reads, release } = await withSlowInvoices();

    await user.dblClick(screen.getByTestId("bank-reconcile-t1"));
    release();

    await screen.findByTestId("reconcile-dialog");
    expect(reads.count).toBe(1);
    expect(screen.getAllByTestId("reconcile-dialog")).toHaveLength(1);
  });

  // Τα κουμπιά απενεργοποιούνται μόνο αφού η React ξαναζωγραφίσει την οθόνη· δύο γεγονότα που φτάνουν πριν από αυτό (γρήγορο
  // διπλό πάτημα, αφής + ποντικιού) βλέπουν ακόμη ενεργά κουμπιά. Εδώ τα δύο κλικ δίνονται μέσα στην ίδια «στιγμή» (ένα act),
  // ώστε μόνο το κλείδωμα του κώδικα — όχι το disabled — να τα σταματά.
  it("δύο κλικ που φτάνουν πριν ξαναζωγραφιστεί η οθόνη ξεκινούν μία μόνο ενέργεια", async () => {
    const { reads, release } = await withSlowInvoices();

    act(() => {
      fireEvent.click(screen.getByTestId("bank-reconcile-t1"));
      fireEvent.click(screen.getByTestId("bank-reconcile-t2"));
    });
    release();

    const dialog = await screen.findByTestId("reconcile-dialog");
    expect(reads.count).toBe(1);
    expect(dialog).toHaveTextContent("ΕΜΒΑΣΜΑ ΑΛΦΑ");
    expect(screen.getAllByTestId("reconcile-dialog")).toHaveLength(1);
  });

  it("αγνόηση που φτάνει πριν ξαναζωγραφιστεί η οθόνη, μετά από άνοιγμα συμφωνίας, δεν ρωτά καν και δεν εκτελείται", async () => {
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    let posted = 0;
    server.use(http.post(`${API}/bank/reconcile/:id`, () => { posted += 1; return HttpResponse.json({ message: "ok" }); }));
    const { release } = await withSlowInvoices();

    act(() => {
      fireEvent.click(screen.getByTestId("bank-reconcile-t1"));
      fireEvent.click(screen.getByTestId("bank-ignore-t2"));
    });
    release();

    await screen.findByTestId("reconcile-dialog");
    expect(confirm).not.toHaveBeenCalled();
    expect(posted).toBe(0);
  });

  it("όταν ανοίξει το παράθυρο, το κλείδωμα λύνεται: οι ενέργειες ξαναγίνονται διαθέσιμες", async () => {
    const { user, release } = await withSlowInvoices();
    await user.click(screen.getByTestId("bank-reconcile-t1"));
    release();
    await screen.findByTestId("reconcile-dialog");

    allActionButtons().forEach((button) => expect(button).toBeEnabled());
  });

  it("αν αποτύχει η φόρτωση των τιμολογίων, το κλείδωμα λύνεται και ο χρήστης μπορεί να ξαναδοκιμάσει", async () => {
    const user = userEvent.setup();
    serve();
    let reads = 0;
    server.use(http.get(`${API}/invoices`, () => {
      reads += 1;
      return reads === 1 ? HttpResponse.json({ detail: "Η βάση δεν απαντά." }, { status: 503 }) : HttpResponse.json(INVOICES);
    }));
    renderBank();
    await user.click(await screen.findByTestId("bank-reconcile-t1"));
    await screen.findByText("Η βάση δεν απαντά.");
    await waitFor(() => expect(screen.getByTestId("bank-reconcile-t1")).toBeEnabled());

    await user.click(screen.getByTestId("bank-reconcile-t1"));

    await screen.findByTestId("reconcile-dialog");
    expect(reads).toBe(2);
  });

  it("όσο εκτελείται αγνόηση, και οι συμφωνίες περιμένουν", async () => {
    const user = userEvent.setup();
    vi.spyOn(window, "confirm").mockReturnValue(true);
    serve();
    const door = gate();
    server.use(http.post(`${API}/bank/reconcile/:id`, async () => {
      await door.opened;
      return HttpResponse.json({ message: "Η κίνηση σημειώθηκε ως αγνοητέα" });
    }));
    renderBank();
    await screen.findByTestId("bank-row-t1");

    await user.click(screen.getByTestId("bank-ignore-t2"));

    allActionButtons().forEach((button) => expect(button).toBeDisabled());
    door.release();
    await waitFor(() => allActionButtons().forEach((button) => expect(button).toBeEnabled()));
  });

  it("αν ο χρήστης απαντήσει «Όχι» στην ερώτηση της αγνόησης, δεν κλειδώνει τίποτα", async () => {
    const user = userEvent.setup();
    vi.spyOn(window, "confirm").mockReturnValue(false);
    serve();
    renderBank();
    await screen.findByTestId("bank-row-t1");

    await user.click(screen.getByTestId("bank-ignore-t2"));

    allActionButtons().forEach((button) => expect(button).toBeEnabled());
  });

  it("δεν ρωτά «να αγνοηθεί;» όσο άλλη ενέργεια είναι σε εξέλιξη", async () => {
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    const { user, release } = await withSlowInvoices();
    await user.click(screen.getByTestId("bank-reconcile-t1"));

    await user.click(screen.getByTestId("bank-ignore-t2"));

    expect(confirm).not.toHaveBeenCalled();
    release();
    await screen.findByTestId("reconcile-dialog");
  });
});

// ============================================================================ D-09 ====

describe("D-09 — μετά από άρνηση του server η λίστα ξαναφορτώνεται", () => {
  const settled = "Η κίνηση έχει ήδη συμφωνηθεί με το τιμολόγιο ΤΠΥ-2026-0001 και δεν μπορεί να αγνοηθεί.";

  it("αρνημένη αγνόηση: η λίστα ξαναδιαβάζεται και το μήνυμα του server φαίνεται", async () => {
    const user = userEvent.setup();
    vi.spyOn(window, "confirm").mockReturnValue(true);
    const requested = serve();
    server.use(http.post(`${API}/bank/reconcile/:id`, () => HttpResponse.json({ detail: settled }, { status: 409 })));
    renderBank();
    await screen.findByTestId("bank-row-t1");
    const loads = requested.length;

    await user.click(screen.getByTestId("bank-ignore-t1"));

    expect(await screen.findByText(settled)).toBeInTheDocument();
    await waitFor(() => expect(requested.length).toBeGreaterThan(loads));
  });

  it("αποτυχία χωρίς απάντηση του server (πτώση σύνδεσης) δεν προκαλεί άσκοπη ανανέωση", async () => {
    const user = userEvent.setup();
    vi.spyOn(window, "confirm").mockReturnValue(true);
    const requested = serve();
    server.use(http.post(`${API}/bank/reconcile/:id`, () => HttpResponse.error()));
    renderBank();
    await screen.findByTestId("bank-row-t1");
    const loads = requested.length;

    await user.click(screen.getByTestId("bank-ignore-t1"));

    await screen.findByText("Κάτι πήγε στραβά. Δοκιμάστε ξανά.");
    await waitFor(() => expect(screen.getByTestId("bank-ignore-t1")).toBeEnabled());
    expect(requested.length).toBe(loads);
  });

  it("αρνημένη συμφωνία: η λίστα πίσω από το παράθυρο ξαναδιαβάζεται, το παράθυρο μένει με το μήνυμα", async () => {
    const { user, requested } = await openDialog("t1");
    server.use(http.post(`${API}/bank/reconcile/:id`, () => HttpResponse.json(
      { detail: "Η κίνηση έχει ήδη συμφωνηθεί με το τιμολόγιο ΤΠΥ-2026-0001." }, { status: 409 })));
    const loads = requested.length;
    await user.click(screen.getByTestId("pick-invoice-i1"));
    await user.click(screen.getByTestId("reconcile-confirm"));

    await waitFor(() => expect(requested.length).toBeGreaterThan(loads));
    expect(within(screen.getByTestId("reconcile-dialog")).getByTestId("reconcile-error")).toHaveTextContent("έχει ήδη συμφωνηθεί");
  });

  it("η κίνηση που επεξεργάζεται άλλη ενέργεια (tx_busy) αναφέρεται όπως την περιγράφει ο server", async () => {
    const { user } = await openDialog("t1");
    const busy = "Η κίνηση επεξεργάζεται αυτή τη στιγμή από άλλη ενέργεια — ανανεώστε και ξαναδοκιμάστε σε λίγο.";
    server.use(http.post(`${API}/bank/reconcile/:id`, () => HttpResponse.json({ detail: busy }, { status: 409 })));
    await user.click(screen.getByTestId("pick-invoice-i1"));
    await user.click(screen.getByTestId("reconcile-confirm"));

    expect(await screen.findByTestId("reconcile-error")).toHaveTextContent(busy);
    expect(screen.getByTestId("reconcile-confirm")).toBeEnabled();                // ακριβές ποσό: ο χρήστης μπορεί να ξαναδοκιμάσει
  });

  it("επιτυχής συμφωνία: ένα μόνο ξαναδιάβασμα της λίστας", async () => {
    const { user, requested } = await openDialog("t1");
    server.use(http.post(`${API}/bank/reconcile/:id`, () => HttpResponse.json({ message: "Συνδέθηκε.", status: "paid", difference: 0 })));
    const loads = requested.length;
    await user.click(screen.getByTestId("pick-invoice-i1"));
    await user.click(screen.getByTestId("reconcile-confirm"));

    await waitFor(() => expect(screen.queryByTestId("reconcile-dialog")).not.toBeInTheDocument());
    await waitFor(() => expect(requested.length).toBe(loads + 2));                 // μία φόρτωση = δύο αιτήματα (unmatched + auto_matched)
  });
});
