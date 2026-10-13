/**
 * Κομμένες λίστες — D-08/«εμφανίζονται μόνο N εγγραφές» (γύρος 5, δεύτερος έλεγχος).
 *
 * Οι λίστες δεν σελιδοποιούνται· όταν ο server κόβει τη λίστα, το δηλώνει με `X-List-Truncated: <N>`. Το frontend δεν το
 * διάβαζε πουθενά: μια λίστα 500 εγγραφών από 612 έμοιαζε πλήρης. Ο interceptor του axios (api.js → listNotice.js) το
 * ανακοινώνει μία φορά ανά λίστα, από ένα σημείο.
 */
import { render, screen, waitFor } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { toast } from "sonner";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { Toaster } from "@/components/ui/sonner";
import axios, { API } from "@/lib/api";
import { announceTruncation, labelFor, resetListNotices } from "@/lib/listNotice";
import { ROWS, renderBank, serve } from "@/test/bankFixtures";
import { server } from "@/test/server";

const respond = (path, { cut = null, body = [] } = {}) =>
  http.get(`${API}${path}`, () => HttpResponse.json(body, { headers: cut ? { "X-List-Truncated": String(cut) } : {} }));

beforeEach(() => { resetListNotices(); });
afterEach(() => { vi.restoreAllMocks(); });

describe("ανακοίνωση κομμένης λίστας", () => {
  it("η λίστα που κόπηκε ανακοινώνεται με το όριο και το όνομά της", async () => {
    server.use(respond("/hearings", { cut: 500 }));
    render(<Toaster />);

    await axios.get(`${API}/hearings`);

    expect(await screen.findByText("Πινάκια: εμφανίζονται μόνο 500 εγγραφές — υπάρχουν και άλλες που δεν φαίνονται.")).toBeInTheDocument();
  });

  it("η ίδια λίστα δεν ανακοινώνεται ξανά σε κάθε ανανέωση (οι οθόνες ανανεώνονται κάθε 15″)", async () => {
    const warning = vi.spyOn(toast, "warning");
    server.use(respond("/hearings", { cut: 500 }));

    await axios.get(`${API}/hearings`);
    await axios.get(`${API}/hearings`);
    await axios.get(`${API}/hearings`);

    expect(warning).toHaveBeenCalledTimes(1);
    expect(warning.mock.calls[0][1]).toMatchObject({ id: "list-truncated:/api/hearings", duration: 12000 });
  });

  it("το ερώτημα (query string) δεν ξεχωρίζει τη λίστα", async () => {
    const warning = vi.spyOn(toast, "warning");
    server.use(respond("/hearings", { cut: 500 }));

    await axios.get(`${API}/hearings`);
    await axios.get(`${API}/hearings`, { params: { case_id: "c1" } });

    expect(warning).toHaveBeenCalledTimes(1);
  });

  it("διαφορετικές λίστες ανακοινώνονται χωριστά", async () => {
    const warning = vi.spyOn(toast, "warning");
    server.use(respond("/hearings", { cut: 500 }), respond("/expenses", { cut: 1000 }));

    await axios.get(`${API}/hearings`);
    await axios.get(`${API}/expenses`);

    expect(warning).toHaveBeenCalledTimes(2);
    expect(warning.mock.calls[1][0]).toBe("Έξοδα: εμφανίζονται μόνο 1000 εγγραφές — υπάρχουν και άλλες που δεν φαίνονται.");
  });

  it("πλήρης λίστα (χωρίς κεφαλίδα) δεν ανακοινώνει τίποτα", async () => {
    const warning = vi.spyOn(toast, "warning");
    server.use(respond("/hearings", { cut: null, body: [{ id: "h1" }] }));

    await axios.get(`${API}/hearings`);

    expect(warning).not.toHaveBeenCalled();
  });

  it("όταν η λίστα ξαναγίνει πλήρης, μια μελλοντική κοπή ανακοινώνεται πάλι", async () => {
    const warning = vi.spyOn(toast, "warning");
    server.use(respond("/hearings", { cut: 500 }));
    await axios.get(`${API}/hearings`);
    server.use(respond("/hearings", { cut: null }));
    await axios.get(`${API}/hearings`);
    server.use(respond("/hearings", { cut: 500 }));

    await axios.get(`${API}/hearings`);

    expect(warning).toHaveBeenCalledTimes(2);
  });

  it("η οθόνη που έχει δικό της μήνυμα μπορεί να το αποκλείσει με silentTruncation", async () => {
    const warning = vi.spyOn(toast, "warning");
    server.use(respond("/bank/transactions", { cut: 500 }));

    await axios.get(`${API}/bank/transactions`, { silentTruncation: true });

    expect(warning).not.toHaveBeenCalled();
  });

  it("η απάντηση φτάνει αμετάβλητη στον καλούντα", async () => {
    server.use(respond("/hearings", { cut: 500, body: [{ id: "h1" }, { id: "h2" }] }));

    const response = await axios.get(`${API}/hearings`);

    expect(response.status).toBe(200);
    expect(response.data).toEqual([{ id: "h1" }, { id: "h2" }]);
    expect(response.headers["x-list-truncated"]).toBe("500");
  });

  it("τα σφάλματα περνούν ως έχουν και δεν ανακοινώνουν κοπή", async () => {
    const warning = vi.spyOn(toast, "warning");
    server.use(http.get(`${API}/hearings`, () => HttpResponse.json({ detail: "Η βάση δεν απαντά." }, { status: 503 })));

    await expect(axios.get(`${API}/hearings`)).rejects.toMatchObject({ response: { status: 503 } });

    expect(warning).not.toHaveBeenCalled();
  });

  it("το σώμα της απάντησης δεν επηρεάζει την ανακοίνωση", async () => {
    const warning = vi.spyOn(toast, "warning");
    server.use(respond("/grammatia", { cut: 300, body: { not: "a list" } }));

    await axios.get(`${API}/grammatia`);

    expect(warning).toHaveBeenCalledTimes(1);
  });
});

