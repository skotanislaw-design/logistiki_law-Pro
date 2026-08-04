import { useEffect, useState } from "react";
import { useSearchParams, useNavigate } from "react-router-dom";
import axios from "axios";
import { Button } from "@/components/ui/button";
import { CheckCircle, XCircle, Spinner } from "@phosphor-icons/react";

const API = process.env.REACT_APP_BACKEND_URL + "/api";

export default function PaymentResult({ mode = "success" }) {
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const sessionId = params.get("session_id");
  const [status, setStatus] = useState("checking");
  const [tx, setTx] = useState(null);

  useEffect(() => {
    if (mode === "cancel" || !sessionId) {
      setStatus(mode === "cancel" ? "cancelled" : "error");
      return;
    }
    let attempts = 0;
    const poll = async () => {
      try {
        const { data } = await axios.get(`${API}/payments/status/${sessionId}`);
        setTx(data);
        if (data.payment_status === "paid") { setStatus("paid"); return; }
        if (data.payment_status === "failed" || data.payment_status === "expired") { setStatus("failed"); return; }
        if (attempts++ < 15) setTimeout(poll, 2000);
        else setStatus("timeout");
      } catch {
        setStatus("error");
      }
    };
    poll();
  }, [sessionId, mode]);

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
              Το τιμολόγιο {tx?.invoice_number} για {(tx?.amount || 0).toFixed(2)} € εξοφλήθηκε.
            </p>
            <p className="text-xs text-muted-foreground mt-2">Θα λάβετε επιβεβαίωση στο email σας.</p>
            <Button className="mt-6" onClick={() => navigate(-1)}>Επιστροφή</Button>
          </>
        )}
        {(status === "cancelled" || status === "failed" || status === "timeout" || status === "error") && (
          <>
            <XCircle size={56} className="mx-auto text-[hsl(var(--destructive))] mb-4" />
            <h1 className="font-brand text-2xl">
              {status === "cancelled" ? "Ακύρωση πληρωμής" : "Η πληρωμή δεν ολοκληρώθηκε"}
            </h1>
            <p className="text-sm text-muted-foreground mt-2">
              {status === "cancelled" ? "Μπορείτε να δοκιμάσετε ξανά όποτε θέλετε." : "Παρακαλώ επικοινωνήστε με το γραφείο ή δοκιμάστε ξανά."}
            </p>
            <Button className="mt-6" variant="outline" onClick={() => navigate(-1)}>Επιστροφή</Button>
          </>
        )}
      </div>
    </div>
  );
}
