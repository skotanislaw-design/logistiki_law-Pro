/**
 * Φάση 1 «ψηφιακού λογιστή» — Ρυθμίσεις: πίνακας εκπτωσιμότητας ΦΠΑ εισροών.
 *
 * Ο πίνακας ορίζει πόσο ΦΠΑ μπαίνει στη δήλωση Φ2, και τον υπογράφει ο λογιστής του γραφείου· το σύστημα δεν έχει δικά του
 * ποσοστά. Εδώ ελέγχεται ότι η οθόνη:
 *   · κρατά το «κενό» (δεν υπάρχει κανόνας) διαφορετικό από το 0 και το 100,
 *   · δεν αφήνει να φύγει προς τον server ένα άκυρο ποσοστό,
 *   · δεν χάνει κατηγορίες που έχουν κανόνα αλλά δεν είναι στην τρέχουσα λίστα,
 *   · προειδοποιεί όταν αλλάζει ο πίνακας ενώ γράφει ήδη «επιβεβαιώθηκε από τον λογιστή».
 */
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { describe, expect, it, vi } from "vitest";

import { Toaster } from "@/components/ui/sonner";
import { VatDeductibilityRules } from "@/components/VatDeductibilityRules";
import { EXPENSE_CATEGORIES, parsePercent } from "@/lib/vatRules";
import Settings from "@/pages/Settings";
import { server } from "@/test/server";

vi.mock("@/components/TwoFactorSettings", () => ({ TwoFactorSettings: () => null }));
vi.mock("@/components/BankConnection", () => ({ BankConnection: () => null }));

const API = "http://localhost:8001/api";

const settingsDoc = (over = {}) => ({
  company_name: "Skotanis & Associates", afm: "123456783", doy: "", address: "", phone: "", email: "",
  vat_period: "monthly", iban: "", bank_name: "", smtp_host: "", smtp_port: 587, smtp_user: "", smtp_password: "",
  smtp_from_name: "", aade_user_id: "", aade_subscription_key: "", aade_environment: "sandbox", mydata_connected: false,
  withholding_enabled: true, withholding_category: "3", withholding_rate: 0.2, withholding_min_net: 300,
  vat_deductibility_rules: {}, vat_rules_confirmed_by: "", updated_at: "2026-10-01T09:00:00+00:00",
  ...over,
});

/** Ο server επιστρέφει ολόκληρο το έγγραφο: ό,τι στάλθηκε αλλάζει, ό,τι δεν στάλθηκε μένει όπως ήταν, με νέο `updated_at` (όπως το PUT /settings). */
function serve(doc, { onPut, reply } = {}) {
  server.use(
    http.get(`${API}/settings`, () => HttpResponse.json(doc)),
    http.put(`${API}/settings`, async ({ request }) => {
      const body = await request.json();
      onPut?.(body);
      return reply ? reply(body) : HttpResponse.json({ ...doc, ...body, updated_at: "2026-10-18T10:00:00+00:00" });
    }),
  );
}

const rule = (category) => screen.getByTestId(`vat-rule-${category}`);
const renderSettings = () => render(<><Settings /><Toaster /></>);

async function replace(user, input, text) {
  await user.clear(input);
  if (text) await user.type(input, text);
}

describe("parsePercent — τι θεωρείται έγκυρο ποσοστό", () => {
  it.each([
    ["", undefined], ["   ", undefined], [null, undefined], [undefined, undefined],
    ["0", 0], ["100", 100], ["50", 50], ["33,5", 33.5], ["33.5", 33.5], [" 20 ", 20], ["7.", 7], [".5", 0.5],
  ])("«%s» → %s", (text, expected) => expect(parsePercent(text)).toBe(expected));

  it.each(["101", "100.01", "-1", "abc", "10%", "1e2", "5,5,5", "٥"])("«%s» δεν είναι έγκυρο", (text) => {
    expect(parsePercent(text)).toBeNull();
  });
});

