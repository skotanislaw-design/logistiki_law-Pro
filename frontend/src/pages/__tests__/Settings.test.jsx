/**
 * Ρυθμίσεις — παρακράτηση φόρου (#60) και επαναφορά αντιγράφου ασφαλείας (#59).
 *
 * Η κατηγορία παρακράτησης διαβιβάζεται στο myDATA ως `taxCategory`, αλλά δεν υπήρχε πεδίο στην οθόνη: άλλαζε μόνο με
 * κλήση API. Η επαναφορά αντιγράφου δείχνει πλέον τι έγινε με το ελεγκτικό ίχνος, για να μη χρειάζεται ο διαχειριστής να
 * μαντέψει αν κάτι δεν επανήλθε.
 */
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { afterEach, describe, expect, it, vi } from "vitest";

import { Toaster } from "@/components/ui/sonner";
import Settings from "@/pages/Settings";
import { MACHINE_ZONES, freezeAt, useMachineZone } from "@/test/machineZone";
import { server } from "@/test/server";

// Τα δύο ανεξάρτητα components κάνουν δικές τους κλήσεις και δεν αφορούν αυτά τα tests.
vi.mock("@/components/TwoFactorSettings", () => ({ TwoFactorSettings: () => null }));
vi.mock("@/components/BankConnection", () => ({ BankConnection: () => null }));

const API = "http://localhost:8001/api";

const settingsDoc = (over = {}) => ({
  company_name: "Skotanis & Associates", afm: "123456783", doy: "", address: "", phone: "", email: "",
  vat_period: "monthly", iban: "", bank_name: "", smtp_host: "", smtp_port: 587, smtp_user: "", smtp_password: "",
  smtp_from_name: "", aade_user_id: "", aade_subscription_key: "", aade_environment: "sandbox", mydata_connected: false,
  withholding_enabled: true, withholding_category: "3", withholding_rate: 0.2, withholding_min_net: 300,
  ...over,
});

function serveSettings(doc, onPut) {
  server.use(
    http.get(`${API}/settings`, () => HttpResponse.json(doc)),
    http.put(`${API}/settings`, async ({ request }) => {
      const body = await request.json();
      if (onPut) return onPut(body);
      return HttpResponse.json(body);
    }),
  );
}

describe("Ρυθμίσεις — παρακράτηση φόρου", () => {
  it("δείχνει την αποθηκευμένη κατηγορία και το ποσοστό", async () => {
    serveSettings(settingsDoc({ withholding_category: "10", withholding_rate: 0.15 }));
    render(<Settings />);
    expect(await screen.findByTestId("settings-withholding")).toBeInTheDocument();
    expect(screen.getByTestId("settings-withholding-category")).toHaveTextContent("10 — Προκαταβλητέος φόρος αμοιβών δικηγόρων");
    expect(screen.getByTestId("settings-withholding-rate")).toHaveValue(15);
    expect(screen.queryByTestId("settings-withholding-legacy")).not.toBeInTheDocument();
  });

  it("γραφείο που έμεινε στην κατηγορία «2» βλέπει υπενθύμιση, χωρίς να αλλάξει τίποτα αυτόματα", async () => {
    let saved = null;
    const user = userEvent.setup();
    serveSettings(settingsDoc({ withholding_category: "2" }), (body) => { saved = body; return HttpResponse.json(body); });
    render(<Settings />);
    expect(await screen.findByTestId("settings-withholding-legacy")).toBeInTheDocument();
    await user.click(screen.getByTestId("save-settings-button"));
    await waitFor(() => expect(saved).not.toBeNull());
    expect(saved.withholding_category).toBe("2");
    expect(saved.withholding_rate).toBe(0.2);
  });

  it("η αλλαγή κατηγορίας προτείνει το ποσοστό της και ό,τι φαίνεται είναι αυτό που αποθηκεύεται", async () => {
    let saved = null;
    const user = userEvent.setup();
    serveSettings(settingsDoc(), (body) => { saved = body; return HttpResponse.json(body); });
    render(<><Settings /><Toaster /></>);
    await user.click(await screen.findByTestId("settings-withholding-category"));
    await user.click(await screen.findByRole("option", { name: /10 — Προκαταβλητέος/ }));
    expect(screen.getByTestId("settings-withholding-rate")).toHaveValue(15);
    await user.click(screen.getByTestId("save-settings-button"));
    await waitFor(() => expect(saved).not.toBeNull());
    expect(saved.withholding_category).toBe("10");
    expect(saved.withholding_rate).toBe(0.15);
    expect(await screen.findByText("Οι ρυθμίσεις αποθηκεύτηκαν")).toBeInTheDocument();
  });

  it("το ποσοστό πληκτρολογείται ελεύθερα και στέλνεται ως κλάσμα", async () => {
    let saved = null;
    const user = userEvent.setup();
    serveSettings(settingsDoc(), (body) => { saved = body; return HttpResponse.json(body); });
    render(<Settings />);
    const rate = await screen.findByTestId("settings-withholding-rate");
    await user.clear(rate);
    await user.type(rate, "12.5");
    expect(rate).toHaveValue(12.5);
    await user.click(screen.getByTestId("save-settings-button"));
    await waitFor(() => expect(saved).not.toBeNull());
    expect(saved.withholding_rate).toBe(0.125);
    expect(saved.withholding_category).toBe("3");
  });

  it("το σφάλμα του server για μη έγκυρη τιμή φτάνει στον χρήστη", async () => {
    const user = userEvent.setup();
    serveSettings(settingsDoc(), () => HttpResponse.json(
      { detail: "Η κατηγορία παρακράτησης «7» δεν είναι έγκυρη. Επιτρέπονται οι κατηγορίες 2, 3 ή 10." }, { status: 400 }));
    render(<><Settings /><Toaster /></>);
    await user.click(await screen.findByTestId("save-settings-button"));
    expect(await screen.findByText(/Επιτρέπονται οι κατηγορίες 2, 3 ή 10/)).toBeInTheDocument();
  });
});