describe("ονόματα λιστών", () => {
  it.each([
    ["/api/bank/transactions", "Κινήσεις τράπεζας"],
    ["/api/cases", "Υποθέσεις"],
    ["/api/cases/abc123", "Η υπόθεση"],
    ["/api/expenses", "Έξοδα"],
    ["/api/grammatia", "Γραμμάτια"],
    ["/api/hearings", "Πινάκια"],
    ["/api/kyc/records", "Φάκελοι KYC"],
    ["/api/paravola", "Παράβολα"],
    ["/api/payroll/employees", "Εργαζόμενοι"],
    ["/api/payroll/runs", "Μισθοδοσίες"],
    ["/api/trust/accounts", "Λογαριασμοί ξένων κεφαλαίων"],
    ["/api/trust/accounts/c1", "Κινήσεις λογαριασμού"],
    ["/api/portal-chat/messages/c1", "Συνομιλία"],
    ["/api/portal/tok123/chat", "Συνομιλία"],
    ["/api/something-new", "Η λίστα"],
  ])("%s → %s", (path, label) => {
    expect(labelFor(path)).toBe(label);
  });

  it("δεν μπερδεύει διαδρομές που απλώς τελειώνουν παρόμοια", () => {
    expect(labelFor("/api/cases/abc/notes")).toBe("Η λίστα");
    expect(labelFor("/api/hearings/alerts/list")).toBe("Η λίστα");
  });
});

describe("ο interceptor είναι εγκατεστημένος στο κοινό axios", () => {
  it("η εφαρμογή περνά κάθε απάντηση από τον ίδιο έλεγχο (ακόμη και χωρίς Toaster δεν προκαλεί σφάλμα)", async () => {
    server.use(respond("/hearings", { cut: 500 }));
    await expect(axios.get(`${API}/hearings`)).resolves.toBeTruthy();
  });

  it("μια απάντηση χωρίς config ή headers δεν σπάει τον έλεγχο", () => {
    expect(announceTruncation({ status: 200 })).toEqual({ status: 200 });
    expect(announceTruncation(undefined)).toBeUndefined();
  });

  it("περιμένει το toast στην οθόνη όταν υπάρχει Toaster", async () => {
    server.use(respond("/paravola", { cut: 200 }));
    render(<Toaster />);
    await axios.get(`${API}/paravola`);
    await waitFor(() => expect(screen.getByText(/Παράβολα: εμφανίζονται μόνο 200/)).toBeInTheDocument());
  });
});

describe("Τράπεζα — έχει δικό της, ακριβέστερο μήνυμα", () => {
  it("η κομμένη λίστα κινήσεων δείχνει μόνο το δικό της μήνυμα (με τα σύνολα), όχι και δεύτερο, γενικό", async () => {
    const warning = vi.spyOn(toast, "warning");
    serve({ summary: { total: 612, unmatched: 612, auto_matched: 0, manual_matched: 0, auto_confirmed: 0, ignored: 0 } });
    server.use(http.get(`${API}/bank/transactions`, () => HttpResponse.json(ROWS.unmatched, { headers: { "X-List-Truncated": "500" } })));

    renderBank();

    expect(await screen.findByTestId("bank-truncated")).toBeInTheDocument();
    expect(warning).not.toHaveBeenCalled();
  });
});
