/**
 * Το portal είναι η μόνη οθόνη που βλέπει **πελάτης** του γραφείου, χωρίς λογαριασμό.
 * Ελέγχεται ότι τα ποσά εμφανίζονται σε ελληνική μορφή και ότι ένας άκυρος σύνδεσμος
 * εξηγείται αντί να αφήνει λευκή σελίδα.
 */
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { HttpResponse, http } from "msw";
import { describe, expect, it, vi } from "vitest";

import { Toaster } from "@/components/ui/sonner";
import ClientPortal from "@/pages/ClientPortal";
import { server } from "@/test/server";

const API = "http://localhost:8001/api";
const TOKEN = "test-token-abc123";

function renderPortal() {
  return render(
    <MemoryRouter initialEntries={[`/portal/${TOKEN}`]}>
      <Routes>
        <Route path="/portal/:token" element={<ClientPortal />} />
      </Routes>
    </MemoryRouter>,
  );
}

const PAYLOAD = {
  firm: { name: "ΔΙΚΗΓΟΡΙΚΟ ΓΡΑΦΕΙΟ", address: "", phone: "", email: "" },
  client: { name: "ΠΕΛΑΤΗΣ ΕΝΑ", afm: "123456783", email: "" },
  invoices: [
    { id: "1", number: "ΤΠΥ-2026-0001", date: "2026-09-10", total: 1240, status: "issued" },
  ],
  cases: [],
  trust: null,
  totals: { unpaid_count: 1, unpaid_total: 1240, paid_count: 0, active_cases: 0 },
};

describe("Πύλη πελάτη", () => {
  it("δείχνει τον φάκελο με ελληνική μορφή ποσών", async () => {
    server.use(http.get(`${API}/portal/${TOKEN}`, () => HttpResponse.json(PAYLOAD)));
    renderPortal();

    expect(await screen.findByText(/ΠΕΛΑΤΗΣ ΕΝΑ/)).toBeInTheDocument();
    expect(screen.getByText("ΤΠΥ-2026-0001")).toBeInTheDocument();
    // Υποδιαστολή κόμμα και σύμβολο μετά τον αριθμό — όχι «1240.00 EUR».
    expect(screen.getAllByText(/1240,00 €/).length).toBeGreaterThan(0);
  });

  it("οι ημερομηνίες του φακέλου δεν μετατοπίζονται ημέρα, όποια κι αν είναι η ζώνη της συσκευής", async () => {
    // Το vitest τρέχει σε ζώνη UTC−11: ένα απλό `new Date("2026-09-10").toLocaleDateString()` θα έγραφε 9/9/2026.
    server.use(http.get(`${API}/portal/${TOKEN}`, () => HttpResponse.json({
      ...PAYLOAD,
      cases: [{ id: "c1", number: "Α-1", title: "Υπόθεση", court: "Πλημμελειοδικείο", status: "active",
                next_action: "Δικάσιμος", next_action_date: "2026-11-02" }],
    })));
    renderPortal();
    expect(await screen.findByText("ΤΠΥ-2026-0001")).toBeInTheDocument();
    expect(screen.getByText("10/9/2026")).toBeInTheDocument();
    expect(screen.getByText(/Δικάσιμος/)).toHaveTextContent("· 2/11/2026");
  });

  it("εξηγεί τον ληγμένο σύνδεσμο αντί να αφήνει λευκή σελίδα", async () => {
    server.use(
      http.get(`${API}/portal/${TOKEN}`, () =>
        HttpResponse.json({ detail: "Ο σύνδεσμος έχει λήξει" }, { status: 401 }),
      ),
    );
    renderPortal();

    expect(await screen.findByTestId("portal-error")).toBeInTheDocument();
    expect(screen.getByText("Ο σύνδεσμος έχει λήξει")).toBeInTheDocument();
  });
});