describe("Ρυθμίσεις — ποσοστό παρακράτησης: ό,τι φαίνεται είναι ό,τι αποθηκεύεται", () => {
  const rateField = () => screen.getByTestId("settings-withholding-rate");
  const saveButton = () => screen.getByTestId("save-settings-button");

  it("αν το πεδίο αδειάσει, η αποθήκευση κλειδώνει και λέει γιατί — δεν στέλνεται σιωπηλά το παλιό ποσοστό", async () => {
    let puts = 0;
    const user = userEvent.setup();
    serveSettings(settingsDoc(), (body) => { puts += 1; return HttpResponse.json(body); });
    render(<><Settings /><Toaster /></>);
    await screen.findByTestId("settings-withholding");
    expect(screen.queryByTestId("settings-withholding-rate-error")).not.toBeInTheDocument();
    expect(saveButton()).toBeEnabled();

    await user.clear(rateField());
    expect(rateField()).toHaveValue(null);
    expect(screen.getByTestId("settings-withholding-rate-error")).toHaveTextContent("Δώστε ποσοστό παρακράτησης από 0 έως 50");
    expect(rateField()).toHaveAttribute("aria-invalid", "true");
    expect(saveButton()).toBeDisabled();
    await user.click(saveButton());
    expect(puts).toBe(0);
    expect(screen.queryByText("Οι ρυθμίσεις αποθηκεύτηκαν")).not.toBeInTheDocument();
  });

  it("μόλις δοθεί έγκυρο ποσοστό, το σφάλμα φεύγει και η αποθήκευση στέλνει το νέο", async () => {
    let saved = null;
    const user = userEvent.setup();
    serveSettings(settingsDoc(), (body) => { saved = body; return HttpResponse.json(body); });
    render(<Settings />);
    await screen.findByTestId("settings-withholding");
    await user.clear(rateField());
    expect(saveButton()).toBeDisabled();
    await user.type(rateField(), "15");
    expect(screen.queryByTestId("settings-withholding-rate-error")).not.toBeInTheDocument();
    expect(rateField()).not.toHaveAttribute("aria-invalid");
    expect(saveButton()).toBeEnabled();
    await user.click(saveButton());
    await waitFor(() => expect(saved).not.toBeNull());
    expect(saved.withholding_rate).toBe(0.15);
  });

  it.each([["60", "πάνω από το όριο του 50%"], ["50.5", "λίγο πάνω από το όριο"], ["-5", "αρνητικό"]])(
    "το ποσοστό %s (%s) κλειδώνει την αποθήκευση", async (typed) => {
      const user = userEvent.setup();
      serveSettings(settingsDoc());
      render(<Settings />);
      await screen.findByTestId("settings-withholding");
      await user.clear(rateField());
      await user.type(rateField(), typed);
      expect(screen.getByTestId("settings-withholding-rate-error")).toBeInTheDocument();
      expect(saveButton()).toBeDisabled();
    });

  it.each([["0", 0], ["50", 0.5], ["7.1", 0.071], ["29", 0.29]])(
    "το ποσοστό %s%% είναι έγκυρο και στέλνεται ως %s", async (typed, expected) => {
      let saved = null;
      const user = userEvent.setup();
      serveSettings(settingsDoc(), (body) => { saved = body; return HttpResponse.json(body); });
      render(<Settings />);
      await screen.findByTestId("settings-withholding");
      await user.clear(rateField());
      await user.type(rateField(), typed);
      expect(screen.queryByTestId("settings-withholding-rate-error")).not.toBeInTheDocument();
      await user.click(saveButton());
      await waitFor(() => expect(saved).not.toBeNull());
      expect(saved.withholding_rate).toBe(expected);
    });

  it("το αποθηκευμένο ποσοστό εμφανίζεται με τα δεκαδικά του, όχι στρογγυλεμένο", async () => {
    serveSettings(settingsDoc({ withholding_rate: 0.125 }));
    render(<Settings />);
    await screen.findByTestId("settings-withholding");
    expect(rateField()).toHaveValue(12.5);
  });

  it("μετά την αποθήκευση το πεδίο δείχνει την τιμή που κράτησε ο server", async () => {
    const user = userEvent.setup();
    serveSettings(settingsDoc(), (body) => HttpResponse.json({ ...body, withholding_rate: 0.13 }));
    render(<><Settings /><Toaster /></>);
    await screen.findByTestId("settings-withholding");
    await user.clear(rateField());
    await user.type(rateField(), "12.5");
    await user.click(saveButton());
    await screen.findByText("Οι ρυθμίσεις αποθηκεύτηκαν");
    expect(rateField()).toHaveValue(13);
  });

  it("η αλλαγή κατηγορίας ακυρώνει ό,τι είχε πληκτρολογηθεί και προτείνει το ποσοστό της", async () => {
    const user = userEvent.setup();
    serveSettings(settingsDoc());
    render(<Settings />);
    await screen.findByTestId("settings-withholding");
    await user.clear(rateField());
    await user.type(rateField(), "12.5");
    await user.click(screen.getByTestId("settings-withholding-category"));
    await user.click(await screen.findByRole("option", { name: /10 — Προκαταβλητέος/ }));
    expect(rateField()).toHaveValue(15);
    expect(saveButton()).toBeEnabled();
  });

  it("η αλλαγή κατηγορίας διορθώνει και ένα άκυρο (κενό) ποσοστό", async () => {
    const user = userEvent.setup();
    serveSettings(settingsDoc());
    render(<Settings />);
    await screen.findByTestId("settings-withholding");
    await user.clear(rateField());
    expect(saveButton()).toBeDisabled();
    await user.click(screen.getByTestId("settings-withholding-category"));
    await user.click(await screen.findByRole("option", { name: /2 — Δικαιώματα/ }));
    expect(screen.queryByTestId("settings-withholding-rate-error")).not.toBeInTheDocument();
    expect(rateField()).toHaveValue(20);
    expect(saveButton()).toBeEnabled();
  });
});

