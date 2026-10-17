/**
 * Φάση 1 «ψηφιακού λογιστή» — φόρμα τιμολογίου: σε ποια υπόθεση και σε ποιον δικηγόρο ανήκει.
 *
 * Υπόθεση και δικηγόρος είναι προαιρετικά μεταδεδομένα οργάνωσης. Ο server (backend/dimensions.py) είναι η πηγή της αλήθειας:
 * αν δεν δοθεί δικηγόρος, το τιμολόγιο κληρονομεί τον υπεύθυνο της υπόθεσης. Η φόρμα λοιπόν στέλνει κενό (όχι τον υπεύθυνο που
 * «βλέπει»), ώστε η κληρονομιά να γίνεται μία φορά, στον server.
 */
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";

import { Toaster } from "@/components/ui/sonner";
import Invoices from "@/pages/Invoices";
import { CASE_A, CASE_B, CLIENT_A, CLIENT_B, USERS } from "@/test/lookupFixtures";
import { server } from "@/test/server";

const API = "http://localhost:8001/api";

const draft = (over = {}) => ({
  id: "d1", number: "", status: "draft", client_id: "client-a", client_name: "ΑΛΦΑ ΑΕ", date: "2026-10-12",
  line_items: [{ description: "Υπηρεσία", quantity: 1, unit_price: 100, vat_rate: 24 }],
  payment_method: "Τραπεζική κατάθεση", notes: "", subtotal: 100, vat_amount: 24, total: 124, mydata_status: "not_sent",
  case_id: "", lawyer_id: "", ...over,
});

const LOOKUPS = { cases: [CASE_A, CASE_B], clients: [CLIENT_A, CLIENT_B], users: USERS };

const reply = (rows) => (rows === "fail" ? new HttpResponse(null, { status: 500 }) : HttpResponse.json(rows));

/** Μετρά πόσες φορές ζητήθηκαν οι λίστες υποθέσεων και δικηγόρων: η φόρμα τις φορτώνει μόνο όταν ανοίξει. */
function serve({ listed = [], lookups = LOOKUPS, extra = [] } = {}) {
  const calls = { cases: 0, users: 0 };
  server.use(
    ...extra,
    http.get(`${API}/invoices`, () => HttpResponse.json(listed)),
    http.get(`${API}/cases`, () => { calls.cases += 1; return reply(lookups.cases); }),
    http.get(`${API}/clients`, () => reply(lookups.clients)),
    http.get(`${API}/auth/users`, () => { calls.users += 1; return reply(lookups.users); }),
  );
  return calls;
}

async function pick(user, triggerId, optionName) {
  await user.click(await screen.findByTestId(triggerId));
  await user.click(await screen.findByRole("option", { name: optionName }));
}