describe("Ρυθμίσεις — πίνακας εκπτωσιμότητας ΦΠΑ", () => {
  it("χωρίς κανόνες: όλα τα πεδία κενά και δηλώνεται ότι δεν έχει οριστεί κανένας κανόνας", async () => {
    serve(settingsDoc());
    renderSettings();
    expect(await screen.findByTestId("vat-rules-count")).toHaveTextContent("Δεν έχει οριστεί κανένας κανόνας.");
    for (const category of EXPENSE_CATEGORIES) {
      expect(rule(category)).toHaveValue("");
      expect(rule(category)).toHaveAttribute("placeholder", "χωρίς κανόνα");
    }
  });

  it("δείχνει τους αποθηκευμένους κανόνες, και το 0 μένει 0 (δεν γίνεται κενό)", async () => {
    serve(settingsDoc({ vat_deductibility_rules: { "Ενοίκιο": 100, "Φιλοξενία / Εστίαση": 0, "Μεταφορικά": 33.5 } }));
    renderSettings();
    await screen.findByTestId("vat-rules-count");
    expect(rule("Ενοίκιο")).toHaveValue("100");
    expect(rule("Φιλοξενία / Εστίαση")).toHaveValue("0");
    expect(rule("Μεταφορικά")).toHaveValue("33.5");
    expect(rule("Ασφάλιστρα")).toHaveValue("");
    expect(screen.getByTestId("vat-rules-count")).toHaveTextContent(`Ορισμένοι κανόνες: 3 από ${EXPENSE_CATEGORIES.length} κατηγορίες.`);
  });

  it("κατηγορία με κανόνα που δεν υπάρχει στη λίστα δεν χάνεται: φαίνεται, και διατηρείται όταν αλλάζει άλλος κανόνας", async () => {
    const user = userEvent.setup();
    let saved = null;
    serve(settingsDoc({ vat_deductibility_rules: { "Παλιά κατηγορία": 25, "Ενοίκιο": 100 } }), { onPut: (b) => { saved = b; } });
    renderSettings();
    expect(await screen.findByTestId("vat-rule-Παλιά κατηγορία")).toHaveValue("25");
    expect(screen.getByTestId("vat-rules-count")).toHaveTextContent(`2 από ${EXPENSE_CATEGORIES.length + 1} κατηγορίες`);
    await replace(user, screen.getByTestId("vat-rule-Ενοίκιο"), "80");
    await user.click(screen.getByTestId("save-settings-button"));
    await waitFor(() => expect(saved).not.toBeNull());
    expect(saved.vat_deductibility_rules).toEqual({ "Παλιά κατηγορία": 25, "Ενοίκιο": 80 });
  });

  it("το «33,5» πληκτρολογείται με κόμμα χωρίς να χάνεται η υποδιαστολή και στέλνεται ως αριθμός 33.5", async () => {
    const user = userEvent.setup();
    let saved = null;
    serve(settingsDoc(), { onPut: (b) => { saved = b; } });
    renderSettings();
    const input = await screen.findByTestId("vat-rule-Μεταφορικά");
    await user.type(input, "33,5");
    expect(input).toHaveValue("33,5");
    await user.click(screen.getByTestId("save-settings-button"));
    await waitFor(() => expect(saved).not.toBeNull());
    expect(saved.vat_deductibility_rules).toEqual({ "Μεταφορικά": 33.5 });
    expect(await screen.findByText("Οι ρυθμίσεις αποθηκεύτηκαν")).toBeInTheDocument();
  });

  it("το 0 στέλνεται ως 0 (κανόνας «δεν εκπίπτει»), όχι ως απουσία κανόνα", async () => {
    const user = userEvent.setup();
    let saved = null;
    serve(settingsDoc(), { onPut: (b) => { saved = b; } });
    renderSettings();
    await user.type(await screen.findByTestId("vat-rule-Φιλοξενία / Εστίαση"), "0");
    await user.click(screen.getByTestId("save-settings-button"));
    await waitFor(() => expect(saved).not.toBeNull());
    expect(saved.vat_deductibility_rules).toStrictEqual({ "Φιλοξενία / Εστίαση": 0 });
  });

  it("το άδειασμα ενός πεδίου αφαιρεί τον κανόνα· οι υπόλοιποι μένουν", async () => {
    const user = userEvent.setup();
    let saved = null;
    serve(settingsDoc({ vat_deductibility_rules: { "Ενοίκιο": 100, "Μεταφορικά": 50 } }), { onPut: (b) => { saved = b; } });
    renderSettings();
    await replace(user, await screen.findByTestId("vat-rule-Ενοίκιο"), "");
    expect(screen.getByTestId("vat-rules-count")).toHaveTextContent(`Ορισμένοι κανόνες: 1 από ${EXPENSE_CATEGORIES.length}`);
    await user.click(screen.getByTestId("save-settings-button"));
    await waitFor(() => expect(saved).not.toBeNull());
    expect(saved.vat_deductibility_rules).toStrictEqual({ "Μεταφορικά": 50 });
  });

  it.each(["150", "abc", "-5", "10%"])("το άκυρο «%s» δείχνει σφάλμα και απενεργοποιεί την αποθήκευση", async (bad) => {
    const user = userEvent.setup();
    let puts = 0;
    serve(settingsDoc({ vat_deductibility_rules: { "Ενοίκιο": 100 } }), { onPut: () => { puts += 1; } });
    renderSettings();
    const input = await screen.findByTestId("vat-rule-Ενοίκιο");
    await replace(user, input, bad);
    expect(await screen.findByTestId("vat-rules-error")).toBeInTheDocument();
    expect(input).toHaveAttribute("aria-invalid", "true");
    const save = screen.getByTestId("save-settings-button");
    expect(save).toBeDisabled();
    await user.click(save);
    expect(puts).toBe(0);

    // Μόλις διορθωθεί, το σφάλμα φεύγει και η αποθήκευση ξαναγίνεται δυνατή.
    await replace(user, input, "20");
    await waitFor(() => expect(screen.queryByTestId("vat-rules-error")).not.toBeInTheDocument());
    expect(save).toBeEnabled();
  });

  it("το άκυρο κείμενο δεν αλλάζει τον πίνακα που θα σταλεί (μένει η προηγούμενη έγκυρη τιμή)", async () => {
    const user = userEvent.setup();
    serve(settingsDoc({ vat_deductibility_rules: { "Ενοίκιο": 100 } }));
    renderSettings();
    const input = await screen.findByTestId("vat-rule-Ενοίκιο");
    await user.type(input, "x");                                    // «100x»: άκυρο
    expect(input).toHaveValue("100x");
    expect(screen.getByTestId("vat-rules-error")).toBeInTheDocument();
    expect(screen.getByTestId("vat-rules-count")).toHaveTextContent(`Ορισμένοι κανόνες: 1 από ${EXPENSE_CATEGORIES.length}`);
  });

  it("άκυρο κείμενο σε κατηγορία χωρίς κανόνα δεν δημιουργεί κανόνα", async () => {
    const user = userEvent.setup();
    let saved = null;
    serve(settingsDoc(), { onPut: (b) => { saved = b; } });
    renderSettings();
    await user.type(await screen.findByTestId("vat-rule-Μεταφορικά"), "abc");
    expect(screen.getByTestId("vat-rules-error")).toBeInTheDocument();
    expect(screen.getByTestId("vat-rules-count")).toHaveTextContent("Δεν έχει οριστεί κανένας κανόνας.");
    await replace(user, screen.getByTestId("vat-rule-Μεταφορικά"), "");           // διορθώνεται με άδειασμα
    await user.click(screen.getByTestId("save-settings-button"));
    await waitFor(() => expect(saved).not.toBeNull());
    expect(saved).not.toHaveProperty("vat_deductibility_rules");                   // ο πίνακας δεν άλλαξε: δεν στέλνεται καθόλου
  });

  it("το άδειασμα του πεδίου είναι ρητή αφαίρεση κανόνα, όχι σφάλμα", async () => {
    const user = userEvent.setup();
    serve(settingsDoc({ vat_deductibility_rules: { "Ενοίκιο": 100 } }));
    renderSettings();
    await replace(user, await screen.findByTestId("vat-rule-Ενοίκιο"), "");
    expect(screen.queryByTestId("vat-rules-error")).not.toBeInTheDocument();
    expect(screen.getByTestId("vat-rules-count")).toHaveTextContent("Δεν έχει οριστεί κανένας κανόνας.");
    expect(screen.getByTestId("save-settings-button")).toBeEnabled();
  });

  it("το όνομα του λογιστή που επιβεβαίωσε τον πίνακα στέλνεται όπως γράφτηκε", async () => {
    const user = userEvent.setup();
    let saved = null;
    serve(settingsDoc(), { onPut: (b) => { saved = b; } });
    renderSettings();
    await user.type(await screen.findByTestId("vat-rule-Ενοίκιο"), "100");
    await user.type(screen.getByTestId("vat-rules-confirmed-by"), "Χριστόφορος, 20/10/2026");
    await user.click(screen.getByTestId("save-settings-button"));
    await waitFor(() => expect(saved).not.toBeNull());
    expect(saved.vat_rules_confirmed_by).toBe("Χριστόφορος, 20/10/2026");
    expect(saved.vat_deductibility_rules).toStrictEqual({ "Ενοίκιο": 100 });
  });

  it("σφάλμα του server (π.χ. απαγορευμένος χαρακτήρας) εμφανίζεται και δεν χάνονται οι αλλαγές της φόρμας", async () => {
    const user = userEvent.setup();
    serve(settingsDoc(), {
      reply: () => HttpResponse.json({ detail: "Η κατηγορία «Α.Β» δεν μπορεί να περιέχει τελεία ή το σύμβολο «$»." }, { status: 400 }),
    });
    renderSettings();
    const input = await screen.findByTestId("vat-rule-Ενοίκιο");
    await user.type(input, "40");
    await user.click(screen.getByTestId("save-settings-button"));
    expect(await screen.findByText(/δεν μπορεί να περιέχει τελεία/)).toBeInTheDocument();
    expect(input).toHaveValue("40");
  });
});

