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

  it("προειδοποιεί ότι το ίχνος μόνο συμπληρώνεται και δείχνει τι έγινε μετά την επαναφορά", async () => {
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
    expect(confirm.mock.calls[0][0]).toMatch(/ελεγκτικό ίχνος μόνο συμπληρώνεται/);
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

describe("Ρυθμίσεις — αντίγραφο ασφαλείας: τι δεν περιλαμβάνεται", () => {
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
