/**
 * Η σελίδα όπου επιστρέφει όποιος πλήρωσε με κάρτα από σύνδεσμο υπενθύμισης ή από την οθόνη τιμολογίων.
 * Το «Επιστροφή» έκανε `navigate(-1)`, δηλαδή οδηγούσε πίσω στη σελίδα πληρωμής του Stripe που είχε ήδη ολοκληρωθεί.
 */
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";

import { AuthProvider } from "@/context/AuthContext";
import PaymentResult from "@/pages/PaymentResult";
import { server } from "@/test/server";

const API = "http://localhost:8001/api";

function renderAt(url, mode = "success") {
  return render(
    <AuthProvider>
      <MemoryRouter initialEntries={[url]}>
        <Routes>
          <Route path="/payment/success" element={<PaymentResult mode="success" />} />
          <Route path="/payment/cancel" element={<PaymentResult mode={mode} />} />
          <Route path="/invoices" element={<div data-testid="invoices-page" />} />
        </Routes>
      </MemoryRouter>
    </AuthProvider>,
  );
}

const loggedIn = () => http.get(`${API}/auth/me`, () => HttpResponse.json({ email: "dikigoros@example.gr", name: "Δικηγόρος" }));
const loggedOut = () => http.get(`${API}/auth/me`, () => HttpResponse.json({ detail: "Μη εξουσιοδοτημένος" }, { status: 401 }));
const paid = () => http.get(`${API}/payments/status/cs_1`, () =>
  HttpResponse.json({ session_id: "cs_1", status: "completed", payment_status: "paid", invoice_number: "ΤΠΥ-2026-0001", amount: 124 }));

describe("Αποτέλεσμα πληρωμής", () => {
  it("το προσωπικό του γραφείου επιστρέφει στα τιμολόγια, όχι στη σελίδα του Stripe", async () => {
    const user = userEvent.setup();
    server.use(loggedIn(), paid());
    renderAt("/payment/success?session_id=cs_1");

    expect(await screen.findByTestId("payment-paid")).toHaveTextContent(/ΤΠΥ-2026-0001/);
    await user.click(await screen.findByTestId("payment-back"));
    expect(await screen.findByTestId("invoices-page")).toBeInTheDocument();
  });

  it("ο πελάτης χωρίς λογαριασμό δεν βλέπει κουμπί προς την εφαρμογή· ενημερώνεται ότι μπορεί να κλείσει τη σελίδα", async () => {
    server.use(loggedOut(), paid());
    renderAt("/payment/success?session_id=cs_1");

    expect(await screen.findByTestId("payment-paid")).toBeInTheDocument();
    expect(await screen.findByTestId("payment-close")).toHaveTextContent(/κλείσετε αυτή τη σελίδα/);
    expect(screen.queryByTestId("payment-back")).not.toBeInTheDocument();
  });

  it("η ακύρωση λέει ότι μπορεί να δοκιμάσει ξανά, και ισχύει το ίδιο για την έξοδο", async () => {
    server.use(loggedOut());
    renderAt("/payment/cancel?invoice=abc", "cancel");
    expect(await screen.findByTestId("payment-cancelled")).toHaveTextContent(/δοκιμάσετε ξανά/);
    expect(await screen.findByTestId("payment-close")).toBeInTheDocument();
  });

  it("χωρίς session_id δεν ρωτά τον server και λέει ότι η πληρωμή δεν ολοκληρώθηκε", async () => {
    server.use(loggedIn());                                   // κάθε αίτημα προς /payments/status θα ήταν σφάλμα (setupTests)
    renderAt("/payment/success");
    expect(await screen.findByTestId("payment-error")).toHaveTextContent(/δεν ολοκληρώθηκε/);
    expect(await screen.findByTestId("payment-back")).toBeInTheDocument();
  });
});