describe("Πύλη πελάτη — ποσό που οφείλεται", () => {
  // Το σύνολο 1.240 € δεν είναι αυτό που χρωστά ο πελάτης: με παρακράτηση πληρώνει 1.040 €,
  // και αν 400 € έχουν ήδη εξοφληθεί από τον πελατολογικό λογαριασμό του, μένουν 640 €.
  const withDue = {
    ...PAYLOAD,
    invoices: [
      { id: "1", number: "ΤΠΥ-2026-0001", date: "2026-09-10", total: 1240, payable: 1040, outstanding: 640, status: "issued" },
      { id: "2", number: "ΤΠΥ-2026-0002", date: "2026-09-11", total: 124, payable: 124, outstanding: 124, status: "issued" },
      { id: "3", number: "ΤΠΥ-2026-0003", date: "2026-09-12", total: 500, payable: 500, outstanding: 0, status: "paid" },
    ],
    totals: { unpaid_count: 2, unpaid_total: 764, paid_count: 1, active_cases: 0 },
  };

  it("δείχνει «Οφείλεται» μόνο όπου το ποσό διαφέρει από το σύνολο και το τιμολόγιο είναι ανεξόφλητο", async () => {
    server.use(http.get(`${API}/portal/${TOKEN}`, () => HttpResponse.json(withDue)));
    renderPortal();

    const due = await screen.findByTestId("portal-due-1");
    expect(due).toHaveTextContent(/Οφείλεται: 640,00\s€/);
    expect(screen.queryByTestId("portal-due-2")).not.toBeInTheDocument();     // οφείλεται όσο το σύνολο
    expect(screen.queryByTestId("portal-due-3")).not.toBeInTheDocument();     // εξοφλημένο
  });

  it("δεν σπάει όταν ο server (παλιά έκδοση) δεν στέλνει «outstanding»", async () => {
    const legacy = { ...withDue, invoices: [{ id: "1", number: "ΤΠΥ-2026-0001", date: "2026-09-10", total: 1240, status: "issued" }] };
    server.use(http.get(`${API}/portal/${TOKEN}`, () => HttpResponse.json(legacy)));
    renderPortal();
    expect(await screen.findByText("ΤΠΥ-2026-0001")).toBeInTheDocument();
    expect(screen.queryByTestId("portal-due-1")).not.toBeInTheDocument();
  });
});


// ----------------------------------------------------------------------------------------- επιστροφή από την πληρωμή ---
//
// Το backend στέλνει τον πελάτη μετά την πληρωμή στη σελίδα της πύλης με `?paid=1&session_id=…` (ή `?cancelled=1`). Η σελίδα
// δεν πρέπει να δηλώνει επιτυχία μόνο επειδή υπάρχει η παράμετρος: το webhook μπορεί να καθυστερεί και η πληρωμή να μην
// έχει εφαρμοστεί ακόμη. Παλιότερα έδειχνε «ολοκληρώθηκε επιτυχώς» για `?paid=1`, που κανείς δεν έστελνε ποτέ.

function renderReturned(search, props = {}) {
  return render(
    <>
      <MemoryRouter initialEntries={[`/portal/${TOKEN}${search}`]}>
        <Routes>
          <Route path="/portal/:token" element={<ClientPortal pollMs={5} maxPolls={4} {...props} />} />
        </Routes>
      </MemoryRouter>
      <Toaster />
    </>,
  );
}

const tx = (payment_status, extra = {}) => ({
  session_id: "cs_1", status: "initiated", payment_status, invoice_number: "ΤΠΥ-2026-0001", amount: 1240, ...extra,
});

function servePortal(counter) {
  server.use(http.get(`${API}/portal/${TOKEN}`, () => { counter.loads += 1; return HttpResponse.json(PAYLOAD); }));
}

