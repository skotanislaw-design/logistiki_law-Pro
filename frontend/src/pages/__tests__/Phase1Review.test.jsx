/**
 * Φάση 1 «ψηφιακού λογιστή» — διορθώσεις μετά την ανεξάρτητη ανασκόπηση του κώδικα (17/10/2026): η πλευρά του frontend.
 *
 * Δύο αρχές διατρέχουν τις διορθώσεις:
 *   · μια οθόνη στέλνει μόνο ό,τι άλλαξε ο χρήστης — όχι ολόκληρη την εικόνα που φόρτωσε πριν από ώρα, γιατί εκείνη θα έγραφε
 *     πάνω σε ό,τι άλλαξε στο μεταξύ (πίνακας εκπτωσιμότητας ΦΠΑ, πελάτης και δικηγόρος υπόθεσης) — Ε1·
 *   · ό,τι δεν μπορεί να αναιρεθεί (σήμανση «Υποβλήθηκε») ρωτά πριν, και ό,τι απαγορεύεται (ρόλος δικηγόρου) φαίνεται — Ε5.
 * Επιπλέον: η επιβεβαίωση του λογιστή αποσύρεται όταν αλλάζει ο πίνακας (Ε4) και ο δικηγόρος που προέκυψε από την
 * προηγούμενη υπόθεση δεν μένει όταν αλλάζει η υπόθεση (Ε7).
 */
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { useState } from "react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ExpenseMatterFields } from "@/components/ExpenseMatterFields";
import { InvoiceMatterEditor } from "@/components/InvoiceMatterEditor";
import { Toaster } from "@/components/ui/sonner";
import { lawyerAfterCaseChange, LAWYER_ROLES } from "@/lib/matters";
import {
  confirmationWithdrawn, rulesSignature, settingsPayload, settingsPayloadWithoutVatRules, vatBaseline, vatRulesEdited,
} from "@/lib/vatRules";
import Autopilot from "@/pages/Autopilot";
import CaseDetail from "@/pages/CaseDetail";
import Invoices from "@/pages/Invoices";
import Onboarding from "@/pages/Onboarding";
import Settings from "@/pages/Settings";
import Vat from "@/pages/Vat";
import { CASE_A, CASE_B, CLIENT_A, CLIENT_B, USERS, lookupHandlers } from "@/test/lookupFixtures";
import { server } from "@/test/server";

const auth = vi.hoisted(() => ({ role: "admin" }));
vi.mock("@/context/AuthContext", () => ({
  useAuth: () => ({ user: { name: "Χρήστος Σκοτάνης", role: auth.role } }),
}));
vi.mock("@/components/TwoFactorSettings", () => ({ TwoFactorSettings: () => null }));
vi.mock("@/components/BankConnection", () => ({ BankConnection: () => null }));

const API = "http://localhost:8001/api";

beforeEach(() => { auth.role = "admin"; });
afterEach(() => vi.restoreAllMocks());

async function pick(user, triggerId, optionName) {
  await user.click(await screen.findByTestId(triggerId));
  await user.click(await screen.findByRole("option", { name: optionName }));
}

async function replace(user, input, text) {
  await user.clear(input);
  if (text) await user.type(input, text);
}

const settingsDoc = (over = {}) => ({
  company_name: "Skotanis & Associates", afm: "123456783", doy: "", address: "", phone: "", email: "",
  vat_period: "monthly", iban: "", bank_name: "", smtp_host: "", smtp_port: 587, smtp_user: "", smtp_password: "",
  smtp_from_name: "", aade_user_id: "", aade_subscription_key: "", aade_environment: "sandbox", mydata_connected: false,
  withholding_enabled: true, withholding_category: "3", withholding_rate: 0.2, withholding_min_net: 300,
  vat_deductibility_rules: {}, vat_rules_confirmed_by: "", updated_at: "2026-10-01T09:00:00+00:00",
  ...over,
});

const CONFIRMED = () => settingsDoc({ vat_deductibility_rules: { "Ενοίκιο": 50 }, vat_rules_confirmed_by: "Χριστόφορος, 20/10/2026" });

/**
 * Ο server όπως τον περιγράφει το backend/routes_settings.py: ό,τι δεν στάλθηκε μένει όπως ήταν, και αν αλλάξει ο πίνακας
 * χωρίς νέο όνομα επιβεβαίωσης, η επιβεβαίωση ανακαλείται.
 */
