import { useCallback, useEffect, useState } from "react";
import axios, { API, dateFmt, errMsg } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Bank, ArrowSquareOut, Warning } from "@phosphor-icons/react";
import { useAuth } from "@/context/AuthContext";
import { toast } from "sonner";

/** Σύνδεση τραπεζικού λογαριασμού μέσω PSD2 — οι κινήσεις κατεβαίνουν μόνες τους. */
export function BankConnection() {
  const { user } = useAuth();
  const isAdmin = user?.role === "admin";
  const [status, setStatus] = useState(null);
  const [institutions, setInstitutions] = useState([]);
  const [chosen, setChosen] = useState("");
  const [secrets, setSecrets] = useState({ gocardless_secret_id: "", gocardless_secret_key: "" });
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const { data } = await axios.get(`${API}/bank/openbanking/status`);
      setStatus(data);
    } catch (e) {
      toast.error(errMsg(e));
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const run = async (fn) => {
    setBusy(true);
    try { await fn(); } catch (e) { toast.error(errMsg(e)); } finally { setBusy(false); }
  };

  const saveSecrets = () => run(async () => {
    await axios.patch(`${API}/settings`, secrets);
    setSecrets({ gocardless_secret_id: "", gocardless_secret_key: "" });
    toast.success("Τα διαπιστευτήρια αποθηκεύτηκαν");
    load();
  });

  const loadInstitutions = () => run(async () => {
    const { data } = await axios.get(`${API}/bank/openbanking/institutions`);
    setInstitutions(data);
    if (!data.length) toast.warning("Ο πάροχος δεν επέστρεψε τράπεζες για την Ελλάδα.");
  });

  const startLink = () => run(async () => {
    const { data } = await axios.post(`${API}/bank/openbanking/link`, { institution_id: chosen });
    window.open(data.link, "_blank", "noopener");
    toast.info("Ολοκληρώστε την έγκριση στη σελίδα της τράπεζας και μετά πατήστε «Ολοκλήρωση σύνδεσης».");
  });

  const complete = () => run(async () => {
    const { data } = await axios.post(`${API}/bank/openbanking/complete`);
    toast.success(data.message);
    load();
  });

  const disconnect = () => run(async () => {
    if (!window.confirm("Διακοπή σύνδεσης με την τράπεζα;")) return;
    const { data } = await axios.post(`${API}/bank/openbanking/disconnect`);
    toast.success(data.message);
    load();
  });

  if (!status) return null;

  return (
    <div className="lux-card p-6 space-y-4" data-testid="settings-openbanking">
      <div className="flex items-center justify-between">
        <h3 className="font-heading text-lg font-medium flex items-center gap-2">
          <Bank size={20} className="text-[hsl(var(--gold))]" /> Τραπεζική σύνδεση (open banking)
        </h3>
        <Badge variant={status.enabled ? "default" : "secondary"} data-testid="openbanking-badge">
          {status.enabled ? "Συνδεδεμένη" : "Ανενεργή"}
        </Badge>
      </div>
      <div className="gold-rule">◆</div>

      <p className="text-sm text-muted-foreground">
        Αντί να κατεβάζετε CSV από το e-banking, οι κινήσεις έρχονται αυτόματα μέσω PSD2 και συμφωνούνται
        με τα ανείσπρακτα τιμολόγιά σας. Χρειάζεται λογαριασμός στο GoCardless Bank Account Data (δωρεάν βαθμίδα).
      </p>

      {status.expiring_soon && (
        <div className="flex items-start gap-2 text-sm border border-[hsl(var(--warning))]/40 bg-[hsl(var(--warning))]/10 rounded-lg p-3">
          <Warning size={18} className="text-[hsl(var(--warning))] shrink-0 mt-0.5" />
          <span>Η συγκατάθεση PSD2 λήγει στις {dateFmt(status.valid_until)}. Ανανεώστε τη σύνδεση για να μη σταματήσουν οι αυτόματες εισπράξεις.</span>
        </div>
      )}

      {!status.configured && (
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          <div>
            <Label className="text-xs">Secret ID</Label>
            <Input className="mt-1.5" value={secrets.gocardless_secret_id} disabled={!isAdmin}
              onChange={(e) => setSecrets({ ...secrets, gocardless_secret_id: e.target.value })}
              data-testid="openbanking-secret-id" />
          </div>
          <div>
            <Label className="text-xs">Secret Key</Label>
            <Input type="password" className="mt-1.5" value={secrets.gocardless_secret_key} disabled={!isAdmin}
              onChange={(e) => setSecrets({ ...secrets, gocardless_secret_key: e.target.value })}
              data-testid="openbanking-secret-key" />
          </div>
          <div className="sm:col-span-2">
            <Button onClick={saveSecrets} disabled={busy || !isAdmin || !secrets.gocardless_secret_id} data-testid="openbanking-save-secrets">
              Αποθήκευση διαπιστευτηρίων
            </Button>
          </div>
        </div>
      )}

      {status.configured && !status.enabled && isAdmin && (
        <div className="space-y-3">
          <div className="flex flex-wrap items-end gap-3">
            <Button variant="outline" onClick={loadInstitutions} disabled={busy} data-testid="openbanking-load-banks">
              Φόρτωση τραπεζών
            </Button>
            {institutions.length > 0 && (
              <div className="flex-1 min-w-[220px]">
                <Label className="text-xs">Τράπεζα</Label>
                <Select value={chosen} onValueChange={setChosen}>
                  <SelectTrigger className="mt-1.5" data-testid="openbanking-bank-select">
                    <SelectValue placeholder="Επιλέξτε τράπεζα" />
                  </SelectTrigger>
                  <SelectContent>
                    {institutions.map((i) => <SelectItem key={i.id} value={i.id}>{i.name}</SelectItem>)}
                  </SelectContent>
                </Select>
              </div>
            )}
            {chosen && (
              <Button onClick={startLink} disabled={busy} data-testid="openbanking-start-link">
                <ArrowSquareOut size={15} className="mr-1.5" /> Σύνδεση στην τράπεζα
              </Button>
            )}
          </div>
          <Button variant="ghost" size="sm" onClick={complete} disabled={busy} data-testid="openbanking-complete">
            Ολοκλήρωση σύνδεσης (μετά την έγκριση)
          </Button>
        </div>
      )}

      {status.enabled && (
        <div className="flex flex-wrap items-center justify-between gap-3 text-sm">
          <div>
            <div>{status.institution || "Τράπεζα"} · {status.accounts.length} λογαριασμ{status.accounts.length === 1 ? "ός" : "οί"}</div>
            <div className="text-muted-foreground text-xs">
              Τελευταίος συγχρονισμός: {status.last_sync ? dateFmt(status.last_sync) : "—"} ·
              {" "}Ισχύει έως {status.valid_until ? dateFmt(status.valid_until) : "—"}
            </div>
          </div>
          {isAdmin && (
            <Button variant="outline" size="sm" onClick={disconnect} disabled={busy} data-testid="openbanking-disconnect">
              Διακοπή σύνδεσης
            </Button>
          )}
        </div>
      )}
    </div>
  );
}
