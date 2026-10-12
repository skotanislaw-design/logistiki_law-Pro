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
    expect(panel).toHaveTextContent("Η επαναφορά ολοκληρώθηκε.");
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
