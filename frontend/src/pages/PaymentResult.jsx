import { useEffect, useState } from "react";
import { useSearchParams, useNavigate } from "react-router-dom";
import axios from "axios";
import { Button } from "@/components/ui/button";
import { CheckCircle, WarningCircle, XCircle, Spinner } from "@phosphor-icons/react";
import { useAuth } from "@/context/AuthContext";
import { money } from "@/lib/api";

const API = process.env.REACT_APP_BACKEND_URL + "/api";

// Η σελίδα ρωτά τον server κάθε POLL_MS και τα παρατάει μετά από MAX_POLLS ερωτήσεις (~30″). Το webhook του Stripe μπορεί να
// καθυστερήσει περισσότερο· τότε η σελίδα το λέει ανοιχτά αντί να δηλώσει αποτυχία.
const POLL_MS = 2000;
const MAX_POLLS = 15;

// Μόνο όταν ο server λέει ρητά «failed» ή «expired» η πληρωμή δηλώνεται αποτυχημένη και προτείνεται νέα προσπάθεια. Όταν η
// κατάσταση ΔΕΝ είναι γνωστή (η ερώτηση απέτυχε, η πληρωμή είναι ακόμη σε εξέλιξη, ο σύνδεσμος δεν έχει στοιχεία πληρωμής) η
// κάρτα μπορεί να έχει ήδη χρεωθεί: η πρόσκληση «δοκιμάστε ξανά» θα οδηγούσε σε διπλή πληρωμή, γι' αυτό το κείμενο λέει το
// αντίθετο. Η ίδια διατύπωση χρησιμοποιείται και στην πύλη πελάτη (ClientPortal.jsx).
const NOTICE = {
  cancelled: {
    icon: "bad", title: "Ακύρωση πληρωμής",
    text: "Δεν έγινε καμία χρέωση. Μπορείτε να δοκιμάσετε ξανά όποτε θέλετε.",
  },
  failed: {
    icon: "bad", title: "Η πληρωμή δεν ολοκληρώθηκε",
    text: "Αν έχει γίνει χρέωση στην κάρτα σας, επικοινωνήστε με το γραφείο. Μπορείτε επίσης να δοκιμάσετε ξανά.",
  },
  timeout: {
    icon: "wait", title: "Η πληρωμή σας επεξεργάζεται ακόμη",
    text: "Αν έχετε χρεωθεί, μην πληρώσετε ξανά: το τιμολόγιο θα εμφανιστεί ως εξοφλημένο μόλις επιβεβαιωθεί η πληρωμή. Ανανεώστε τη σελίδα σε λίγο ή επικοινωνήστε με το γραφείο.",
  },
  error: {
    icon: "wait", title: "Δεν ήταν δυνατή η επιβεβαίωση της πληρωμής αυτή τη στιγμή",
    text: "Αν έχετε χρεωθεί, μην πληρώσετε ξανά. Ανανεώστε τη σελίδα σε λίγο ή επικοινωνήστε με το γραφείο.",
  },
  nosession: {
    icon: "wait", title: "Δεν βρέθηκε πληρωμή προς επιβεβαίωση",
    text: "Ο σύνδεσμος δεν περιέχει στοιχεία πληρωμής. Αν πληρώσατε πριν από λίγο, μην πληρώσετε ξανά και επικοινωνήστε με το γραφείο.",
  },
};

// Το «πίσω» του browser οδηγούσε στη σελίδα πληρωμής του Stripe (που έχει πια ολοκληρωθεί): αδιέξοδο. Το προσωπικό του
// γραφείου επιστρέφει στα τιμολόγια· ο πελάτης (που φτάνει εδώ από σύνδεσμο πληρωμής σε email υπενθύμισης, χωρίς λογαριασμό)
// δεν έχει πού να πάει στην εφαρμογή και απλώς ενημερώνεται ότι μπορεί να κλείσει τη σελίδα.
function Exit({ user, navigate, outline = false }) {
  if (user) {
    return (
      <Button className="mt-6" variant={outline ? "outline" : "default"} onClick={() => navigate("/invoices")} data-testid="payment-back">
        Επιστροφή στα τιμολόγια
      </Button>
    );
  }
  return <p className="text-xs text-muted-foreground mt-6" data-testid="payment-close">Μπορείτε να κλείσετε αυτή τη σελίδα.</p>;
}

export default function PaymentResult({ mode = "success" }) {
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const { user } = useAuth() || {};
  const sessionId = params.get("session_id");
  const [status, setStatus] = useState("checking");
  const [tx, setTx] = useState(null);

  useEffect(() => {
    if (mode === "cancel" || !sessionId) {
      setStatus(mode === "cancel" ? "cancelled" : "nosession");
      return undefined;
    }
    setStatus("checking");
    let asked = 0;
    let timer = null;
    let stopped = false;           // η σελίδα έφυγε (ή άλλαξε συνεδρία): καμία απάντηση δεν αγγίζει πια την οθόνη
    const poll = async () => {
      try {
        const { data } = await axios.get(`${API}/payments/status/${sessionId}`);
        if (stopped) return;
        setTx(data);
        if (data.payment_status === "paid") { setStatus("paid"); return; }
        if (data.payment_status === "failed" || data.payment_status === "expired") { setStatus("failed"); return; }
        if (asked++ < MAX_POLLS) timer = setTimeout(poll, POLL_MS);
        else setStatus("timeout");
      } catch {
        if (!stopped) setStatus("error");
      }
    };
    poll();
    return () => { stopped = true; clearTimeout(timer); };
  }, [sessionId, mode]);

  const notice = NOTICE[status];

  return (
    <div className="min-h-screen flex items-center justify-center p-6">
      <div className="lux-card p-10 max-w-md text-center" data-testid={`payment-${status}`}>
        {status === "checking" && (
          <>
            <Spinner size={48} className="mx-auto text-[hsl(var(--gold))] animate-spin mb-4" />
            <h1 className="font-brand text-2xl">Επιβεβαίωση πληρωμής...</h1>
            <p className="text-sm text-muted-foreground mt-2">Παρακαλώ περιμένετε</p>
          </>
        )}
        {status === "paid" && (
          <>
            <CheckCircle size={56} weight="fill" className="mx-auto text-[hsl(var(--success))] mb-4" />
            <h1 className="font-brand text-2xl">Επιτυχής πληρωμή!</h1>
            <p className="text-sm text-muted-foreground mt-2">
              Το τιμολόγιο {tx?.invoice_number} για {money(tx?.amount)} εξοφλήθηκε.
            </p>
            <p className="text-xs text-muted-foreground mt-2">Θα λάβετε επιβεβαίωση στο email σας.</p>
            <Exit user={user} navigate={navigate} />
          </>
        )}
        {notice && (
          <>
            {notice.icon === "bad"
              ? <XCircle size={56} className="mx-auto text-[hsl(var(--destructive))] mb-4" />
              : <WarningCircle size={56} className="mx-auto text-[hsl(var(--gold))] mb-4" />}
            <h1 className="font-brand text-2xl">{notice.title}</h1>
            <p className="text-sm text-muted-foreground mt-2">{notice.text}</p>
            <Exit user={user} navigate={navigate} outline />
          </>
        )}
      </div>
    </div>
  );
}
