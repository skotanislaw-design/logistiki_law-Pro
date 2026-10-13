/**
 * ΔΙΟΡΘΩΘΗΚΕ (frontend/src/pages/Settings.jsx): το ίδιο αρχείο δεν μπορούσε να επιλεγεί ξανά στην επαναφορά αντιγράφου και στην
 * εισαγωγή πελατολογίου.
 *
 * Το `<input type="file">` ενεργοποιεί το `change` μόνο όταν αλλάξει η επιλογή. Η οθόνη «Τράπεζα» το λύνει με `e.target.value = ""`
 * (βλ. Bank.jsx → onPickFile, «να μπορεί να ξαναεπιλεγεί το ίδιο αρχείο»)· οι Ρυθμίσεις όχι. Έτσι, αν ο διαχειριστής αρνιόταν την
 * επιβεβαίωση της επαναφοράς, ή αν ο server απαντούσε με προσωρινό σφάλμα, ή αν διόρθωνε ένα πελατολόγιο και το αποθήκευε με το
 * ίδιο όνομα, δεν μπορούσε να ξαναδοκιμάσει με το ίδιο αρχείο χωρίς να ανανεώσει τη σελίδα ή να διαλέξει άλλο αρχείο.
 * (Το user-event προσομοιώνει ακριβώς τη συμπεριφορά του browser: ίδια επιλογή = κανένα `change`.)
 *
 * Ισχύει: ο χειριστής μηδενίζει την τιμή του πεδίου μετά την ανάγνωση του αρχείου.
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

const settingsDoc = () => ({
  company_name: "Skotanis & Associates", afm: "123456783", doy: "", address: "", phone: "", email: "",
  vat_period: "monthly", iban: "", bank_name: "", smtp_host: "", smtp_port: 587, smtp_user: "", smtp_password: "",
  smtp_from_name: "", aade_user_id: "", aade_subscription_key: "", aade_environment: "sandbox", mydata_connected: false,
  withholding_enabled: true, withholding_category: "3", withholding_rate: 0.2, withholding_min_net: 300,
});

describe("Ρυθμίσεις — το ίδιο αρχείο μπορεί να ξαναεπιλεγεί", () => {
  afterEach(() => vi.restoreAllMocks());

  it("επαναφορά αντιγράφου: μετά την άρνηση της επιβεβαίωσης, το ίδιο ZIP ξαναστέλνεται όταν ο διαχειριστής το ξαναεπιλέξει", async () => {
    const confirm = vi.spyOn(window, "confirm").mockReturnValueOnce(false).mockReturnValue(true);
    const user = userEvent.setup({ applyAccept: false });
    let posts = 0;
    server.use(
      http.get(`${API}/settings`, () => HttpResponse.json(settingsDoc())),
      http.post(`${API}/backup/import`, () => {
        posts += 1;
        return HttpResponse.json({ message: "Η επαναφορά ολοκληρώθηκε.", restored: {}, already_present: {}, differing: {}, failed: 0, notes: [], errors: [] });
      }),
    );
    render(<><Settings /><Toaster /></>);
    const zip = new File(["zip"], "backup.zip", { type: "application/zip" });
    const input = await screen.findByTestId("import-backup-input");
    await user.upload(input, zip);
    expect(confirm).toHaveBeenCalledTimes(1);
    expect(posts).toBe(0);

    await user.upload(input, zip);                                               // το ΙΔΙΟ αρχείο, μετά την άρνηση
    await waitFor(() => expect(confirm).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(posts).toBe(1));
  });

  it("εισαγωγή πελατολογίου: το ίδιο αρχείο ξαναστέλνεται μετά από σφάλμα του server", async () => {
    const user = userEvent.setup({ applyAccept: false });
    let posts = 0;
    server.use(
      http.get(`${API}/settings`, () => HttpResponse.json(settingsDoc())),
      http.post(`${API}/import/clients`, () => {
        posts += 1;
        return posts === 1
          ? HttpResponse.json({ detail: "Προσωρινό σφάλμα." }, { status: 503 })
          : HttpResponse.json({ message: "Εισήχθησαν 3 πελάτες.", total_rows: 3, errors: [] });
      }),
    );
    render(<><Settings /><Toaster /></>);
    const csv = new File(["Όνομα;ΑΦΜ\nΑΛΦΑ ΑΕ;123456783\n"], "pelates.csv", { type: "text/csv" });
    const input = await screen.findByTestId("import-clients-input");
    await user.upload(input, csv);
    await waitFor(() => expect(posts).toBe(1));
    await waitFor(() => expect(screen.getByTestId("import-clients-button")).toBeEnabled());

    await user.upload(input, csv);                                               // το ΙΔΙΟ αρχείο, μετά το σφάλμα
    expect(await screen.findByTestId("import-result")).toHaveTextContent("Εισήχθησαν 3 πελάτες.");
    expect(posts).toBe(2);
  });
});
