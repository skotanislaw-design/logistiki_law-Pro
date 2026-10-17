/**
 * Φάση 1 «ψηφιακού λογιστή» — υποθέσεις: ο υπεύθυνος δικηγόρος και τα συνδεδεμένα παραστατικά.
 *
 * Ο υπεύθυνος δικηγόρος είναι αυτός από τον οποίο κληρονομούν τα έξοδα και τα τιμολόγια της υπόθεσης (βλ.
 * backend/dimensions.py). Εδώ ελέγχεται ότι η οθόνη τον δείχνει, τον στέλνει στον server, και ότι ένα σφάλμα (λίστα που
 * δεν φόρτωσε, διαγραφή που απορρίφθηκε) φαίνεται αντί να κρύβεται.
 */
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { Toaster } from "@/components/ui/sonner";
import CaseDetail from "@/pages/CaseDetail";
import Cases from "@/pages/Cases";
import { CASE_A, CASE_B, CLIENT_A, CLIENT_B, USERS, lookupHandlers } from "@/test/lookupFixtures";
import { server } from "@/test/server";

const API = "http://localhost:8001/api";

afterEach(() => vi.restoreAllMocks());

const fullCase = (over = {}) => ({
  ...CASE_A, case_type: "Αστικό", court: "", opposing_party: "", status: "active", next_action: "", next_action_date: "",
  gak: "", eak: "", gak_year: "", notes: [], history: [], ...over,
});

async function pick(user, triggerId, optionName) {
  await user.click(await screen.findByTestId(triggerId));
  await user.click(await screen.findByRole("option", { name: optionName }));
}