function serveSettings(doc, { onPut } = {}) {
  let stored = doc;
  server.use(
    http.get(`${API}/settings`, () => HttpResponse.json(stored)),
    http.put(`${API}/settings`, async ({ request }) => {
      const body = await request.json();
      onPut?.(body);
      const next = { ...stored, ...body, updated_at: `2026-10-18T10:00:0${Math.floor(Math.random() * 9)}+00:00` };
      const changed = "vat_deductibility_rules" in body
        && rulesSignature(body.vat_deductibility_rules) !== rulesSignature(stored.vat_deductibility_rules);
      if (changed && (!("vat_rules_confirmed_by" in body) || body.vat_rules_confirmed_by === stored.vat_rules_confirmed_by)) {
        next.vat_rules_confirmed_by = "";
      }
      stored = next;
      return HttpResponse.json(next);
    }),
  );
}

const renderSettings = () => render(<><Settings /><Toaster /></>);

// ============================================================ Ε1 · σταματά η σιωπηλή αντικατάσταση ====

describe("Ε1 · ΦΠΑ: η αλλαγή περιόδου στέλνει μόνο το πεδίο που άλλαξε", () => {
  it("PATCH με το vat_period και τίποτε άλλο· κανένα PUT ολόκληρων των ρυθμίσεων", async () => {
    const user = userEvent.setup();
    let patched = null;
    let puts = 0;
    server.use(
      http.get(`${API}/dashboard/summary`, () => HttpResponse.json({ output_vat: 240, input_vat: 54, vat_due: 186, next_vat_deadline: "2026-11-30" })),
      http.get(`${API}/settings`, () => HttpResponse.json(CONFIRMED())),
      http.patch(`${API}/settings`, async ({ request }) => { patched = await request.json(); return HttpResponse.json(CONFIRMED()); }),
      http.put(`${API}/settings`, () => { puts += 1; return HttpResponse.json(CONFIRMED()); }),
    );
    render(<MemoryRouter><Vat /><Toaster /></MemoryRouter>);
    await pick(user, "vat-period-select", "Τριμηνιαία Δήλωση");
    await waitFor(() => expect(patched).not.toBeNull());
    expect(patched).toEqual({ vat_period: "quarterly" });
    expect(puts).toBe(0);
    expect(await screen.findByText("Η περίοδος ΦΠΑ ενημερώθηκε")).toBeInTheDocument();
  });
});

describe("Ε1 · Ρυθμίσεις: μετά από επαναφορά αντιγράφου", () => {
  it("η φόρμα ξαναδιαβάζει τον πίνακα, και η αποθήκευση που ακολουθεί δεν τον στέλνει σαν δική της αλλαγή", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    const user = userEvent.setup({ applyAccept: false });
    const puts = [];
    let restored = false;
    const BEFORE = CONFIRMED();
    const RESTORED = settingsDoc({
      company_name: "ΕΠΑΝΗΛΘΕ ΕΠΕ", vat_deductibility_rules: { "Ενοίκιο": 20 }, vat_rules_confirmed_by: "Χριστόφορος, 01/09/2026",
      updated_at: "2026-10-17T12:00:00+00:00",
    });
    server.use(
      http.get(`${API}/settings`, () => HttpResponse.json(restored ? RESTORED : BEFORE)),
      http.put(`${API}/settings`, async ({ request }) => { puts.push(await request.json()); return HttpResponse.json(RESTORED); }),
      http.post(`${API}/backup/import`, () => {
        restored = true;
        return HttpResponse.json({ message: "Η επαναφορά ολοκληρώθηκε.", restored: { settings: 1 }, already_present: {}, differing: {}, failed: 0, notes: [], errors: [] });
      }),
    );
    renderSettings();
    expect(await screen.findByTestId("settings-company-name")).toHaveValue("Skotanis & Associates");
    await user.upload(screen.getByTestId("import-backup-input"), new File(["zip"], "backup.zip", { type: "application/zip" }));
    await screen.findByTestId("backup-result");
    await waitFor(() => expect(screen.getByTestId("vat-rule-Ενοίκιο")).toHaveValue("20"));
    await user.click(screen.getByTestId("save-settings-button"));
    await waitFor(() => expect(puts).toHaveLength(1));
    expect(puts[0].company_name).toBe("ΕΠΑΝΗΛΘΕ ΕΠΕ");
    expect(puts[0]).not.toHaveProperty("vat_deductibility_rules");
    expect(puts[0]).not.toHaveProperty("vat_rules_confirmed_by");
  });
});

