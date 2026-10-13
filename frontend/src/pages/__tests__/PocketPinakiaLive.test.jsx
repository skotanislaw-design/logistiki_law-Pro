/**
 * Πινάκιο Τσέπης — D-08 του δεύτερου ελέγχου του γύρου 5 (ελεγκτής D).
 *
 * Η οθόνη υπάρχει για ένα πράγμα: να δείχνει το τρέχον νούμερο της αίθουσας. Τρία ελαττώματα την έκαναν να δείχνει ξεπερασμένο
 * νούμερο ΧΩΡΙΣ καμία ένδειξη:
 *  (α) η αποτυχία ανακοινωνόταν μία φορά ανά σειρά αποτυχιών — και όταν ο χρήστης πατούσε ο ίδιος «Ανανέωση» και αυτή
 *      αποτύγχανε, δεν έβλεπε τίποτα· μετά το πρώτο toast δεν έμενε ένδειξη ότι τα νούμερα δεν ανανεώνονται·
 *  (β) δεν υπήρχε προστασία σειράς: μια αργή απάντηση που ξεκίνησε νωρίτερα μπορούσε να σβήσει νεότερη·
 *  (γ) το «+1» έστελνε απόλυτη τιμή (εμφανιζόμενο + 1): από ξεπερασμένη οθόνη γύριζε το νούμερο πίσω.
 */
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { HttpResponse } from "msw";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { Toaster } from "@/components/ui/sonner";
import PocketPinakia from "@/pages/PocketPinakia";
import { DOWN, failure, gate, hearing, hearingsAt, serveHearings, serveLiveUpdate } from "@/test/liveFixtures";

const renderPocket = () => render(<MemoryRouter><PocketPinakia /><Toaster /></MemoryRouter>);
const current = () => screen.getByTestId("pocket-current-h1");

/** Προχωρά μόνο τον τακτικό χρονομετρητή (setInterval) και το ρολόι· το δίκτυο και οι ειδοποιήσεις μένουν πραγματικά. */
const clock = (now = "2026-10-14T07:27:20Z") => vi.useFakeTimers({ toFake: ["setInterval", "clearInterval", "Date"], now: new Date(now) });
const poll = (ms = 30_000) => act(async () => { vi.advanceTimersByTime(ms); });
const settle = () => act(async () => { await new Promise((resolve) => setTimeout(resolve, 60)); });

afterEach(() => { vi.useRealTimers(); vi.restoreAllMocks(); });

