import { useCallback, useEffect, useState } from "react";
import { QRCodeSVG } from "qrcode.react";
import axios, { API, dateFmt, errMsg } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import { ShieldCheck, Warning, Copy } from "@phosphor-icons/react";
import { toast } from "sonner";

/** Ρύθμιση ταυτοποίησης δύο παραγόντων: setup → σάρωση QR → επιβεβαίωση → εφεδρικοί κωδικοί. */
export function TwoFactorSettings() {
  const [status, setStatus] = useState(null);
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [setup, setSetup] = useState(null);      // {secret, secret_formatted, otpauth_uri}
  const [backupCodes, setBackupCodes] = useState(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const { data } = await axios.get(`${API}/auth/2fa/status`);
      setStatus(data);
    } catch (e) {
      toast.error(errMsg(e));
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const run = async (fn) => {
    setBusy(true);
    try {
      await fn();
    } catch (e) {
      toast.error(errMsg(e));
    } finally {
      setBusy(false);
    }
  };

  const startSetup = () => run(async () => {
    const { data } = await axios.post(`${API}/auth/2fa/setup`, { password });
    setSetup(data);
    setPassword("");
    setCode("");
  });

  const confirmEnable = () => run(async () => {
    const { data } = await axios.post(`${API}/auth/2fa/enable`, { code });
    setBackupCodes(data.backup_codes);
    setSetup(null);
    setCode("");
    toast.success("Η ταυτοποίηση δύο παραγόντων ενεργοποιήθηκε");
    load();
  });

  const disable = () => run(async () => {
    await axios.post(`${API}/auth/2fa/disable`, { password, code });
    setPassword("");
    setCode("");
    setBackupCodes(null);
    toast.success("Η ταυτοποίηση δύο παραγόντων απενεργοποιήθηκε");
    load();
  });

  const regenerate = () => run(async () => {
    const { data } = await axios.post(`${API}/auth/2fa/backup-codes`, { password });
    setBackupCodes(data.backup_codes);
    setPassword("");
    toast.success("Νέοι εφεδρικοί κωδικοί — οι παλιοί ακυρώθηκαν");
    load();
  });

  const copy = (text) => {
    navigator.clipboard?.writeText(text).then(
      () => toast.success("Αντιγράφηκε"),
      () => toast.error("Η αντιγραφή απέτυχε"),
    );
  };

  if (!status) return null;

  return (
    <div className="lux-card p-6 space-y-4" data-testid="settings-2fa">
      <div className="flex items-center justify-between">
        <h3 className="font-heading text-lg font-medium flex items-center gap-2">
          <ShieldCheck size={20} className="text-[hsl(var(--gold))]" /> Ταυτοποίηση δύο παραγόντων
        </h3>
        <Badge variant={status.enabled ? "default" : "secondary"} data-testid="2fa-status-badge">
          {status.enabled ? "Ενεργή" : "Ανενεργή"}
        </Badge>
      </div>
      <div className="gold-rule">◆</div>

      <p className="text-sm text-muted-foreground">
        Ένας κλεμμένος κωδικός δεν αρκεί για πρόσβαση στα οικονομικά και στα δεδομένα των πελατών σας.
        Χρειάζεται εφαρμογή ταυτοποίησης (Google Authenticator, Authy, 1Password κ.ά.).
      </p>

      {backupCodes && (
        <div className="border border-[hsl(var(--gold))]/40 rounded-lg p-4 bg-[hsl(var(--gold))]/5" data-testid="2fa-backup-codes">
          <div className="flex items-center gap-2 text-sm font-medium mb-2">
            <Warning size={16} className="text-[hsl(var(--warning))]" />
            Εφεδρικοί κωδικοί — δεν θα εμφανιστούν ξανά
          </div>
          <div className="grid grid-cols-2 gap-1.5 font-mono text-sm">
            {backupCodes.map((c) => <span key={c}>{c}</span>)}
          </div>
          <div className="flex gap-2 mt-3">
            <Button size="sm" variant="outline" onClick={() => copy(backupCodes.join("\n"))} data-testid="2fa-copy-backup">
              <Copy size={14} className="mr-1.5" /> Αντιγραφή
            </Button>
            <Button size="sm" variant="ghost" onClick={() => setBackupCodes(null)}>Τους αποθήκευσα</Button>
          </div>
        </div>
      )}

      {!status.enabled && !setup && (
        <div className="flex flex-wrap items-end gap-3">
          <div className="flex-1 min-w-[220px]">
            <Label className="text-xs">Επιβεβαιώστε τον κωδικό πρόσβασής σας</Label>
            <Input type="password" value={password} onChange={(e) => setPassword(e.target.value)}
              className="mt-1.5" autoComplete="current-password" data-testid="2fa-password" />
          </div>
          <Button onClick={startSetup} disabled={busy || !password} data-testid="2fa-start-setup">
            Ενεργοποίηση
          </Button>
        </div>
      )}

      {setup && (
        <div className="space-y-4" data-testid="2fa-setup">
          <div className="flex flex-wrap gap-6 items-start">
            <div className="bg-white p-3 rounded-lg shrink-0">
              <QRCodeSVG value={setup.otpauth_uri} size={168} level="M" />
            </div>
            <div className="flex-1 min-w-[220px] space-y-3">
              <div>
                <div className="text-xs text-muted-foreground mb-1">Ή καταχωρήστε το κλειδί χειροκίνητα:</div>
                <div className="flex items-center gap-2">
                  <code className="font-mono text-sm break-all" data-testid="2fa-secret">{setup.secret_formatted}</code>
                  <Button size="icon" variant="ghost" onClick={() => copy(setup.secret)}><Copy size={14} /></Button>
                </div>
              </div>
              <div>
                <Label className="text-xs">Κωδικός από την εφαρμογή</Label>
                <Input value={code} onChange={(e) => setCode(e.target.value)} placeholder="123456"
                  inputMode="numeric" autoComplete="one-time-code"
                  className="mt-1.5 font-mono tracking-[0.3em] text-center" data-testid="2fa-confirm-code" />
              </div>
              <div className="flex gap-2">
                <Button onClick={confirmEnable} disabled={busy || code.length < 6} data-testid="2fa-confirm">
                  Επιβεβαίωση
                </Button>
                <Button variant="ghost" onClick={() => { setSetup(null); setCode(""); }}>Ακύρωση</Button>
              </div>
            </div>
          </div>
        </div>
      )}

      {status.enabled && (
        <div className="space-y-4">
          <div className="text-sm text-muted-foreground">
            Ενεργοποιήθηκε {status.enabled_at ? dateFmt(status.enabled_at) : "—"} ·{" "}
            <span className={status.backup_codes_remaining <= 2 ? "text-[hsl(var(--warning))]" : ""}>
              {status.backup_codes_remaining} εφεδρικοί κωδικοί σε ισχύ
            </span>
          </div>
          <div className="flex flex-wrap items-end gap-3">
            <div className="flex-1 min-w-[200px]">
              <Label className="text-xs">Κωδικός πρόσβασης</Label>
              <Input type="password" value={password} onChange={(e) => setPassword(e.target.value)}
                className="mt-1.5" autoComplete="current-password" data-testid="2fa-password" />
            </div>
            <div className="w-40">
              <Label className="text-xs">Κωδικός εφαρμογής</Label>
              <Input value={code} onChange={(e) => setCode(e.target.value)} placeholder="123456"
                className="mt-1.5 font-mono" data-testid="2fa-disable-code" />
            </div>
            <Button variant="outline" onClick={regenerate} disabled={busy || !password} data-testid="2fa-regenerate">
              Νέοι εφεδρικοί κωδικοί
            </Button>
            <Button variant="destructive" onClick={disable} disabled={busy || !password || !code} data-testid="2fa-disable">
              Απενεργοποίηση
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}
