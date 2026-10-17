/**
 * Φάση 1 «ψηφιακού λογιστή» — έξοδα: σε ποιον ανήκουν και πόσο ΦΠΑ τους εκπίπτει.
 *
 * Η σελίδα στέλνει στον server ό,τι δείχνει η φόρμα, και ο server (backend/dimensions.py) είναι η πηγή της αλήθειας.
 * Εδώ ελέγχεται ότι η φόρμα δεν μπορεί να φτιάξει αντιφατικό συνδυασμό (υπόθεση σε γενικό έξοδο), ότι η αλλαγή εύρους
 * καθαρίζει ό,τι δεν ισχύει πια και ότι το κενό ποσοστό εκπτωσιμότητας σημαίνει «ακολούθησε τον κανόνα», όχι 0.
 */
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { describe, expect, it, vi } from "vitest";

import { Toaster } from "@/components/ui/sonner";
import Expenses from "@/pages/Expenses";
import { CASE_A, CASE_B, CLIENT_A, CLIENT_B, USERS, lookupHandlers } from "@/test/lookupFixtures";
import { server } from "@/test/server";

const API = "http://localhost:8001/api";

const expense = (over = {}) => ({
  id: "e1", vendor: "Ταξί Αθηνών", date: "2026-10-05", amount: 124, vat_amount: 24, vat_rate: 24, category: "Μεταφορικά",
  description: "", source: "manual", receipt_image_url: null,
  scope: "unassigned", case_id: "", client_id: "", lawyer_id: "", rebillable: false, vat_deductible_pct: null, ...over,
});

const LOOKUPS = { cases: [CASE_A, CASE_B], clients: [CLIENT_A, CLIENT_B], users: USERS };

function serve(listed, extra = []) {
  server.use(
    ...lookupHandlers(LOOKUPS),
    http.get(`${API}/expenses`, () => HttpResponse.json(listed)),
    ...extra,
  );
}

async function pick(user, triggerId, optionName) {
  await user.click(await screen.findByTestId(triggerId));
  await user.click(await screen.findByRole("option", { name: optionName }));
}

