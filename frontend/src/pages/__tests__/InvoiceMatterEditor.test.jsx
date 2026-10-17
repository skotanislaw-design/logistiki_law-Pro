/**
 * Φάση 1 «ψηφιακού λογιστή» — «υπόθεση τιμολογίου» μετά την έκδοση.
 *
 * Ένα εκδοθέν τιμολόγιο δεν αλλάζει (είναι το παραστατικό που πήγε στην ΑΑΔΕ)· η υπόθεση και ο δικηγόρος όμως είναι
 * μεταδεδομένα οργάνωσης και μπορούν να μπουν ή να διορθωθούν οποτεδήποτε, από το χωριστό `PATCH /invoices/:id/matter`
 * που αγγίζει μόνο αυτά τα δύο πεδία.
 */
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { describe, expect, it, vi } from "vitest";

import { InvoiceDetailDialog } from "@/components/InvoiceDetailDialog";
import { InvoiceMatterEditor } from "@/components/InvoiceMatterEditor";
import { Toaster } from "@/components/ui/sonner";
import Invoices from "@/pages/Invoices";
import { CASE_A, CASE_B, CLIENT_A, CLIENT_B, USERS } from "@/test/lookupFixtures";
import { server } from "@/test/server";

const API = "http://localhost:8001/api";
const LOOKUPS = { cases: [CASE_A, CASE_B], clients: [CLIENT_A, CLIENT_B], users: USERS };

const issued = (over = {}) => ({
  id: "i1", number: "ΤΠΥ-2026-0001", status: "issued", client_id: "client-a", client_name: "ΑΛΦΑ ΑΕ", date: "2026-10-05",
  line_items: [{ description: "Υπηρεσία", quantity: 1, unit_price: 100, vat_rate: 24 }], payment_method: "Τραπεζική κατάθεση",
  subtotal: 100, vat_amount: 24, total: 124, outstanding: 124, mydata_status: "synced", mydata_mark: "400001234567890",
  case_id: "", lawyer_id: "", ...over,
});

const reply = (rows) => (rows === "fail" ? new HttpResponse(null, { status: 500 }) : HttpResponse.json(rows));

function serveLookups(lookups = LOOKUPS) {
  const calls = { cases: 0, users: 0 };
  server.use(
    http.get(`${API}/cases`, () => { calls.cases += 1; return reply(lookups.cases); }),
    http.get(`${API}/clients`, () => reply(lookups.clients)),
    http.get(`${API}/auth/users`, () => { calls.users += 1; return reply(lookups.users); }),
  );
  return calls;
}

function servePatch(onPatch) {
  server.use(http.patch(`${API}/invoices/i1/matter`, async ({ request }) => {
    const body = await request.json();
    return onPatch(body);
  }));
}

async function pick(user, triggerId, optionName) {
  await user.click(await screen.findByTestId(triggerId));
  await user.click(await screen.findByRole("option", { name: optionName }));
}

const renderEditor = (invoice, onSaved) => render(<><InvoiceMatterEditor invoice={invoice} onSaved={onSaved} /><Toaster /></>);