describe("Ε1 · Ρυθμίσεις: ο πίνακας ΦΠΑ και η επιβεβαίωση στέλνονται μόνο αν τα άλλαξε ο χρήστης", () => {
  it("αποθήκευση που δεν αγγίζει τον πίνακα δεν τον στέλνει ούτε τον σβήνει — ούτε την επιβεβαίωση", async () => {
    const user = userEvent.setup();
    let saved = null;
    serveSettings(CONFIRMED(), { onPut: (b) => { saved = b; } });
    renderSettings();
    await replace(user, await screen.findByTestId("settings-company-name"), "Νέα επωνυμία");
    await user.click(screen.getByTestId("save-settings-button"));
    await waitFor(() => expect(saved).not.toBeNull());
    expect(saved.company_name).toBe("Νέα επωνυμία");
    expect(saved).not.toHaveProperty("vat_deductibility_rules");
    expect(saved).not.toHaveProperty("vat_rules_confirmed_by");
    expect(screen.getByTestId("vat-rule-Ενοίκιο")).toHaveValue("50");
    expect(screen.getByTestId("vat-rules-confirmed-by")).toHaveValue("Χριστόφορος, 20/10/2026");
  });

  it("αλλαγή του πίνακα χωρίς αλλαγή ονόματος στέλνει μόνο τον πίνακα· ο server ανακαλεί την επιβεβαίωση και η οθόνη το λέει", async () => {
    const user = userEvent.setup();
    let saved = null;
    serveSettings(CONFIRMED(), { onPut: (b) => { saved = b; } });
    renderSettings();
    await replace(user, await screen.findByTestId("vat-rule-Ενοίκιο"), "80");
    await user.click(screen.getByTestId("save-settings-button"));
    await waitFor(() => expect(saved).not.toBeNull());
    expect(saved.vat_deductibility_rules).toEqual({ "Ενοίκιο": 80 });
    expect(saved).not.toHaveProperty("vat_rules_confirmed_by");
    expect(await screen.findByText(/η επιβεβαίωση του λογιστή αποσύρθηκε/)).toBeInTheDocument();
    await waitFor(() => expect(screen.getByTestId("vat-rules-confirmed-by")).toHaveValue(""));
    expect(screen.getByTestId("vat-rule-Ενοίκιο")).toHaveValue("80");
  });

  it("αλλαγή του πίνακα μαζί με νέο όνομα λογιστή στέλνει και τα δύο και δεν δείχνει προειδοποίηση", async () => {
    const user = userEvent.setup();
    let saved = null;
    serveSettings(CONFIRMED(), { onPut: (b) => { saved = b; } });
    renderSettings();
    await replace(user, await screen.findByTestId("vat-rule-Ενοίκιο"), "80");
    await replace(user, screen.getByTestId("vat-rules-confirmed-by"), "Χριστόφορος, 27/10/2026");
    await user.click(screen.getByTestId("save-settings-button"));
    await waitFor(() => expect(saved).not.toBeNull());
    expect(saved).toMatchObject({ vat_deductibility_rules: { "Ενοίκιο": 80 }, vat_rules_confirmed_by: "Χριστόφορος, 27/10/2026" });
    expect(await screen.findByText("Οι ρυθμίσεις αποθηκεύτηκαν")).toBeInTheDocument();
    expect(screen.queryByText(/αποσύρθηκε/)).not.toBeInTheDocument();
    expect(screen.getByTestId("vat-rules-confirmed-by")).toHaveValue("Χριστόφορος, 27/10/2026");
  });

  it("αλλαγή μόνο του ονόματος στέλνει μόνο το όνομα", async () => {
    const user = userEvent.setup();
    let saved = null;
    serveSettings(CONFIRMED(), { onPut: (b) => { saved = b; } });
    renderSettings();
    await replace(user, await screen.findByTestId("vat-rules-confirmed-by"), "Άλλος λογιστής, 21/10/2026");
    await user.click(screen.getByTestId("save-settings-button"));
    await waitFor(() => expect(saved).not.toBeNull());
    expect(saved.vat_rules_confirmed_by).toBe("Άλλος λογιστής, 21/10/2026");
    expect(saved).not.toHaveProperty("vat_deductibility_rules");
    expect(screen.queryByText(/αποσύρθηκε/)).not.toBeInTheDocument();
  });

  it("αν ο χρήστης σβήσει μόνος του το όνομα, δεν εμφανίζεται μήνυμα ανάκλησης που δεν έγινε από τον server", async () => {
    const user = userEvent.setup();
    serveSettings(CONFIRMED());
    renderSettings();
    await replace(user, await screen.findByTestId("vat-rules-confirmed-by"), "");
    await user.click(screen.getByTestId("save-settings-button"));
    expect(await screen.findByText("Οι ρυθμίσεις αποθηκεύτηκαν")).toBeInTheDocument();
    expect(screen.queryByText(/αποσύρθηκε/)).not.toBeInTheDocument();
  });

  it("μετά την αποθήκευση ο πίνακας που επέστρεψε ο server γίνεται η νέα βάση: η επόμενη αποθήκευση δεν τον ξαναστέλνει", async () => {
    const user = userEvent.setup();
    const puts = [];
    serveSettings(CONFIRMED(), { onPut: (b) => puts.push(b) });
    renderSettings();
    await replace(user, await screen.findByTestId("vat-rule-Ενοίκιο"), "80");
    await replace(user, screen.getByTestId("vat-rules-confirmed-by"), "Χριστόφορος, 27/10/2026");
    await user.click(screen.getByTestId("save-settings-button"));
    await waitFor(() => expect(puts).toHaveLength(1));
    await screen.findByText("Οι ρυθμίσεις αποθηκεύτηκαν");
    await replace(user, screen.getByTestId("settings-company-name"), "Άλλη επωνυμία");
    await user.click(screen.getByTestId("save-settings-button"));
    await waitFor(() => expect(puts).toHaveLength(2));
    expect(puts[1]).not.toHaveProperty("vat_deductibility_rules");
    expect(puts[1]).not.toHaveProperty("vat_rules_confirmed_by");
  });

  it("η προειδοποίηση πάνω από το πεδίο λέει ότι η επιβεβαίωση θα αποσυρθεί, και τι να κάνει ο χρήστης", async () => {
    const user = userEvent.setup();
    serveSettings(CONFIRMED());
    renderSettings();
    await replace(user, await screen.findByTestId("vat-rule-Ενοίκιο"), "80");
    const note = await screen.findByTestId("vat-rules-changed-note");
    expect(note).toHaveTextContent("η επιβεβαίωση του λογιστή αποσύρεται");
    expect(note).toHaveTextContent("νέο όνομα και ημερομηνία");
  });
});