describe("Έξοδα — αντιστοίχιση σε υπόθεση / πελάτη / δικηγόρο", () => {
  it("το έξοδο υπόθεσης στέλνει εύρος, υπόθεση και ρητό ποσοστό — χωρίς πελάτη, που τον ορίζει ο server από την υπόθεση", async () => {
    const user = userEvent.setup();
    let posted = null;
    serve([], [http.post(`${API}/expenses`, async ({ request }) => { posted = await request.json(); return HttpResponse.json({ id: "e9", ...posted }); })]);
    render(<><Expenses /><Toaster /></>);

    await user.click(await screen.findByTestId("new-expense-button"));
    await user.type(screen.getByTestId("expense-vendor-input"), "Ταξί");
    await pick(user, "expense-scope-select", "Υπόθεση");
    await pick(user, "expense-case-select", /ΥΠΘ-2026-0001/);
    expect(await screen.findByTestId("expense-derived-client")).toHaveTextContent("ΑΛΦΑ ΑΕ");
    await user.type(screen.getByTestId("expense-vat-deductible-input"), "50");
    await user.click(screen.getByTestId("save-expense-button"));

    await waitFor(() => expect(posted).not.toBeNull());
    expect(posted).toMatchObject({ scope: "case", case_id: "case-a", client_id: "", lawyer_id: "", rebillable: false, vat_deductible_pct: 50 });
  });

  it("το νέο έξοδο ξεκινά ως «δεν έχει ταξινομηθεί», χωρίς υπόθεση, πελάτη, δικηγόρο ή ποσοστό", async () => {
    const user = userEvent.setup();
    let posted = null;
    serve([], [http.post(`${API}/expenses`, async ({ request }) => { posted = await request.json(); return HttpResponse.json({ id: "e9", ...posted }); })]);
    render(<><Expenses /><Toaster /></>);

    await user.click(await screen.findByTestId("new-expense-button"));
    await user.type(screen.getByTestId("expense-vendor-input"), "Προμηθευτής");
    expect(screen.queryByTestId("expense-case-select")).not.toBeInTheDocument();
    expect(screen.queryByTestId("expense-client-select")).not.toBeInTheDocument();
    expect(screen.queryByTestId("expense-rebillable")).not.toBeInTheDocument();      // δεν χρεώνεται έξοδο χωρίς πελάτη
    await user.click(screen.getByTestId("save-expense-button"));

    await waitFor(() => expect(posted).not.toBeNull());
    expect(posted).toMatchObject({ scope: "unassigned", case_id: "", client_id: "", lawyer_id: "", rebillable: false, vat_deductible_pct: null });
  });

  it("η αλλαγή εύρους καθαρίζει ό,τι δεν ισχύει πια: από «Υπόθεση» σε «Γενικό έξοδο γραφείου» φεύγουν υπόθεση, πελάτης και χρέωση", async () => {
    const user = userEvent.setup();
    let put = null;
    serve([expense({ scope: "case", case_id: "case-a", client_id: "client-a", rebillable: true })],
      [http.put(`${API}/expenses/e1`, async ({ request }) => { put = await request.json(); return HttpResponse.json(put); })]);
    render(<><Expenses /><Toaster /></>);

    await user.click(await screen.findByTestId("edit-expense-e1"));
    expect(await screen.findByTestId("expense-rebillable")).toBeChecked();
    await pick(user, "expense-scope-select", "Γενικό έξοδο γραφείου");
    expect(screen.queryByTestId("expense-case-select")).not.toBeInTheDocument();
    expect(screen.queryByTestId("expense-rebillable")).not.toBeInTheDocument();
    await user.click(screen.getByTestId("save-expense-button"));

    await waitFor(() => expect(put).not.toBeNull());
    expect(put).toMatchObject({ scope: "office", case_id: "", client_id: "", rebillable: false });
  });

  it("το έξοδο πελάτη (χωρίς υπόθεση) στέλνει τον πελάτη και μπορεί να χρεωθεί", async () => {
    const user = userEvent.setup();
    let posted = null;
    serve([], [http.post(`${API}/expenses`, async ({ request }) => { posted = await request.json(); return HttpResponse.json({ id: "e9", ...posted }); })]);
    render(<><Expenses /><Toaster /></>);

    await user.click(await screen.findByTestId("new-expense-button"));
    await user.type(screen.getByTestId("expense-vendor-input"), "Συμβολαιογράφος");
    await pick(user, "expense-scope-select", "Πελάτης");
    await pick(user, "expense-client-select", "Β. Παπαδόπουλος");
    await user.click(screen.getByTestId("expense-rebillable"));
    await user.click(screen.getByTestId("save-expense-button"));

    await waitFor(() => expect(posted).not.toBeNull());
    expect(posted).toMatchObject({ scope: "client", client_id: "client-b", case_id: "", rebillable: true });
  });

  it("η λίστα δικηγόρων έχει μόνο δικηγόρους και διαχειριστές — όχι τον λογιστή", async () => {
    const user = userEvent.setup();
    serve([]);
    render(<><Expenses /><Toaster /></>);

    await user.click(await screen.findByTestId("new-expense-button"));
    await user.click(await screen.findByTestId("expense-lawyer-select"));
    expect(await screen.findByRole("option", { name: "Χρήστος Σκοτάνης" })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "Μαρία Δικηγόρος" })).toBeInTheDocument();
    expect(screen.queryByRole("option", { name: "Χριστόφορος Λογιστής" })).not.toBeInTheDocument();
  });

  it("αν δεν φορτώσει η λίστα υποθέσεων, η φόρμα το δηλώνει αντί να δείχνει σιωπηλά άδεια λίστα", async () => {
    const user = userEvent.setup();
    server.use(
      ...lookupHandlers({ cases: "fail", clients: [CLIENT_A], users: USERS }),
      http.get(`${API}/expenses`, () => HttpResponse.json([])),
    );
    render(<><Expenses /><Toaster /></>);

    await user.click(await screen.findByTestId("new-expense-button"));
    await pick(user, "expense-scope-select", "Υπόθεση");
    expect(await screen.findByText("Η λίστα υποθέσεων δεν φορτώθηκε.")).toBeInTheDocument();
  });
});