describe("Επεξεργαστής υπόθεσης τιμολογίου", () => {
  it("τιμολόγιο χωρίς σύνδεση δείχνει «—» και δεν φορτώνει καμία λίστα μέχρι να πατηθεί «Αλλαγή»", async () => {
    const user = userEvent.setup();
    const calls = serveLookups();
    renderEditor(issued());
    expect(screen.getByTestId("invoice-matter-case")).toHaveTextContent("—");
    expect(screen.getByTestId("invoice-matter-lawyer")).toHaveTextContent("—");
    await new Promise((resolve) => setTimeout(resolve, 80));                       // ένα αίτημα που ξεκινά τώρα θα έφτανε εντός ms
    expect(calls).toEqual({ cases: 0, users: 0 });
    await user.click(screen.getByTestId("invoice-matter-edit"));
    await screen.findByTestId("invoice-matter-form");
    await waitFor(() => expect(calls).toEqual({ cases: 1, users: 1 }));
  });

  it("τιμολόγιο με σύνδεση φορτώνει τις λίστες αμέσως και δείχνει ονόματα, όχι κωδικούς", async () => {
    serveLookups();
    renderEditor(issued({ case_id: "case-a", lawyer_id: "u-law2" }));
    await waitFor(() => expect(screen.getByTestId("invoice-matter-case")).toHaveTextContent("ΥΠΘ-2026-0001 — Αγωγή αποζημίωσης"));
    expect(screen.getByTestId("invoice-matter-lawyer")).toHaveTextContent("Μαρία Δικηγόρος");
    expect(screen.queryByText("case-a")).not.toBeInTheDocument();
  });

  it("όσο οι λίστες φορτώνουν δείχνει «…» και όχι «άγνωστη υπόθεση»", async () => {
    let release;
    const gate = new Promise((resolve) => { release = resolve; });
    server.use(
      http.get(`${API}/cases`, async () => { await gate; return HttpResponse.json([CASE_A]); }),
      http.get(`${API}/clients`, () => HttpResponse.json([])),
      http.get(`${API}/auth/users`, () => HttpResponse.json(USERS)),
    );
    renderEditor(issued({ case_id: "case-a" }));
    expect(screen.getByTestId("invoice-matter-case")).toHaveTextContent("…");
    expect(screen.queryByText("(άγνωστη υπόθεση)")).not.toBeInTheDocument();
    release();
    await waitFor(() => expect(screen.getByTestId("invoice-matter-case")).toHaveTextContent("ΥΠΘ-2026-0001"));
  });

  it("όσο φορτώνει η λίστα δικηγόρων δείχνει «…» και όχι «άγνωστος χρήστης»", async () => {
    let release;
    const gate = new Promise((resolve) => { release = resolve; });
    server.use(
      http.get(`${API}/cases`, () => HttpResponse.json([CASE_A])),
      http.get(`${API}/clients`, () => HttpResponse.json([])),
      http.get(`${API}/auth/users`, async () => { await gate; return HttpResponse.json(USERS); }),
    );
    renderEditor(issued({ lawyer_id: "u-law2" }));
    expect(screen.getByTestId("invoice-matter-lawyer")).toHaveTextContent("…");
    expect(screen.queryByText("(άγνωστος χρήστης)")).not.toBeInTheDocument();
    release();
    await waitFor(() => expect(screen.getByTestId("invoice-matter-lawyer")).toHaveTextContent("Μαρία Δικηγόρος"));
  });

  it("τιμολόγιο συνδεδεμένο μόνο με δικηγόρο (χωρίς υπόθεση) φορτώνει τις λίστες και δείχνει το όνομά του", async () => {
    const calls = serveLookups();
    renderEditor(issued({ case_id: "", lawyer_id: "u-law2" }));
    await waitFor(() => expect(screen.getByTestId("invoice-matter-lawyer")).toHaveTextContent("Μαρία Δικηγόρος"));
    expect(screen.getByTestId("invoice-matter-case")).toHaveTextContent("—");
    expect(calls.users).toBe(1);
  });

  it("υπόθεση ή δικηγόρος που δεν υπάρχουν στις λίστες φαίνονται ως άγνωστοι, όχι ως κενό", async () => {
    serveLookups();
    renderEditor(issued({ case_id: "case-gone", lawyer_id: "u-gone" }));
    await waitFor(() => expect(screen.getByTestId("invoice-matter-case")).toHaveTextContent("(άγνωστη υπόθεση)"));
    expect(screen.getByTestId("invoice-matter-lawyer")).toHaveTextContent("(άγνωστος χρήστης)");
  });

  it("η αποθήκευση στέλνει μόνο υπόθεση και δικηγόρο στο PATCH, ενημερώνει την περίληψη και ειδοποιεί τον γονέα", async () => {
    const user = userEvent.setup();
    serveLookups();
    let patched = null;
    servePatch((body) => { patched = body; return HttpResponse.json(issued({ case_id: "case-a", lawyer_id: "u-law2" })); });
    const onSaved = vi.fn();
    renderEditor(issued(), onSaved);

    await user.click(screen.getByTestId("invoice-matter-edit"));
    await pick(user, "invoice-matter-case-select", /ΥΠΘ-2026-0001/);
    await pick(user, "invoice-matter-lawyer-select", "Μαρία Δικηγόρος");
    await user.click(screen.getByTestId("invoice-matter-save"));

    await waitFor(() => expect(patched).not.toBeNull());
    expect(patched).toStrictEqual({ case_id: "case-a", lawyer_id: "u-law2" });          // κανένα άλλο πεδίο του τιμολογίου
    expect(await screen.findByText("Η σύνδεση του τιμολογίου ενημερώθηκε")).toBeInTheDocument();
    expect(onSaved).toHaveBeenCalledTimes(1);
    expect(screen.queryByTestId("invoice-matter-form")).not.toBeInTheDocument();
    expect(screen.getByTestId("invoice-matter-case")).toHaveTextContent("ΥΠΘ-2026-0001");
    expect(screen.getByTestId("invoice-matter-lawyer")).toHaveTextContent("Μαρία Δικηγόρος");
  });

  it("με υπόθεση και χωρίς ρητό δικηγόρο, η κενή επιλογή γράφει «Ο υπεύθυνος της υπόθεσης» και στέλνεται κενό", async () => {
    const user = userEvent.setup();
    serveLookups();
    let patched = null;
    servePatch((body) => { patched = body; return HttpResponse.json(issued({ case_id: "case-a", lawyer_id: "u-law" })); });   // ο server κληρονομεί
    renderEditor(issued());
    await user.click(screen.getByTestId("invoice-matter-edit"));
    expect(await screen.findByTestId("invoice-matter-lawyer-select")).toHaveTextContent("Κανένας");
    await pick(user, "invoice-matter-case-select", /ΥΠΘ-2026-0001/);
    expect(screen.getByTestId("invoice-matter-lawyer-select")).toHaveTextContent("Ο υπεύθυνος της υπόθεσης");
    await user.click(screen.getByTestId("invoice-matter-save"));
    await waitFor(() => expect(patched).not.toBeNull());
    expect(patched).toStrictEqual({ case_id: "case-a", lawyer_id: "" });
    // Η περίληψη δείχνει αυτό που αποθηκεύτηκε στον server (ο υπεύθυνος που κληρονομήθηκε), όχι ό,τι στάλθηκε.
    await waitFor(() => expect(screen.getByTestId("invoice-matter-lawyer")).toHaveTextContent("Χρήστος Σκοτάνης"));
  });

  it("η αφαίρεση της σύνδεσης στέλνει κενά και η περίληψη επιστρέφει σε «—»", async () => {
    const user = userEvent.setup();
    serveLookups();
    let patched = null;
    servePatch((body) => { patched = body; return HttpResponse.json(issued()); });
    renderEditor(issued({ case_id: "case-a", lawyer_id: "u-law2" }));
    await waitFor(() => expect(screen.getByTestId("invoice-matter-case")).toHaveTextContent("ΥΠΘ-2026-0001"));
    await user.click(screen.getByTestId("invoice-matter-edit"));
    await pick(user, "invoice-matter-case-select", "Καμία");
    await pick(user, "invoice-matter-lawyer-select", "Κανένας");
    await user.click(screen.getByTestId("invoice-matter-save"));
    await waitFor(() => expect(patched).not.toBeNull());
    expect(patched).toStrictEqual({ case_id: "", lawyer_id: "" });
    await waitFor(() => expect(screen.getByTestId("invoice-matter-case")).toHaveTextContent("—"));
    expect(screen.getByTestId("invoice-matter-lawyer")).toHaveTextContent("—");
  });

  it("η «Ακύρωση» πετά τις αλλαγές χωρίς κλήση στον server, και το επόμενο άνοιγμα ξεκινά από την αποθηκευμένη σύνδεση", async () => {
    const user = userEvent.setup();
    serveLookups();
    let patches = 0;
    servePatch(() => { patches += 1; return HttpResponse.json(issued()); });
    const onSaved = vi.fn();
    renderEditor(issued({ case_id: "case-a" }), onSaved);
    await waitFor(() => expect(screen.getByTestId("invoice-matter-case")).toHaveTextContent("ΥΠΘ-2026-0001"));
    await user.click(screen.getByTestId("invoice-matter-edit"));
    await pick(user, "invoice-matter-case-select", /ΥΠΘ-2026-0002/);
    await user.click(screen.getByRole("button", { name: "Ακύρωση" }));
    expect(screen.getByTestId("invoice-matter-case")).toHaveTextContent("ΥΠΘ-2026-0001");
    await user.click(screen.getByTestId("invoice-matter-edit"));
    expect(await screen.findByTestId("invoice-matter-case-select")).toHaveTextContent("ΥΠΘ-2026-0001");
    expect(patches).toBe(0);
    expect(onSaved).not.toHaveBeenCalled();
  });

  it("η απόρριψη από τον server εμφανίζεται, η φόρμα μένει ανοιχτή με ό,τι επιλέχθηκε και ο γονέας δεν ειδοποιείται", async () => {
    const user = userEvent.setup();
    serveLookups();
    servePatch(() => HttpResponse.json({ detail: "Ο χρήστης που επιλέχθηκε δεν είναι δικηγόρος του γραφείου." }, { status: 400 }));
    const onSaved = vi.fn();
    renderEditor(issued(), onSaved);
    await user.click(screen.getByTestId("invoice-matter-edit"));
    await pick(user, "invoice-matter-lawyer-select", "Μαρία Δικηγόρος");
    await user.click(screen.getByTestId("invoice-matter-save"));
    expect(await screen.findByText("Ο χρήστης που επιλέχθηκε δεν είναι δικηγόρος του γραφείου.")).toBeInTheDocument();
    expect(screen.getByTestId("invoice-matter-form")).toBeInTheDocument();
    expect(screen.getByTestId("invoice-matter-lawyer-select")).toHaveTextContent("Μαρία Δικηγόρος");
    expect(onSaved).not.toHaveBeenCalled();
    expect(screen.getByTestId("invoice-matter-save")).toBeEnabled();                  // ξαναδοκιμάζεται
  });

  it("το διπλό κλικ στην αποθήκευση στέλνει ένα μόνο αίτημα", async () => {
    const user = userEvent.setup();
    serveLookups();
    let patches = 0;
    let release;
    const gate = new Promise((resolve) => { release = resolve; });
    server.use(http.patch(`${API}/invoices/i1/matter`, async () => {
      patches += 1;
      await gate;
      return HttpResponse.json(issued({ lawyer_id: "u-law2" }));
    }));
    renderEditor(issued());
    await user.click(screen.getByTestId("invoice-matter-edit"));
    await pick(user, "invoice-matter-lawyer-select", "Μαρία Δικηγόρος");
    const save = screen.getByTestId("invoice-matter-save");
    await user.click(save);
    await user.click(save);
    expect(save).toBeDisabled();
    expect(patches).toBe(1);
    release();
    await waitFor(() => expect(screen.queryByTestId("invoice-matter-form")).not.toBeInTheDocument());
    expect(patches).toBe(1);
  });

  it("αν η λίστα υποθέσεων δεν φορτώσει, το δηλώνει και η αλλαγή δικηγόρου εξακολουθεί να είναι δυνατή", async () => {
    const user = userEvent.setup();
    serveLookups({ cases: "fail", clients: [], users: USERS });
    let patched = null;
    servePatch((body) => { patched = body; return HttpResponse.json(issued({ lawyer_id: "u-law2" })); });
    renderEditor(issued());
    await user.click(screen.getByTestId("invoice-matter-edit"));
    expect(await screen.findByText("Η λίστα υποθέσεων δεν φορτώθηκε.")).toBeInTheDocument();
    await pick(user, "invoice-matter-lawyer-select", "Μαρία Δικηγόρος");
    await user.click(screen.getByTestId("invoice-matter-save"));
    await waitFor(() => expect(patched).not.toBeNull());
    expect(patched).toStrictEqual({ case_id: "", lawyer_id: "u-law2" });
  });

  it("ο λογιστής του γραφείου δεν προσφέρεται ως δικηγόρος", async () => {
    const user = userEvent.setup();
    serveLookups();
    renderEditor(issued());
    await user.click(screen.getByTestId("invoice-matter-edit"));
    await user.click(await screen.findByTestId("invoice-matter-lawyer-select"));
    expect(await screen.findByRole("option", { name: "Μαρία Δικηγόρος" })).toBeInTheDocument();
    expect(screen.queryByRole("option", { name: "Χριστόφορος Λογιστής" })).not.toBeInTheDocument();
  });
});

