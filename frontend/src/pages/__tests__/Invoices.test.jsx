/**
 * Ο αριθμός ενός παραστατικού δίνεται στην έκδοση (βλ. backend/invoice_services.issue_draft).
 * Το πρόχειρο έρχεται από το API με `number: ""` και η οθόνη πρέπει να το δείχνει ως
 * «Πρόχειρο», όχι ως κενό κελί ή «undefined».
 */
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { Toaster } from "@/components/ui/sonner";
import Invoices from "@/pages/Invoices";
import { invoiceLabel, owedAmount, todayAthens, unpaidTotal } from "@/lib/utils";
import { server } from "@/test/server";

const API = "http://localhost:8001/api";

const base = {
  client_id: "c1", client_name: "ΑΛΦΑ ΑΕ", date: "2026-10-12", line_items: [],
  subtotal: 100, vat_amount: 24, total: 124, mydata_status: "not_sent",
};

describe("Τιμολόγια — αρίθμηση στην έκδοση", () => {
  it("δείχνει «Πρόχειρο» στο πρόχειρο και τον αριθμό στο εκδοθέν", async () => {
    server.use(
      http.get(`${API}/invoices`, () =>
        HttpResponse.json([
          { ...base, id: "d1", number: "", status: "draft" },
          { ...base, id: "i1", number: "ΤΠΥ-2026-0001", status: "issued", mydata_status: "synced" },
        ]),
      ),
      http.get(`${API}/clients`, () => HttpResponse.json([])),
    );
    render(<><Invoices /><Toaster /></>);

    const draftRow = await screen.findByTestId("invoice-row-d1");
    expect(within(draftRow).getAllByRole("cell")[0]).toHaveTextContent("Πρόχειρο");
    const issuedRow = await screen.findByTestId("invoice-row-i1");
    expect(within(issuedRow).getAllByRole("cell")[0]).toHaveTextContent("ΤΠΥ-2026-0001");
    expect(screen.queryByText("undefined")).not.toBeInTheDocument();
  });

  it("η ετικέτα ακολουθεί τον αριθμό όταν υπάρχει", () => {
    expect(invoiceLabel({ number: "ΑΠΥ-2026-0003" })).toBe("ΑΠΥ-2026-0003");
    expect(invoiceLabel({ number: "" })).toBe("Πρόχειρο");
    expect(invoiceLabel(null)).toBe("Πρόχειρο");
  });

  it("η προεπιλεγμένη ημερομηνία είναι μορφής ΕΕΕΕ-ΜΜ-ΗΗ", () => {
    expect(todayAthens()).toMatch(/^\d{4}-\d{2}-\d{2}$/);
  });
});

describe("Τιμολόγια — νέα φόρμα", () => {
  afterEach(() => vi.useRealTimers());

  it("η προεπιλεγμένη ημερομηνία είναι η σημερινή στην Αθήνα, όχι η ημερομηνία UTC", async () => {
    // 01:30 τα ξημερώματα της 12ης Οκτωβρίου στην Αθήνα = 22:30 της 11ης σε UTC
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date("2026-10-11T22:30:00Z"));
    const user = userEvent.setup({ pointerEventsCheck: 0 });
    server.use(
      http.get(`${API}/invoices`, () => HttpResponse.json([])),
      http.get(`${API}/clients`, () => HttpResponse.json([])),
    );
    render(<><Invoices /><Toaster /></>);
    await user.click(await screen.findByTestId("new-invoice-button"));
    expect(await screen.findByDisplayValue("2026-10-12")).toBeInTheDocument();
    expect(screen.queryByDisplayValue("2026-10-11")).not.toBeInTheDocument();
  });
});

