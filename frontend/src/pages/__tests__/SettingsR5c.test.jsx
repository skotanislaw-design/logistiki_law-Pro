/**
 * Ρυθμίσεις — δεύτερος έλεγχος του γύρου 5 (δοκιμές μεταλλάξεων): το ποσοστό που προτείνει κάθε κατηγορία παρακράτησης είναι
 * αυτό που εμφανίζεται ΚΑΙ αυτό που αποθηκεύεται (η κατηγορία «3», που ξεκινούν τα νέα γραφεία, δεν είχε δική της δοκιμή).
 */
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { describe, expect, it, vi } from "vitest";

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

describe("Ρυθμίσεις — προτεινόμενο ποσοστό ανά κατηγορία παρακράτησης", () => {
  it.each([
    ["10", "3 — Αμοιβές συμβούλων διοίκησης", 20, 0.2],
    ["3", "10 — Προκαταβλητέος φόρος αμοιβών δικηγόρων", 15, 0.15],
    ["3", "2 — Δικαιώματα", 20, 0.2],
  ])("από την κατηγορία «%s» στην «%s»: το πεδίο δείχνει ποσοστό %s και στέλνεται το κλάσμα %s", async (from, label, shown, stored) => {
    let saved = null;
    const user = userEvent.setup();
    const rate = { 10: 0.15, 3: 0.2 }[from];
    server.use(
      http.get(`${API}/settings`, () => HttpResponse.json(settingsDoc({ withholding_category: from, withholding_rate: rate }))),
      http.put(`${API}/settings`, async ({ request }) => { saved = await request.json(); return HttpResponse.json(saved); }),
    );
    render(<Settings />);
    await user.click(await screen.findByTestId("settings-withholding-category"));
    await user.click(await screen.findByRole("option", { name: label }));
    expect(screen.getByTestId("settings-withholding-rate")).toHaveValue(shown);
    await user.click(screen.getByTestId("save-settings-button"));
    await waitFor(() => expect(saved).not.toBeNull());
    expect(saved.withholding_category).toBe(label.split(" ")[0]);
    expect(saved.withholding_rate).toBe(stored);
  });
});