describe("Ρυθμίσεις — επαναφορά αντιγράφου", () => {
  afterEach(() => vi.restoreAllMocks());

  const zip = () => new File(["zip"], "backup.zip", { type: "application/zip" });

  it("προειδοποιεί ότι η επαναφορά μόνο προσθέτει και δείχνει τι έγινε μετά την επαναφορά", async () => {
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    const user = userEvent.setup({ applyAccept: false });
    serveSettings(settingsDoc());
    server.use(http.post(`${API}/backup/import`, () => HttpResponse.json({
      message: "Η επαναφορά ολοκληρώθηκε.",
      restored: { clients: 4, audit_logs: 3 }, already_present: { audit_logs: 120 }, differing: { audit_logs: 1 }, failed: 1,
      notes: [
        "Το ελεγκτικό ίχνος συμπληρώθηκε με 3 γραμμές· 120 υπήρχαν ήδη και δεν αγγίχθηκαν.",
        "ΠΡΟΣΟΧΗ: 1 υπάρχουσες γραμμές ελεγκτικού ίχνους διαφέρουν από το αντίγραφο.",
      ],
      errors: ["clients: το έγγραφο 66f0 ανήκει σε άλλο γραφείο και δεν επαναφέρθηκε"],
    })));
    render(<><Settings /><Toaster /></>);
    await user.upload(await screen.findByTestId("import-backup-input"), zip());

    const panel = await screen.findByTestId("backup-result");
    expect(confirm).toHaveBeenCalledTimes(1);
    expect(confirm.mock.calls[0][0]).toMatch(/ΠΡΟΣΘΕΤΕΙ από το αντίγραφο μόνο όσα έγγραφα λείπουν/);
    expect(confirm.mock.calls[0][0]).toMatch(/Δεν αντικαθιστά υπάρχοντα τιμολόγια, πληρωμές/);
    expect(confirm.mock.calls[0][0]).toMatch(/ελεγκτικό ίχνος/);
    expect(confirm.mock.calls[0][0]).not.toMatch(/αντικαθιστά τα δεδομένα με ίδιο id/);
    expect(panel).toHaveTextContent("Η επαναφορά ολοκληρώθηκε, αλλά χρειάζεται έλεγχο.");
    const notes = screen.getAllByTestId("backup-note").map((n) => n.textContent);
    expect(notes[0]).toMatch(/συμπληρώθηκε με 3 γραμμές· 120 υπήρχαν ήδη/);
    expect(notes[1]).toMatch(/ΠΡΟΣΟΧΗ/);
    expect(panel).toHaveTextContent("ανήκει σε άλλο γραφείο και δεν επαναφέρθηκε");
    expect(panel).toHaveTextContent("Έγγραφα που δεν επαναφέρθηκαν (1)");
  });

  it("δεν στέλνει τίποτα αν ο χρήστης αρνηθεί την επιβεβαίωση", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(false);
    const user = userEvent.setup({ applyAccept: false });
    let called = false;
    serveSettings(settingsDoc());
    server.use(http.post(`${API}/backup/import`, () => { called = true; return HttpResponse.json({}); }));
    render(<Settings />);
    await user.upload(await screen.findByTestId("import-backup-input"), zip());
    expect(called).toBe(false);
    expect(screen.queryByTestId("backup-result")).not.toBeInTheDocument();
  });

  it("δύο ίδιες σημειώσεις εμφανίζονται και οι δύο, χωρίς προειδοποίηση της React για διπλά κλειδιά", async () => {
    const errors = vi.spyOn(console, "error").mockImplementation(() => {});
    vi.spyOn(window, "confirm").mockReturnValue(true);
    const user = userEvent.setup({ applyAccept: false });
    serveSettings(settingsDoc());
    server.use(http.post(`${API}/backup/import`, () => HttpResponse.json({
      message: "Η επαναφορά ολοκληρώθηκε.", restored: {}, already_present: {}, differing: {}, failed: 0,
      notes: ["Το αντίγραφο περιείχε εγγραφή χωρίς id.", "Το αντίγραφο περιείχε εγγραφή χωρίς id."], errors: [],
    })));
    render(<><Settings /><Toaster /></>);

    await user.upload(await screen.findByTestId("import-backup-input"), zip());

    await screen.findByTestId("backup-result");
    expect(screen.getAllByTestId("backup-note").map((n) => n.textContent)).toEqual([
      "Το αντίγραφο περιείχε εγγραφή χωρίς id.", "Το αντίγραφο περιείχε εγγραφή χωρίς id.",
    ]);
    expect(errors.mock.calls.flat().join(" ")).not.toMatch(/same key|unique "key"/);
  });

  it("καθαρή επαναφορά: μόνο το μήνυμα, χωρίς προειδοποιήσεις", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    const user = userEvent.setup({ applyAccept: false });
    serveSettings(settingsDoc());
    server.use(http.post(`${API}/backup/import`, () => HttpResponse.json({
      message: "Η επαναφορά ολοκληρώθηκε.", restored: { clients: 2 }, already_present: {}, differing: {}, failed: 0,
      notes: [], errors: [],
    })));
    render(<><Settings /><Toaster /></>);
    await user.upload(await screen.findByTestId("import-backup-input"), zip());
    const panel = await screen.findByTestId("backup-result");
    expect(panel).toHaveTextContent("Η επαναφορά ολοκληρώθηκε.");
    expect(screen.queryAllByTestId("backup-note")).toHaveLength(0);
    expect(panel).not.toHaveTextContent("δεν επαναφέρθηκαν");
  });
});