describe("Ε1 · βοηθητικές συναρτήσεις του πίνακα ΦΠΑ", () => {
  const baseline = vatBaseline(CONFIRMED());

  it("η βάση σύγκρισης έχει τον πίνακα και το όνομα, ακόμη και για παλιά απάντηση χωρίς τα πεδία", () => {
    expect(baseline).toEqual({ rules: { "Ενοίκιο": 50 }, confirmedBy: "Χριστόφορος, 20/10/2026" });
    expect(vatBaseline({})).toEqual({ rules: {}, confirmedBy: "" });
    expect(vatBaseline(null)).toEqual({ rules: {}, confirmedBy: "" });
  });

  it("η υπογραφή δεν εξαρτάται από τη σειρά των κατηγοριών", () => {
    expect(rulesSignature({ "Α": 1, "Β": 2 })).toBe(rulesSignature({ "Β": 2, "Α": 1 }));
    expect(rulesSignature({ "Α": 1 })).not.toBe(rulesSignature({ "Α": 2 }));
  });

  it("το σώμα του PUT κρατά τα υπόλοιπα πεδία και αφαιρεί τον πίνακα/όνομα που δεν άλλαξαν", () => {
    const form = { ...CONFIRMED(), company_name: "Άλλο" };
    const body = settingsPayload(form, baseline);
    expect(body.company_name).toBe("Άλλο");
    expect(body).not.toHaveProperty("vat_deductibility_rules");
    expect(body).not.toHaveProperty("vat_rules_confirmed_by");
  });

  it("χωρίς βάση σύγκρισης δεν στέλνεται ποτέ ο πίνακας (ασφαλής προεπιλογή)", () => {
    const body = settingsPayload(CONFIRMED(), null);
    expect(body).not.toHaveProperty("vat_deductibility_rules");
    expect(body).not.toHaveProperty("vat_rules_confirmed_by");
    expect(vatRulesEdited(CONFIRMED(), null)).toBe(false);
  });

  it("ο πίνακας που αδειάζει ολόκληρος είναι αλλαγή και στέλνεται ως {}", () => {
    const form = { ...CONFIRMED(), vat_deductibility_rules: {} };
    expect(settingsPayload(form, baseline).vat_deductibility_rules).toEqual({});
  });

  it("η ανάκληση αναγνωρίζεται μόνο όταν άλλαξε ο πίνακας, υπήρχε όνομα και ο server επέστρεψε κενό", () => {
    const edited = { ...CONFIRMED(), vat_deductibility_rules: { "Ενοίκιο": 80 } };
    expect(confirmationWithdrawn(edited, baseline, { vat_rules_confirmed_by: "" })).toBe(true);
    expect(confirmationWithdrawn(edited, baseline, { vat_rules_confirmed_by: "Νέο όνομα" })).toBe(false);
    expect(confirmationWithdrawn(CONFIRMED(), baseline, { vat_rules_confirmed_by: "" })).toBe(false);   // ο πίνακας δεν άλλαξε
    expect(confirmationWithdrawn(edited, vatBaseline(settingsDoc()), { vat_rules_confirmed_by: "" })).toBe(false);   // δεν υπήρχε όνομα
  });

  it("το Onboarding δεν στέλνει ποτέ πίνακα ή επιβεβαίωση", () => {
    const body = settingsPayloadWithoutVatRules(CONFIRMED());
    expect(body).not.toHaveProperty("vat_deductibility_rules");
    expect(body).not.toHaveProperty("vat_rules_confirmed_by");
    expect(body.company_name).toBe("Skotanis & Associates");
  });
});