describe("Υποθέσεις — λίστα και νέα υπόθεση", () => {
  // Το πρώτο handler που ταιριάζει κερδίζει: τα `extra` μπαίνουν πρώτα ώστε να μπορούν να αντικαταστήσουν τα προεπιλεγμένα.
  function serveList(cases, extra = []) {
    server.use(
      ...extra,
      ...lookupHandlers({ cases, clients: [CLIENT_A, CLIENT_B], users: USERS }),
      http.get(`${API}/cases/types/list`, () => HttpResponse.json(["Αστικό", "Ποινικό"])),
    );
  }
  const renderCases = () => render(<MemoryRouter><Cases /><Toaster /></MemoryRouter>);

  it("η στήλη «Δικηγόρος» δείχνει το όνομα του υπευθύνου και «—» όταν δεν έχει οριστεί", async () => {
    serveList([CASE_A, CASE_B]);
    renderCases();
    await waitFor(() => expect(screen.getByTestId("case-lawyer-case-a")).toHaveTextContent("Χρήστος Σκοτάνης"));
    expect(screen.getByTestId("case-lawyer-case-b")).toHaveTextContent("—");
  });

  it("η νέα υπόθεση στέλνει τον υπεύθυνο δικηγόρο που επιλέχθηκε", async () => {
    const user = userEvent.setup();
    let posted = null;
    serveList([], [http.post(`${API}/cases`, async ({ request }) => {
      posted = await request.json();
      return HttpResponse.json({ id: "case-new", number: "ΥΠΘ-2026-0009", ...posted });
    })]);
    renderCases();
    await user.click(await screen.findByTestId("new-case-button"));
    await user.type(await screen.findByTestId("case-title-input"), "Νέα αγωγή");
    await pick(user, "case-lawyer-select", "Μαρία Δικηγόρος");
    await user.click(screen.getByTestId("save-case-button"));
    await waitFor(() => expect(posted).not.toBeNull());
    expect(posted).toMatchObject({ title: "Νέα αγωγή", lawyer_id: "u-law2" });
  });

  it("οι χρήστες που δεν είναι δικηγόροι (ο λογιστής) δεν προσφέρονται ως υπεύθυνοι", async () => {
    const user = userEvent.setup();
    serveList([]);
    renderCases();
    await user.click(await screen.findByTestId("new-case-button"));
    await user.click(await screen.findByTestId("case-lawyer-select"));
    expect(await screen.findByRole("option", { name: "Μαρία Δικηγόρος" })).toBeInTheDocument();
    expect(screen.queryByRole("option", { name: "Χριστόφορος Λογιστής" })).not.toBeInTheDocument();
  });

  it("χωρίς επιλογή, η υπόθεση δημιουργείται χωρίς υπεύθυνο (κενό, όχι «__none__»)", async () => {
    const user = userEvent.setup();
    let posted = null;
    serveList([], [http.post(`${API}/cases`, async ({ request }) => {
      posted = await request.json();
      return HttpResponse.json({ id: "case-new", number: "ΥΠΘ-2026-0009", ...posted });
    })]);
    renderCases();
    await user.click(await screen.findByTestId("new-case-button"));
    await user.type(await screen.findByTestId("case-title-input"), "Χωρίς υπεύθυνο");
    await user.click(screen.getByTestId("save-case-button"));
    await waitFor(() => expect(posted).not.toBeNull());
    expect(posted.lawyer_id).toBe("");
  });

  it("αν επιλεγεί πρώτα δικηγόρος και μετά «Δεν έχει οριστεί», η υπόθεση δημιουργείται χωρίς υπεύθυνο (κενό, όχι «__none__»)", async () => {
    const user = userEvent.setup();
    let posted = null;
    serveList([], [http.post(`${API}/cases`, async ({ request }) => {
      posted = await request.json();
      return HttpResponse.json({ id: "case-new", number: "ΥΠΘ-2026-0009", ...posted });
    })]);
    renderCases();
    await user.click(await screen.findByTestId("new-case-button"));
    await user.type(await screen.findByTestId("case-title-input"), "Άλλαξα γνώμη");
    await pick(user, "case-lawyer-select", "Μαρία Δικηγόρος");
    expect(screen.getByTestId("case-lawyer-select")).toHaveTextContent("Μαρία Δικηγόρος");
    await pick(user, "case-lawyer-select", "Δεν έχει οριστεί");
    expect(screen.getByTestId("case-lawyer-select")).toHaveTextContent("Δεν έχει οριστεί");
    await user.click(screen.getByTestId("save-case-button"));
    await waitFor(() => expect(posted).not.toBeNull());
    expect(posted.lawyer_id).toBe("");
  });

  it("αν η λίστα δικηγόρων δεν φορτώσει, η φόρμα το δηλώνει και η υπόθεση μπορεί να δημιουργηθεί", async () => {
    const user = userEvent.setup();
    let posted = null;
    server.use(
      ...lookupHandlers({ cases: [], clients: [CLIENT_A], users: "fail" }),
      http.get(`${API}/cases/types/list`, () => HttpResponse.json(["Αστικό"])),
      http.post(`${API}/cases`, async ({ request }) => {
        posted = await request.json();
        return HttpResponse.json({ id: "case-new", number: "ΥΠΘ-2026-0009", ...posted });
      }),
    );
    renderCases();
    await user.click(await screen.findByTestId("new-case-button"));
    expect(await screen.findByText("Η λίστα δικηγόρων δεν φορτώθηκε.")).toBeInTheDocument();
    await user.type(screen.getByTestId("case-title-input"), "Παρά το σφάλμα");
    await user.click(screen.getByTestId("save-case-button"));
    await waitFor(() => expect(posted).not.toBeNull());
    expect(posted.lawyer_id).toBe("");
  });

  it("η διαγραφή υπόθεσης με συνδεδεμένα παραστατικά απορρίπτεται και ο server εξηγεί γιατί", async () => {
    const user = userEvent.setup();
    vi.spyOn(window, "confirm").mockReturnValue(true);
    let listed = 0;
    serveList([CASE_A], [
      http.get(`${API}/cases`, () => { listed += 1; return HttpResponse.json([CASE_A]); }),
      http.delete(`${API}/cases/case-a`, () => HttpResponse.json(
        { detail: "Η υπόθεση έχει συνδεδεμένα 2 έξοδα και 1 τιμολόγια και δεν διαγράφεται. Αρχειοθετήστε την." }, { status: 409 })),
    ]);
    renderCases();
    await user.click(await screen.findByTestId("delete-case-case-a"));
    expect(await screen.findByText(/έχει συνδεδεμένα 2 έξοδα και 1 τιμολόγια/)).toBeInTheDocument();
    expect(screen.getByTestId("case-row-case-a")).toBeInTheDocument();       // η υπόθεση μένει στη λίστα
    expect(listed).toBe(1);                                                   // και δεν ξαναφορτώθηκε σαν να είχε διαγραφεί
  });
});