describe("Τιμολόγια — υπόθεση και δικηγόρος στη φόρμα", () => {
  it("οι λίστες υποθέσεων και δικηγόρων φορτώνουν μόνο όταν ανοίξει η φόρμα", async () => {
    const user = userEvent.setup();
    const calls = serve();
    render(<><Invoices /><Toaster /></>);
    await screen.findByTestId("new-invoice-button");
    expect(calls).toEqual({ cases: 0, users: 0 });
    await user.click(screen.getByTestId("new-invoice-button"));
    await screen.findByTestId("invoice-case-select");
    await waitFor(() => expect(calls).toEqual({ cases: 1, users: 1 }));
  });

  it("το νέο τιμολόγιο ξεκινά χωρίς υπόθεση και χωρίς δικηγόρο και τα στέλνει κενά", async () => {
    const user = userEvent.setup();
    let posted = null;
    serve({ extra: [http.post(`${API}/invoices`, async ({ request }) => { posted = await request.json(); return HttpResponse.json(draft()); })] });
    render(<><Invoices /><Toaster /></>);
    await user.click(await screen.findByTestId("new-invoice-button"));
    expect(await screen.findByTestId("invoice-case-select")).toHaveTextContent("Καμία");
    expect(screen.getByTestId("invoice-lawyer-select")).toHaveTextContent("Κανένας");
    await pick(user, "invoice-client-select", "ΑΛΦΑ ΑΕ");
    await user.click(screen.getByTestId("save-invoice-button"));
    await waitFor(() => expect(posted).not.toBeNull());
    expect(posted).toMatchObject({ client_id: "client-a", case_id: "", lawyer_id: "" });
  });

  it("η υπόθεση και ο δικηγόρος που επιλέχθηκαν φτάνουν στον server", async () => {
    const user = userEvent.setup();
    let posted = null;
    serve({ extra: [http.post(`${API}/invoices`, async ({ request }) => { posted = await request.json(); return HttpResponse.json(draft()); })] });
    render(<><Invoices /><Toaster /></>);
    await user.click(await screen.findByTestId("new-invoice-button"));
    await pick(user, "invoice-client-select", "ΑΛΦΑ ΑΕ");
    await pick(user, "invoice-case-select", /ΥΠΘ-2026-0001/);
    await pick(user, "invoice-lawyer-select", "Μαρία Δικηγόρος");
    await user.click(screen.getByTestId("save-invoice-button"));
    await waitFor(() => expect(posted).not.toBeNull());
    expect(posted).toMatchObject({ client_id: "client-a", case_id: "case-a", lawyer_id: "u-law2" });
    expect(await screen.findByText("Το τιμολόγιο δημιουργήθηκε")).toBeInTheDocument();
  });

  it("με υπόθεση χωρίς ρητό δικηγόρο, η φόρμα λέει ότι θα ισχύσει ο υπεύθυνος της υπόθεσης και δεν τον στέλνει η ίδια", async () => {
    const user = userEvent.setup();
    let posted = null;
    serve({ extra: [http.post(`${API}/invoices`, async ({ request }) => { posted = await request.json(); return HttpResponse.json(draft()); })] });
    render(<><Invoices /><Toaster /></>);
    await user.click(await screen.findByTestId("new-invoice-button"));
    await pick(user, "invoice-client-select", "ΑΛΦΑ ΑΕ");
    expect(screen.getByTestId("invoice-lawyer-select")).toHaveTextContent("Κανένας");
    await pick(user, "invoice-case-select", /ΥΠΘ-2026-0001/);
    expect(screen.getByTestId("invoice-lawyer-select")).toHaveTextContent("Ο υπεύθυνος της υπόθεσης");
    await user.click(screen.getByTestId("save-invoice-button"));
    await waitFor(() => expect(posted).not.toBeNull());
    expect(posted.case_id).toBe("case-a");
    expect(posted.lawyer_id).toBe("");
  });

  it("οι υποθέσεις του επιλεγμένου πελάτη εμφανίζονται πρώτες, χωρίς να κρύβονται οι υπόλοιπες", async () => {
    const user = userEvent.setup();
    serve();
    render(<><Invoices /><Toaster /></>);
    await user.click(await screen.findByTestId("new-invoice-button"));
    await pick(user, "invoice-client-select", "Β. Παπαδόπουλος");
    await user.click(await screen.findByTestId("invoice-case-select"));
    const options = (await screen.findAllByRole("option")).map((o) => o.textContent);
    const first = options.findIndex((t) => t.includes("ΥΠΘ-2026-0002"));
    const second = options.findIndex((t) => t.includes("ΥΠΘ-2026-0001"));
    expect(first).toBeGreaterThanOrEqual(0);
    expect(second).toBeGreaterThanOrEqual(0);
    expect(first).toBeLessThan(second);
  });

  it("ο λογιστής του γραφείου δεν προσφέρεται ως δικηγόρος του τιμολογίου", async () => {
    const user = userEvent.setup();
    serve();
    render(<><Invoices /><Toaster /></>);
    await user.click(await screen.findByTestId("new-invoice-button"));
    await user.click(await screen.findByTestId("invoice-lawyer-select"));
    expect(await screen.findByRole("option", { name: "Μαρία Δικηγόρος" })).toBeInTheDocument();
    expect(screen.queryByRole("option", { name: "Χριστόφορος Λογιστής" })).not.toBeInTheDocument();
  });

  it("αν οι λίστες δεν φορτώσουν, η φόρμα το δηλώνει και το τιμολόγιο μπορεί να αποθηκευτεί χωρίς υπόθεση", async () => {
    const user = userEvent.setup();
    let posted = null;
    serve({
      lookups: { cases: "fail", clients: [CLIENT_A], users: "fail" },
      extra: [http.post(`${API}/invoices`, async ({ request }) => { posted = await request.json(); return HttpResponse.json(draft()); })],
    });
    render(<><Invoices /><Toaster /></>);
    await user.click(await screen.findByTestId("new-invoice-button"));
    expect(await screen.findByText("Η λίστα υποθέσεων δεν φορτώθηκε.")).toBeInTheDocument();
    expect(screen.getByText("Η λίστα δικηγόρων δεν φορτώθηκε.")).toBeInTheDocument();
    await pick(user, "invoice-client-select", "ΑΛΦΑ ΑΕ");
    await user.click(screen.getByTestId("save-invoice-button"));
    await waitFor(() => expect(posted).not.toBeNull());
    expect(posted).toMatchObject({ case_id: "", lawyer_id: "" });
  });

  it("η απόρριψη από τον server (π.χ. υπόθεση άλλου γραφείου) εμφανίζεται και η φόρμα μένει ανοιχτή", async () => {
    const user = userEvent.setup();
    serve({ extra: [http.post(`${API}/invoices`, () => HttpResponse.json({ detail: "Η υπόθεση δεν βρέθηκε" }, { status: 404 }))] });
    render(<><Invoices /><Toaster /></>);
    await user.click(await screen.findByTestId("new-invoice-button"));
    await pick(user, "invoice-client-select", "ΑΛΦΑ ΑΕ");
    await pick(user, "invoice-case-select", /ΥΠΘ-2026-0001/);
    await user.click(screen.getByTestId("save-invoice-button"));
    expect(await screen.findByText("Η υπόθεση δεν βρέθηκε")).toBeInTheDocument();
    expect(screen.getByTestId("invoice-case-select")).toHaveTextContent("ΥΠΘ-2026-0001");
  });
});