describe("Πινάκιο Τσέπης — αποτυχία φόρτωσης και ανανέωσης", () => {
  it("αν δεν φορτώσει ποτέ, δεν λέει «δεν υπάρχουν πινάκια»: δείχνει ότι η φόρτωση απέτυχε", async () => {
    serveHearings(() => failure());
    renderPocket();

    const banner = await screen.findByTestId("pocket-stale");
    expect(banner).toHaveTextContent("Δεν ήταν δυνατή η φόρτωση");
    expect(screen.queryByTestId("pocket-empty")).not.toBeInTheDocument();
    expect(screen.queryByTestId("pocket-loading")).not.toBeInTheDocument();
  });

  it("όσο φορτώνει δεν λέει ότι δεν υπάρχουν πινάκια", async () => {
    const door = gate();
    serveHearings(async () => { await door.opened; return hearingsAt(4); });
    renderPocket();

    expect(await screen.findByTestId("pocket-loading")).toBeInTheDocument();
    expect(screen.queryByTestId("pocket-empty")).not.toBeInTheDocument();
    door.release();
    expect(await screen.findByTestId("pocket-hearing-h1")).toBeInTheDocument();
    expect(screen.queryByTestId("pocket-loading")).not.toBeInTheDocument();
  });

  it("αν φορτώσει και είναι όντως άδεια, το λέει", async () => {
    serveHearings(() => HttpResponse.json([]));
    renderPocket();
    expect(await screen.findByTestId("pocket-empty")).toBeInTheDocument();
    expect(screen.queryByTestId("pocket-stale")).not.toBeInTheDocument();
  });

  it("αποτυχημένη ανανέωση: τα νούμερα μένουν και η ένδειξη λέει από ποια ώρα δεν ενημερώθηκε (ώρα Ελλάδας)", async () => {
    clock("2026-10-14T07:27:20Z");                          // 10:27 στην Ελλάδα (θερινή ώρα)
    serveHearings((n) => (n === 1 ? hearingsAt(4) : failure()));
    renderPocket();
    await screen.findByTestId("pocket-hearing-h1");
    expect(screen.queryByTestId("pocket-stale")).not.toBeInTheDocument();

    await poll();

    const banner = await screen.findByTestId("pocket-stale");
    expect(banner).toHaveTextContent(/Δεν ενημερώθηκε από 10:27 — τα πινάκια που βλέπετε μπορεί να έχουν αλλάξει\./);
    expect(current()).toHaveTextContent("4");
  });

  it("η ένδειξη μένει μετά από κάθε επόμενη αποτυχία και δεν αλλάζει την ώρα της τελευταίας επιτυχίας", async () => {
    clock("2026-10-14T07:27:20Z");
    serveHearings((n) => (n === 1 ? hearingsAt(4) : failure()));
    renderPocket();
    await screen.findByTestId("pocket-hearing-h1");

    await poll();
    await screen.findByTestId("pocket-stale");
    await poll();
    await poll();
    await settle();

    expect(screen.getByTestId("pocket-stale")).toHaveTextContent(/Δεν ενημερώθηκε από 10:27 —/);
  });

  it("η αποτυχία ανακοινώνεται με ειδοποίηση μία φορά ανά σειρά αποτυχιών, όχι σε κάθε κύκλο", async () => {
    clock();
    serveHearings((n) => (n === 1 ? hearingsAt(4) : failure()));
    renderPocket();
    await screen.findByTestId("pocket-hearing-h1");

    await poll(); await settle();
    await poll(); await settle();
    await poll(); await settle();

    expect(screen.getAllByText(DOWN)).toHaveLength(1);
  });

  it("το χειροκίνητο «Ανανέωση» ανακοινώνει την αποτυχία ακόμη κι αν ανακοινώθηκε ήδη στην ίδια σειρά", async () => {
    clock();
    const user = userEvent.setup();
    serveHearings((n) => (n === 1 ? hearingsAt(4) : failure()));
    renderPocket();
    await screen.findByTestId("pocket-hearing-h1");
    await poll(); await settle();
    expect(screen.getAllByText(DOWN)).toHaveLength(1);

    await user.click(screen.getByTestId("pocket-refresh"));

    await waitFor(() => expect(screen.getAllByText(DOWN)).toHaveLength(2));
  });

  it("το κουμπί «Επανάληψη» της ένδειξης ξαναδοκιμάζει και ανακοινώνει", async () => {
    const user = userEvent.setup();
    const calls = serveHearings((n) => (n === 1 ? failure() : hearingsAt(6)));
    renderPocket();
    await screen.findByTestId("pocket-stale");

    await user.click(screen.getByTestId("pocket-stale-retry"));

    await waitFor(() => expect(current()).toHaveTextContent("6"));
    expect(calls()).toBe(2);
    expect(screen.queryByTestId("pocket-stale")).not.toBeInTheDocument();
  });

  it("μια επιτυχημένη ανανέωση σβήνει την ένδειξη και ξαναοπλίζει την ανακοίνωση", async () => {
    clock();
    serveHearings((n) => (n === 2 ? failure() : n === 3 ? hearingsAt(5) : n === 4 ? failure() : hearingsAt(5)));
    renderPocket();
    await screen.findByTestId("pocket-hearing-h1");

    await poll(); await screen.findByTestId("pocket-stale");        // n=2: αποτυχία
    await poll(); await waitFor(() => expect(screen.queryByTestId("pocket-stale")).not.toBeInTheDocument());   // n=3: επιτυχία
    expect(current()).toHaveTextContent("5");
    await poll(); await screen.findByTestId("pocket-stale");        // n=4: νέα αποτυχία → νέα ανακοίνωση

    expect(screen.getAllByText(DOWN)).toHaveLength(2);
  });
});

