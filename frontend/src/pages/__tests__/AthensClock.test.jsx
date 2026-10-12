/**
 * Ώρα Ελλάδας στις οθόνες (Γύρος 5, ανασκόπηση 4).
 *
 * Κάθε ημερομηνία και ώρα της εφαρμογής είναι της Ελλάδας, όπου κι αν βρίσκεται η συσκευή: ο server υπολογίζει εκεί
 * προθεσμίες, αρίθμηση και αναφορές, και ο δικηγόρος μπορεί να ανοίξει την εφαρμογή από το εξωτερικό. Τα tests παγώνουν
 * το ρολόι στη «δύσκολη ώρα» — 01:30 τα ξημερώματα της Δευτέρας 12/10/2026 στην Αθήνα, που στο UTC είναι ακόμη 11/10 —
 * και επαναλαμβάνονται σε πέντε ζώνες του μηχανήματος· ό,τι εξαρτάται από τη ζώνη της συσκευής (ή από το UTC)
 * αποτυγχάνει σε τουλάχιστον μία από αυτές.
 *
 * Εδώ ελέγχεται ότι **κάθε οθόνη χρησιμοποιεί** τους βοηθούς του src/lib/utils.js· οι ίδιοι οι βοηθοί ελέγχονται στο
 * src/lib/__tests__/dates.test.js.
 */
import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { Topbar } from "@/components/Layout/Topbar";
import { OneScreenDashboard } from "@/components/OneScreenDashboard";
import { Toaster } from "@/components/ui/sonner";
import Analytics from "@/pages/Analytics";
import Autopilot from "@/pages/Autopilot";
import Documents from "@/pages/Documents";
import Invoices from "@/pages/Invoices";
import Payroll from "@/pages/Payroll";
import Pinakia from "@/pages/Pinakia";
import PocketPinakia from "@/pages/PocketPinakia";
import Signatures from "@/pages/Signatures";
import { MACHINE_ZONES, freezeAt, useMachineZone } from "@/test/machineZone";
import { server } from "@/test/server";

const API = "http://localhost:8001/api";

const NIGHT = "2026-10-11T22:30:00Z";           // Δευτέρα 12/10/2026 01:30 στην Αθήνα · στο UTC ακόμη 11/10
const AFTER_MIDNIGHT = "2026-10-12T21:30:00Z";   // Τρίτη 13/10/2026 00:30 στην Αθήνα · στο UTC ακόμη 12/10
const MONTH_END = "2026-10-31T22:30:00Z";        // Κυριακή 1/11/2026 00:30 στην Αθήνα · στο UTC ακόμη Οκτώβριος
const YEAR_END = "2026-12-31T22:30:00Z";         // 1/1/2027 00:30 στην Αθήνα · στο UTC ακόμη 2026

// Η συνεδρία που «βλέπει» η εφαρμογή· η επάνω μπάρα και το Autopilot διαβάζουν τον χρήστη από το context.
vi.mock("@/context/AuthContext", () => ({
  useAuth: () => ({ user: { name: "Χρήστος Σκοτάνης", role: "admin" } }),
}));
vi.mock("@/components/NotificationBell", () => ({ NotificationBell: () => null }));

// Τα γραφήματα (recharts) δεν μετρούν διαστάσεις στο jsdom και δεν είναι αυτό που ελέγχεται εδώ.
vi.mock("recharts", async () => {
  const React = await import("react");
  const Box = ({ children }) => React.createElement("div", null, children);
  const Nothing = () => null;
  return {
    ResponsiveContainer: Box, BarChart: Box, PieChart: Box, Pie: Box, Bar: Nothing, Cell: Nothing, XAxis: Nothing,
    YAxis: Nothing, CartesianGrid: Nothing, Tooltip: Nothing, Legend: Nothing,
  };
});

const inRouter = (ui) => render(<MemoryRouter>{ui}</MemoryRouter>);