describe("Υπόθεση — σελίδα λεπτομερειών", () => {
  function serveCase(data, extra = []) {
    server.use(
      ...lookupHandlers({ users: USERS }),
      http.get(`${API}/cases/case-a`, () => HttpResponse.json({
        case: fullCase(), grammatia: [], paravola: [], fees_total: 0, expenses_total: 0, ...data })),
      http.get(`${API}/hearings`, () => HttpResponse.json([])),
      ...extra,
    );
  }
  const renderDetail = () => render(
    <MemoryRouter initialEntries={["/cases/case-a"]}>
      <Routes><Route path="/cases/:id" element={<CaseDetail />} /></Routes>
      <Toaster />
    </MemoryRouter>,
  );

  it("δείχνει τον υπεύθυνο δικηγόρο και τα συνδεδεμένα τιμολόγια και έξοδα", async () => {
    serveCase({ linked: { invoices: { count: 2, subtotal: 500 }, expenses: { count: 3, total: 186 } } });
    renderDetail();
    expect(await screen.findByTestId("case-detail-lawyer-select")).toHaveTextContent("Χρήστος Σκοτάνης");
    const linked = await screen.findByTestId("case-linked");
    expect(linked).toHaveTextContent("Τιμολόγια (2)");
    expect(screen.getByTestId("case-linked-invoices-total")).toHaveTextContent("500,00");
    expect(linked).toHaveTextContent("Έξοδα (3)");
    expect(screen.getByTestId("case-linked-expenses-total")).toHaveTextContent("186,00");
  });

  it("χωρίς συνδεδεμένα στοιχεία από τον server (παλιά απάντηση), το πλαίσιο δεν εμφανίζεται", async () => {
    serveCase({});
    renderDetail();
    await screen.findByTestId("case-detail-lawyer-select");
    expect(screen.queryByTestId("case-linked")).not.toBeInTheDocument();
  });

  it("η αλλαγή υπευθύνου στέλνει την υπόθεση με τον νέο δικηγόρο, χωρίς τον πελάτη (που δεν αλλάζει)", async () => {
    const user = userEvent.setup();
    let put = null;
    serveCase({}, [http.put(`${API}/cases/case-a`, async ({ request }) => {
      put = await request.json();
      return HttpResponse.json(put);
    })]);
    renderDetail();
    await pick(user, "case-detail-lawyer-select", "Μαρία Δικηγόρος");
    await waitFor(() => expect(put).not.toBeNull());
    expect(put).toMatchObject({ id: "case-a", title: "Αγωγή αποζημίωσης", lawyer_id: "u-law2" });
    // Ο πελάτης δεν στέλνεται: ο server τον αλλάζει μόνο αν σταλεί (και μεταφέρει την αλλαγή στα έξοδα της υπόθεσης).
    expect(put).not.toHaveProperty("client_id");
    expect(put).not.toHaveProperty("client_name");
    expect(await screen.findByText("Ο υπεύθυνος δικηγόρος ενημερώθηκε")).toBeInTheDocument();
  });

  it("μετά την αλλαγή υπευθύνου η σελίδα ξαναφορτώνει την υπόθεση και δείχνει ό,τι αποθήκευσε ο server", async () => {
    const user = userEvent.setup();
    let loads = 0;
    let current = fullCase();
    server.use(
      http.put(`${API}/cases/case-a`, async ({ request }) => {
        const body = await request.json();
        current = { ...current, lawyer_id: body.lawyer_id };
        return HttpResponse.json(current);
      }),
      http.get(`${API}/cases/case-a`, () => {
        loads += 1;
        return HttpResponse.json({ case: current, grammatia: [], paravola: [], fees_total: 0, expenses_total: 0 });
      }),
      ...lookupHandlers({ users: USERS }),
      http.get(`${API}/hearings`, () => HttpResponse.json([])),
    );
    renderDetail();
    expect(await screen.findByTestId("case-detail-lawyer-select")).toHaveTextContent("Χρήστος Σκοτάνης");
    expect(loads).toBe(1);

    await pick(user, "case-detail-lawyer-select", "Μαρία Δικηγόρος");

    await waitFor(() => expect(loads).toBe(2));
    await waitFor(() => expect(screen.getByTestId("case-detail-lawyer-select")).toHaveTextContent("Μαρία Δικηγόρος"));
  });

  it("η επιλογή «Δεν έχει οριστεί» στέλνει κενό υπεύθυνο", async () => {
    const user = userEvent.setup();
    let put = null;
    serveCase({}, [http.put(`${API}/cases/case-a`, async ({ request }) => {
      put = await request.json();
      return HttpResponse.json(put);
    })]);
    renderDetail();
    await pick(user, "case-detail-lawyer-select", "Δεν έχει οριστεί");
    await waitFor(() => expect(put).not.toBeNull());
    expect(put.lawyer_id).toBe("");
  });

  it("δικηγόρος που δεν υπάρχει πια στη λίστα εμφανίζεται ως άγνωστος, όχι ως κενό πεδίο", async () => {
    server.use(
      ...lookupHandlers({ users: USERS }),
      http.get(`${API}/cases/case-a`, () => HttpResponse.json({
        case: fullCase({ lawyer_id: "u-gone" }), grammatia: [], paravola: [], fees_total: 0, expenses_total: 0 })),
      http.get(`${API}/hearings`, () => HttpResponse.json([])),
    );
    renderDetail();
    expect(await screen.findByTestId("case-detail-lawyer-select")).toHaveTextContent("(άγνωστος χρήστης)");
  });

  it("αν ο server απορρίψει τον νέο υπεύθυνο, το σφάλμα φαίνεται", async () => {
    const user = userEvent.setup();
    serveCase({}, [http.put(`${API}/cases/case-a`, () => HttpResponse.json(
      { detail: "Ο χρήστης που επιλέχθηκε δεν είναι δικηγόρος του γραφείου." }, { status: 400 }))]);
    renderDetail();
    await pick(user, "case-detail-lawyer-select", "Μαρία Δικηγόρος");
    expect(await screen.findByText("Ο χρήστης που επιλέχθηκε δεν είναι δικηγόρος του γραφείου.")).toBeInTheDocument();
    expect(within(screen.getByTestId("case-detail-lawyer-select")).queryByText("Μαρία Δικηγόρος")).not.toBeInTheDocument();
  });
});
