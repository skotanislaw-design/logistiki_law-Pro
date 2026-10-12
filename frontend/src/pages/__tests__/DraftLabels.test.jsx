/**
 * Το πρόχειρο παραστατικό δεν έχει ακόμη αριθμό (δίνεται στην έκδοση — βλ. issue_draft).
 * Κάθε οθόνη που δείχνει τιμολόγιο πρέπει να γράφει «Πρόχειρο», όχι κενό. Το Copilot δημιουργεί
 * μόνο πρόχειρα και ο πίνακας ελέγχου δείχνει και πρόχειρα στα «Πρόσφατα», οπότε εκεί
 * το κενό θα ήταν το συνηθισμένο, όχι το σπάνιο.
 */
import { render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { HttpResponse, http } from "msw";
import { describe, expect, it, vi } from "vitest";

import { InvoiceDetailDialog } from "@/components/InvoiceDetailDialog";
import { OneScreenDashboard } from "@/components/OneScreenDashboard";
import { ViewModeProvider } from "@/context/ViewModeContext";
import Copilot from "@/pages/Copilot";
import Dashboard from "@/pages/Dashboard";
import { server } from "@/test/server";

const API = "http://localhost:8001/api";

const draft = { id: "d1", number: "", status: "draft", client_name: "ΑΛΦΑ ΑΕ", total: 124, subtotal: 100, vat_amount: 24, line_items: [], date: "2026-10-12" };
const issued = { id: "i1", number: "ΤΠΥ-2026-0007", status: "issued", client_name: "ΒΗΤΑ ΕΠΕ", total: 62, subtotal: 50, vat_amount: 12, line_items: [], date: "2026-10-12" };

const summary = {
  income_total: 0, expense_total: 0, vat_due: 0, next_vat_deadline: "2026-11-30", next_efka_deadline: "2026-10-31",
  pending_mydata: 0, draft_invoices: 1, chart_data: [], recent_invoices: [draft, issued], recent_expenses: [],
  year_income: 0, year_profit: 0, unpaid_count: 0, unpaid_total: 0, output_vat: 0, input_vat: 0,
  advice: "", obligations: [],
};

// Τα γραφήματα (recharts) δεν μετρούν διαστάσεις στο jsdom και δεν είναι αυτό που ελέγχεται εδώ.
vi.mock("recharts", async () => {
  const React = await import("react");
  const Box = ({ children }) => React.createElement("div", null, children);
  const Nothing = () => null;
  return {
    ResponsiveContainer: Box, BarChart: Box, Bar: Nothing, XAxis: Nothing, YAxis: Nothing,
    CartesianGrid: Nothing, Tooltip: Nothing, Legend: Nothing,
  };
});

describe("Πρόχειρο παραστατικό χωρίς αριθμό", () => {
  it("στον κλασικό πίνακα ελέγχου", async () => {
    server.use(
      http.get(`${API}/settings`, () => HttpResponse.json({ onboarding_completed: true })),
      http.get(`${API}/automation/status`, () => HttpResponse.json(null)),
      http.get(`${API}/dashboard/summary`, () => HttpResponse.json(summary)),
    );
    localStorage.setItem("nomos_view_mode", "classic");
    render(<MemoryRouter><ViewModeProvider><Dashboard /></ViewModeProvider></MemoryRouter>);
    const page = await screen.findByTestId("dashboard-page");
    expect(within(page).getByText("Πρόχειρο")).toBeInTheDocument();
    expect(within(page).getByText("ΤΠΥ-2026-0007")).toBeInTheDocument();
  });

  it("στον πίνακα ελέγχου μίας οθόνης", () => {
    render(<OneScreenDashboard data={summary} />);
    const card = screen.getByTestId("os-recent-card");
    expect(within(card).getByText(/Πρόχειρο/)).toBeInTheDocument();
    expect(within(card).getByText(/ΤΠΥ-2026-0007/)).toBeInTheDocument();
  });

  it("στον τίτλο των λεπτομερειών τιμολογίου", () => {
    render(<InvoiceDetailDialog invoice={draft} open onOpenChange={() => {}} />);
    expect(screen.getByRole("heading", { name: "Τιμολόγιο Πρόχειρο" })).toBeInTheDocument();
  });

  it("στην κάρτα που δημιουργεί το Copilot", async () => {
    sessionStorage.setItem("copilot_session", "s1");
    server.use(http.get(`${API}/copilot/history/s1`, () => HttpResponse.json([
      { role: "assistant", content: "Δημιούργησα πρόχειρο τιμολόγιο.", invoice: draft },
    ])));
    render(<MemoryRouter><Copilot /></MemoryRouter>);
    const card = await screen.findByTestId("copilot-invoice-card");
    expect(card).toHaveTextContent("Πρόχειρο");
    expect(card).toHaveTextContent("ΑΛΦΑ ΑΕ");
  });
});