describe("Ρυθμίσεις — προειδοποίηση όταν αλλάζει πίνακας που φέρει ήδη επιβεβαίωση", () => {
  const confirmed = () => settingsDoc({ vat_deductibility_rules: { "Ενοίκιο": 100 }, vat_rules_confirmed_by: "Χριστόφορος, 20/10/2026" });
  const note = () => screen.queryByTestId("vat-rules-changed-note");

  it("δεν εμφανίζεται όσο ο πίνακας είναι όπως τον επιβεβαίωσε ο λογιστής", async () => {
    serve(confirmed());
    renderSettings();
    await screen.findByTestId("vat-rule-Ενοίκιο");
    expect(note()).not.toBeInTheDocument();
  });

  it("εμφανίζεται μόλις αλλάξει κάποιο ποσοστό και φεύγει αν επανέλθει η αρχική τιμή", async () => {
    const user = userEvent.setup();
    serve(confirmed());
    renderSettings();
    const input = await screen.findByTestId("vat-rule-Ενοίκιο");
    await replace(user, input, "50");
    expect(note()).toBeInTheDocument();
    expect(note()).toHaveAttribute("role", "status");
    await replace(user, input, "100");
    expect(note()).not.toBeInTheDocument();
  });

  it("εμφανίζεται και όταν προστεθεί ή αφαιρεθεί κανόνας", async () => {
    const user = userEvent.setup();
    serve(confirmed());
    renderSettings();
    await user.type(await screen.findByTestId("vat-rule-Μεταφορικά"), "50");
    expect(note()).toBeInTheDocument();
    await replace(user, screen.getByTestId("vat-rule-Μεταφορικά"), "");
    expect(note()).not.toBeInTheDocument();
    await replace(user, screen.getByTestId("vat-rule-Ενοίκιο"), "");
    expect(note()).toBeInTheDocument();
  });

  it("δεν εμφανίζεται για άκυρο κείμενο, που δεν αλλάζει τον πίνακα", async () => {
    const user = userEvent.setup();
    serve(confirmed());
    renderSettings();
    await user.type(await screen.findByTestId("vat-rule-Ενοίκιο"), "x");           // «100x»
    expect(screen.getByTestId("vat-rules-error")).toBeInTheDocument();
    expect(note()).not.toBeInTheDocument();
  });

  it("δεν εξαρτάται από τη σειρά των κατηγοριών: αφαίρεση και επαναπροσθήκη του ίδιου κανόνα δεν είναι αλλαγή", async () => {
    const user = userEvent.setup();
    serve(settingsDoc({ vat_deductibility_rules: { "Ενοίκιο": 100, "Μεταφορικά": 25 }, vat_rules_confirmed_by: "Χριστόφορος, 20/10/2026" }));
    renderSettings();
    const input = await screen.findByTestId("vat-rule-Ενοίκιο");
    await replace(user, input, "");
    expect(note()).toBeInTheDocument();
    await replace(user, input, "100");                                              // ο κανόνας ξαναμπαίνει τελευταίος στο αντικείμενο
    expect(note()).not.toBeInTheDocument();
  });

  it("φεύγει αν σβηστεί το όνομα της επιβεβαίωσης (ο χρήστης δηλώνει ότι ο πίνακας δεν είναι επιβεβαιωμένος)", async () => {
    const user = userEvent.setup();
    serve(confirmed());
    renderSettings();
    await replace(user, await screen.findByTestId("vat-rule-Ενοίκιο"), "50");
    expect(note()).toBeInTheDocument();
    await replace(user, screen.getByTestId("vat-rules-confirmed-by"), "");
    expect(note()).not.toBeInTheDocument();
  });

  it("δεν εμφανίζεται ποτέ όταν δεν υπάρχει επιβεβαίωση", async () => {
    const user = userEvent.setup();
    serve(settingsDoc({ vat_deductibility_rules: { "Ενοίκιο": 100 } }));
    renderSettings();
    await replace(user, await screen.findByTestId("vat-rule-Ενοίκιο"), "50");
    expect(note()).not.toBeInTheDocument();
  });

  it("μετά την αποθήκευση ο αποθηκευμένος πίνακας γίνεται η νέα βάση και η προειδοποίηση φεύγει", async () => {
    const user = userEvent.setup();
    let saved = null;
    serve(confirmed(), { onPut: (b) => { saved = b; } });
    renderSettings();
    await replace(user, await screen.findByTestId("vat-rule-Ενοίκιο"), "50");
    expect(note()).toBeInTheDocument();
    await user.click(screen.getByTestId("save-settings-button"));
    await waitFor(() => expect(saved).not.toBeNull());
    await waitFor(() => expect(note()).not.toBeInTheDocument());
    expect(screen.getByTestId("vat-rule-Ενοίκιο")).toHaveValue("50");
  });
});