describe("Πινάκιο Τσέπης — ρυθμός ανανέωσης και ένδειξη «φορτώνει»", () => {
  it("ανανεώνεται κάθε 30″: ούτε πιο νωρίς ούτε πιο αργά", async () => {
    clock();
    const calls = serveHearings(() => hearingsAt(4));
    renderPocket();
    await screen.findByTestId("pocket-hearing-h1");
    expect(calls()).toBe(1);

    await poll(29_999); await settle();
    expect(calls()).toBe(1);
    await poll(1); await settle();
    expect(calls()).toBe(2);
    await poll(30_000); await settle();
    expect(calls()).toBe(3);
  });

  it("το κουμπί ανανέωσης περιμένει όσο υπάρχει έστω ένα αίτημα σε εξέλιξη, και ξεκλειδώνει όταν τελειώσουν όλα", async () => {
    const first = gate();
    const second = gate();
    serveHearings(async (n) => {
      if (n === 1) { await first.opened; return hearingsAt(4); }
      await second.opened;
      return hearingsAt(5);
    });
    renderPocket();
    await waitFor(() => expect(screen.getByTestId("pocket-refresh")).toBeDisabled());

    await act(async () => { window.dispatchEvent(new Event("online")); });            // δεύτερο αίτημα, ενώ το πρώτο τρέχει
    first.release();
    await waitFor(() => expect(current()).toHaveTextContent("4"));
    expect(screen.getByTestId("pocket-refresh")).toBeDisabled();                       // το δεύτερο τρέχει ακόμη

    second.release();
    await waitFor(() => expect(screen.getByTestId("pocket-refresh")).toBeEnabled());
  });

  it("αποτυχία που φτάνει αφού έκλεισε η οθόνη δεν εμφανίζει ειδοποίηση σε όποια σελίδα βρίσκεται πια ο χρήστης", async () => {
    const door = gate();
    serveHearings(async (n) => { if (n === 1) return hearingsAt(4); await door.opened; return failure(); });
    const { unmount } = renderPocket();
    await screen.findByTestId("pocket-hearing-h1");
    await act(async () => { window.dispatchEvent(new Event("online")); });

    unmount();
    render(<Toaster />);
    door.release();
    await settle();

    expect(screen.queryByText(DOWN)).not.toBeInTheDocument();
  });
});

describe("Πινάκιο Τσέπης — σειρά των απαντήσεων", () => {
  it("αργή απάντηση που ξεκίνησε νωρίτερα δεν σβήνει νεότερη εικόνα", async () => {
    clock();
    const user = userEvent.setup();
    const slow = gate();
    let slowAnswered = false;
    serveHearings(async (n) => {
      if (n === 1) return hearingsAt(3);
      if (n === 2) { await slow.opened; slowAnswered = true; return hearingsAt(3); }     // ξεκίνησε πριν το «+1», γυρνά μετά
      return hearingsAt(4);
    });
    serveLiveUpdate();
    renderPocket();
    await screen.findByTestId("pocket-hearing-h1");
    expect(current()).toHaveTextContent("3");

    await poll();                                              // n=2: κρεμασμένη
    await user.click(screen.getByTestId("pocket-next-h1"));    // POST → ανανέωση n=3 → 4
    await waitFor(() => expect(current()).toHaveTextContent("4"));

    slow.release();
    await waitFor(() => expect(slowAnswered).toBe(true));
    await settle();

    expect(current()).toHaveTextContent("4");
  });

  it("η αποτυχία παλιότερου αιτήματος, μετά από νεότερη επιτυχία, δεν εμφανίζει ένδειξη «δεν ενημερώθηκε»", async () => {
    clock();
    const user = userEvent.setup();
    const slow = gate();
    let slowAnswered = false;
    serveHearings(async (n) => {
      if (n === 1) return hearingsAt(3);
      if (n === 2) { await slow.opened; slowAnswered = true; return failure(); }
      return hearingsAt(4);
    });
    serveLiveUpdate();
    renderPocket();
    await screen.findByTestId("pocket-hearing-h1");

    await poll();
    await user.click(screen.getByTestId("pocket-next-h1"));
    await waitFor(() => expect(current()).toHaveTextContent("4"));
    slow.release();
    await waitFor(() => expect(slowAnswered).toBe(true));
    await settle();

    expect(screen.queryByTestId("pocket-stale")).not.toBeInTheDocument();
    expect(screen.queryByText(DOWN)).not.toBeInTheDocument();
  });
});