describe("Ρυθμίσεις — επαναφορά αντιγράφου: επιτυχία ή προειδοποίηση", () => {
  afterEach(() => vi.restoreAllMocks());

  const zip = () => new File(["zip"], "backup.zip", { type: "application/zip" });

  const reply = (over = {}) => ({
    message: "Η επαναφορά ολοκληρώθηκε.", restored: { clients: 2 }, already_present: {}, differing: {}, failed: 0,
    notes: [], errors: [], ...over,
  });

  async function restoreWith(result, { renderToaster = true } = {}) {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    const user = userEvent.setup({ applyAccept: false });
    serveSettings(settingsDoc());
    server.use(http.post(`${API}/backup/import`, () => HttpResponse.json(result)));
    render(<>{<Settings />}{renderToaster && <Toaster />}</>);
    await user.upload(await screen.findByTestId("import-backup-input"), zip());
    return screen.findByTestId("backup-result");
  }

  const toastOfType = (type) => document.querySelector(`[data-sonner-toast][data-type="${type}"]`);

  it("καθαρή επαναφορά: πράσινο ✓ και ειδοποίηση επιτυχίας", async () => {
    const panel = await restoreWith(reply());
    expect(panel).toHaveAttribute("data-attention", "false");
    expect(panel).toHaveAttribute("role", "status");
    expect(panel).toHaveTextContent("✓ Η επαναφορά ολοκληρώθηκε.");
    expect(screen.queryByTestId("backup-attention")).not.toBeInTheDocument();
    await waitFor(() => expect(toastOfType("success")).not.toBeNull());
    expect(toastOfType("warning")).toBeNull();
  });

  it("έγγραφα που δεν επανήλθαν: προειδοποίηση με τον αριθμό τους, όχι ✓", async () => {
    const panel = await restoreWith(reply({ failed: 3, errors: ["clients: το έγγραφο 66f0 ανήκει σε άλλο γραφείο"] }));
    expect(panel).toHaveAttribute("data-attention", "true");
    expect(panel).toHaveAttribute("role", "alert");
    expect(screen.getByTestId("backup-attention")).toHaveTextContent("Έγγραφα που δεν επαναφέρθηκαν: 3.");
    expect(screen.getByTestId("backup-attention")).not.toHaveTextContent("ελεγκτικού ίχνους");
    expect(panel).not.toHaveTextContent("✓");
    await waitFor(() => expect(toastOfType("warning")).not.toBeNull());
    expect(toastOfType("success")).toBeNull();
  });

  it("γραμμές ελεγκτικού ίχνους που διαφέρουν: προειδοποίηση με το άθροισμα όλων των συλλογών", async () => {
    const panel = await restoreWith(reply({ differing: { audit_logs: 2, other: 3 } }));
    expect(panel).toHaveAttribute("data-attention", "true");
    expect(screen.getByTestId("backup-attention")).toHaveTextContent("Γραμμές ελεγκτικού ίχνους που διαφέρουν από το αντίγραφο: 5.");
    expect(screen.getByTestId("backup-attention")).not.toHaveTextContent("Έγγραφα που δεν επαναφέρθηκαν");
    await waitFor(() => expect(toastOfType("warning")).not.toBeNull());
  });

  it("μηδενικά πλήθη στα «differing» δεν είναι προειδοποίηση", async () => {
    const panel = await restoreWith(reply({ differing: { audit_logs: 0 }, failed: 0 }));
    expect(panel).toHaveAttribute("data-attention", "false");
    await waitFor(() => expect(toastOfType("success")).not.toBeNull());
    expect(toastOfType("warning")).toBeNull();
  });

  it("η ετικέτα «Έγγραφα που δεν επαναφέρθηκαν» δείχνει όλα τα αποτυχημένα, όχι μόνο όσα χωρούν στη λίστα", async () => {
    const errors = Array.from({ length: 15 }, (_, i) => `clients: σφάλμα ${i + 1}`);
    const panel = await restoreWith(reply({ failed: 20, errors }));
    expect(panel).toHaveTextContent("Έγγραφα που δεν επαναφέρθηκαν (20)");
    expect(panel).toHaveTextContent("clients: σφάλμα 15");
    expect(panel).not.toHaveTextContent("clients: σφάλμα 16");
  });

  it("αν ο server δεν στείλει πλήθος, η ετικέτα μετρά τα σφάλματα που έστειλε", async () => {
    const panel = await restoreWith(reply({ failed: undefined, errors: ["a", "b"], differing: { audit_logs: 1 } }));
    expect(panel).toHaveTextContent("Έγγραφα που δεν επαναφέρθηκαν (2)");
  });

  it("μια νέα επαναφορά σβήνει το αποτέλεσμα της προηγούμενης πριν δείξει το δικό της", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    const user = userEvent.setup({ applyAccept: false });
    serveSettings(settingsDoc());
    server.use(http.post(`${API}/backup/import`, () => HttpResponse.json(reply({ message: "ΠΡΩΤΗ ΕΠΑΝΑΦΟΡΑ" }))));
    render(<><Settings /><Toaster /></>);
    await user.upload(await screen.findByTestId("import-backup-input"), zip());
    expect(await screen.findByTestId("backup-result")).toHaveTextContent("ΠΡΩΤΗ ΕΠΑΝΑΦΟΡΑ");

    server.use(http.post(`${API}/backup/import`, () => HttpResponse.json({ detail: "Το αρχείο δεν είναι έγκυρο αντίγραφο." }, { status: 400 })));
    await user.upload(screen.getByTestId("import-backup-input"), zip());
    expect(await screen.findByText("Το αρχείο δεν είναι έγκυρο αντίγραφο.")).toBeInTheDocument();
    expect(screen.queryByTestId("backup-result")).not.toBeInTheDocument();
  });
});