describe("VatDeductibilityRules — πεδίο επιβεβαίωσης και ενημέρωση του γονέα", () => {
  it("το όνομα της επιβεβαίωσης έχει όριο 120 χαρακτήρων και στο πεδίο (ο server το επιβάλλει επίσης)", () => {
    render(<VatDeductibilityRules rules={{}} confirmedBy="" onChange={() => {}} />);
    expect(screen.getByTestId("vat-rules-confirmed-by")).toHaveAttribute("maxlength", "120");
  });

  it("ο γονέας μαθαίνει ότι υπάρχει άκυρο ποσοστό, και ότι δεν υπάρχει πια όταν ο πίνακας φεύγει από την οθόνη", async () => {
    const user = userEvent.setup();
    const onInvalidChange = vi.fn();
    const { unmount } = render(<VatDeductibilityRules rules={{}} confirmedBy="" onChange={() => {}} onInvalidChange={onInvalidChange} />);

    await user.type(screen.getByTestId("vat-rule-Ενοίκιο"), "abc");
    expect(onInvalidChange).toHaveBeenLastCalledWith(true);
    unmount();
    expect(onInvalidChange).toHaveBeenLastCalledWith(false);          // αλλιώς η Αποθήκευση θα έμενε κλειδωμένη για πάντα
  });
});