describe("Πινάκιο Τσέπης — το «+1» δεν γυρίζει το νούμερο πίσω", () => {
  it("στέλνει και το νούμερο που βλέπει ο χρήστης", async () => {
    const user = userEvent.setup();
    serveHearings(() => hearingsAt(4));
    const bodies = serveLiveUpdate();
    renderPocket();
    await screen.findByTestId("pocket-hearing-h1");

    await user.click(screen.getByTestId("pocket-next-h1"));

    await waitFor(() => expect(bodies).toHaveLength(1));
    expect(bodies[0]).toEqual({ current_number: 5, expected_current: 4 });
  });

  it("από νούμερο 0 (δεν έχει ξεκινήσει) στέλνει expected_current 0, όχι παράλειψη του πεδίου", async () => {
    const user = userEvent.setup();
    serveHearings(() => hearingsAt(0, { status: "scheduled", current_number: 0 }));
    const bodies = serveLiveUpdate();
    renderPocket();
    await screen.findByTestId("pocket-hearing-h1");

    await user.click(screen.getByTestId("pocket-next-h1"));

    await waitFor(() => expect(bodies).toHaveLength(1));
    expect(bodies[0]).toEqual({ current_number: 1, expected_current: 0 });
  });

  it("αν ο server αρνηθεί επειδή το νούμερο άλλαξε: δείχνει το μήνυμα, φέρνει αμέσως την τρέχουσα εικόνα, χωρίς «Καταχωρήθηκε» και χωρίς δόνηση", async () => {
    const user = userEvent.setup();
    const vibrate = vi.fn();
    Object.defineProperty(navigator, "vibrate", { configurable: true, value: vibrate });
    // Πινάκιο 12· η οθόνη δείχνει 10, άρα ένα «+1» θα έφερνε το 11 — αυτό που κανονικά δονεί («απομένει ένα νούμερο»).
    const REFUSED = "Το νούμερο που εκδικάζεται άλλαξε στο 11 (εσείς βλέπατε το 10). Η ενέργεια δεν εφαρμόστηκε· ελέγξτε το τρέχον νούμερο και δοκιμάστε ξανά.";
    const calls = serveHearings((n) => (n === 1 ? hearingsAt(10) : hearingsAt(11)));
    serveLiveUpdate(() => HttpResponse.json({ detail: REFUSED }, { status: 409 }));
    renderPocket();
    await screen.findByTestId("pocket-hearing-h1");

    await user.click(screen.getByTestId("pocket-next-h1"));

    expect(await screen.findByText(REFUSED)).toBeInTheDocument();
    await waitFor(() => expect(current()).toHaveTextContent("11"));
    expect(calls()).toBe(2);
    expect(screen.queryByText("Καταχωρήθηκε")).not.toBeInTheDocument();
    expect(vibrate).not.toHaveBeenCalled();
  });

  it("αν η αλλαγή δεν έφτασε στον server (πτώση σύνδεσης) δεν γίνεται άσκοπη ανανέωση και δεν υπάρχει δόνηση", async () => {
    const user = userEvent.setup();
    const vibrate = vi.fn();
    Object.defineProperty(navigator, "vibrate", { configurable: true, value: vibrate });
    const calls = serveHearings(() => hearingsAt(11));
    serveLiveUpdate(() => HttpResponse.error());
    renderPocket();
    await screen.findByTestId("pocket-hearing-h1");

    await user.click(screen.getByTestId("pocket-next-h1"));

    await screen.findByText(/Network Error|Κάτι πήγε στραβά/);
    expect(calls()).toBe(1);
    expect(vibrate).not.toHaveBeenCalled();
  });

  it("δονεί όταν, μετά από αποδεκτό «+1», μένει ένα νούμερο μέχρι τη σειρά του", async () => {
    const user = userEvent.setup();
    const vibrate = vi.fn();
    Object.defineProperty(navigator, "vibrate", { configurable: true, value: vibrate });
    serveHearings(() => hearingsAt(10));                       // πινάκιο 12· μετά το «+1» εκδικάζεται το 11
    serveLiveUpdate();
    renderPocket();
    await screen.findByTestId("pocket-hearing-h1");

    await user.click(screen.getByTestId("pocket-next-h1"));

    await waitFor(() => expect(vibrate).toHaveBeenCalledTimes(1));
  });

  it("οι αλλαγές κατάστασης (έναρξη, αναβολή, ολοκλήρωση) δεν στέλνουν αναμενόμενο νούμερο", async () => {
    const user = userEvent.setup();
    serveHearings(() => hearingsAt(4));
    const bodies = serveLiveUpdate();
    renderPocket();
    await screen.findByTestId("pocket-hearing-h1");

    await user.click(screen.getByTestId("pocket-start-h1"));
    await user.click(screen.getByTestId("pocket-postpone-h1"));
    await user.click(screen.getByTestId("pocket-complete-h1"));

    await waitFor(() => expect(bodies).toHaveLength(3));
    expect(bodies).toEqual([{ status: "in_progress" }, { status: "postponed" }, { status: "completed" }]);
  });
});

