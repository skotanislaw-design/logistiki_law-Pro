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