// Χειροκίνητο «ρολόι» για τις περιοδικές ανανεώσεις: πιάνει τον χρονομετρητή με τη δοσμένη περίοδο και επιτρέπει
// να τον «χτυπήσουμε» όσες φορές θέλουμε, χωρίς να περιμένουμε δευτερόλεπτα ούτε να πειράξουμε τους υπόλοιπους.
function captureInterval(period) {
  const real = globalThis.setInterval;
  const callbacks = [];
  vi.spyOn(globalThis, "setInterval").mockImplementation((fn, ms, ...rest) => {
    if (ms === period) {
      callbacks.push(fn);
      return 0;
    }
    return real.call(globalThis, fn, ms, ...rest);
  });
  return {
    registered: () => callbacks.length,
    tick: () => act(async () => { await Promise.all(callbacks.map((fn) => fn())); }),
  };
}

const errorToasts = () => [...document.querySelectorAll('li[data-sonner-toast][data-type="error"]')];

const summary = {
  income_total: 0, expense_total: 0, vat_due: 0, next_vat_deadline: "2026-11-30", next_efka_deadline: "2026-10-31",
  pending_mydata: 0, draft_invoices: 0, chart_data: [], recent_invoices: [], recent_expenses: [],
  year_income: 0, year_profit: 0, unpaid_count: 0, unpaid_total: 0, output_vat: 0, input_vat: 0,
  advice: "", obligations: [],
};

const hearing = (over = {}) => ({
  id: "h1", court: "Πλημμελειοδικείο Αθηνών", case_title: "Υπόθεση Α", date: "2026-10-12", time: "09:00",
  pinakio_number: 12, current_number: 3, notify_threshold: 5, status: "in_progress",
  events: [{ at: "2026-10-11T22:30:00+00:00", event: "Ενημέρωση νούμερου" }], ...over,
});

const invoice = (id, date, over = {}) => ({
  id, number: `ΤΠΥ-${id}`, client_id: "c1", client_name: "ΑΛΦΑ ΑΕ", date, status: "issued", line_items: [],
  subtotal: 100, vat_amount: 24, total: 124, outstanding: 124, mydata_status: "synced", ...over,
});

afterEach(() => vi.restoreAllMocks());

