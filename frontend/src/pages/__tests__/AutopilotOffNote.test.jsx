/**
 * Autopilot — τι σημαίνει «κλειστός» (γύρος 5, δεύτερος έλεγχος).
 *
 * Με κλειστό τον γενικό διακόπτη ο server συνεχίζει να τρέχει δύο ελέγχους (`ALWAYS_ON_JOBS`: κενά αρίθμησης, πληρωμές με
 * κάρτα). Η οθόνη το λέει ρητά, ώστε το «κλειστό» να μη διαβάζεται ως «δεν τρέχει τίποτα».
 */
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse, http } from "msw";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import { Toaster } from "@/components/ui/sonner";
import Autopilot from "@/pages/Autopilot";
import { server } from "@/test/server";

const API = "http://localhost:8001/api";

vi.mock("@/context/AuthContext", () => ({
  useAuth: () => ({ user: { name: "Χρήστος Σκοτάνης", role: "admin" } }),
}));

function serve(settings) {
  const state = { settings };
  server.use(
    http.get(`${API}/automation/status`, () => HttpResponse.json({
      open_items: 0, last_7_days: {}, cycle_minutes: 15, last_digest_at: null,
      capabilities: { email: true, mydata: true, stripe: true, ai: true, imap: true, mydata_expenses: true, openbanking: true },
    })),
    http.get(`${API}/automation/notifications`, () => HttpResponse.json([])),
    http.get(`${API}/automation/runs`, () => HttpResponse.json([])),
    http.get(`${API}/automation/vat-periods`, () => HttpResponse.json([])),
    http.get(`${API}/settings`, () => HttpResponse.json(state.settings)),
    http.patch(`${API}/settings`, async ({ request }) => {
      state.settings = { ...state.settings, ...(await request.json()) };
      return HttpResponse.json(state.settings);
    }),
  );
}

const renderAutopilot = () => render(<MemoryRouter><Autopilot /><Toaster /></MemoryRouter>);

describe("Autopilot — ο γενικός διακόπτης", () => {
  it("όταν είναι κλειστός, λέει ότι δύο έλεγχοι συνεχίζουν να τρέχουν και τι κάνουν", async () => {
    serve({ autopilot_enabled: false });
    renderAutopilot();

    const note = await screen.findByTestId("autopilot-off-note");
    expect(note).toHaveTextContent("Ο Autopilot είναι κλειστός");
    expect(note).toHaveTextContent("Δύο έλεγχοι συνεχίζουν πάντα να τρέχουν");
    expect(note).toHaveTextContent("κενών στην αρίθμηση των τιμολογίων");
    expect(note).toHaveTextContent("πληρωμών με κάρτα");
    expect(note).toHaveTextContent("Δεν στέλνουν τίποτα σε τρίτους");
  });

  it("όταν είναι ανοιχτός, δεν υπάρχει τέτοια σημείωση", async () => {
    serve({ autopilot_enabled: true });
    renderAutopilot();

    await screen.findByTestId("autopilot-page");
    expect(screen.queryByTestId("autopilot-off-note")).not.toBeInTheDocument();
  });

  it("εμφανίζεται και εξαφανίζεται μόλις αλλάξει ο διακόπτης", async () => {
    const user = userEvent.setup();
    serve({ autopilot_enabled: true });
    renderAutopilot();
    await screen.findByTestId("autopilot-page");

    await user.click(screen.getByTestId("autopilot-toggle"));
    expect(await screen.findByTestId("autopilot-off-note")).toBeInTheDocument();

    await user.click(screen.getByTestId("autopilot-toggle"));
    await waitFor(() => expect(screen.queryByTestId("autopilot-off-note")).not.toBeInTheDocument());
  });
});