describe("Ε1 · Onboarding δεν γράφει πάνω στον πίνακα ΦΠΑ", () => {
  it("η αποθήκευση ενός βήματος στέλνει τα στοιχεία του γραφείου χωρίς πίνακα και χωρίς επιβεβαίωση", async () => {
    const user = userEvent.setup();
    let saved = null;
    server.use(
      http.get(`${API}/settings`, () => HttpResponse.json(CONFIRMED())),
      http.put(`${API}/settings`, async ({ request }) => { saved = await request.json(); return HttpResponse.json(CONFIRMED()); }),
    );
    render(<MemoryRouter><Onboarding /><Toaster /></MemoryRouter>);
    await replace(user, await screen.findByTestId("onb-company"), "Νέα επωνυμία");
    await user.click(screen.getByTestId("onb-next-button"));
    await waitFor(() => expect(saved).not.toBeNull());
    expect(saved.company_name).toBe("Νέα επωνυμία");
    expect(saved).not.toHaveProperty("vat_deductibility_rules");
    expect(saved).not.toHaveProperty("vat_rules_confirmed_by");
  });
});

describe("Ε1 · Υπόθεση: το ΓΑΚ/ΕΑΚ δεν στέλνει πελάτη και δικηγόρο", () => {
  const fullCase = (over = {}) => ({
    ...CASE_A, case_type: "Αστικό", court: "", opposing_party: "", status: "active", next_action: "", next_action_date: "",
    gak: "", eak: "", gak_year: "", notes: [], history: [], ...over,
  });

  it("η αποθήκευση του ΓΑΚ στέλνει τα στοιχεία της υπόθεσης χωρίς client_id, client_name και lawyer_id", async () => {
    const user = userEvent.setup();
    let put = null;
    server.use(
      ...lookupHandlers({ users: USERS }),
      http.get(`${API}/cases/case-a`, () => HttpResponse.json({ case: fullCase(), grammatia: [], paravola: [], fees_total: 0, expenses_total: 0 })),
      http.get(`${API}/hearings`, () => HttpResponse.json([])),
      http.put(`${API}/cases/case-a`, async ({ request }) => { put = await request.json(); return HttpResponse.json(put); }),
    );
    render(<MemoryRouter initialEntries={["/cases/case-a"]}><Routes><Route path="/cases/:id" element={<CaseDetail />} /></Routes><Toaster /></MemoryRouter>);
    await user.click(await screen.findByTestId("edit-gak-button"));
    await user.type(await screen.findByTestId("gak-input"), "1234");
    await user.click(screen.getByTestId("gak-save"));
    await waitFor(() => expect(put).not.toBeNull());
    expect(put).toMatchObject({ id: "case-a", title: "Αγωγή αποζημίωσης", gak: "1234" });
    for (const field of ["client_id", "client_name", "lawyer_id"]) expect(put).not.toHaveProperty(field);
  });
});

// ============================================================ Ε5 · ποιος σημειώνει «Υποβλήθηκε» ====

