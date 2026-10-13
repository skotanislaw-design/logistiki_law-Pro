/**
 * Αποτέλεσμα πληρωμής — δεύτερος έλεγχος του γύρου 5 (δοκιμές μεταλλάξεων).
 *
 * Η σελίδα δεν πρέπει να δηλώνει επιτυχία παρά μόνο όταν ο server λέει «paid»: ούτε για αποτυχημένη ή ληγμένη πληρωμή, ούτε
 * για ενδιάμεση κατάσταση, ούτε επειδή πέρασε ο χρόνος αναμονής. Το χρονικό μέρος (ρωτά κάθε 2″, τα παρατάει στα 30″)
 * ελέγχεται με ψεύτικο ρολόι: μόνο οι χρονομετρητές «πλαστογραφούνται» και το δίκτυο αντικαθίσταται από απαντήσεις στο
 * `axios.get`, ώστε το test να μη χρειάζεται να περιμένει πραγματικά δευτερόλεπτα.
 *
 * Η διατύπωση των καταστάσεων «timeout» και «error» ΔΕΝ ελέγχεται εδώ επίτηδες (μόνο η κατάσταση της οθόνης): το κείμενο «Η πληρωμή
 * δεν ολοκληρώθηκε — δοκιμάστε ξανά» σε άγνωστη κατάσταση είναι αντικείμενο του BugPaymentResultUnknownStateWording.test.jsx.
 */
import { act, render, screen } from "@testing-library/react";
import axios from "axios";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { HttpResponse, http } from "msw";
import { afterEach, describe, expect, it, vi } from "vitest";

import PaymentResult from "@/pages/PaymentResult";
import { server } from "@/test/server";

const API = "http://localhost:8001/api";

function renderResult(url = "/payment/success?session_id=cs_1") {
  return render(
    <MemoryRouter initialEntries={[url]}>
      <Routes>
        <Route path="/payment/success" element={<PaymentResult mode="success" />} />
      </Routes>
    </MemoryRouter>,
  );
}

const reply = (payment_status, over = {}) => ({
  data: { session_id: "cs_1", status: "initiated", payment_status, invoice_number: "ΤΠΥ-2026-0001", amount: 124.5, ...over },
});

/** Προχωρά το ψεύτικο ρολόι κατά `ms` και αφήνει να ολοκληρωθούν οι ασύγχρονες ενημερώσεις της οθόνης. */
const advance = (ms) => act(async () => { await vi.advanceTimersByTimeAsync(ms); });

/** Οι διαδοχικές απαντήσεις του server στην ερώτηση «πού βρίσκεται η πληρωμή;» (η τελευταία επαναλαμβάνεται). */
function polling(...replies) {
  vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
  let n = 0;
  return vi.spyOn(axios, "get").mockImplementation(async () => {
    const next = replies[Math.min(n, replies.length - 1)];
    n += 1;
    return next;
  });
}

describe("Αποτέλεσμα πληρωμής — επιτυχία μόνο όταν το λέει ο server", () => {
  afterEach(() => { vi.useRealTimers(); vi.restoreAllMocks(); });

  it.each([["failed"], ["expired"]])("η πληρωμή «%s» δεν δηλώνεται επιτυχημένη και η σελίδα σταματά να ρωτά", async (status) => {
    const get = polling(reply(status));
    renderResult();
    await advance(0);
    const notice = screen.getByTestId("payment-failed");
    expect(notice).toHaveTextContent("Η πληρωμή δεν ολοκληρώθηκε");
    expect(screen.queryByTestId("payment-paid")).not.toBeInTheDocument();
    await advance(10_000);
    expect(get).toHaveBeenCalledTimes(1);
  });

  it("μια ενδιάμεση κατάσταση (initiated, unpaid) δεν είναι επιτυχία: ρωτά ξανά κάθε 2″ μέχρι να πληρωθεί, και μετά σταματά", async () => {
    const get = polling(reply("initiated"), reply("unpaid"), reply("paid"));
    renderResult();
    await advance(0);
    expect(screen.getByTestId("payment-checking")).toBeInTheDocument();
    expect(screen.queryByTestId("payment-paid")).not.toBeInTheDocument();
    expect(get).toHaveBeenCalledTimes(1);

    await advance(2_000);
    expect(screen.getByTestId("payment-checking")).toBeInTheDocument();
    expect(get).toHaveBeenCalledTimes(2);

    await advance(2_000);
    expect(screen.getByTestId("payment-paid")).toHaveTextContent("Επιτυχής πληρωμή!");
    await advance(20_000);
    expect(get).toHaveBeenCalledTimes(3);
  });

  it("αν η πληρωμή μένει «pending», μετά από ~30″ σταματά την αναμονή — δεν δηλώνει επιτυχία — και δεν ρωτά άλλο", async () => {
    const get = polling(reply("pending"));
    renderResult();
    await advance(0);
    expect(get).toHaveBeenCalledTimes(1);
    expect(screen.getByTestId("payment-checking")).toBeInTheDocument();

    await advance(20_000);
    expect(screen.getByTestId("payment-checking")).toBeInTheDocument();          // ακόμη περιμένει
    expect(screen.queryByTestId("payment-paid")).not.toBeInTheDocument();

    await advance(12_000);
    expect(screen.getByTestId("payment-timeout")).toBeInTheDocument();
    expect(screen.queryByTestId("payment-paid")).not.toBeInTheDocument();
    expect(screen.queryByTestId("payment-checking")).not.toBeInTheDocument();

    const asked = get.mock.calls.length;
    await advance(60_000);
    expect(get.mock.calls.length).toBe(asked);
  });

  it("το ποσό γράφεται με τα λεπτά του (124,50 €), όχι στρογγυλεμένο στο ευρώ", async () => {
    polling(reply("paid", { amount: 124.5 }));
    renderResult();
    await advance(0);
    expect(screen.getByTestId("payment-paid")).toHaveTextContent(/Το τιμολόγιο ΤΠΥ-2026-0001 για 124[.,]50\s€ εξοφλήθηκε/);
  });
});

describe("Αποτέλεσμα πληρωμής — αποτυχία ανάγνωσης", () => {
  it("αν δεν διαβαστεί η κατάσταση της πληρωμής, η σελίδα βγαίνει από την αναμονή — δεν μένει στο «Επιβεβαίωση πληρωμής…» για πάντα", async () => {
    server.use(http.get(`${API}/payments/status/cs_1`, () => HttpResponse.json({ detail: "Δεν βρέθηκε συναλλαγή" }, { status: 404 })));
    renderResult();
    expect(await screen.findByTestId("payment-error")).toBeInTheDocument();
    expect(screen.queryByTestId("payment-checking")).not.toBeInTheDocument();
    expect(screen.queryByTestId("payment-paid")).not.toBeInTheDocument();
  });
});