describe("Τιμολόγια — έκδοση", () => {
  const draft = (id) => ({ ...base, id, number: "", status: "draft" });
  const issuedReply = (over = {}) => ({
    ...base, id: "d1", number: "ΤΠΥ-2026-0001", status: "issued", mydata_status: "synced", mydata_mark: "400001234567890", ...over,
  });

  beforeEach(() => {
    // η λήψη PDF μετά την έκδοση χρειάζεται URL.createObjectURL και «κλικ» σε σύνδεσμο — δεν υπάρχουν στο jsdom
    URL.createObjectURL = vi.fn(() => "blob:test");
    URL.revokeObjectURL = vi.fn();
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
  });
  afterEach(() => vi.restoreAllMocks());

  function handlers(issue, listed = [draft("d1"), draft("d2")]) {
    server.use(
      http.get(`${API}/invoices`, () => HttpResponse.json(listed)),
      http.get(`${API}/clients`, () => HttpResponse.json([])),
      http.post(`${API}/invoices/:id/issue`, issue),
      http.get(`${API}/invoices/:id/pdf`, () =>
        new HttpResponse(new Blob(["%PDF-1.4"]), { headers: { "Content-Type": "application/pdf" } })),
    );
  }

  it("το διπλό κλικ στην «Έκδοση» στέλνει ένα μόνο αίτημα και κλειδώνει τις εκδόσεις όσο τρέχει", async () => {
    const user = userEvent.setup();
    let calls = 0;
    let release;
    const gate = new Promise((resolve) => { release = resolve; });
    handlers(async () => {
      calls += 1;
      await gate;
      return HttpResponse.json(issuedReply());
    });
    render(<><Invoices /><Toaster /></>);

    const first = await screen.findByTestId("issue-invoice-d1");
    await user.click(first);
    await user.click(first);                                   // «νευρικό» δεύτερο κλικ
    expect(first).toBeDisabled();
    expect(screen.getByTestId("issue-invoice-d2")).toBeDisabled();   // ούτε άλλο πρόχειρο όσο τρέχει η έκδοση
    expect(calls).toBe(1);

    release();
    expect(await screen.findByText(/εκδόθηκε και διαβιβάστηκε/)).toBeInTheDocument();
    expect(calls).toBe(1);
    await waitFor(() => expect(screen.getByTestId("issue-invoice-d2")).toBeEnabled());   // ξεκλειδώνει μετά το τέλος
  });

  it("ξεκλειδώνει και δείχνει το σφάλμα όταν η έκδοση αρνηθεί", async () => {
    const user = userEvent.setup();
    handlers(() => HttpResponse.json({ detail: "Η ημερομηνία δεν μπορεί να είναι μελλοντική" }, { status: 400 }));
    render(<><Invoices /><Toaster /></>);
    await user.click(await screen.findByTestId("issue-invoice-d1"));
    expect(await screen.findByText(/δεν μπορεί να είναι μελλοντική/)).toBeInTheDocument();
    await waitFor(() => expect(screen.getByTestId("issue-invoice-d1")).toBeEnabled());
  });

  it.each([
    ["πραγματικό MARK", { mydata_status: "synced", mydata_mark: "400001234567890" }, /διαβιβάστηκε στο myDATA$/],
    ["προσομοίωση", { mydata_status: "synced", mydata_mark: "MOCK-1234" }, /\(προσομοίωση\) στο myDATA/],
    ["εκκρεμεί η διαβίβαση", { mydata_status: "pending", mydata_mark: null }, /διαβίβαση στο myDATA εκκρεμεί/],
  ])("το μήνυμα της έκδοσης δεν υπόσχεται διαβίβαση που δεν έγινε (%s)", async (_label, reply, expected) => {
    const user = userEvent.setup();
    handlers(() => HttpResponse.json(issuedReply(reply)), [draft("d1")]);
    render(<><Invoices /><Toaster /></>);
    await user.click(await screen.findByTestId("issue-invoice-d1"));
    const toast = await screen.findByText(expected);
    expect(toast).toBeInTheDocument();
  });
});

describe("Τιμολόγια — πιστωτικό", () => {
  beforeEach(() => vi.spyOn(window, "confirm").mockReturnValue(true));
  afterEach(() => vi.restoreAllMocks());

  it("δημιουργεί πρόχειρο πιστωτικό και το λέει καθαρά (δεν εκδίδεται με το κλικ)", async () => {
    const user = userEvent.setup();
    let posted = 0;
    server.use(
      http.get(`${API}/invoices`, () => HttpResponse.json([
        { ...base, id: "p1", number: "ΤΠΥ-2026-0001", status: "paid", mydata_status: "synced", mydata_mark: "400001234567890" },
      ])),
      http.get(`${API}/clients`, () => HttpResponse.json([])),
      http.post(`${API}/invoices/p1/credit-note`, () => {
        posted += 1;
        return HttpResponse.json({ ...base, id: "cn1", number: "", status: "draft", total: -124 });
      }),
    );
    render(<><Invoices /><Toaster /></>);
    const button = await screen.findByTestId("credit-note-p1");
    expect(button).toHaveAttribute("title", expect.stringContaining("πρόχειρου πιστωτικού"));   // όχι «Έκδοση πιστωτικού»
    await user.click(button);
    expect(await screen.findByText(/Δημιουργήθηκε πρόχειρο πιστωτικό/)).toBeInTheDocument();
    expect(posted).toBe(1);
    expect(window.confirm).toHaveBeenCalledWith(expect.stringContaining("ΤΠΥ-2026-0001"));
  });
});

