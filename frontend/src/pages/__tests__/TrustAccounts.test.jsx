/**
 * Πελατολογικοί λογαριασμοί — χρήματα πελατών. Ο έλεγχος των κανόνων γίνεται στο backend·
 * εδώ ελέγχεται ότι η οθόνη τους **δείχνει** και τους σέβεται:
 *
 * - η ημερομηνία κίνησης είναι η σημερινή στην Αθήνα (όχι UTC, που μετά τα μεσάνυχτα είναι χθεσινή)·
 * - τα ανοιχτά τιμολόγια ζητούνται από τον server, που ξέρει το οφειλόμενο μετά την παρακράτηση·
 * - το μήνυμα του server (π.χ. «Απομένουν 24,00 €») φτάνει στον χρήστη·
 * - αν το υπόλοιπο δεν συμφωνεί με τις κινήσεις, εμφανίζεται προειδοποίηση.
 */
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { afterEach, describe, expect, it, vi } from "vitest";

import { Toaster } from "@/components/ui/sonner";
import TrustAccounts from "@/pages/TrustAccounts";
import { todayAthens } from "@/lib/utils";
import { server } from "@/test/server";

const API = "http://localhost:8001/api";

const account = {
  id: "a1", client_id: "c1", client_name: "ΑΛΦΑ ΑΕ", balance: 76, total_deposits: 200, total_withdrawals: 124,
};

function baseHandlers(extra = []) {
  server.use(
    http.get(`${API}/trust/accounts`, () => HttpResponse.json([account])),
    http.get(`${API}/trust/summary`, () => HttpResponse.json({ total_balance: 76, accounts_with_balance: 1 })),
    http.get(`${API}/clients`, () => HttpResponse.json([{ id: "c1", name: "ΑΛΦΑ ΑΕ" }])),
    ...extra,
  );
}

function renderPage() {
  return render(<><TrustAccounts /><Toaster /></>);
}

describe("Πελατολογικοί λογαριασμοί", () => {
  afterEach(() => vi.useRealTimers());

  it("προειδοποιεί όταν το υπόλοιπο δεν συμφωνεί με τις κινήσεις", async () => {
    const user = userEvent.setup();
    baseHandlers([
      http.get(`${API}/trust/accounts/c1`, () => HttpResponse.json({
        account, transactions: [],
        ledger_check: { ledger_balance: 111, difference: -35, consistent: false },
      })),
    ]);
    renderPage();
    await user.click(await screen.findByTestId("view-trust-c1"));
    expect(await screen.findByTestId("trust-ledger-warning")).toHaveTextContent(/δεν συμφωνεί/);
  });

  it("δεν δείχνει προειδοποίηση όταν όλα συμφωνούν", async () => {
    const user = userEvent.setup();
    baseHandlers([
      http.get(`${API}/trust/accounts/c1`, () => HttpResponse.json({
        account, transactions: [],
        ledger_check: { ledger_balance: 76, difference: 0, consistent: true },
      })),
    ]);
    renderPage();
    await user.click(await screen.findByTestId("view-trust-c1"));
    expect(await screen.findByTestId("trust-detail-dialog")).toBeInTheDocument();
    expect(screen.queryByTestId("trust-ledger-warning")).not.toBeInTheDocument();
  });

  it("ζητά από τον server τα ανοιχτά τιμολόγια και στέλνει σημερινή ημερομηνία Αθήνας", async () => {
    // 01:30 τα ξημερώματα της 12ης Οκτωβρίου στην Αθήνα είναι ακόμη 11 Οκτωβρίου σε UTC.
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date("2026-10-11T22:30:00Z"));
    const user = userEvent.setup();
    let openInvoicesAsked = false;
    let posted = null;
    baseHandlers([
      http.get(`${API}/trust/accounts/c1/open-invoices`, () => {
        openInvoicesAsked = true;
        return HttpResponse.json([]);
      }),
      http.post(`${API}/trust/transactions`, async ({ request }) => {
        posted = await request.json();
        return HttpResponse.json({ message: "Η κίνηση καταχωρήθηκε. Απομένουν 24.00 € για το τιμολόγιο ΤΠΥ-2026-0001." });
      }),
    ]);
    renderPage();
    await user.click(await screen.findByTestId("add-tx-c1"));
    await waitFor(() => expect(openInvoicesAsked).toBe(true));
    expect(todayAthens()).toBe("2026-10-12");
    expect(screen.getByDisplayValue("2026-10-12")).toBeInTheDocument();

    const amount = screen.getByTestId("trust-amount-input");
    await user.clear(amount);
    await user.type(amount, "50");
    await user.click(screen.getByTestId("save-trust-tx-button"));

    await waitFor(() => expect(posted).not.toBeNull());
    expect(posted).toMatchObject({ client_id: "c1", type: "deposit", amount: 50, date: "2026-10-12", invoice_id: null });
    expect(await screen.findByText(/Απομένουν 24\.00 €/)).toBeInTheDocument();     // το μήνυμα του server
  });

  it("δείχνει στον χρήστη γιατί απορρίφθηκε μια κίνηση", async () => {
    const user = userEvent.setup();
    baseHandlers([
      http.get(`${API}/trust/accounts/c1/open-invoices`, () => HttpResponse.json([])),
      http.post(`${API}/trust/transactions`, () =>
        HttpResponse.json({ detail: "Ανεπαρκές υπόλοιπο πελατολογικού λογαριασμού. Διαθέσιμο: 76.00 €" }, { status: 400 })),
    ]);
    renderPage();
    await user.click(await screen.findByTestId("add-tx-c1"));
    const amount = screen.getByTestId("trust-amount-input");
    await user.clear(amount);
    await user.type(amount, "500");
    await user.click(screen.getByTestId("save-trust-tx-button"));
    expect(await screen.findByText(/Ανεπαρκές υπόλοιπο/)).toBeInTheDocument();
  });
});