describe("Πινάκιο Τσέπης — ανανέωση όταν η σελίδα ξαναγίνεται ορατή", () => {
  const visibility = (hidden) => {
    Object.defineProperty(document, "hidden", { configurable: true, get: () => hidden });
    document.dispatchEvent(new Event("visibilitychange"));
  };
  afterEach(() => { delete document.hidden; });

  it("όταν η σελίδα γίνει ορατή ανανεώνει αμέσως, χωρίς να περιμένει τον επόμενο κύκλο", async () => {
    const calls = serveHearings((n) => hearingsAt(3 + n));
    renderPocket();
    await waitFor(() => expect(current()).toHaveTextContent("4"));

    await act(async () => { visibility(false); });

    await waitFor(() => expect(current()).toHaveTextContent("5"));
    expect(calls()).toBe(2);
  });

  it("όσο η σελίδα είναι κρυφή δεν ρωτά", async () => {
    const calls = serveHearings(() => hearingsAt(4));
    renderPocket();
    await screen.findByTestId("pocket-hearing-h1");

    await act(async () => { visibility(true); });
    await settle();

    expect(calls()).toBe(1);
  });

  it("όταν επανέλθει το δίκτυο ανανεώνει αμέσως", async () => {
    const calls = serveHearings((n) => hearingsAt(3 + n));
    renderPocket();
    await waitFor(() => expect(current()).toHaveTextContent("4"));

    await act(async () => { window.dispatchEvent(new Event("online")); });

    await waitFor(() => expect(current()).toHaveTextContent("5"));
    expect(calls()).toBe(2);
  });

  it("μετά το κλείσιμο της σελίδας δεν ρωτά ούτε στον κύκλο ούτε στα γεγονότα ορατότητας/δικτύου", async () => {
    clock();
    const calls = serveHearings(() => hearingsAt(4));
    const { unmount } = renderPocket();
    await screen.findByTestId("pocket-hearing-h1");

    unmount();
    await poll(120_000);
    await act(async () => { visibility(false); window.dispatchEvent(new Event("online")); });
    await settle();

    expect(calls()).toBe(1);
  });
});

describe("Πινάκιο Τσέπης — τα στοιχεία που εμφανίζονται", () => {
  it("δείχνει νούμερο πινακίου, εκδικαζόμενο και πόσα απομένουν", async () => {
    serveHearings(() => HttpResponse.json([hearing({ pinakio_number: 12, current_number: 10 })]));
    renderPocket();
    expect(await screen.findByTestId("pocket-my-number-h1")).toHaveTextContent("12");
    expect(current()).toHaveTextContent("10");
    expect(screen.getByTestId("pocket-hearing-h1")).toHaveTextContent("Απομένουν 2 νούμερα");
  });
});