describe("Έξοδα — εκπτωσιμότητα ΦΠΑ στη φόρμα", () => {
  it("το κενό πεδίο στέλνει null («ακολούθησε τον κανόνα της κατηγορίας»), όχι 0 που θα σήμαινε «καμία έκπτωση»", async () => {
    const user = userEvent.setup();
    let put = null;
    serve([expense({ vat_deductible_pct: 50 })],
      [http.put(`${API}/expenses/e1`, async ({ request }) => { put = await request.json(); return HttpResponse.json(put); })]);
    render(<><Expenses /><Toaster /></>);

    await user.click(await screen.findByTestId("edit-expense-e1"));
    const input = await screen.findByTestId("expense-vat-deductible-input");
    expect(input).toHaveValue(50);
    await user.clear(input);
    await user.click(screen.getByTestId("save-expense-button"));

    await waitFor(() => expect(put).not.toBeNull());
    expect(put.vat_deductible_pct).toBeNull();
  });

  it("το ρητό 0 είναι απόφαση και στέλνεται ως 0", async () => {
    const user = userEvent.setup();
    let put = null;
    serve([expense()], [http.put(`${API}/expenses/e1`, async ({ request }) => { put = await request.json(); return HttpResponse.json(put); })]);
    render(<><Expenses /><Toaster /></>);

    await user.click(await screen.findByTestId("edit-expense-e1"));
    const input = await screen.findByTestId("expense-vat-deductible-input");
    await user.type(input, "0");
    expect(input).toHaveValue(0);                      // το 0 μένει ορατό — δεν γίνεται κενό, που θα σήμαινε «ακολούθησε τον κανόνα»
    await user.click(screen.getByTestId("save-expense-button"));

    await waitFor(() => expect(put).not.toBeNull());
    expect(put.vat_deductible_pct).toBe(0);
  });

  it("έξοδο με ρητό 0 ανοίγει δείχνοντας 0 (όχι κενό πεδίο)", async () => {
    const user = userEvent.setup();
    serve([expense({ vat_deductible_pct: 0 })]);
    render(<><Expenses /><Toaster /></>);

    await user.click(await screen.findByTestId("edit-expense-e1"));
    expect(await screen.findByTestId("expense-vat-deductible-input")).toHaveValue(0);
  });

  it("έξοδο που ήρθε από παλιότερη έκδοση του API, χωρίς τα νέα πεδία, ανοίγει με ασφαλείς προεπιλογές", async () => {
    const user = userEvent.setup();
    let put = null;
    const legacy = expense();
    ["scope", "case_id", "client_id", "lawyer_id", "rebillable", "vat_deductible_pct"].forEach((k) => delete legacy[k]);
    serve([legacy], [http.put(`${API}/expenses/e1`, async ({ request }) => { put = await request.json(); return HttpResponse.json(put); })]);
    render(<><Expenses /><Toaster /></>);

    await user.click(await screen.findByTestId("edit-expense-e1"));
    expect(await screen.findByTestId("expense-vat-deductible-input")).toHaveValue(null);
    await user.click(screen.getByTestId("save-expense-button"));

    await waitFor(() => expect(put).not.toBeNull());
    expect(put).toMatchObject({ scope: "unassigned", case_id: "", client_id: "", lawyer_id: "", rebillable: false, vat_deductible_pct: null });
  });
});