describe("Πύλη πελάτη — επιστροφή από την πληρωμή", () => {
  it("δείχνει επιτυχία μόνο αφού ο server επιβεβαιώσει την πληρωμή, και φορτώνει ξανά τον φάκελο", async () => {
    const counter = { loads: 0, polls: 0 };
    servePortal(counter);
    server.use(http.get(`${API}/payments/status/cs_1`, () => {
      counter.polls += 1;
      return HttpResponse.json(counter.polls < 3 ? tx("pending") : tx("paid", { status: "completed" }));
    }));

    renderReturned("?paid=1&session_id=cs_1");

    expect(await screen.findByTestId("portal-payment-checking")).toBeInTheDocument();
    expect(screen.queryByTestId("portal-payment-paid")).not.toBeInTheDocument();       // όσο εκκρεμεί, καμία επιτυχία

    const paid = await screen.findByTestId("portal-payment-paid");
    expect(paid).toHaveTextContent(/ολοκληρώθηκε επιτυχώς/);
    expect(paid).toHaveTextContent("ΤΠΥ-2026-0001");
    expect(paid).toHaveTextContent(/1240,00\s€/);
    expect(counter.polls).toBe(3);
    await waitFor(() => expect(counter.loads).toBe(2));                               // ο φάκελος ξαναφορτώθηκε μετά την επιβεβαίωση
    expect(screen.queryByTestId("portal-payment-checking")).not.toBeInTheDocument();
  });

  it("δεν δηλώνει επιτυχία όταν η πληρωμή απέτυχε ή έληξε", async () => {
    for (const status of ["failed", "expired"]) {
      server.use(
        http.get(`${API}/portal/${TOKEN}`, () => HttpResponse.json(PAYLOAD)),
        http.get(`${API}/payments/status/cs_1`, () => HttpResponse.json(tx(status))),
      );
      const { unmount } = renderReturned("?paid=1&session_id=cs_1");
      const failed = await screen.findByTestId("portal-payment-failed");
      expect(failed).toHaveTextContent(/δεν ολοκληρώθηκε/);
      expect(failed).toHaveTextContent(/επικοινωνήστε με το γραφείο/);
      expect(screen.queryByTestId("portal-payment-paid")).not.toBeInTheDocument();
      unmount();
    }
  });

  it("αν η επιβεβαίωση αργεί πάνω από το όριο, λέει ότι η πληρωμή επεξεργάζεται — όχι ότι ολοκληρώθηκε", async () => {
    let polls = 0;
    server.use(
      http.get(`${API}/portal/${TOKEN}`, () => HttpResponse.json(PAYLOAD)),
      http.get(`${API}/payments/status/cs_1`, () => { polls += 1; return HttpResponse.json(tx("pending")); }),
    );
    renderReturned("?paid=1&session_id=cs_1");
    const notice = await screen.findByTestId("portal-payment-timeout");
    expect(notice).toHaveTextContent(/επεξεργάζεται ακόμη/);
    expect(polls).toBe(4);                                                            // maxPolls, ούτε μία παραπάνω
    expect(screen.queryByTestId("portal-payment-paid")).not.toBeInTheDocument();
  });

  it("αν δεν μπορεί να επιβεβαιωθεί, το λέει και δεν σιωπά", async () => {
    server.use(
      http.get(`${API}/portal/${TOKEN}`, () => HttpResponse.json(PAYLOAD)),
      http.get(`${API}/payments/status/cs_1`, () => HttpResponse.json({ detail: "Δεν βρέθηκε συναλλαγή" }, { status: 404 })),
    );
    renderReturned("?paid=1&session_id=cs_1");
    expect(await screen.findByTestId("portal-payment-error")).toHaveTextContent(/Δεν ήταν δυνατή η επιβεβαίωση/);
  });

  it("το «?paid=1» χωρίς session_id δεν αποδεικνύει τίποτα: ούτε επιτυχία ούτε ερώτηση στον server", async () => {
    server.use(http.get(`${API}/portal/${TOKEN}`, () => HttpResponse.json(PAYLOAD)));   // κάθε άλλο αίτημα = σφάλμα (setupTests)
    renderReturned("?paid=1");
    const notice = await screen.findByTestId("portal-payment-unverified");
    expect(notice).toHaveTextContent(/Επιστρέψατε από τη σελίδα πληρωμής/);
    expect(screen.queryByTestId("portal-payment-paid")).not.toBeInTheDocument();
  });

  it("η ακύρωση λέει ότι δεν έγινε χρέωση και δεν ρωτά για πληρωμή", async () => {
    server.use(http.get(`${API}/portal/${TOKEN}`, () => HttpResponse.json(PAYLOAD)));
    renderReturned("?cancelled=1");
    const notice = await screen.findByTestId("portal-payment-cancelled");
    expect(notice).toHaveTextContent(/ακυρώθηκε/);
    expect(notice).toHaveTextContent(/Δεν έγινε καμία χρέωση/);
  });

  it("χωρίς παραμέτρους επιστροφής δεν εμφανίζεται καμία ειδοποίηση πληρωμής", async () => {
    server.use(http.get(`${API}/portal/${TOKEN}`, () => HttpResponse.json(PAYLOAD)));
    renderReturned("");
    await screen.findByText(/ΠΕΛΑΤΗΣ ΕΝΑ/);
    expect(document.querySelector("[data-testid^='portal-payment-']")).toBeNull();
  });

  it("ο φάκελος που δεν φορτώνει δείχνει το σφάλμα, ακόμη κι αν ο πελάτης επέστρεψε από πληρωμή", async () => {
    server.use(http.get(`${API}/portal/${TOKEN}`, () => HttpResponse.json({ detail: "Ο σύνδεσμος έχει λήξει" }, { status: 401 })));
    renderReturned("?cancelled=1");
    expect(await screen.findByTestId("portal-error")).toHaveTextContent("Ο σύνδεσμος έχει λήξει");
  });
});