describe("Επεξεργαστής υπόθεσης μέσα στο παράθυρο του τιμολογίου", () => {
  function serveInvoices(rows) {
    let listed = 0;
    server.use(
      http.get(`${API}/invoices`, () => { listed += 1; return HttpResponse.json(rows); }),
    );
    return () => listed;
  }

  it("εκδοθέν τιμολόγιο: η υπόθεση μπαίνει από το παράθυρο λεπτομερειών και η λίστα ξαναφορτώνει", async () => {
    const user = userEvent.setup();
    serveLookups();
    const listed = serveInvoices([issued()]);
    let patched = null;
    servePatch((body) => { patched = body; return HttpResponse.json(issued({ case_id: "case-b", lawyer_id: "" })); });
    render(<><Invoices /><Toaster /></>);

    await user.click(await screen.findByTestId("view-invoice-i1"));
    const dialog = await screen.findByTestId("invoice-detail-dialog");
    expect(within(dialog).getByTestId("invoice-matter-case")).toHaveTextContent("—");
    const before = listed();

    await user.click(within(dialog).getByTestId("invoice-matter-edit"));
    await pick(user, "invoice-matter-case-select", /ΥΠΘ-2026-0002/);
    await user.click(within(dialog).getByTestId("invoice-matter-save"));

    await waitFor(() => expect(patched).toStrictEqual({ case_id: "case-b", lawyer_id: "" }));
    await waitFor(() => expect(listed()).toBe(before + 1));                            // η λίστα ξαναφορτώνει μετά την αποθήκευση
    expect(within(dialog).getByTestId("invoice-matter-case")).toHaveTextContent("ΥΠΘ-2026-0002");
  });

  it("το παράθυρο ενός άλλου τιμολογίου δεν κληρονομεί την κατάσταση του προηγούμενου", async () => {
    const user = userEvent.setup();
    serveLookups();
    serveInvoices([issued({ id: "i1", case_id: "case-a" }), issued({ id: "i2", number: "ΤΠΥ-2026-0002" })]);
    render(<><Invoices /><Toaster /></>);

    await user.click(await screen.findByTestId("view-invoice-i1"));
    await waitFor(() => expect(screen.getByTestId("invoice-matter-case")).toHaveTextContent("ΥΠΘ-2026-0001"));
    await user.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByTestId("invoice-detail-dialog")).not.toBeInTheDocument());

    await user.click(await screen.findByTestId("view-invoice-i2"));
    await waitFor(() => expect(screen.getByTestId("invoice-matter-case")).toHaveTextContent("—"));
  });
});