describe("Ρυθμίσεις — επαναφορά αντιγράφου: τρόπος επαναφοράς και έλεγχοι μετά από αυτήν", () => {
  afterEach(() => vi.restoreAllMocks());

  const zip = () => new File(["zip"], "backup.zip", { type: "application/zip" });
  const okChecks = () => ({
    numbering: { ok: true, problems: {} },
    trust_ledger: { ok: true, accounts: 3, inconsistent: [] },
  });
  const reply = (over = {}) => ({
    message: "Η επαναφορά ολοκληρώθηκε.", mode: "merge", restored: { clients: 2 }, replaced: {}, already_present: {},
    kept: {}, differing: {}, failed: 0, checks: okChecks(), notes: [], errors: [], ...over,
  });
  const toastOfType = (type) => document.querySelector(`[data-sonner-toast][data-type="${type}"]`);

  // Ανεβάζει ένα αρχείο και επιστρέφει όσα είδε ο server: την παράμετρο `mode` κάθε αιτήματος.
  async function restore({ result = reply(), confirmAnswer = true, overwrite = false } = {}) {
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(confirmAnswer);
    const user = userEvent.setup({ applyAccept: false });
    const modes = [];
    serveSettings(settingsDoc());
    server.use(http.post(`${API}/backup/import`, ({ request }) => {
      modes.push(new URL(request.url).searchParams.get("mode"));
      return HttpResponse.json(result);
    }));
    render(<><Settings /><Toaster /></>);
    if (overwrite) await user.click(await screen.findByTestId("backup-overwrite-toggle"));
    await user.upload(await screen.findByTestId("import-backup-input"), zip());
    return { confirm, modes, user };
  }

  it("η αντικατάσταση είναι σβηστή από προεπιλογή και το αίτημα φεύγει με mode=merge", async () => {
    expect.hasAssertions();
    const { confirm, modes } = await restore();
    await screen.findByTestId("backup-result");
    expect(modes).toEqual(["merge"]);
    expect(confirm.mock.calls[0][0]).toMatch(/ΠΡΟΣΘΕΤΕΙ/);
    expect(confirm.mock.calls[0][0]).not.toMatch(/ΑΝΤΙΚΑΤΑΣΤΑΣΗ ΔΕΔΟΜΕΝΩΝ/);
    expect(screen.getByTestId("backup-overwrite-toggle")).not.toBeChecked();
    expect(screen.queryByTestId("backup-mode")).not.toBeInTheDocument();
  });

  it("με την επιλογή αντικατάστασης φεύγει mode=overwrite και η επιβεβαίωση λέει τι χάνεται και τι δεν αγγίζεται ποτέ", async () => {
    const { confirm, modes } = await restore({
      overwrite: true,
      result: reply({ mode: "overwrite", replaced: { clients: 2, cases: 1 }, restored: { clients: 2, cases: 1 } }),
    });
    await screen.findByTestId("backup-result");
    expect(modes).toEqual(["overwrite"]);
    const text = confirm.mock.calls[0][0];
    expect(text).toMatch(/ΑΝΤΙΚΑΤΑΣΤΑΣΗ ΔΕΔΟΜΕΝΩΝ ΕΡΓΑΣΙΑΣ/);
    expect(text).toMatch(/οι αλλαγές που έγιναν μετά θα χαθούν/);
    expect(text).toMatch(/Τιμολόγια, πληρωμές, τραπεζικές κινήσεις.*ΔΕΝ αντικαθίστανται ποτέ/);
    expect(text).not.toMatch(/ΠΡΟΣΘΕΤΕΙ/);
    expect(screen.getByTestId("backup-mode")).toHaveTextContent("Τρόπος επαναφοράς: αντικατάσταση δεδομένων εργασίας (αντικαταστάθηκαν 3 στοιχεία).");
  });

  it("το κουμπί λέει ρητά ότι η επαναφορά θα αντικαταστήσει δεδομένα, μόλις ανάψει η επιλογή", async () => {
    const user = userEvent.setup();
    serveSettings(settingsDoc());
    render(<Settings />);
    const button = await screen.findByTestId("import-backup-button");
    expect(button).toHaveTextContent("Επαναφορά από Backup");
    await user.click(screen.getByTestId("backup-overwrite-toggle"));
    expect(button).toHaveTextContent("Επαναφορά με αντικατάσταση");
    await user.click(screen.getByTestId("backup-overwrite-toggle"));
    expect(button).toHaveTextContent("Επαναφορά από Backup");
  });

  it("η επιλογή δεν μένει αναμμένη: η επόμενη επαναφορά είναι ξανά μόνο προσθήκη", async () => {
    const { modes, user } = await restore({ overwrite: true, result: reply({ mode: "overwrite" }) });
    await screen.findByTestId("backup-result");
    await waitFor(() => expect(screen.getByTestId("backup-overwrite-toggle")).not.toBeChecked());
    await user.upload(screen.getByTestId("import-backup-input"), zip());
    await waitFor(() => expect(modes).toEqual(["overwrite", "merge"]));
  });

  it("αν ο χρήστης αρνηθεί την επιβεβαίωση, μπορεί να ανάψει την επιλογή και να διαλέξει ξανά το ΙΔΙΟ αρχείο", async () => {
    const confirm = vi.spyOn(window, "confirm").mockReturnValueOnce(false).mockReturnValue(true);
    const user = userEvent.setup({ applyAccept: false });
    const modes = [];
    serveSettings(settingsDoc());
    server.use(http.post(`${API}/backup/import`, ({ request }) => {
      modes.push(new URL(request.url).searchParams.get("mode"));
      return HttpResponse.json(reply({ mode: "overwrite" }));
    }));
    render(<Settings />);
    const file = zip();
    const input = await screen.findByTestId("import-backup-input");
    await user.upload(input, file);
    expect(confirm).toHaveBeenCalledTimes(1);
    expect(modes).toEqual([]);
    expect(screen.getByTestId("backup-overwrite-toggle")).not.toBeChecked();

    await user.click(screen.getByTestId("backup-overwrite-toggle"));
    await user.upload(input, file);           // ίδιο αρχείο: ένας browser δεν ξαναστέλνει «change» αν η επιλογή δεν αλλάξει
    expect(await screen.findByTestId("backup-result")).toBeInTheDocument();
    expect(confirm).toHaveBeenCalledTimes(2);
    expect(modes).toEqual(["overwrite"]);
  });

  it.each([
    ["η αρίθμηση τιμολογίων βρήκε προβλήματα",
      { numbering: { ok: false, problems: { gaps: 2 } }, trust_ledger: okChecks().trust_ledger },
      "Ο έλεγχος αρίθμησης τιμολογίων βρήκε προβλήματα."],
    ["ο έλεγχος αρίθμησης δεν ολοκληρώθηκε",
      { numbering: { ok: null, error: "timeout" }, trust_ledger: okChecks().trust_ledger },
      "Ο έλεγχος αρίθμησης τιμολογίων δεν ολοκληρώθηκε."],
    ["πελατολογικοί λογαριασμοί δεν συμφωνούν",
      { numbering: okChecks().numbering, trust_ledger: { ok: false, accounts: 4, inconsistent: [{ client_name: "Α" }, { client_name: "Β" }] } },
      "Πελατολογικοί λογαριασμοί που δεν συμφωνούν με τις κινήσεις τους: 2."],
    ["ο έλεγχος πελατολογικού δεν ολοκληρώθηκε",
      { numbering: okChecks().numbering, trust_ledger: { ok: null, error: "timeout" } },
      "Ο έλεγχος των πελατολογικών λογαριασμών δεν ολοκληρώθηκε."],
  ])("%s: προειδοποίηση, όχι επιτυχία", async (_label, checks, headline) => {
    const { modes } = await restore({ result: reply({ checks }) });
    const panel = await screen.findByTestId("backup-result");
    expect(modes).toEqual(["merge"]);
    expect(panel).toHaveAttribute("data-attention", "true");
    expect(panel).toHaveAttribute("role", "alert");
    expect(screen.getByTestId("backup-attention")).toHaveTextContent("Η επαναφορά ολοκληρώθηκε, αλλά χρειάζεται έλεγχο.");
    expect(screen.getByTestId("backup-attention")).toHaveTextContent(headline);
    expect(screen.getByTestId("backup-attention")).not.toHaveTextContent("Έγγραφα που δεν επαναφέρθηκαν");
    expect(panel).not.toHaveTextContent("✓ Η επαναφορά ολοκληρώθηκε.");
    await waitFor(() => expect(toastOfType("warning")).not.toBeNull());
    expect(toastOfType("success")).toBeNull();
  });

  it("όταν περάσουν και οι δύο έλεγχοι: επιτυχία με τις γραμμές ✓ και τον αριθμό των λογαριασμών", async () => {
    await restore();
    const panel = await screen.findByTestId("backup-result");
    expect(panel).toHaveAttribute("data-attention", "false");
    expect(panel).toHaveAttribute("role", "status");
    expect(screen.getByTestId("backup-check-numbering")).toHaveTextContent("✓ Έλεγχος αρίθμησης τιμολογίων: δεν βρέθηκε πρόβλημα.");
    expect(screen.getByTestId("backup-check-trust")).toHaveTextContent("✓ Πελατολογικοί λογαριασμοί: τα υπόλοιπα συμφωνούν με τις κινήσεις τους (3 λογαριασμοί).");
    await waitFor(() => expect(toastOfType("success")).not.toBeNull());
  });

  it("χωρίς πελατολογικούς λογαριασμούς η γραμμή ✓ δεν γράφει «(0 λογαριασμοί)»", async () => {
    await restore({
      result: reply({ checks: { numbering: okChecks().numbering, trust_ledger: { ok: true, accounts: 0, inconsistent: [] } } }),
    });
    expect(await screen.findByTestId("backup-check-trust"))
      .toHaveTextContent(/^✓ Πελατολογικοί λογαριασμοί: τα υπόλοιπα συμφωνούν με τις κινήσεις τους\.$/);
  });

  it("η γραμμή ✓ δεν εμφανίζεται για έλεγχο που απέτυχε ή δεν έτρεξε", async () => {
    await restore({ result: reply({ checks: { numbering: { ok: false, problems: { gaps: 1 } }, trust_ledger: { ok: null, error: "x" } } }) });
    const panel = await screen.findByTestId("backup-result");
    expect(screen.queryByTestId("backup-check-numbering")).not.toBeInTheDocument();
    expect(screen.queryByTestId("backup-check-trust")).not.toBeInTheDocument();
    expect(panel).not.toHaveTextContent("✓");
  });

  it("υπάρχοντα στοιχεία που διέφεραν και κρατήθηκαν: προειδοποίηση με το άθροισμα όλων των συλλογών", async () => {
    await restore({
      result: reply({
        kept: { clients: 2, settings: 1 },
        notes: ["3 υπάρχοντα έγγραφα διέφεραν από το αντίγραφο και δεν άλλαξαν (πελάτες: 2, ρυθμίσεις: 1)."],
      }),
    });
    const panel = await screen.findByTestId("backup-result");
    expect(panel).toHaveAttribute("data-attention", "true");
    expect(screen.getByTestId("backup-attention")).toHaveTextContent("Υπάρχοντα στοιχεία που διέφεραν από το αντίγραφο και δεν άλλαξαν: 3.");
    expect(screen.getAllByTestId("backup-note")[0]).toHaveTextContent("δεν άλλαξαν (πελάτες: 2, ρυθμίσεις: 1)");
    await waitFor(() => expect(toastOfType("warning")).not.toBeNull());
  });

  it("μηδενικά πλήθη στα «kept» δεν είναι προειδοποίηση", async () => {
    await restore({ result: reply({ kept: { clients: 0 } }) });
    expect(await screen.findByTestId("backup-result")).toHaveAttribute("data-attention", "false");
  });

  it("απάντηση χωρίς «checks» (παλαιότερη έκδοση) κρίνεται όπως πριν, χωρίς γραμμές ελέγχου", async () => {
    const { checks, ...withoutChecks } = reply();
    expect(checks).toBeDefined();
    await restore({ result: withoutChecks });
    const panel = await screen.findByTestId("backup-result");
    expect(panel).toHaveAttribute("data-attention", "false");
    expect(screen.queryByTestId("backup-check-numbering")).not.toBeInTheDocument();
    expect(screen.queryByTestId("backup-check-trust")).not.toBeInTheDocument();
  });

  it("μετά την επαναφορά η φόρμα ξαναφορτώνεται από τον server και το «Αποθήκευση» δεν ξαναγράφει τα παλιά στοιχεία", async () => {
    const user = userEvent.setup({ applyAccept: false });
    vi.spyOn(window, "confirm").mockReturnValue(true);
    let doc = settingsDoc({ company_name: "ΠΑΛΙΑ ΕΠΩΝΥΜΙΑ" });
    let saved = null;
    server.use(
      http.get(`${API}/settings`, () => HttpResponse.json(doc)),
      http.put(`${API}/settings`, async ({ request }) => { saved = await request.json(); return HttpResponse.json(saved); }),
      http.post(`${API}/backup/import`, () => {
        doc = settingsDoc({ company_name: "ΕΠΑΝΕΛΘΟΥΣΑ ΕΠΩΝΥΜΙΑ" });
        return HttpResponse.json(reply({ mode: "overwrite" }));
      }),
    );
    render(<><Settings /><Toaster /></>);
    expect(await screen.findByTestId("settings-company-name")).toHaveValue("ΠΑΛΙΑ ΕΠΩΝΥΜΙΑ");
    await user.upload(screen.getByTestId("import-backup-input"), zip());
    await screen.findByTestId("backup-result");
    await waitFor(() => expect(screen.getByTestId("settings-company-name")).toHaveValue("ΕΠΑΝΕΛΘΟΥΣΑ ΕΠΩΝΥΜΙΑ"));

    await user.click(screen.getByTestId("save-settings-button"));
    await waitFor(() => expect(saved).not.toBeNull());
    expect(saved.company_name).toBe("ΕΠΑΝΕΛΘΟΥΣΑ ΕΠΩΝΥΜΙΑ");
  });

  it("αν η ανανέωση των ρυθμίσεων αποτύχει, φαίνεται σφάλμα και το αποτέλεσμα της επαναφοράς μένει", async () => {
    const user = userEvent.setup({ applyAccept: false });
    vi.spyOn(window, "confirm").mockReturnValue(true);
    let reads = 0;
    server.use(
      http.get(`${API}/settings`, () => {
        reads += 1;
        return reads === 1 ? HttpResponse.json(settingsDoc()) : HttpResponse.json({ detail: "Η βάση δεν απαντά." }, { status: 500 });
      }),
      http.post(`${API}/backup/import`, () => HttpResponse.json(reply())),
    );
    render(<><Settings /><Toaster /></>);
    await user.upload(await screen.findByTestId("import-backup-input"), zip());
    expect(await screen.findByTestId("backup-result")).toHaveAttribute("data-attention", "false");
    expect(await screen.findByText(/Οι ρυθμίσεις δεν ανανεώθηκαν μετά την επαναφορά — ανανεώστε τη σελίδα πριν τις αποθηκεύσετε/)).toBeInTheDocument();
    expect(reads).toBe(2);
  });

  it("αποτυχημένη επαναφορά (σφάλμα του server) δεν ξαναφορτώνει τις ρυθμίσεις: δεν άλλαξε τίποτα", async () => {
    const user = userEvent.setup({ applyAccept: false });
    vi.spyOn(window, "confirm").mockReturnValue(true);
    let reads = 0;
    server.use(
      http.get(`${API}/settings`, () => { reads += 1; return HttpResponse.json(settingsDoc()); }),
      http.post(`${API}/backup/import`, () => HttpResponse.json({ detail: "Το αρχείο δεν είναι έγκυρο αντίγραφο." }, { status: 400 })),
    );
    render(<><Settings /><Toaster /></>);
    await user.upload(await screen.findByTestId("import-backup-input"), zip());
    expect(await screen.findByText("Το αρχείο δεν είναι έγκυρο αντίγραφο.")).toBeInTheDocument();
    expect(reads).toBe(1);
    expect(screen.queryByTestId("backup-result")).not.toBeInTheDocument();
  });
});