describe("Έξοδα — στήλη «Αντιστοίχιση»", () => {
  it("δείχνει υπόθεση, πελάτη, γραφείο ή «—» ανά εύρος", async () => {
    serve([
      expense({ id: "e1", scope: "case", case_id: "case-a", client_id: "client-a", rebillable: true }),
      expense({ id: "e2", scope: "client", client_id: "client-b" }),
      expense({ id: "e3", scope: "office" }),
      expense({ id: "e4", scope: "unassigned" }),
    ]);
    render(<><Expenses /><Toaster /></>);

    await waitFor(() => expect(within(screen.getByTestId("expense-allocation-e1")).queryByText(/ΥΠΘ-2026-0001/)).toBeInTheDocument());
    expect(screen.getByTestId("expense-allocation-e1")).toHaveTextContent("ΥΠΘ-2026-0001 — Αγωγή αποζημίωσης");
    expect(screen.getByTestId("expense-allocation-e1")).toHaveTextContent("↻");                    // θα χρεωθεί στον πελάτη
    expect(screen.getByTestId("expense-allocation-e2")).toHaveTextContent("Β. Παπαδόπουλος");
    expect(screen.getByTestId("expense-allocation-e3")).toHaveTextContent("Γραφείο");
    expect(screen.getByTestId("expense-allocation-e4")).toHaveTextContent("—");
    expect(screen.getByTestId("expense-allocation-e4")).not.toHaveTextContent("↻");
  });

  it("αν οι λίστες δεν φορτώσουν, η στήλη δείχνει το εύρος αντί για κενό", async () => {
    server.use(
      ...lookupHandlers({ cases: "fail", clients: "fail", users: "fail" }),
      http.get(`${API}/expenses`, () => HttpResponse.json([
        expense({ id: "e1", scope: "case", case_id: "case-a" }), expense({ id: "e2", scope: "client", client_id: "client-b" })])),
    );
    render(<><Expenses /><Toaster /></>);

    expect(await screen.findByTestId("expense-allocation-e1")).toHaveTextContent("Υπόθεση");
    expect(screen.getByTestId("expense-allocation-e2")).toHaveTextContent("Πελάτης");
  });
});

describe("Έξοδα — ανάγνωση απόδειξης από φωτογραφία", () => {
  it("το έξοδο από φωτογραφία ξεκινά ως «δεν έχει ταξινομηθεί» και αντιστοιχίζεται πριν αποθηκευτεί", async () => {
    const user = userEvent.setup({ applyAccept: false });
    let posted = null;
    serve([], [
      http.post(`${API}/expenses/ocr-parse`, () => HttpResponse.json({
        parsed: { vendor: "Βιβλιοπωλείο", date: "2026-10-05", amount: 12.4, vat_amount: 2.4, vat_rate: 24, category: "Λοιπά" },
        receipt_image_url: "/uploads/x.png",
      })),
      http.post(`${API}/expenses`, async ({ request }) => { posted = await request.json(); return HttpResponse.json({ id: "e9", ...posted }); }),
    ]);
    render(<><Expenses /><Toaster /></>);

    await user.upload(await screen.findByTestId("ocr-file-input"), new File(["x"], "apodeixi.png", { type: "image/png" }));
    expect(await screen.findByDisplayValue("Βιβλιοπωλείο")).toBeInTheDocument();
    expect(screen.getByTestId("expense-scope-select")).toHaveTextContent("Δεν έχει ταξινομηθεί");
    expect(screen.getByTestId("expense-vat-deductible-input")).toHaveValue(null);
    await pick(user, "expense-scope-select", "Γενικό έξοδο γραφείου");
    await user.click(screen.getByTestId("save-expense-button"));

    await waitFor(() => expect(posted).not.toBeNull());
    expect(posted).toMatchObject({
      source: "photo_ocr", vendor: "Βιβλιοπωλείο", scope: "office", case_id: "", client_id: "", lawyer_id: "", rebillable: false,
      vat_deductible_pct: null,
    });
  });
});