describe("Πύλη πελάτη — έναρξη πληρωμής", () => {
  it("στέλνει το token και τη σελίδα της πύλης, και οδηγεί τον πελάτη στη σελίδα πληρωμής", async () => {
    const user = userEvent.setup();
    const redirect = vi.fn();
    let body = null;
    server.use(
      http.get(`${API}/portal/${TOKEN}`, () => HttpResponse.json(PAYLOAD)),
      http.post(`${API}/payments/portal-checkout`, async ({ request }) => {
        body = await request.json();
        return HttpResponse.json({ checkout_url: "https://checkout.stripe.test/1", session_id: "cs_1" });
      }),
    );
    renderReturned("", { redirect });
    await user.click(await screen.findByTestId("portal-pay-1"));

    await waitFor(() => expect(redirect).toHaveBeenCalledWith("https://checkout.stripe.test/1"));
    expect(body).toEqual({ invoice_id: "1", origin_url: `${window.location.origin}/portal/${TOKEN}`, client_token: TOKEN });
  });

  it("δείχνει στον πελάτη την εξήγηση του server και ξαναενεργοποιεί το κουμπί", async () => {
    const user = userEvent.setup();
    const redirect = vi.fn();
    server.use(
      http.get(`${API}/portal/${TOKEN}`, () => HttpResponse.json(PAYLOAD)),
      http.post(`${API}/payments/portal-checkout`, () =>
        HttpResponse.json({ detail: "Το τιμολόγιο είναι ήδη εξοφλημένο" }, { status: 400 })),
    );
    renderReturned("", { redirect });
    await user.click(await screen.findByTestId("portal-pay-1"));

    expect(await screen.findByText("Το τιμολόγιο είναι ήδη εξοφλημένο")).toBeInTheDocument();
    expect(redirect).not.toHaveBeenCalled();
    await waitFor(() => expect(screen.getByTestId("portal-pay-1")).toBeEnabled());
  });

  it("το κουμπί δεν μένει κλειδωμένο όταν ο browser επαναφέρει τη σελίδα από την προσωρινή μνήμη (Πίσω από το Stripe)", async () => {
    const user = userEvent.setup();
    server.use(
      http.get(`${API}/portal/${TOKEN}`, () => HttpResponse.json(PAYLOAD)),
      http.post(`${API}/payments/portal-checkout`, () => HttpResponse.json({ checkout_url: "https://checkout.stripe.test/1" })),
    );
    renderReturned("", { redirect: () => {} });               // η «μετάβαση» δεν γίνεται: η σελίδα μένει με paying ενεργό
    await user.click(await screen.findByTestId("portal-pay-1"));
    await waitFor(() => expect(screen.getByTestId("portal-pay-1")).toBeDisabled());

    act(() => { window.dispatchEvent(Object.assign(new Event("pageshow"), { persisted: true })); });
    await waitFor(() => expect(screen.getByTestId("portal-pay-1")).toBeEnabled());
  });

  it("ένα pageshow που δεν προέρχεται από την προσωρινή μνήμη δεν αλλάζει τίποτα", async () => {
    const user = userEvent.setup();
    server.use(
      http.get(`${API}/portal/${TOKEN}`, () => HttpResponse.json(PAYLOAD)),
      http.post(`${API}/payments/portal-checkout`, () => HttpResponse.json({ checkout_url: "https://checkout.stripe.test/1" })),
    );
    renderReturned("", { redirect: () => {} });
    await user.click(await screen.findByTestId("portal-pay-1"));
    await waitFor(() => expect(screen.getByTestId("portal-pay-1")).toBeDisabled());

    act(() => { window.dispatchEvent(Object.assign(new Event("pageshow"), { persisted: false })); });
    expect(screen.getByTestId("portal-pay-1")).toBeDisabled();
  });
});