describe.each(MACHINE_ZONES)("ώρα Ελλάδας στις οθόνες — μηχάνημα σε %s", (zone) => {
  useMachineZone(zone);

  describe("επάνω μπάρα και επισκόπηση", () => {
    it("η επάνω μπάρα δείχνει ημερομηνία και ρολόι Ελλάδας", () => {
      freezeAt(NIGHT);
      inRouter(<Topbar />);
      expect(screen.getByTestId("topbar")).toHaveTextContent("Δευτέρα 12 Οκτωβρίου 2026");
      expect(screen.getByTestId("topbar-clock")).toHaveTextContent("1:30:00 π.μ.");
    });

    it("η επισκόπηση μίας οθόνης γράφει τη σημερινή ημερομηνία Ελλάδας", () => {
      freezeAt(NIGHT);
      render(<OneScreenDashboard data={summary} />);
      expect(screen.getByText("Δευτέρα 12 Οκτωβρίου 2026")).toBeInTheDocument();
    });

    it("η ημερομηνία αλλάζει στα μεσάνυχτα Ελλάδας, όχι του μηχανήματος", () => {
      freezeAt(AFTER_MIDNIGHT);
      render(<OneScreenDashboard data={summary} />);
      expect(screen.getByText("Τρίτη 13 Οκτωβρίου 2026")).toBeInTheDocument();
    });
  });

  describe("Έγγραφα", () => {
    it("η επιστολή γράφει «Αθήνα, <σημερινή ημερομηνία Ελλάδας>», όπως θα τυπωθεί και στο Word/PDF", () => {
      freezeAt(NIGHT);
      render(<><Documents /><Toaster /></>);
      expect(screen.getByTestId("documents-preview")).toHaveTextContent("Αθήνα, 12 Οκτωβρίου 2026");
    });

    it("την 1η του μήνα και του έτους δεν μένει στην προηγούμενη ημέρα", () => {
      freezeAt(YEAR_END);
      render(<><Documents /><Toaster /></>);
      expect(screen.getByTestId("documents-preview")).toHaveTextContent("Αθήνα, 1 Ιανουαρίου 2027");
    });
  });

  describe("Μισθοδοσία", () => {
    const employee = { id: "e1", name: "Μαρία Παπά", afm: "", amka: "", position: "Γραμματέας", gross_salary: 1200, dependents: 0, active: true };

    function servePayroll(posted = []) {
      server.use(
        http.get(`${API}/payroll/employees`, () => HttpResponse.json([employee])),
        http.get(`${API}/payroll/runs`, () => HttpResponse.json([])),
        http.post(`${API}/payroll/run`, async ({ request }) => {
          posted.push(await request.json());
          return HttpResponse.json({ id: "run1" });
        }),
      );
      return posted;
    }

    it("η προεπιλεγμένη περίοδος είναι ο τρέχων μήνας Ελλάδας", async () => {
      freezeAt(NIGHT);
      servePayroll();
      render(<><Payroll /><Toaster /></>);
      expect(await screen.findByTestId("employee-row-e1")).toBeInTheDocument();
      expect(screen.getByTestId("payroll-period-input")).toHaveValue("2026-10");
    });

    it("τα ξημερώματα της 1ης του μήνα η περίοδος είναι ο νέος μήνας και αυτός στέλνεται στον υπολογισμό", async () => {
      freezeAt(MONTH_END);                                     // Οκτώβριος στο UTC, 1 Νοεμβρίου στην Αθήνα
      const posted = servePayroll();
      const user = userEvent.setup();
      render(<><Payroll /><Toaster /></>);
      expect(await screen.findByTestId("employee-row-e1")).toBeInTheDocument();
      expect(screen.getByTestId("payroll-period-input")).toHaveValue("2026-11");
      await user.click(screen.getByTestId("run-payroll-e1"));
      await waitFor(() => expect(posted).toEqual([{ employee_id: "e1", period: "2026-11" }]));
    });
  });

  describe("Τιμολόγια — καθυστέρηση 30+ ημερών", () => {
    function serveInvoices(rows) {
      server.use(
        http.get(`${API}/invoices`, () => HttpResponse.json(rows)),
        http.get(`${API}/clients`, () => HttpResponse.json([])),
      );
    }

    it("μετρά ημερολογιακές ημέρες Ελλάδας: 30 ημέρες είναι καθυστέρηση, 29 όχι", async () => {
      freezeAt(NIGHT);                                         // Δευτέρα 12/10 στην Αθήνα
      serveInvoices([
        invoice("a", "2026-09-12"),                            // ακριβώς 30 ημέρες
        invoice("b", "2026-09-13"),                            // 29 ημέρες
        invoice("c", "2026-10-13"),                            // μελλοντική ημερομηνία
        invoice("d", "2026-08-01", { status: "paid", outstanding: 0 }),   // εξοφλημένο: ποτέ «καθυστέρηση»
      ]);
      render(<><Invoices /><Toaster /></>);
      const rowA = await screen.findByTestId("invoice-row-a");
      expect(within(rowA).getByText(/30η/)).toBeInTheDocument();
      for (const id of ["b", "c", "d"]) {
        expect(within(screen.getByTestId(`invoice-row-${id}`)).queryByText(/\d+η$/)).not.toBeInTheDocument();
      }
      expect(screen.getByTestId("kpi-unpaid")).toHaveTextContent("· 1 με καθυστέρηση 30+ ημερών");
    });

    it("μετά τα μεσάνυχτα Ελλάδας μπαίνει στην καθυστέρηση και το τιμολόγιο της επόμενης ημέρας", async () => {
      freezeAt(AFTER_MIDNIGHT);                                // Τρίτη 13/10 στην Αθήνα, ακόμη 12/10 στο UTC
      serveInvoices([invoice("a", "2026-09-12"), invoice("b", "2026-09-13"), invoice("c", "2026-09-14")]);
      render(<><Invoices /><Toaster /></>);
      const rowB = await screen.findByTestId("invoice-row-b");
      expect(within(rowB).getByText(/30η/)).toBeInTheDocument();
      expect(within(screen.getByTestId("invoice-row-a")).getByText(/31η/)).toBeInTheDocument();
      expect(within(screen.getByTestId("invoice-row-c")).queryByText(/\d+η$/)).not.toBeInTheDocument();
      expect(screen.getByTestId("kpi-unpaid")).toHaveTextContent("· 2 με καθυστέρηση 30+ ημερών");
    });

    it("όταν δεν υπάρχει καθυστέρηση δεν γράφει τίποτα", async () => {
      freezeAt(NIGHT);
      serveInvoices([invoice("b", "2026-09-13")]);
      render(<><Invoices /><Toaster /></>);
      await screen.findByTestId("invoice-row-b");
      expect(screen.getByTestId("kpi-unpaid")).not.toHaveTextContent("καθυστέρηση");
    });
  });

  describe("Πινάκια", () => {
    it("η ροή της συνεδρίασης δείχνει ώρα και ημερομηνία Ελλάδας", async () => {
      freezeAt(NIGHT);
      server.use(http.get(`${API}/hearings`, () => HttpResponse.json([hearing()])));
      render(<><Pinakia /><Toaster /></>);
      const entry = await screen.findByText("Ενημέρωση νούμερου");
      expect(entry).toHaveTextContent("1:30:00 π.μ. · 12/10/2026");
    });

    it("δείχνει μία ειδοποίηση ανά σειρά αποτυχιών ανανέωσης και ξανά μόλις κάποια ανανέωση πετύχει", async () => {
      let mode = "fail";
      let calls = 0;
      server.use(http.get(`${API}/hearings`, () => {
        calls += 1;
        return mode === "fail"
          ? HttpResponse.json({ detail: `Αποτυχία σύνδεσης (${calls})` }, { status: 503 })
          : HttpResponse.json([hearing()]);
      }));
      const clock = captureInterval(15000);
      render(<><Pinakia /><Toaster /></>);

      expect(await screen.findByText("Αποτυχία σύνδεσης (1)")).toBeInTheDocument();     // 1η αποτυχία: ανακοινώνεται
      expect(clock.registered()).toBe(1);                                               // η σελίδα ανανεώνεται κάθε 15″
      await clock.tick();
      await clock.tick();
      expect(calls).toBe(3);                                                            // 2η και 3η αποτυχία: σιωπηλές

      mode = "ok";
      await clock.tick();                                                               // η ανανέωση πετυχαίνει…
      expect(await screen.findByTestId("hearing-card-h1")).toBeInTheDocument();
      mode = "fail";
      await clock.tick();                                                               // …και η επόμενη αποτυχία ανακοινώνεται ξανά
      expect(await screen.findByText("Αποτυχία σύνδεσης (5)")).toBeInTheDocument();

      expect(errorToasts().map((t) => t.textContent)).toEqual(["Αποτυχία σύνδεσης (5)", "Αποτυχία σύνδεσης (1)"]);
    });

    it("η επιτυχημένη ανανέωση δεν ανακοινώνει τίποτα", async () => {
      server.use(http.get(`${API}/hearings`, () => HttpResponse.json([hearing()])));
      const clock = captureInterval(15000);
      render(<><Pinakia /><Toaster /></>);
      await screen.findByTestId("hearing-card-h1");
      await clock.tick();
      await clock.tick();
      expect(errorToasts()).toEqual([]);
    });
  });

  describe("Πινάκιο τσέπης", () => {
    const serve = (list, alerts = []) => server.use(
      http.get(`${API}/hearings`, () => HttpResponse.json(list)),
      http.get(`${API}/hearings/alerts/list`, () => HttpResponse.json(alerts)),
    );

    it("κρατά τα σημερινά και επόμενα πινάκια με βάση την ημερομηνία Ελλάδας", async () => {
      freezeAt(NIGHT);                                         // σήμερα είναι 12/10 στην Αθήνα
      serve([
        hearing({ id: "yesterday-done", date: "2026-10-11", status: "completed" }),
        hearing({ id: "yesterday-postponed", date: "2026-10-11", status: "postponed" }),
        hearing({ id: "today-done", date: "2026-10-12", status: "completed" }),
        hearing({ id: "older-live", date: "2026-10-10", status: "in_progress" }),   // τρέχει ακόμη: μένει
        hearing({ id: "tomorrow", date: "2026-10-13", status: "scheduled" }),
      ]);
      inRouter(<><PocketPinakia /><Toaster /></>);
      expect(await screen.findByTestId("pocket-hearing-today-done")).toBeInTheDocument();
      expect(screen.getByTestId("pocket-hearing-older-live")).toBeInTheDocument();
      expect(screen.getByTestId("pocket-hearing-tomorrow")).toBeInTheDocument();
      expect(screen.queryByTestId("pocket-hearing-yesterday-done")).not.toBeInTheDocument();
      expect(screen.queryByTestId("pocket-hearing-yesterday-postponed")).not.toBeInTheDocument();
    });

    it("μετά τα μεσάνυχτα Ελλάδας το χθεσινό ολοκληρωμένο πινάκιο φεύγει από τη λίστα", async () => {
      freezeAt(AFTER_MIDNIGHT);                                // σήμερα είναι 13/10 στην Αθήνα, ακόμη 12/10 στο UTC
      serve([hearing({ id: "done-12", date: "2026-10-12", status: "completed" }), hearing({ id: "next-13", date: "2026-10-13", status: "scheduled" })]);
      inRouter(<><PocketPinakia /><Toaster /></>);
      expect(await screen.findByTestId("pocket-hearing-next-13")).toBeInTheDocument();
      expect(screen.queryByTestId("pocket-hearing-done-12")).not.toBeInTheDocument();
    });

    it("το ιστορικό της συνεδρίασης δείχνει ώρα Ελλάδας", async () => {
      freezeAt(NIGHT);
      serve([hearing()]);
      inRouter(<><PocketPinakia /><Toaster /></>);
      const card = await screen.findByTestId("pocket-hearing-h1");
      expect(card).toHaveTextContent("· 01:30 π.μ. — Ενημέρωση νούμερου");
    });

    it("δείχνει μία ειδοποίηση ανά σειρά αποτυχιών ανανέωσης και ξανά μόλις κάποια ανανέωση πετύχει", async () => {
      let mode = "fail";
      let calls = 0;
      server.use(
        http.get(`${API}/hearings`, () => {
          calls += 1;
          return mode === "fail"
            ? HttpResponse.json({ detail: `Αποτυχία σύνδεσης (${calls})` }, { status: 503 })
            : HttpResponse.json([hearing()]);
        }),
        http.get(`${API}/hearings/alerts/list`, () => HttpResponse.json([])),
      );
      const clock = captureInterval(30000);
      inRouter(<><PocketPinakia /><Toaster /></>);
      const refresh = screen.getByTestId("pocket-refresh");
      const settled = () => waitFor(() => expect(refresh).toBeEnabled());

      expect(await screen.findByText("Αποτυχία σύνδεσης (1)")).toBeInTheDocument();
      await settled();
      expect(clock.registered()).toBe(1);                                               // ανανέωση κάθε 30″
      await clock.tick(); await settled();
      await clock.tick(); await settled();
      expect(calls).toBe(3);                                                            // 2η και 3η αποτυχία: σιωπηλές

      mode = "ok";
      await clock.tick(); await settled();
      expect(await screen.findByTestId("pocket-hearing-h1")).toBeInTheDocument();
      mode = "fail";
      await clock.tick(); await settled();
      expect(await screen.findByText("Αποτυχία σύνδεσης (5)")).toBeInTheDocument();

      expect(errorToasts().map((t) => t.textContent)).toEqual(["Αποτυχία σύνδεσης (5)", "Αποτυχία σύνδεσης (1)"]);
    });
  });

  describe("Υπογραφές και ιστορικό Autopilot", () => {
    it("η ημερομηνία και ώρα της υπογραφής είναι της Ελλάδας", async () => {
      server.use(http.get(`${API}/signatures`, () => HttpResponse.json([
        { id: "s1", document_title: "Εντολή ανάθεσης", signer_name: "ΑΛΦΑ ΑΕ", signer_role: "client",
          signed_at: "2026-10-11T22:30:00+00:00", signature_hash: "abcdef0123456789abcdef" },
      ])));
      render(<><Signatures /><Toaster /></>);
      expect(await screen.findByTestId("sig-row-s1")).toHaveTextContent("12/10/2026, 1:30:00 π.μ.");
    });

    it("το ιστορικό κύκλων του Autopilot δείχνει πότε έτρεξε ο κύκλος σε ώρα Ελλάδας", async () => {
      server.use(
        http.get(`${API}/automation/status`, () => HttpResponse.json({
          open_items: 0, last_7_days: {}, cycle_minutes: 15, last_digest_at: null,
          capabilities: { email: true, mydata: true, stripe: true, ai: true, imap: true, mydata_expenses: true, openbanking: true },
        })),
        http.get(`${API}/automation/notifications`, () => HttpResponse.json([])),
        http.get(`${API}/automation/runs`, () => HttpResponse.json([
          { id: "r1", label: "Υπενθυμίσεις πληρωμής", status: "ok", stats: { sent: 1 }, started_at: "2026-10-11T22:30:00+00:00" },
        ])),
        http.get(`${API}/automation/vat-periods`, () => HttpResponse.json([])),
        http.get(`${API}/settings`, () => HttpResponse.json({ autopilot_enabled: true })),
      );
      const user = userEvent.setup();
      inRouter(<><Autopilot /><Toaster /></>);
      await user.click(await screen.findByTestId("tab-runs"));
      const row = (await screen.findByText("Υπενθυμίσεις πληρωμής")).closest("tr");
      expect(row).toHaveTextContent("12/10/2026, 1:30:00 π.μ.");
    });
  });

  describe("Αναλύσεις", () => {
    const profitability = (start) => ({
      period: { start },
      totals: { revenue: 0, paid: 0, unpaid: 0, avg_invoice: 0, invoice_count: 0, collection_rate_pct: 0 },
      top_clients: [], case_types: [],
    });

    it("το έτος του τίτλου είναι το έτος της περιόδου που υπολόγισε ο server", async () => {
      freezeAt(YEAR_END);
      server.use(http.get(`${API}/analytics/profitability`, () => HttpResponse.json(profitability("2027-01-01"))));
      render(<><Analytics /><Toaster /></>);
      expect(await screen.findByText(/Έτος 2027 — από 1\/1\/2027 έως σήμερα/)).toBeInTheDocument();
    });

    it("ο τίτλος ακολουθεί την περίοδο που υπολόγισε ο server, όχι το ρολόι της συσκευής", async () => {
      freezeAt(NIGHT);                                         // 2026 στο ρολόι· η αναφορά του server αφορά το 2025
      server.use(http.get(`${API}/analytics/profitability`, () => HttpResponse.json(profitability("2025-01-01"))));
      render(<><Analytics /><Toaster /></>);
      expect(await screen.findByText(/Έτος 2025 — από 1\/1\/2025 έως σήμερα/)).toBeInTheDocument();
    });

    it("αν ο server δεν στείλει περίοδο, χρησιμοποιεί το έτος Ελλάδας και όχι της συσκευής", async () => {
      freezeAt(YEAR_END);
      server.use(http.get(`${API}/analytics/profitability`, () => HttpResponse.json(profitability(""))));
      render(<><Analytics /><Toaster /></>);
      expect(await screen.findByText(/^Έτος 2027 — από - έως σήμερα$/)).toBeInTheDocument();
    });
  });
});
