/**
 * Η σελίδα όπου επιστρέφει όποιος πλήρωσε με κάρτα από σύνδεσμο υπενθύμισης ή από την οθόνη τιμολογίων.
 * Το «Επιστροφή» έκανε `navigate(-1)`, δηλαδή οδηγούσε πίσω στη σελίδα πληρωμής του Stripe που είχε ήδη ολοκληρωθεί.
 */
import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import axios from "axios";
import { MemoryRouter, Route, Routes, useNavigate } from "react-router-dom";
import { HttpResponse, http } from "msw";
import { afterEach, describe, expect, it, vi } from "vitest";

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

  it("χωρίς session_id δεν ρωτά τον server και λέει ότι δεν βρέθηκε πληρωμή — χωρίς να προτρέπει σε νέα πληρωμή", async () => {
    let asked = 0;
    server.use(loggedIn(), http.get(`${API}/payments/status/:id`, () => { asked += 1; return HttpResponse.json({}); }));
    renderAt("/payment/success");
    const notice = await screen.findByTestId("payment-nosession");
    expect(notice).toHaveTextContent("Δεν βρέθηκε πληρωμή προς επιβεβαίωση");
    expect(notice).toHaveTextContent("μην πληρώσετε ξανά");
    expect(notice).not.toHaveTextContent("δοκιμάστε ξανά");
    expect(await screen.findByTestId("payment-back")).toBeInTheDocument();
    expect(asked).toBe(0);
  });
});

describe("Αποτέλεσμα πληρωμής — τι λέει σε κάθε κατάσταση", () => {
  it("αποτυχημένη ή ληγμένη πληρωμή: το λέει καθαρά, προειδοποιεί για πιθανή χρέωση και επιτρέπει νέα προσπάθεια", async () => {
    server.use(loggedOut(), http.get(`${API}/payments/status/cs_1`, () =>
      HttpResponse.json({ session_id: "cs_1", status: "expired", payment_status: "expired", invoice_number: "ΤΠΥ-2026-0001", amount: 124 })));
    renderAt("/payment/success?session_id=cs_1");
    const notice = await screen.findByTestId("payment-failed");
    expect(notice).toHaveTextContent("Η πληρωμή δεν ολοκληρώθηκε");
    expect(notice).toHaveTextContent("Αν έχει γίνει χρέωση στην κάρτα σας, επικοινωνήστε με το γραφείο");
    expect(notice).toHaveTextContent("δοκιμάσετε ξανά");
    expect(await screen.findByTestId("payment-close")).toBeInTheDocument();
  });

  it("αν η κατάσταση δεν διαβάζεται: ουδέτερο μήνυμα, ρητή προτροπή να μην ξαναπληρώσει και διέξοδος για το προσωπικό", async () => {
    server.use(loggedIn(), http.get(`${API}/payments/status/cs_1`, () => HttpResponse.json({ detail: "Η υπηρεσία δεν απαντά." }, { status: 503 })));
    renderAt("/payment/success?session_id=cs_1");
    const notice = await screen.findByTestId("payment-error");
    expect(notice).toHaveTextContent("Δεν ήταν δυνατή η επιβεβαίωση της πληρωμής αυτή τη στιγμή");
    expect(notice).toHaveTextContent("Αν έχετε χρεωθεί, μην πληρώσετε ξανά");
    expect(notice).toHaveTextContent("επικοινωνήστε με το γραφείο");
    expect(notice).not.toHaveTextContent("δεν ολοκληρώθηκε");
    expect(notice).not.toHaveTextContent("δοκιμάστε ξανά");
    expect(await screen.findByTestId("payment-back")).toBeInTheDocument();
  });

  // Το κόκκινο «Χ» δηλώνει βέβαιη αποτυχία. Σε άγνωστη κατάσταση (η κάρτα μπορεί να έχει χρεωθεί) η εικόνα πρέπει να είναι ουδέτερη.
  const iconOf = (notice) => notice.querySelector("svg");
  const RED = "text-[hsl(var(--destructive))]";

  it("η βέβαιη αποτυχία και η ακύρωση έχουν κόκκινη εικόνα", async () => {
    server.use(loggedOut(), http.get(`${API}/payments/status/cs_1`, () =>
      HttpResponse.json({ session_id: "cs_1", status: "expired", payment_status: "failed", invoice_number: "ΤΠΥ-2026-0001", amount: 124 })));
    const { unmount } = renderAt("/payment/success?session_id=cs_1");
    expect(iconOf(await screen.findByTestId("payment-failed"))).toHaveClass(RED);
    unmount();
    renderAt("/payment/cancel?invoice=abc", "cancel");
    expect(iconOf(await screen.findByTestId("payment-cancelled"))).toHaveClass(RED);
  });

  it("η άγνωστη κατάσταση (δεν διαβάστηκε, δεν βρέθηκε πληρωμή) δεν έχει κόκκινη εικόνα", async () => {
    server.use(loggedOut(), http.get(`${API}/payments/status/cs_1`, () => HttpResponse.json({ detail: "Η υπηρεσία δεν απαντά." }, { status: 503 })));
    const { unmount } = renderAt("/payment/success?session_id=cs_1");
    expect(iconOf(await screen.findByTestId("payment-error"))).not.toHaveClass(RED);
    unmount();
    renderAt("/payment/success");
    expect(iconOf(await screen.findByTestId("payment-nosession"))).not.toHaveClass(RED);
  });

  it("η ακύρωση λέει ότι δεν έγινε χρέωση", async () => {
    server.use(loggedOut());
    renderAt("/payment/cancel?invoice=abc", "cancel");
    expect(await screen.findByTestId("payment-cancelled")).toHaveTextContent("Δεν έγινε καμία χρέωση");
  });

  it("το ποσό της επιτυχίας γράφεται με διαχωριστικό χιλιάδων και λεπτά", async () => {
    server.use(loggedOut(), http.get(`${API}/payments/status/cs_1`, () =>
      HttpResponse.json({ session_id: "cs_1", status: "completed", payment_status: "paid", invoice_number: "ΤΠΥ-2026-0007", amount: 1234.5 })));
    renderAt("/payment/success?session_id=cs_1");
    expect(await screen.findByTestId("payment-paid")).toHaveTextContent(/για 1\.234,50\s€ εξοφλήθηκε/);
  });
});