describe("Ρυθμίσεις — εισαγωγή πελατολογίου", () => {
  afterEach(() => vi.restoreAllMocks());

  const csv = () => new File(["Όνομα,ΑΦΜ\nΑ,094259216\nΒ,123456783"], "clients.csv", { type: "text/csv" });

  it("στέλνει το αρχείο και δείχνει το αποτέλεσμα", async () => {
    const user = userEvent.setup({ applyAccept: false });
    const sent = [];
    serveSettings(settingsDoc());
    server.use(http.post(`${API}/import/clients`, async ({ request }) => {
      sent.push(await (await request.formData()).get("file").text());
      return HttpResponse.json({ message: "Εισήχθησαν 2 πελάτες.", total_rows: 2, errors: [] });
    }));
    render(<><Settings /><Toaster /></>);
    await user.upload(await screen.findByTestId("import-clients-input"), csv());
    const panel = await screen.findByTestId("import-result");
    expect(sent).toEqual(["Όνομα,ΑΦΜ\nΑ,094259216\nΒ,123456783"]);
    expect(panel).toHaveTextContent("✓ Εισήχθησαν 2 πελάτες.");
    expect(panel).toHaveTextContent("Σύνολο γραμμών: 2");
  });

  it("μετά από αποτυχία το ΙΔΙΟ αρχείο μπορεί να ξαναδιαλεχτεί και να ξαναστείλει", async () => {
    const user = userEvent.setup({ applyAccept: false });
    let attempts = 0;
    serveSettings(settingsDoc());
    server.use(http.post(`${API}/import/clients`, () => {
      attempts += 1;
      return attempts === 1
        ? HttpResponse.json({ detail: "Η βάση δεν απαντά." }, { status: 503 })
        : HttpResponse.json({ message: "Εισήχθησαν 2 πελάτες.", total_rows: 2, errors: [] });
    }));
    render(<><Settings /><Toaster /></>);
    const file = csv();
    const input = await screen.findByTestId("import-clients-input");
    await user.upload(input, file);
    expect(await screen.findByText("Η βάση δεν απαντά.")).toBeInTheDocument();
    await user.upload(input, file);       // ένας browser δεν ξαναστέλνει «change» για την ίδια επιλογή αν δεν μηδενιστεί το πεδίο
    expect(await screen.findByTestId("import-result")).toHaveTextContent("✓ Εισήχθησαν 2 πελάτες.");
    expect(attempts).toBe(2);
  });
});

