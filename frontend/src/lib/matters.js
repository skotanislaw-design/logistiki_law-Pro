import { useEffect, useState } from "react";
import axios, { API } from "@/lib/api";

// Το Select του Radix δεν δέχεται κενή τιμή σε επιλογή: το «καμία» παριστάνεται με αυτή τη σταθερά.
export const NONE = "__none__";

// Ρόλοι που μπορούν να είναι υπεύθυνοι δικηγόροι (ο λογιστής του γραφείου δεν είναι δικηγόρος).
export const LAWYER_ROLES = ["admin", "lawyer"];

// Εύρος εξόδου: σε ποιον ανήκει. Οι τιμές είναι αυτές του backend (βλ. backend/dimensions.py).
export const SCOPE_LABEL = {
  unassigned: "Δεν έχει ταξινομηθεί",
  office: "Γενικό έξοδο γραφείου",
  client: "Πελάτης",
  case: "Υπόθεση",
};

export const caseLabel = (c) => (c ? [c.number, c.title].filter(Boolean).join(" — ") : "");

/**
 * Οι λίστες που χρειάζονται οι επιλογές υπόθεσης / πελάτη / δικηγόρου.
 *
 * Φορτώνουν μόνο όσο `enabled` (π.χ. όταν ανοίξει η φόρμα) και ποτέ δεν σπάνε τη σελίδα: ένα σφάλμα αφήνει τη λίστα
 * άδεια και σημειώνεται στο `failed`, ώστε η φόρμα να το δηλώνει αντί να δείχνει σιωπηλά «δεν υπάρχουν υποθέσεις».
 */
export function useMatterLookups(enabled = true, { cases = true, clients = true, lawyers = true } = {}) {
  const [state, setState] = useState({ cases: [], clients: [], lawyers: [], failed: {}, loaded: false });

  useEffect(() => {
    if (!enabled) return undefined;
    let cancelled = false;
    const wanted = [
      cases && ["cases", `${API}/cases`],
      clients && ["clients", `${API}/clients`],
      lawyers && ["lawyers", `${API}/auth/users`],
    ].filter(Boolean);
    Promise.allSettled(wanted.map(([, url]) => axios.get(url))).then((results) => {
      if (cancelled) return;
      const next = { cases: [], clients: [], lawyers: [], failed: {}, loaded: true };
      results.forEach((res, i) => {
        const key = wanted[i][0];
        const rows = res.status === "fulfilled" ? res.value.data : null;
        // Σφάλμα δικτύου/server ΚΑΙ απάντηση 200 που δεν είναι λίστα (π.χ. σελίδα σφάλματος ενός proxy) μετρούν το ίδιο:
        // η λίστα μένει άδεια και δηλώνεται, αντί να σπάσει η φόρμα ή να δείξει σιωπηλά «δεν υπάρχουν υποθέσεις».
        if (!Array.isArray(rows)) {
          next.failed[key] = true;
          return;
        }
        next[key] = key === "lawyers" ? rows.filter((u) => LAWYER_ROLES.includes(u.role)) : rows;
      });
      setState(next);
    });
    return () => { cancelled = true; };
  }, [enabled, cases, clients, lawyers]);

  return state;
}

/** Οι επιλογές μιας λίστας + η τρέχουσα τιμή, αν δεν υπάρχει στη λίστα (π.χ. κομμένη λίστα): αλλιώς το Select θα έδειχνε κενό. */
export function withCurrent(options, currentId, missingLabel) {
  if (!currentId || options.some((o) => o.value === currentId)) return options;
  return [...options, { value: currentId, label: missingLabel }];
}

/**
 * Ο δικηγόρος που μένει στη φόρμα όταν ο χρήστης αλλάζει την υπόθεση.
 *
 * Ο server αποθηκεύει τον δικηγόρο «υλοποιημένο»: ό,τι προκύπτει από την υπόθεση γράφεται στο έγγραφο. Μια φόρμα που
 * ξαναστέλνει τον υπεύθυνο της προηγούμενης υπόθεσης δεν ξεχωρίζει από ρητή επιλογή του χρήστη, οπότε το έξοδο ή το
 * τιμολόγιο θα έμενε με τον δικηγόρο της παλιάς υπόθεσης. Όταν ο δικηγόρος της φόρμας είναι ο υπεύθυνος της προηγούμενης
 * υπόθεσης, κανείς δεν τον διάλεξε ρητά: μένει κενός («ο υπεύθυνος της υπόθεσης») και τον συμπληρώνει ο server από τη νέα.
 * Δικηγόρος που επιλέχθηκε ρητά μένει, και η αποσύνδεση της υπόθεσης δεν αλλάζει τον δικηγόρο.
 */
export function lawyerAfterCaseChange(cases, previousCaseId, nextCaseId, lawyerId) {
  if (!lawyerId || !nextCaseId || !previousCaseId || previousCaseId === nextCaseId) return lawyerId || "";
  const previous = (cases || []).find((c) => c.id === previousCaseId);
  return previous && previous.lawyer_id === lawyerId ? "" : lawyerId;
}