describe("Παράθυρο λεπτομερειών — αλλαγή τιμολογίου ενώ είναι ανοιχτό", () => {
  it("ο επεξεργαστής δεν κρατά τη σύνδεση (ούτε μισοτελειωμένη αλλαγή) του προηγούμενου τιμολογίου", async () => {
    const user = userEvent.setup();
    serveLookups();
    const dialog = (invoice) => (
      <><InvoiceDetailDialog invoice={invoice} open onOpenChange={() => {}} onEmailSent={() => {}} /><Toaster /></>
    );
    const { rerender } = render(dialog(issued({ id: "i1", case_id: "case-a", lawyer_id: "u-law2" })));
    await waitFor(() => expect(screen.getByTestId("invoice-matter-case")).toHaveTextContent("ΥΠΘ-2026-0001"));
    await user.click(screen.getByTestId("invoice-matter-edit"));                  // μισοτελειωμένη αλλαγή στο πρώτο τιμολόγιο
    expect(await screen.findByTestId("invoice-matter-form")).toBeInTheDocument();

    rerender(dialog(issued({ id: "i2", number: "ΤΠΥ-2026-0002", case_id: "", lawyer_id: "" })));

    expect(screen.queryByTestId("invoice-matter-form")).not.toBeInTheDocument();
    expect(screen.getByTestId("invoice-matter-case")).toHaveTextContent("—");
    expect(screen.getByTestId("invoice-matter-lawyer")).toHaveTextContent("—");
  });
});