describe("Ε5 · Autopilot: η σήμανση «Υποβλήθηκε» ρωτά, αναφέρει σφάλμα και σέβεται τον ρόλο", () => {
  const period = (over = {}) => ({
    period: "2026-09", output_vat: 240, input_vat: 54, vat_due: 186, due_date: "2026-10-31", status: "computed", ...over,
  });

  function serveAutopilot({ periods = [period()], onSubmit } = {}) {
    const calls = { periods: 0, submitted: [] };
    server.use(
      http.get(`${API}/automation/status`, () => HttpResponse.json({
        open_items: 0, last_7_days: {}, cycle_minutes: 15, last_digest_at: null,
        capabilities: { email: true, mydata: true, stripe: true, ai: true, imap: true, mydata_expenses: true, openbanking: true },
      })),
      http.get(`${API}/automation/notifications`, () => HttpResponse.json([])),
      http.get(`${API}/automation/runs`, () => HttpResponse.json([])),
      http.get(`${API}/automation/vat-periods`, () => { calls.periods += 1; return HttpResponse.json(periods); }),
      http.get(`${API}/settings`, () => HttpResponse.json({ vat_period: "monthly", autopilot_enabled: true })),
      http.post(`${API}/automation/vat-periods/:period/submitted`, ({ params }) => {
        calls.submitted.push(params.period);
        return onSubmit ? onSubmit(params.period) : HttpResponse.json({ message: `Η περίοδος ${params.period} σημειώθηκε ως υποβληθείσα` });
      }),
    );
    return calls;
  }

  async function openVatTab(user) {
    render(<MemoryRouter><Autopilot /><Toaster /></MemoryRouter>);
    await user.click(await screen.findByTestId("tab-vat"));
    return screen.findByTestId("vat-submitted-2026-09");
  }

  it("ρωτά πριν κλειδώσει την περίοδο· αν ο χρήστης αρνηθεί, δεν στέλνεται τίποτα", async () => {
    const user = userEvent.setup();
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    const calls = serveAutopilot();
    await user.click(await openVatTab(user));
    expect(confirm).toHaveBeenCalledTimes(1);
    expect(confirm.mock.calls[0][0]).toContain("2026-09");
    expect(confirm.mock.calls[0][0]).toContain("παγώνουν");
    expect(confirm.mock.calls[0][0]).toContain("TAXISnet");
    expect(calls.submitted).toEqual([]);
  });

  it("με επιβεβαίωση στέλνει τη σήμανση, δείχνει το μήνυμα του server και ξαναφορτώνει τις περιόδους", async () => {
    const user = userEvent.setup();
    vi.spyOn(window, "confirm").mockReturnValue(true);
    const calls = serveAutopilot();
    await user.click(await openVatTab(user));
    await waitFor(() => expect(calls.submitted).toEqual(["2026-09"]));
    expect(await screen.findByText("Η περίοδος 2026-09 σημειώθηκε ως υποβληθείσα")).toBeInTheDocument();
    await waitFor(() => expect(calls.periods).toBeGreaterThanOrEqual(2));
  });

  it("η ερώτηση αναφέρει το ΦΠΑ που εκπίπτει χωρίς κανόνα, όταν υπάρχει", async () => {
    const user = userEvent.setup();
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    serveAutopilot({ periods: [period({ input_vat_unruled: 31.2 })] });
    await user.click(await openVatTab(user));
    expect(confirm.mock.calls[0][0]).toContain("31,20");
    expect(confirm.mock.calls[0][0]).toContain("χωρίς να έχει οριστεί κανόνας");
  });

  it("η ερώτηση δεν μιλά για κανόνες όταν όλα έχουν κανόνα", async () => {
    const user = userEvent.setup();
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    serveAutopilot({ periods: [period({ input_vat_unruled: 0 })] });
    await user.click(await openVatTab(user));
    expect(confirm.mock.calls[0][0]).not.toContain("χωρίς να έχει οριστεί κανόνας");
  });

  it("η άρνηση του server (403) εμφανίζεται ως σφάλμα και δεν σπάει η σελίδα", async () => {
    const user = userEvent.setup();
    vi.spyOn(window, "confirm").mockReturnValue(true);
    serveAutopilot({
      onSubmit: () => HttpResponse.json({ detail: "Την υποβολή ΦΠΑ τη σημειώνει μόνο ο διαχειριστής ή ο λογιστής του γραφείου." }, { status: 403 }),
    });
    await user.click(await openVatTab(user));
    expect(await screen.findByText(/μόνο ο διαχειριστής ή ο λογιστής του γραφείου/)).toBeInTheDocument();
    expect(screen.getByTestId("vat-submitted-2026-09")).toBeEnabled();
    expect(screen.queryByText(/σημειώθηκε ως υποβληθείσα/)).not.toBeInTheDocument();
  });

  it("ο λογιστής του γραφείου μπορεί να σημειώσει την περίοδο", async () => {
    auth.role = "accountant";
    const user = userEvent.setup();
    vi.spyOn(window, "confirm").mockReturnValue(true);
    const calls = serveAutopilot();
    const button = await openVatTab(user);
    expect(button).toBeEnabled();
    await user.click(button);
    await waitFor(() => expect(calls.submitted).toEqual(["2026-09"]));
  });

  it("ο δικηγόρος βλέπει το κουμπί αδρανές με εξήγηση, και δεν γίνεται κανένα αίτημα", async () => {
    auth.role = "lawyer";
    const user = userEvent.setup();
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    const calls = serveAutopilot();
    const button = await openVatTab(user);
    expect(button).toBeDisabled();
    expect(button).toHaveAttribute("title", expect.stringContaining("διαχειριστής ή ο λογιστής"));
    await user.click(button);
    expect(confirm).not.toHaveBeenCalled();
    expect(calls.submitted).toEqual([]);
  });

  it("περίοδος που έχει ήδη υποβληθεί δείχνει την ένδειξη και όχι κουμπί", async () => {
    const user = userEvent.setup();
    serveAutopilot({ periods: [period({ status: "submitted" })] });
    render(<MemoryRouter><Autopilot /><Toaster /></MemoryRouter>);
    await user.click(await screen.findByTestId("tab-vat"));
    expect(await screen.findByTestId("vat-period-2026-09")).toHaveTextContent("Υποβλήθηκε");
    expect(screen.queryByTestId("vat-submitted-2026-09")).not.toBeInTheDocument();
  });
});