describe("Τιμολόγια — ανείσπρακτα", () => {
  const issued = (id, client, total, outstanding, over = {}) => ({
    ...base, id, number: `ΤΠΥ-2026-000${id.slice(1)}`, status: "issued", client_id: client, total, outstanding, ...over,
  });

  function listed(rows) {
    server.use(
      http.get(`${API}/invoices`, () => HttpResponse.json(rows)),
      http.get(`${API}/clients`, () => HttpResponse.json([])),
    );
  }

  it("το KPI «Ανείσπρακτα» δείχνει ό,τι πράγματι οφείλεται, όχι το μεικτό σύνολο", async () => {
    // 1.240 με παρακράτηση 200 και 400 ήδη εξοφλημένα από πελατολογικό → οφείλονται 640
    listed([issued("i1", "c1", 1240, 640), issued("i2", "c2", 124, 124), { ...base, id: "p1", number: "ΤΠΥ-2026-0003", status: "paid", total: 500, outstanding: 0 }]);
    render(<><Invoices /><Toaster /></>);
    expect(await screen.findByTestId("kpi-unpaid-total")).toHaveTextContent(/764,00/);
    expect(screen.getByTestId("kpi-unpaid-count")).toHaveTextContent("2");
    expect(screen.getByTestId("kpi-unpaid-total")).not.toHaveTextContent(/1\.364/);
  });

  it("δείχνει «Οφείλεται» μόνο στη γραμμή όπου το ποσό διαφέρει από το σύνολο", async () => {
    listed([issued("i1", "c1", 1240, 640), issued("i2", "c2", 124, 124)]);
    render(<><Invoices /><Toaster /></>);
    expect(await screen.findByTestId("due-i1")).toHaveTextContent(/Οφείλεται:\s*640,00/);
    expect(screen.queryByTestId("due-i2")).not.toBeInTheDocument();
  });

  it("το πιστωτικό συμψηφίζεται με τον ίδιο πελάτη και δεν δείχνει «Οφείλεται»", async () => {
    listed([issued("i1", "c1", 1240, 1240), issued("i2", "c1", -496, -496, { credit_note_for: "i1" })]);
    render(<><Invoices /><Toaster /></>);
    expect(await screen.findByTestId("kpi-unpaid-total")).toHaveTextContent(/744,00/);
    expect(screen.queryByTestId("due-i2")).not.toBeInTheDocument();
  });

  it("server παλαιότερης έκδοσης (χωρίς «outstanding»): το σύνολο όπως πριν, χωρίς σφάλμα", async () => {
    listed([{ ...base, id: "i1", number: "ΤΠΥ-2026-0001", status: "issued", client_id: "c1", total: 124 }]);
    render(<><Invoices /><Toaster /></>);
    expect(await screen.findByTestId("kpi-unpaid-total")).toHaveTextContent(/124,00/);
    expect(screen.queryByTestId("due-i1")).not.toBeInTheDocument();
  });
});

describe("Τιμολόγια — κομμένα εξοφλημένα", () => {
  const issued = { ...base, id: "i1", number: "ΤΠΥ-2019-0001", status: "issued", mydata_status: "synced", outstanding: 124 };

  it("όταν ο server δηλώσει ότι κόπηκαν παλαιότερα εξοφλημένα, το λέει και ξεκαθαρίζει ποια σύνολα είναι πλήρη", async () => {
    server.use(
      http.get(`${API}/invoices`, () => HttpResponse.json([issued], { headers: { "X-Closed-Truncated": "1" } })),
      http.get(`${API}/clients`, () => HttpResponse.json([])),
    );
    render(<><Invoices /><Toaster /></>);
    const notice = await screen.findByTestId("closed-truncated-notice");
    expect(notice).toHaveTextContent("τελευταία 1.000 εξοφλημένα");
    expect(notice).toHaveTextContent("«Ανείσπρακτα» είναι πλήρη");
    expect(screen.getByTestId("kpi-unpaid-total")).toHaveTextContent("124,00");
  });

  it("χωρίς την κεφαλίδα δεν δείχνει καμία ειδοποίηση", async () => {
    server.use(
      http.get(`${API}/invoices`, () => HttpResponse.json([issued])),
      http.get(`${API}/clients`, () => HttpResponse.json([])),
    );
    render(<><Invoices /><Toaster /></>);
    await screen.findByTestId("invoice-row-i1");
    expect(screen.queryByTestId("closed-truncated-notice")).not.toBeInTheDocument();
  });
});

describe("unpaidTotal / owedAmount", () => {
  const row = (client, total, outstanding, status = "issued") => ({ client_id: client, total, outstanding, status });

  it("αθροίζει το οφειλόμενο των εκδοθέντων, όχι το σύνολο", () => {
    expect(unpaidTotal([row("a", 1240, 640), row("b", 124, 124)])).toBe(764);
  });

  it("αγνοεί ό,τι δεν είναι εκδοθέν", () => {
    expect(unpaidTotal([row("a", 100, 100, "paid"), row("a", 100, 100, "draft"), row("a", 100, 100, "cancelled")])).toBe(0);
  });

  it("το πιστωτικό ενός πελάτη δεν μειώνει το χρέος άλλου και δεν δίνει αρνητικό υπόλοιπο", () => {
    expect(unpaidTotal([row("a", 1240, 1240), row("b", -248, -248)])).toBe(1240);
    expect(unpaidTotal([row("b", -248, -248)])).toBe(0);
    expect(unpaidTotal([row("a", 1240, 1240), row("a", -496, -496)])).toBe(744);
  });

  it("στρογγυλεύει σε λεπτά χωρίς σφάλματα κινητής υποδιαστολής", () => {
    expect(unpaidTotal([row("a", 0.1, 0.1), row("a", 0.2, 0.2)])).toBe(0.3);
  });

  it("το owedAmount πέφτει στο σύνολο όταν λείπει το outstanding, αλλά σέβεται το μηδέν", () => {
    expect(owedAmount({ total: 124 })).toBe(124);
    expect(owedAmount({ total: 124, outstanding: 0 })).toBe(0);
    expect(owedAmount(null)).toBe(0);
  });
});
