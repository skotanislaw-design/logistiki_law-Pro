/**
 * ΔΙΟΡΘΩΘΗΚΕ (frontend/src/pages/Settings.jsx): μετά από επιτυχή επαναφορά αντιγράφου η φόρμα ρυθμίσεων δεν ξαναδιάβαζε τον server.
 *
 * Ο server αντικαθιστά το ζωντανό έγγραφο ρυθμίσεων με αυτό του αντιγράφου (backend/routes_backup.py → _restore_settings),
 * αλλά η οθόνη κρατούσε το `form` που είχε φορτώσει όταν άνοιξε:
 *   1. έδειχνε τις ΠΑΛΙΕΣ τιμές (επωνυμία, ΑΦΜ, IBAN, ποσοστό παρακράτησης, στοιχεία ΑΑΔΕ) — όχι αυτές που κρατά ο server·
 *   2. αν ο διαχειριστής πατούσε «Αποθήκευση Ρυθμίσεων», οι παλιές τιμές γράφονταν ΠΑΝΩ στις ρυθμίσεις που μόλις είχαν επανέλθει.
 *
 * Ισχύει: μετά την επαναφορά η φόρμα ξαναφορτώνει τις ρυθμίσεις (GET /api/settings).
 */
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { afterEach, describe, expect, it, vi } from "vitest";

import { Toaster } from "@/components/ui/sonner";
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
  ...over,
});

const BEFORE = settingsDoc();
const RESTORED = settingsDoc({ company_name: "ΕΠΑΝΗΛΘΕ ΕΠΕ", iban: "GR1601101250000000012300695", withholding_category: "10", withholding_rate: 0.15 });

/** Ο server: πριν από την επαναφορά επιστρέφει τις παλιές ρυθμίσεις, μετά το περιεχόμενο του αντιγράφου. */
function serveRestorableSettings(puts) {
  let restored = false;
  server.use(
    http.get(`${API}/settings`, () => HttpResponse.json(restored ? RESTORED : BEFORE)),
    http.put(`${API}/settings`, async ({ request }) => {
      const body = await request.json();
      puts.push(body);
      return HttpResponse.json(body);
    }),
    http.post(`${API}/backup/import`, () => {
      restored = true;
      return HttpResponse.json({
        message: "Η επαναφορά ολοκληρώθηκε.", restored: { settings: 1 }, already_present: {}, differing: {}, failed: 0, notes: [], errors: [],
      });
    }),
  );
}

async function restoreBackup() {
  vi.spyOn(window, "confirm").mockReturnValue(true);
  const user = userEvent.setup({ applyAccept: false });
  const puts = [];
  serveRestorableSettings(puts);
  render(<><Settings /><Toaster /></>);
  expect(await screen.findByTestId("settings-company-name")).toHaveValue("Skotanis & Associates");
  await user.upload(screen.getByTestId("import-backup-input"), new File(["zip"], "backup.zip", { type: "application/zip" }));
  await screen.findByTestId("backup-result");
  return { user, puts };
}

describe("Ρυθμίσεις — μετά την επαναφορά αντιγράφου η φόρμα δείχνει ό,τι κρατά ο server", () => {
  afterEach(() => vi.restoreAllMocks());

  it("η επωνυμία, το IBAN και το ποσοστό παρακράτησης είναι αυτά του αντιγράφου, όχι τα παλιά", async () => {
    await restoreBackup();
    await waitFor(() => expect(screen.getByTestId("settings-company-name")).toHaveValue("ΕΠΑΝΗΛΘΕ ΕΠΕ"), { timeout: 2000 });
    expect(screen.getByTestId("settings-iban")).toHaveValue("GR1601101250000000012300695");
    expect(screen.getByTestId("settings-withholding-rate")).toHaveValue(15);
  });

  it("το «Αποθήκευση Ρυθμίσεων» μετά την επαναφορά δεν γράφει πάνω της τις παλιές τιμές", async () => {
    const { user, puts } = await restoreBackup();
    await new Promise((resolve) => setTimeout(resolve, 150));                    // αφήνουμε χρόνο στην οθόνη να ξαναδιαβάσει τις ρυθμίσεις
    await user.click(screen.getByTestId("save-settings-button"));
    await waitFor(() => expect(puts).toHaveLength(1));
    expect(puts[0].company_name).toBe("ΕΠΑΝΗΛΘΕ ΕΠΕ");
    expect(puts[0].iban).toBe("GR1601101250000000012300695");
    expect(puts[0].withholding_rate).toBe(0.15);
  });
});