describe("Αποτέλεσμα πληρωμής — η ερώτηση προς τον server σταματά όταν η σελίδα φεύγει", () => {
  afterEach(() => { vi.useRealTimers(); vi.restoreAllMocks(); });

  const pending = { data: { session_id: "cs_1", status: "initiated", payment_status: "pending", invoice_number: "ΤΠΥ-2026-0001", amount: 124 } };
  const advance = (ms) => act(async () => { await vi.advanceTimersByTimeAsync(ms); });

  function Jump() {
    const navigate = useNavigate();
    return <button onClick={() => navigate("/payment/success?session_id=cs_2")}>άλλη πληρωμή</button>;
  }
  const renderWithJump = () => render(
    <MemoryRouter initialEntries={["/payment/success?session_id=cs_1"]}>
      <Jump />
      <Routes><Route path="/payment/success" element={<PaymentResult mode="success" />} /></Routes>
    </MemoryRouter>,
  );

  it("μετά το κλείσιμο της σελίδας δεν ρωτά ξανά", async () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
    const get = vi.spyOn(axios, "get").mockResolvedValue(pending);
    const { unmount } = renderWithJump();
    await advance(0);
    expect(get).toHaveBeenCalledTimes(1);

    unmount();
    await advance(60_000);

    expect(get).toHaveBeenCalledTimes(1);
  });

  it("απάντηση που φτάνει αφού έκλεισε η σελίδα δεν ξεκινά νέο γύρο ερωτήσεων", async () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
    let answer;
    const get = vi.spyOn(axios, "get").mockImplementation(() => new Promise((resolve) => { answer = resolve; }));
    const { unmount } = renderWithJump();
    await advance(0);
    expect(get).toHaveBeenCalledTimes(1);

    unmount();
    answer(pending);
    await advance(60_000);

    expect(get).toHaveBeenCalledTimes(1);
  });

  it("όταν η σελίδα περάσει σε άλλη πληρωμή, δεν εξακολουθεί να δείχνει το αποτέλεσμα της προηγούμενης", async () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
    const answers = {};
    vi.spyOn(axios, "get").mockImplementation((url) => new Promise((resolve) => { answers[url.split("/").pop()] = resolve; }));
    renderWithJump();
    await advance(0);
    await act(async () => { answers.cs_1({ data: { ...pending.data, payment_status: "paid" } }); });
    await advance(0);
    expect(screen.getByTestId("payment-paid")).toBeInTheDocument();

    await act(async () => { screen.getByText("άλλη πληρωμή").click(); });
    await advance(0);

    expect(screen.queryByTestId("payment-paid")).not.toBeInTheDocument();
    expect(screen.getByTestId("payment-checking")).toBeInTheDocument();
  });

  it("αποτυχία ερώτησης για προηγούμενη πληρωμή, που φτάνει αφού η σελίδα πέρασε σε άλλη, δεν χαλά την οθόνη της νέας", async () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
    const settle = {};
    vi.spyOn(axios, "get").mockImplementation((url) => new Promise((resolve, reject) => { settle[url.split("/").pop()] = { resolve, reject }; }));
    renderWithJump();
    await advance(0);
    await act(async () => { screen.getByText("άλλη πληρωμή").click(); });
    await advance(0);

    await act(async () => { settle.cs_1.reject(new Error("Network Error")); });
    await advance(0);

    expect(screen.queryByTestId("payment-error")).not.toBeInTheDocument();
    expect(screen.getByTestId("payment-checking")).toBeInTheDocument();
  });

  it("αν η πληρωμή μένει σε εξέλιξη μετά το όριο αναμονής, λέει να μην ξαναπληρώσει — χωρίς καμία πρόσκληση για νέα προσπάθεια", async () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
    vi.spyOn(axios, "get").mockResolvedValue(pending);
    renderWithJump();
    await advance(32_000);
    const notice = screen.getByTestId("payment-timeout");
    expect(notice).toHaveTextContent("Η πληρωμή σας επεξεργάζεται ακόμη");
    expect(notice).toHaveTextContent("μην πληρώσετε ξανά");
    expect(notice).not.toHaveTextContent(/δοκιμ/);
    expect(notice).not.toHaveTextContent("δεν ολοκληρώθηκε");
  });

  it("η καθυστερημένη απάντηση για προηγούμενη πληρωμή δεν εμφανίζεται ως επιτυχία της νέας", async () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
    const answers = {};
    const get = vi.spyOn(axios, "get").mockImplementation((url) => new Promise((resolve) => { answers[url.split("/").pop()] = resolve; }));
    renderWithJump();
    await advance(0);
    expect(Object.keys(answers)).toEqual(["cs_1"]);

    await act(async () => { screen.getByText("άλλη πληρωμή").click(); });
    await advance(0);
    expect(Object.keys(answers)).toEqual(["cs_1", "cs_2"]);
    expect(screen.getByTestId("payment-checking")).toBeInTheDocument();

    await act(async () => { answers.cs_1({ data: { ...pending.data, session_id: "cs_1", payment_status: "paid" } }); });
    await advance(0);

    expect(screen.queryByTestId("payment-paid")).not.toBeInTheDocument();
    expect(screen.getByTestId("payment-checking")).toBeInTheDocument();
    await act(async () => { answers.cs_2({ data: { ...pending.data, session_id: "cs_2", invoice_number: "ΤΠΥ-2026-0002", payment_status: "paid" } }); });
    await advance(0);
    expect(screen.getByTestId("payment-paid")).toHaveTextContent("ΤΠΥ-2026-0002");
    expect(get).toHaveBeenCalledTimes(2);
  });
});