describe("Ρυθμίσεις — αντίγραφο ασφαλείας: τι δεν περιλαμβάνεται", () => {
  it("η κάρτα εξηγεί πώς λειτουργεί η επαναφορά: μόνο προσθήκη, ποτέ τα βιβλία, κενή βάση για πλήρη επαναφορά", async () => {
    serveSettings(settingsDoc());
    render(<Settings />);
    const rules = await screen.findByTestId("backup-restore-rules");
    expect(rules).toHaveTextContent("προσθέτει μόνο όσα έγγραφα λείπουν");
    expect(rules).toHaveTextContent("δεν αντικαθιστά ποτέ τιμολόγια, πληρωμές, τραπεζικές κινήσεις, πελατολογικό λογαριασμό, περιόδους ΦΠΑ και ελεγκτικό ίχνος");
    expect(rules).toHaveTextContent("επαναφέρετε σε κενή βάση");
    expect(screen.getByTestId("backup-overwrite-toggle")).not.toBeChecked();
  });

  it("η κάρτα το λέει πριν από τη λήψη: αρχεία, χρήστες/πύλη πελατών και SECRETS_KEY", async () => {
    serveSettings(settingsDoc());
    render(<Settings />);
    const box = await screen.findByTestId("backup-caveats");
    expect(box).toHaveTextContent("Τι δεν περιλαμβάνεται στο αντίγραφο");
    expect(box).toHaveTextContent("Αρχεία που έχουν ανέβει");
    expect(box).toHaveTextContent("φωτογραφίες αποδείξεων εξόδων");
    expect(box).toHaveTextContent("Χρήστες, κωδικοί πρόσβασης και σύνδεσμοι της πύλης πελατών");
    expect(box).toHaveTextContent("νέες προσκλήσεις");
    expect(box).toHaveTextContent("SECRETS_KEY");
    expect(box).toHaveTextContent("ξεχωριστά από το αντίγραφο");
    expect(box.querySelectorAll("li")).toHaveLength(3);
  });

  it("η περιγραφή της κάρτας δεν ισχυρίζεται πια ότι το αντίγραφο είναι «πλήρες» χωρίς επιφύλαξη για όσα λείπουν", async () => {
    serveSettings(settingsDoc());
    render(<Settings />);
    const box = await screen.findByTestId("backup-caveats");
    const description = box.previousElementSibling;
    expect(description).toHaveTextContent("ελεγκτικό ίχνος");
    expect(description).not.toHaveTextContent("δεν περιλαμβάνει κωδικούς χρηστών");   // η πλήρης λίστα είναι στο κουτί από κάτω
  });
});

