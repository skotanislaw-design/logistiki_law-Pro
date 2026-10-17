import { HttpResponse, http } from "msw";

const API = "http://localhost:8001/api";

// Υποθέσεις, πελάτες και χρήστες όπως τους επιστρέφει το backend (βλ. lib/matters.js).
export const CASE_A = { id: "case-a", number: "ΥΠΘ-2026-0001", title: "Αγωγή αποζημίωσης", client_id: "client-a", client_name: "ΑΛΦΑ ΑΕ", lawyer_id: "u-law" };
export const CASE_B = { id: "case-b", number: "ΥΠΘ-2026-0002", title: "Ποινική δίωξη", client_id: "client-b", client_name: "Β. Παπαδόπουλος", lawyer_id: "" };
export const CLIENT_A = { id: "client-a", name: "ΑΛΦΑ ΑΕ" };
export const CLIENT_B = { id: "client-b", name: "Β. Παπαδόπουλος" };
export const USERS = [
  { id: "u-law", name: "Χρήστος Σκοτάνης", email: "c@example.gr", role: "admin" },
  { id: "u-law2", name: "Μαρία Δικηγόρος", email: "m@example.gr", role: "lawyer" },
  { id: "u-acc", name: "Χριστόφορος Λογιστής", email: "x@example.gr", role: "accountant" },
];

/** Οι τρεις λίστες που φορτώνουν οι φόρμες αντιστοίχισης. Κάθε ένα μπορεί να αντικατασταθεί ή να αποτύχει (`"fail"`). */
export function lookupHandlers({ cases = [], clients = [], users = [] } = {}) {
  const reply = (rows) => () => (rows === "fail" ? new HttpResponse(null, { status: 500 }) : HttpResponse.json(rows));
  return [
    http.get(`${API}/cases`, reply(cases)),
    http.get(`${API}/clients`, reply(clients)),
    http.get(`${API}/auth/users`, reply(users)),
  ];
}