describe("Εξόφληση τιμολογίου από πελατολογικό — πρόταση ποσού", () => {
  // Το υπόλοιπο του λογαριασμού είναι 76 €. Το πρώτο τιμολόγιο οφείλει 124 €, το δεύτερο (μετά από
  // μερική εξόφληση) 24 € — ο server στέλνει το πραγματικά οφειλόμενο, όχι το σύνολο.
  const open = [
    { id: "i1", number: "ΤΠΥ-2026-0001", total: 124, outstanding: 124 },
    { id: "i2", number: "ΤΠΥ-2026-0002", total: 124, outstanding: 24 },
  ];

  function applyHandlers(requested) {
    baseHandlers([
      http.get(`${API}/trust/accounts/:clientId/open-invoices`, ({ params }) => {
        requested.push(params.clientId);
        return HttpResponse.json(params.clientId === "c1" ? open : [{ id: "i9", number: "ΤΠΥ-2026-0009", total: 50, outstanding: 50 }]);
      }),
    ]);
    // δύο πελάτες· ο δεύτερος δεν έχει γραμμή λογαριασμού (το `server.use` που έρχεται τελευταίο υπερισχύει)
    server.use(http.get(`${API}/clients`, () => HttpResponse.json([
      { id: "c1", name: "ΑΛΦΑ ΑΕ" }, { id: "c2", name: "ΒΗΤΑ ΕΠΕ" },
    ])));
  }

  async function chooseApplyToInvoice(user) {
    await user.click(screen.getByTestId("trust-type-select"));
    await user.click(await screen.findByRole("option", { name: /Εξόφληση τιμολογίου/ }));
  }

  it("προτείνει το μικρότερο από το οφειλόμενο και το διαθέσιμο υπόλοιπο", async () => {
    const user = userEvent.setup({ pointerEventsCheck: 0 });
    applyHandlers([]);
    renderPage();
    await user.click(await screen.findByTestId("add-tx-c1"));
    await chooseApplyToInvoice(user);

    await user.click(screen.getByTestId("trust-invoice-select"));
    // η ετικέτα δείχνει το οφειλόμενο, όχι το σύνολο του τιμολογίου
    expect(await screen.findByRole("option", { name: /ΤΠΥ-2026-0002 — οφείλονται 24,00\s€/ })).toBeInTheDocument();
    await user.click(screen.getByRole("option", { name: /ΤΠΥ-2026-0001 — οφείλονται 124,00\s€/ }));
    expect(screen.getByTestId("trust-amount-input")).toHaveValue(76);        // 124 > 76 διαθέσιμα → 76

    await user.click(screen.getByTestId("trust-invoice-select"));
    await user.click(await screen.findByRole("option", { name: /ΤΠΥ-2026-0002/ }));
    expect(screen.getByTestId("trust-amount-input")).toHaveValue(24);        // 24 < 76 διαθέσιμα → 24
  });

  it("φορτώνει τα ανοιχτά τιμολόγια του νέου πελάτη όταν αλλάξει ο πελάτης στη φόρμα", async () => {
    const user = userEvent.setup({ pointerEventsCheck: 0 });
    const requested = [];
    applyHandlers(requested);
    renderPage();
    await user.click(await screen.findByTestId("new-trust-tx-button"));
    expect(requested).toEqual([]);                                           // χωρίς πελάτη δεν ζητείται τίποτα

    await user.click(screen.getByTestId("trust-client-select"));
    await user.click(await screen.findByRole("option", { name: "ΑΛΦΑ ΑΕ" }));
    await waitFor(() => expect(requested).toEqual(["c1"]));

    await user.click(screen.getByTestId("trust-client-select"));
    await user.click(await screen.findByRole("option", { name: "ΒΗΤΑ ΕΠΕ" }));
    await waitFor(() => expect(requested).toEqual(["c1", "c2"]));

    await chooseApplyToInvoice(user);
    await user.click(screen.getByTestId("trust-invoice-select"));
    expect(await screen.findByRole("option", { name: /ΤΠΥ-2026-0009/ })).toBeInTheDocument();
    expect(screen.queryByRole("option", { name: /ΤΠΥ-2026-0001/ })).not.toBeInTheDocument();   // όχι του προηγούμενου
  });

  it("δεν προτείνει ποσό όταν ο πελάτης δεν έχει ακόμη λογαριασμό (δεν υπάρχει υπόλοιπο να χρεωθεί)", async () => {
    const user = userEvent.setup({ pointerEventsCheck: 0 });
    applyHandlers([]);
    renderPage();
    await user.click(await screen.findByTestId("new-trust-tx-button"));
    await user.click(screen.getByTestId("trust-client-select"));
    await user.click(await screen.findByRole("option", { name: "ΒΗΤΑ ΕΠΕ" }));      // ο c2 δεν έχει γραμμή λογαριασμού
    await chooseApplyToInvoice(user);
    await user.click(screen.getByTestId("trust-invoice-select"));
    await user.click(await screen.findByRole("option", { name: /ΤΠΥ-2026-0009/ }));
    expect(screen.getByTestId("trust-amount-input")).toHaveValue(0);              // όχι 50 €
  });
});