describe.each(MACHINE_ZONES)("Ρυθμίσεις — όνομα αρχείου αντιγράφου, μηχάνημα σε %s", (zone) => {
  useMachineZone(zone);
  afterEach(() => vi.restoreAllMocks());

  it("φέρει την ημερομηνία Ελλάδας, όχι της ζώνης του μηχανήματος ή του UTC", async () => {
    // 01:30 τα ξημερώματα της 12ης Οκτωβρίου στην Αθήνα = 22:30 της 11ης σε UTC
    freezeAt("2026-10-11T22:30:00Z");
    const downloads = [];
    URL.createObjectURL = vi.fn(() => "blob:backup");
    URL.revokeObjectURL = vi.fn();
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function recordDownload() { downloads.push(this.download); });
    serveSettings(settingsDoc());
    server.use(http.get(`${API}/backup/export`, () => new HttpResponse(new Blob(["zip"]), { headers: { "Content-Type": "application/zip" } })));
    const user = userEvent.setup({ advanceTimers: () => {} });
    render(<><Settings /><Toaster /></>);
    await user.click(await screen.findByTestId("export-backup-button"));
    await waitFor(() => expect(downloads).toHaveLength(1));
    expect(downloads[0]).toBe("nomos-one-backup-2026-10-12.zip");
    expect(URL.revokeObjectURL).toHaveBeenCalledWith("blob:backup");
  });
});
