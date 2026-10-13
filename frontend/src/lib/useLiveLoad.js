import { useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import { errMsg } from "@/lib/api";

/**
 * Φορτώνει δεδομένα που πρέπει να είναι «ζωντανά» (πινάκια στο δικαστήριο) και τα ανανεώνει τακτικά.
 *
 * Τρία πράγματα που η απλή `setInterval(load)` δεν κάνει και στην αίθουσα δικαστηρίου μετρούν:
 *  1. **Δεν κρύβει την αποτυχία.** Η αποτυχία ανακοινώνεται με ειδοποίηση μία φορά ανά σειρά αποτυχιών (όχι κάθε 15″), αλλά
 *     ΜΟΝΙΜΑ υπάρχει η ένδειξη `failed` + `lastOkAt`, ώστε η οθόνη να λέει «δεν ενημερώθηκε από HH:MM». Το χειροκίνητο
 *     «Ανανέωση» (`load({ announce: true })`) ανακοινώνει πάντα: αν ο χρήστης το πάτησε, περιμένει απάντηση.
 *  2. **Μια παλιότερη απάντηση δεν σβήνει νεότερη.** Κάθε αίτημα παίρνει αύξοντα αριθμό· απάντηση παλιότερη από αυτήν που
 *     έχει ήδη εμφανιστεί απορρίπτεται (και η αποτυχία της, γιατί η οθόνη είναι ήδη φρεσκότερη από αυτήν).
 *  3. **Ανανεώνεται μόλις γίνει ξανά ορατή η σελίδα ή επανέλθει το δίκτυο** — τα κινητά παγώνουν τους χρονομετρητές όταν
 *     κλειδώνει η οθόνη, και το πρώτο που βλέπει ο δικηγόρος μετά το ξεκλείδωμα πρέπει να είναι τρέχον.
 *
 * `fetch` επιστρέφει τα δεδομένα· `apply(data)` καλείται μόνο για απάντηση που γίνεται δεκτή.
 */
export function useLiveLoad({ fetch, apply, intervalMs }) {
  const [status, setStatus] = useState({ loaded: false, failed: false, lastOkAt: null });
  const [refreshing, setRefreshing] = useState(false);
  const started = useRef(0);        // πόσα αιτήματα ξεκίνησαν
  const applied = useRef(0);        // ο αριθμός του νεότερου αιτήματος που έχει εμφανιστεί
  const inflight = useRef(0);
  const announced = useRef(false);  // η τρέχουσα σειρά αποτυχιών έχει ήδη ανακοινωθεί
  const alive = useRef(true);
  const latest = useRef({ fetch, apply });
  latest.current = { fetch, apply };

  const load = useCallback(async ({ announce = false } = {}) => {
    const mine = ++started.current;
    inflight.current += 1;
    setRefreshing(true);
    try {
      const data = await latest.current.fetch();
      if (!alive.current || mine < applied.current) return;
      applied.current = mine;
      announced.current = false;
      latest.current.apply(data);
      setStatus({ loaded: true, failed: false, lastOkAt: new Date().toISOString() });
    } catch (e) {
      if (!alive.current || mine < applied.current) return;
      if (announce || !announced.current) toast.error(errMsg(e));
      announced.current = true;
      setStatus((s) => ({ ...s, failed: true }));
    } finally {
      inflight.current -= 1;
      if (alive.current) setRefreshing(inflight.current > 0);
    }
  }, []);

  useEffect(() => {
    alive.current = true;
    load();
    const timer = setInterval(() => load(), intervalMs);
    const refresh = () => { if (!document.hidden) load(); };
    document.addEventListener("visibilitychange", refresh);
    window.addEventListener("online", refresh);
    return () => {
      alive.current = false;
      clearInterval(timer);
      document.removeEventListener("visibilitychange", refresh);
      window.removeEventListener("online", refresh);
    };
  }, [load, intervalMs]);

  return { load, refreshing, ...status };
}