describe("Έξοδα — αναφορές που δεν υπάρχουν πια στις λίστες", () => {
  const putHandler = (capture) => http.put(`${API}/expenses/e1`, async ({ request }) => { capture.body = await request.json(); return HttpResponse.json(capture.body); });

  it("υπόθεση και δικηγόρος που λείπουν από τις λίστες φαίνονται ως άγνωστοι και δεν χάνονται σιωπηλά στην αποθήκευση", async () => {
    const user = userEvent.setup();
    const sent = { body: null };
    serve([expense({ scope: "case", case_id: "case-gone", client_id: "client-a", lawyer_id: "u-gone" })], [putHandler(sent)]);
    render(<><Expenses /><Toaster /></>);

    await user.click(await screen.findByTestId("edit-expense-e1"));
    expect(await screen.findByTestId("expense-case-select")).toHaveTextContent("(άγνωστη υπόθεση)");
    expect(screen.getByTestId("expense-lawyer-select")).toHaveTextContent("(άγνωστος χρήστης)");
    await user.click(screen.getByTestId("save-expense-button"));

    await waitFor(() => expect(sent.body).not.toBeNull());
    expect(sent.body).toMatchObject({ scope: "case", case_id: "case-gone", lawyer_id: "u-gone" });
  });

  it("πελάτης που διαγράφηκε φαίνεται ως «(άγνωστος πελάτης)» και δεν χάνεται από το έξοδο", async () => {
    const user = userEvent.setup();
    const sent = { body: null };
    serve([expense({ scope: "client", client_id: "client-gone" })], [putHandler(sent)]);
    render(<><Expenses /><Toaster /></>);

    await user.click(await screen.findByTestId("edit-expense-e1"));
    expect(await screen.findByTestId("expense-client-select")).toHaveTextContent("(άγνωστος πελάτης)");
    await user.click(screen.getByTestId("save-expense-button"));

    await waitFor(() => expect(sent.body).not.toBeNull());
    expect(sent.body).toMatchObject({ scope: "client", client_id: "client-gone" });
  });
});

describe("Έξοδα — επιλογή δικηγόρου", () => {
  const putHandler = (capture) => http.put(`${API}/expenses/e1`, async ({ request }) => { capture.body = await request.json(); return HttpResponse.json(capture.body); });

  it("η επιλογή «Κανένας» καθαρίζει τον δικηγόρο και στέλνει κενό — όχι το εσωτερικό σύμβολο της λίστας", async () => {
    const user = userEvent.setup();
    const sent = { body: null };
    serve([expense({ lawyer_id: "u-law2" })], [putHandler(sent)]);
    render(<><Expenses /><Toaster /></>);

    await user.click(await screen.findByTestId("edit-expense-e1"));
    expect(await screen.findByTestId("expense-lawyer-select")).toHaveTextContent("Μαρία Δικηγόρος");
    await pick(user, "expense-lawyer-select", "Κανένας");
    expect(screen.getByTestId("expense-lawyer-select")).toHaveTextContent("Κανένας");
    await user.click(screen.getByTestId("save-expense-button"));

    await waitFor(() => expect(sent.body).not.toBeNull());
    expect(sent.body.lawyer_id).toBe("");
  });

  it("στο εύρος «Υπόθεση» η επιλογή χωρίς δικηγόρο λέγεται «Ο υπεύθυνος της υπόθεσης» και στέλνει κενό (τον ορίζει ο server)", async () => {
    const user = userEvent.setup();
    const sent = { body: null };
    serve([expense({ scope: "case", case_id: "case-a", client_id: "client-a", lawyer_id: "u-law2" })], [putHandler(sent)]);
    render(<><Expenses /><Toaster /></>);

    await user.click(await screen.findByTestId("edit-expense-e1"));
    await user.click(await screen.findByTestId("expense-lawyer-select"));
    expect(screen.queryByRole("option", { name: "Κανένας" })).not.toBeInTheDocument();
    await user.click(await screen.findByRole("option", { name: "Ο υπεύθυνος της υπόθεσης" }));
    await user.click(screen.getByTestId("save-expense-button"));

    await waitFor(() => expect(sent.body).not.toBeNull());
    expect(sent.body.lawyer_id).toBe("");
  });

  it("σε άλλο εύρος η ίδια επιλογή λέγεται απλώς «Κανένας»", async () => {
    const user = userEvent.setup();
    serve([expense({ scope: "office" })]);
    render(<><Expenses /><Toaster /></>);

    await user.click(await screen.findByTestId("edit-expense-e1"));
    await user.click(await screen.findByTestId("expense-lawyer-select"));
    expect(await screen.findByRole("option", { name: "Κανένας" })).toBeInTheDocument();
    expect(screen.queryByRole("option", { name: "Ο υπεύθυνος της υπόθεσης" })).not.toBeInTheDocument();
  });
});

