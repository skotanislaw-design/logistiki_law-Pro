/**
 * Κοινά δεδομένα και handlers για τα tests των «ζωντανών» οθονών πινακίων (Pinakia, PocketPinakia).
 */
import { HttpResponse, http } from "msw";

import { server } from "@/test/server";

export const API = "http://localhost:8001/api";

export const hearing = (over = {}) => ({
  id: "h1", court: "Πρωτοδικείο Αθηνών", room: "3", date: "2099-01-01", time: "09:00", pinakio_number: 12, current_number: 4,
  status: "in_progress", notify_threshold: 3, case_title: "ΑΛΦΑ ΑΕ κατά ΒΗΤΑ ΕΠΕ", gak: "", events: [], ...over,
});

export const DOWN = "Η υπηρεσία δεν απαντά.";

/** Απάντηση «ο server δεν απαντά» (503) και επιτυχής απάντηση με ένα πινάκιο στο νούμερο `n`. */
export const failure = () => HttpResponse.json({ detail: DOWN }, { status: 503 });
export const hearingsAt = (n, over = {}) => HttpResponse.json([hearing({ current_number: n, ...over })]);

/** Ένα «φράγμα»: η απάντηση που το περιμένει μένει κρεμασμένη μέχρι να κληθεί `release()`. */
export const gate = () => {
  let release;
  const opened = new Promise((resolve) => { release = resolve; });
  return { opened, release };
};

/**
 * Βάζει handlers για όλα τα GET που κάνουν οι δύο οθόνες. `script(n)` αποφασίζει την απάντηση του n-οστού αιτήματος προς
 * `GET /hearings` (1, 2, 3…)· επιστρέφει συνάρτηση που δίνει πόσα αιτήματα έγιναν.
 */
export function serveHearings(script, alerts = []) {
  let calls = 0;
  server.use(
    http.get(`${API}/hearings`, async () => { calls += 1; return script(calls); }),
    http.get(`${API}/hearings/alerts/list`, () => HttpResponse.json(alerts)),
  );
  return () => calls;
}

/** Καταγράφει τα σώματα των POST προς live-update και απαντά με `respond(body)` (προεπιλογή: επιτυχία). */
export function serveLiveUpdate(respond = () => HttpResponse.json({ ok: true })) {
  const bodies = [];
  server.use(http.post(`${API}/hearings/:id/live-update`, async ({ request }) => {
    const body = await request.json();
    bodies.push(body);
    return respond(body, bodies.length);
  }));
  return bodies;
}