// ======================================== Ε7 · ο δικηγόρος της προηγούμενης υπόθεσης δεν μένει ====

describe("Ε7 · lawyerAfterCaseChange", () => {
  const cases = [CASE_A, CASE_B];                                        // A: υπεύθυνος u-law · B: χωρίς υπεύθυνο

  it.each([
    ["κληρονομημένος δικηγόρος (ο υπεύθυνος της προηγούμενης) → κενός, να τον βάλει ο server", "case-a", "case-b", "u-law", ""],
    ["ρητά επιλεγμένος άλλος δικηγόρος → μένει", "case-a", "case-b", "u-law2", "u-law2"],
    ["η νέα υπόθεση δεν είναι υπόθεση (αποσύνδεση) → ο δικηγόρος μένει", "case-a", "", "u-law", "u-law"],
    ["δεν υπήρχε προηγούμενη υπόθεση → ο δικηγόρος που διάλεξε ο χρήστης μένει", "", "case-b", "u-law", "u-law"],
    ["η ίδια υπόθεση ξανά → τίποτα δεν αλλάζει", "case-a", "case-a", "u-law", "u-law"],
    ["χωρίς δικηγόρο → μένει κενός", "case-a", "case-b", "", ""],
    ["προηγούμενη υπόθεση που δεν υπάρχει στη λίστα → ο δικηγόρος μένει (δεν ξέρουμε από πού ήρθε)", "case-gone", "case-b", "u-law", "u-law"],
  ])("%s", (_name, previous, next, lawyer, expected) => {
    expect(lawyerAfterCaseChange(cases, previous, next, lawyer)).toBe(expected);
  });

  it("λίστα που δεν φορτώθηκε (undefined) δεν σπάει τον υπολογισμό", () => {
    expect(lawyerAfterCaseChange(undefined, "case-a", "case-b", "u-law")).toBe("u-law");
    expect(lawyerAfterCaseChange(null, "case-a", "case-b", undefined)).toBe("");
  });

  it("οι ρόλοι που προσφέρονται ως δικηγόροι είναι διαχειριστής και δικηγόρος", () => {
    expect(LAWYER_ROLES).toEqual(["admin", "lawyer"]);
  });
});

describe("Ε7 · φόρμα εξόδου: αλλαγή υπόθεσης", () => {
  const lawyers = USERS.filter((u) => LAWYER_ROLES.includes(u.role));

  function Harness({ initial }) {
    const [form, setForm] = useState(initial);
    return (
      <>
        <ExpenseMatterFields form={form} onChange={(patch) => setForm((f) => ({ ...f, ...patch }))}
          lookups={{ cases: [CASE_A, CASE_B], clients: [CLIENT_A, CLIENT_B], lawyers, failed: {} }} />
        <pre data-testid="form-json">{JSON.stringify(form)}</pre>
      </>
    );
  }
  const formState = () => JSON.parse(screen.getByTestId("form-json").textContent);

  it("ο δικηγόρος που είχε προκύψει από την παλιά υπόθεση αδειάζει, και η φόρμα γράφει «ο υπεύθυνος της υπόθεσης»", async () => {
    const user = userEvent.setup();
    render(<Harness initial={{ scope: "case", case_id: "case-a", lawyer_id: "u-law", client_id: "", rebillable: false }} />);
    expect(screen.getByTestId("expense-lawyer-select")).toHaveTextContent("Χρήστος Σκοτάνης");
    await pick(user, "expense-case-select", /ΥΠΘ-2026-0002/);
    expect(formState()).toMatchObject({ case_id: "case-b", lawyer_id: "" });
    expect(screen.getByTestId("expense-lawyer-select")).toHaveTextContent("Ο υπεύθυνος της υπόθεσης");
  });

  it("ο δικηγόρος που επιλέχθηκε ρητά, διαφορετικός από τον υπεύθυνο, μένει", async () => {
    const user = userEvent.setup();
    render(<Harness initial={{ scope: "case", case_id: "case-a", lawyer_id: "u-law2", client_id: "", rebillable: false }} />);
    await pick(user, "expense-case-select", /ΥΠΘ-2026-0002/);
    expect(formState()).toMatchObject({ case_id: "case-b", lawyer_id: "u-law2" });
    expect(screen.getByTestId("expense-lawyer-select")).toHaveTextContent("Μαρία Δικηγόρος");
  });
});

