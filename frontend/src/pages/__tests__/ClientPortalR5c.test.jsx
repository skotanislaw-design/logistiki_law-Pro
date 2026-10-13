/**
 * Πύλη πελάτη — δεύτερος έλεγχος του γύρου 5 (δοκιμές μεταλλάξεων).
 *
 * Η πύλη είναι η μόνη οθόνη που βλέπει ο πελάτης του γραφείου, χωρίς λογαριασμό: κάθε αριθμός της είναι αυτός που κρατά ο
 * server, κουμπί πληρωμής υπάρχει μόνο εκεί όπου μπορεί να πληρωθεί κάτι, και η ακύρωση δεν ρωτά ποτέ για πληρωμή.
 */
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";

import ClientPortal from "@/pages/ClientPortal";
import { server } from "@/test/server";

const API = "http://localhost:8001/api";
const TOKEN = "test-token-abc123";

function renderPortal(search = "") {
  return render(
    <MemoryRouter initialEntries={[`/portal/${TOKEN}${search}`]}>
      <Routes>
        <Route path="/portal/:token" element={<ClientPortal pollMs={5} maxPolls={4} />} />
      </Routes>
    </MemoryRouter>,
  );
}

// Τα σύνολα των καρτών διαφέρουν σκόπιμα από κάθε ποσό της λίστας, ώστε να φαίνεται από πού διαβάζει η κάθε κάρτα.
const PAYLOAD = {
  firm: { name: "ΔΙΚΗΓΟΡΙΚΟ ΓΡΑΦΕΙΟ", address: "", phone: "", email: "" },
  client: { name: "ΠΕΛΑΤΗΣ ΕΝΑ", afm: "123456783", email: "" },
  invoices: [
    { id: "1", number: "ΤΠΥ-2026-0001", date: "2026-09-10", total: 1240, payable: 1040, outstanding: 640, status: "issued" },
    { id: "2", number: "ΤΠΥ-2026-0002", date: "2026-09-11", total: 124, payable: 124, outstanding: 124, status: "issued" },
    { id: "3", number: "ΤΠΥ-2026-0003", date: "2026-09-12", total: 500, payable: 500, outstanding: 0, status: "paid" },
    { id: "4", number: "ΤΠΥ-2026-0004", date: "2026-09-13", total: 300, payable: 300, outstanding: 300, status: "cancelled" },
  ],
  cases: [],
  trust: { balance: 275.5, total_deposits: 900 },
  totals: { unpaid_count: 2, unpaid_total: 764, paid_count: 1, active_cases: 3 },
};

const servePortal = (payload = PAYLOAD) => server.use(http.get(`${API}/portal/${TOKEN}`, () => HttpResponse.json(payload)));

describe("Πύλη πελάτη — οι κάρτες συνόλων δείχνουν τα νούμερα του server", () => {
  it("τα «Ανείσπρακτα» είναι το ποσό που οφείλεται, όχι το πλήθος των τιμολογίων", async () => {
    servePortal();
    renderPortal();
    const unpaid = await screen.findByTestId("portal-unpaid");
    expect(unpaid).toHaveTextContent(/^764,00\s€$/);
    expect(unpaid.parentElement).toHaveTextContent("2 τιμολόγια");
  });

  it("ο πελατολογικός λογαριασμός δείχνει το υπόλοιπο, όχι τις συνολικές καταθέσεις", async () => {
    servePortal();
    renderPortal();
    const card = (await screen.findByText("Πελατολογικός")).parentElement;
    expect(card).toHaveTextContent("275,50 €");
    expect(card).not.toHaveTextContent("900,00");
  });

  it("χωρίς πελατολογικό λογαριασμό δεν υπάρχει κάρτα γι' αυτόν", async () => {
    servePortal({ ...PAYLOAD, trust: null });
    renderPortal();
    await screen.findByTestId("portal-unpaid");
    expect(screen.queryByText("Πελατολογικός")).not.toBeInTheDocument();
  });

  it("η στήλη «Ποσό» δείχνει το σύνολο του τιμολογίου και, ξεχωριστά, ό,τι οφείλεται από αυτό", async () => {
    servePortal();
    renderPortal();
    const cell = (await screen.findByTestId("portal-due-1")).closest("td");
    expect(cell.childNodes[0].textContent).toMatch(/^1\.240,00\s€$/);             // το σύνολο, όχι το οφειλόμενο
    expect(screen.getByTestId("portal-due-1")).toHaveTextContent("Οφείλεται: 640,00 €");
  });
});

describe("Πύλη πελάτη — κουμπί πληρωμής μόνο όπου υπάρχει τι να πληρωθεί", () => {
  it("τα ανεξόφλητα τιμολόγια έχουν «Πληρωμή»· τα εξοφλημένα και τα ακυρωμένα όχι (έχουν όμως PDF)", async () => {
    servePortal();
    renderPortal();
    expect(await screen.findByTestId("portal-pay-1")).toBeInTheDocument();
    expect(screen.getByTestId("portal-pay-2")).toBeInTheDocument();
    expect(screen.queryByTestId("portal-pay-3")).not.toBeInTheDocument();       // εξοφλημένο
    expect(screen.queryByTestId("portal-pay-4")).not.toBeInTheDocument();       // ακυρωμένο
    for (const id of ["1", "2", "3", "4"]) expect(screen.getByTestId(`portal-pdf-${id}`)).toBeInTheDocument();
  });
});

describe("Πύλη πελάτη — επιστροφή με ακύρωση", () => {
  it("το «?cancelled=1» με session_id στον σύνδεσμο δείχνει ακύρωση και δεν ρωτά τον server για πληρωμή", async () => {
    let polls = 0;
    server.use(
      http.get(`${API}/portal/${TOKEN}`, () => HttpResponse.json(PAYLOAD)),
      http.get(`${API}/payments/status/cs_1`, () => {
        polls += 1;
        return HttpResponse.json({ session_id: "cs_1", status: "completed", payment_status: "paid", invoice_number: "ΤΠΥ-2026-0001", amount: 1240 });
      }),
    );
    renderPortal("?cancelled=1&session_id=cs_1");
    const notice = await screen.findByTestId("portal-payment-cancelled");
    expect(notice).toHaveTextContent("Δεν έγινε καμία χρέωση");
    await new Promise((resolve) => setTimeout(resolve, 50));                     // αφήνουμε χρόνο σε ένα (λανθασμένο) αίτημα να φτάσει
    expect(polls).toBe(0);
    expect(screen.queryByTestId("portal-payment-paid")).not.toBeInTheDocument();
    expect(screen.getByTestId("portal-payment-cancelled")).toBeInTheDocument();
  });
});