describe("Έξοδα — σφάλμα φόρτωσης λιστών", () => {
  it("αν δεν φορτώσει η λίστα πελατών, η φόρμα το δηλώνει στο εύρος «Πελάτης»", async () => {
    const user = userEvent.setup();
    server.use(
      ...lookupHandlers({ cases: [CASE_A], clients: "fail", users: USERS }),
      http.get(`${API}/expenses`, () => HttpResponse.json([])),
    );
    render(<><Expenses /><Toaster /></>);

    await user.click(await screen.findByTestId("new-expense-button"));
    await pick(user, "expense-scope-select", "Πελάτης");
    expect(await screen.findByText("Η λίστα πελατών δεν φορτώθηκε.")).toBeInTheDocument();
    expect(screen.queryByText("Η λίστα υποθέσεων δεν φορτώθηκε.")).not.toBeInTheDocument();
  });

  it("αν δεν φορτώσει η λίστα δικηγόρων, η φόρμα το δηλώνει", async () => {
    const user = userEvent.setup();
    server.use(
      ...lookupHandlers({ cases: [CASE_A], clients: [CLIENT_A], users: "fail" }),
      http.get(`${API}/expenses`, () => HttpResponse.json([])),
    );
    render(<><Expenses /><Toaster /></>);

    await user.click(await screen.findByTestId("new-expense-button"));
    expect(await screen.findByText("Η λίστα δικηγόρων δεν φορτώθηκε.")).toBeInTheDocument();
  });
});

describe("Έξοδα — προειδοποιήσεις του React στη φόρμα αντιστοίχισης", () => {
  it("η επιλογή υπόθεσης και πελάτη δεν προκαλεί προειδοποίηση «από ανεξέλεγκτο σε ελεγχόμενο» (την είδε ο browser στο smoke test)", async () => {
    const user = userEvent.setup();
    const logged = [];
    const record = (...args) => { logged.push(args.map(String).join(" ")); };
    const warn = vi.spyOn(console, "warn").mockImplementation(record);
    const error = vi.spyOn(console, "error").mockImplementation(record);
    try {
      serve([]);
      render(<Expenses />);

      await user.click(await screen.findByTestId("new-expense-button"));
      await pick(user, "expense-scope-select", "Υπόθεση");
      await pick(user, "expense-case-select", /ΥΠΘ-2026-0001/);
      expect(screen.getByTestId("expense-case-select")).toHaveTextContent("ΥΠΘ-2026-0001");
      await pick(user, "expense-scope-select", "Πελάτης");
      await pick(user, "expense-client-select", "ΑΛΦΑ ΑΕ");
      expect(screen.getByTestId("expense-client-select")).toHaveTextContent("ΑΛΦΑ ΑΕ");
    } finally {
      warn.mockRestore();
      error.mockRestore();
    }
    expect(logged.filter((line) => /controlled/i.test(line))).toEqual([]);
  });
});