describe("Ε7 · εκδοθέν τιμολόγιο: αλλαγή υπόθεσης από τον επεξεργαστή", () => {
  const issued = (over = {}) => ({
    id: "i1", number: "ΤΠΥ-2026-0001", status: "issued", client_id: "client-a", client_name: "ΑΛΦΑ ΑΕ", date: "2026-10-05",
    line_items: [], subtotal: 100, vat_amount: 24, total: 124, case_id: "case-a", lawyer_id: "u-law", ...over,
  });

  it("ο κληρονομημένος δικηγόρος δεν ξαναστέλνεται με τη νέα υπόθεση: στέλνεται κενός", async () => {
    const user = userEvent.setup();
    let patched = null;
    server.use(
      ...lookupHandlers({ cases: [CASE_A, CASE_B], clients: [CLIENT_A, CLIENT_B], users: USERS }),
      http.patch(`${API}/invoices/i1/matter`, async ({ request }) => { patched = await request.json(); return HttpResponse.json({ case_id: "case-b", lawyer_id: "" }); }),
    );
    render(<><InvoiceMatterEditor invoice={issued()} /><Toaster /></>);
    await user.click(await screen.findByTestId("invoice-matter-edit"));
    await pick(user, "invoice-matter-case-select", /ΥΠΘ-2026-0002/);
    await user.click(screen.getByTestId("invoice-matter-save"));
    await waitFor(() => expect(patched).not.toBeNull());
    expect(patched).toEqual({ case_id: "case-b", lawyer_id: "" });
  });

  it("ο ρητά επιλεγμένος άλλος δικηγόρος μένει", async () => {
    const user = userEvent.setup();
    let patched = null;
    server.use(
      ...lookupHandlers({ cases: [CASE_A, CASE_B], clients: [CLIENT_A, CLIENT_B], users: USERS }),
      http.patch(`${API}/invoices/i1/matter`, async ({ request }) => { patched = await request.json(); return HttpResponse.json(patched); }),
    );
    render(<><InvoiceMatterEditor invoice={issued({ lawyer_id: "u-law2" })} /><Toaster /></>);
    await user.click(await screen.findByTestId("invoice-matter-edit"));
    await pick(user, "invoice-matter-case-select", /ΥΠΘ-2026-0002/);
    await user.click(screen.getByTestId("invoice-matter-save"));
    await waitFor(() => expect(patched).not.toBeNull());
    expect(patched).toEqual({ case_id: "case-b", lawyer_id: "u-law2" });
  });
});

describe("Ε7 · πρόχειρο τιμολόγιο: αλλαγή υπόθεσης στη φόρμα", () => {
  const draft = (over = {}) => ({
    id: "d1", number: "", status: "draft", client_id: "client-a", client_name: "ΑΛΦΑ ΑΕ", date: "2026-10-12",
    line_items: [{ description: "Υπηρεσία", quantity: 1, unit_price: 100, vat_rate: 24 }],
    payment_method: "Τραπεζική κατάθεση", notes: "", subtotal: 100, vat_amount: 24, total: 124, mydata_status: "not_sent",
    case_id: "case-a", lawyer_id: "u-law", ...over,
  });

  it("ο δικηγόρος της παλιάς υπόθεσης δεν μένει στο PUT όταν αλλάζει η υπόθεση", async () => {
    const user = userEvent.setup();
    let put = null;
    server.use(
      ...lookupHandlers({ cases: [CASE_A, CASE_B], clients: [CLIENT_A, CLIENT_B], users: USERS }),
      http.get(`${API}/invoices`, () => HttpResponse.json([draft()])),
      http.put(`${API}/invoices/d1`, async ({ request }) => { put = await request.json(); return HttpResponse.json(draft()); }),
    );
    render(<><Invoices /><Toaster /></>);
    await user.click(await screen.findByTestId("edit-invoice-d1"));
    await pick(user, "invoice-case-select", /ΥΠΘ-2026-0002/);
    expect(screen.getByTestId("invoice-lawyer-select")).toHaveTextContent("Ο υπεύθυνος της υπόθεσης");
    await user.click(screen.getByTestId("save-invoice-button"));
    await waitFor(() => expect(put).not.toBeNull());
    expect(put).toMatchObject({ case_id: "case-b", lawyer_id: "" });
  });
});