describe("Τιμολόγια — επεξεργασία πρόχειρου με υπόθεση και δικηγόρο", () => {
  it("η φόρμα δείχνει τη σύνδεση που υπάρχει και την ξαναστέλνει αμετάβλητη", async () => {
    const user = userEvent.setup();
    let put = null;
    serve({
      listed: [draft({ case_id: "case-a", lawyer_id: "u-law2" })],
      extra: [http.put(`${API}/invoices/d1`, async ({ request }) => { put = await request.json(); return HttpResponse.json(draft()); })],
    });
    render(<><Invoices /><Toaster /></>);
    await user.click(await screen.findByTestId("edit-invoice-d1"));
    expect(await screen.findByTestId("invoice-case-select")).toHaveTextContent("ΥΠΘ-2026-0001");
    expect(screen.getByTestId("invoice-lawyer-select")).toHaveTextContent("Μαρία Δικηγόρος");
    await user.click(screen.getByTestId("save-invoice-button"));
    await waitFor(() => expect(put).not.toBeNull());
    expect(put).toMatchObject({ client_id: "client-a", case_id: "case-a", lawyer_id: "u-law2" });
    expect(await screen.findByText("Το τιμολόγιο ενημερώθηκε")).toBeInTheDocument();
  });

  it("πρόχειρο χωρίς σύνδεση ανοίγει με «Καμία»/«Κανένας» και δεν στέλνει «undefined»", async () => {
    const user = userEvent.setup();
    let put = null;
    const legacy = draft();                                              // παλιά εγγραφή, πριν υπάρξουν τα πεδία
    delete legacy.case_id;
    delete legacy.lawyer_id;
    serve({
      listed: [legacy],
      extra: [http.put(`${API}/invoices/d1`, async ({ request }) => { put = await request.json(); return HttpResponse.json(draft()); })],
    });
    render(<><Invoices /><Toaster /></>);
    await user.click(await screen.findByTestId("edit-invoice-d1"));
    expect(await screen.findByTestId("invoice-case-select")).toHaveTextContent("Καμία");
    expect(screen.getByTestId("invoice-lawyer-select")).toHaveTextContent("Κανένας");
    await user.click(screen.getByTestId("save-invoice-button"));
    await waitFor(() => expect(put).not.toBeNull());
    expect(put.case_id).toBe("");
    expect(put.lawyer_id).toBe("");
  });

  it("υπόθεση ή δικηγόρος που δεν υπάρχουν πια στις λίστες φαίνονται ως άγνωστοι και δεν χάνονται σιωπηλά", async () => {
    const user = userEvent.setup();
    let put = null;
    serve({
      listed: [draft({ case_id: "case-gone", lawyer_id: "u-gone" })],
      extra: [http.put(`${API}/invoices/d1`, async ({ request }) => { put = await request.json(); return HttpResponse.json(draft()); })],
    });
    render(<><Invoices /><Toaster /></>);
    await user.click(await screen.findByTestId("edit-invoice-d1"));
    expect(await screen.findByTestId("invoice-case-select")).toHaveTextContent("(άγνωστη υπόθεση)");
    expect(screen.getByTestId("invoice-lawyer-select")).toHaveTextContent("(άγνωστος χρήστης)");
    await user.click(screen.getByTestId("save-invoice-button"));
    await waitFor(() => expect(put).not.toBeNull());
    expect(put).toMatchObject({ case_id: "case-gone", lawyer_id: "u-gone" });
  });

  it("η επιλογή «Καμία» αφαιρεί την υπόθεση και στέλνει κενό", async () => {
    const user = userEvent.setup();
    let put = null;
    serve({
      listed: [draft({ case_id: "case-a", lawyer_id: "" })],
      extra: [http.put(`${API}/invoices/d1`, async ({ request }) => { put = await request.json(); return HttpResponse.json(draft()); })],
    });
    render(<><Invoices /><Toaster /></>);
    await user.click(await screen.findByTestId("edit-invoice-d1"));
    await pick(user, "invoice-case-select", "Καμία");
    await user.click(screen.getByTestId("save-invoice-button"));
    await waitFor(() => expect(put).not.toBeNull());
    expect(put.case_id).toBe("");
  });

  it("η επιλογή «Κανένας» αφαιρεί τον δικηγόρο και στέλνει κενό — όχι το εσωτερικό σύμβολο της λίστας", async () => {
    const user = userEvent.setup();
    let put = null;
    serve({
      listed: [draft({ case_id: "", lawyer_id: "u-law2" })],
      extra: [http.put(`${API}/invoices/d1`, async ({ request }) => { put = await request.json(); return HttpResponse.json(draft()); })],
    });
    render(<><Invoices /><Toaster /></>);
    await user.click(await screen.findByTestId("edit-invoice-d1"));
    expect(await screen.findByTestId("invoice-lawyer-select")).toHaveTextContent("Μαρία Δικηγόρος");
    await pick(user, "invoice-lawyer-select", "Κανένας");
    expect(screen.getByTestId("invoice-lawyer-select")).toHaveTextContent("Κανένας");
    await user.click(screen.getByTestId("save-invoice-button"));
    await waitFor(() => expect(put).not.toBeNull());
    expect(put.lawyer_id).toBe("");
  });
});
