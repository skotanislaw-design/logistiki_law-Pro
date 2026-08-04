import { useEffect, useState, useRef } from "react";
import { useNavigate } from "react-router-dom";
import axios, { API, errMsg } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { CheckCircle, ArrowRight, ArrowLeft, Buildings, EnvelopeSimple, Scales, UploadSimple, Confetti } from "@phosphor-icons/react";
import { toast } from "sonner";

const STEPS = [
  { id: "company", title: "Στοιχεία Εταιρείας", icon: Buildings },
  { id: "smtp", title: "Αποστολή Email", icon: EnvelopeSimple },
  { id: "aade", title: "myDATA / ΑΑΔΕ", icon: Scales },
  { id: "clients", title: "Εισαγωγή Πελατών", icon: UploadSimple },
];

export default function Onboarding() {
  const navigate = useNavigate();
  const [step, setStep] = useState(0);
  const [settings, setSettings] = useState(null);
  const [saving, setSaving] = useState(false);
  const [testingSmtp, setTestingSmtp] = useState(false);
  const [testingAade, setTestingAade] = useState(false);
  const [importResult, setImportResult] = useState(null);
  const [importing, setImporting] = useState(false);
  const fileRef = useRef();

  useEffect(() => {
    axios.get(`${API}/settings`).then((r) => setSettings(r.data)).catch((e) => toast.error(errMsg(e)));
  }, []);

  const save = async (finalStep = false) => {
    setSaving(true);
    try {
      const payload = { ...settings };
      if (finalStep) payload.onboarding_completed = true;
      const { data } = await axios.put(`${API}/settings`, payload);
      setSettings(data);
      if (!finalStep) toast.success("Αποθηκεύτηκε");
    } catch (e) {
      toast.error(errMsg(e));
      return false;
    } finally {
      setSaving(false);
    }
    return true;
  };

  const testSmtp = async () => {
    setTestingSmtp(true);
    await save();
    try {
      const { data } = await axios.post(`${API}/settings/test-email`);
      toast.success(data.message);
    } catch (e) {
      toast.error(errMsg(e));
    } finally { setTestingSmtp(false); }
  };

  const testAade = async () => {
    setTestingAade(true);
    await save();
    try {
      const { data } = await axios.post(`${API}/mydata/test-connection`);
      toast.success(data.message);
    } catch (e) {
      toast.error(errMsg(e));
    } finally { setTestingAade(false); }
  };

  const importClients = async (file) => {
    if (!file) return;
    setImporting(true); setImportResult(null);
    try {
      const fd = new FormData();
      fd.append("file", file);
      const { data } = await axios.post(`${API}/import/clients`, fd, { headers: { "Content-Type": "multipart/form-data" } });
      setImportResult(data);
      toast.success(data.message);
    } catch (e) {
      toast.error(errMsg(e));
    } finally { setImporting(false); }
  };

  const downloadTemplate = async () => {
    try {
      const res = await axios.get(`${API}/import/clients/template`, { responseType: "blob" });
      const url = URL.createObjectURL(res.data);
      const a = document.createElement("a");
      a.href = url; a.download = "clients_template.csv"; document.body.appendChild(a); a.click(); a.remove();
      URL.revokeObjectURL(url);
    } catch (e) { toast.error(errMsg(e)); }
  };

  const next = async () => {
    const ok = await save();
    if (ok) setStep(Math.min(step + 1, STEPS.length));
  };

  const finish = async () => {
    const ok = await save(true);
    if (ok) {
      toast.success("Το Nomos One είναι έτοιμο! 🎉");
      navigate("/");
    }
  };

  if (!settings) return <div className="text-muted-foreground p-8">Φόρτωση...</div>;

  const done = step >= STEPS.length;

  return (
    <div className="min-h-screen flex items-center justify-center p-6" data-testid="onboarding-page">
      <div className="w-full max-w-2xl">
        <div className="text-center mb-8">
          <div className="lux-overline mb-2">Nomos One · Setup</div>
          <h1 className="font-brand text-4xl font-bold tracking-wide">Καλωσορίσατε</h1>
          <p className="text-sm text-muted-foreground mt-2">Σε 4 βήματα ρυθμίζουμε την πλατφόρμα σας</p>
        </div>

        {/* Progress dots */}
        <div className="flex items-center justify-between mb-8">
          {STEPS.map((s, i) => (
            <div key={s.id} className="flex-1 flex items-center">
              <div className={`w-9 h-9 rounded-full flex items-center justify-center border transition-colors ${
                i < step ? "bg-[hsl(var(--gold))] text-black border-[hsl(var(--gold))]" :
                i === step ? "border-[hsl(var(--gold))] text-[hsl(var(--gold))]" :
                "border-border text-muted-foreground"
              }`}>
                {i < step ? <CheckCircle size={18} weight="fill" /> : <s.icon size={16} />}
              </div>
              {i < STEPS.length - 1 && <div className={`flex-1 h-px mx-2 ${i < step ? "bg-[hsl(var(--gold))]" : "bg-border"}`} />}
            </div>
          ))}
        </div>

        <div className="lux-card p-8 space-y-5">
          {step === 0 && (
            <>
              <h2 className="font-heading text-xl">Στοιχεία Εταιρείας</h2>
              <p className="text-sm text-muted-foreground">Θα εμφανίζονται στα τιμολόγια και τα emails σας.</p>
              <div>
                <Label>Επωνυμία</Label>
                <Input value={settings.company_name || ""} onChange={(e) => setSettings({ ...settings, company_name: e.target.value })} data-testid="onb-company" />
              </div>
              <div className="grid grid-cols-2 gap-3">
                <div><Label>ΑΦΜ</Label><Input value={settings.afm || ""} onChange={(e) => setSettings({ ...settings, afm: e.target.value })} data-testid="onb-afm" /></div>
                <div><Label>ΔΟΥ</Label><Input value={settings.doy || ""} onChange={(e) => setSettings({ ...settings, doy: e.target.value })} data-testid="onb-doy" /></div>
              </div>
              <div><Label>Διεύθυνση</Label><Input value={settings.address || ""} onChange={(e) => setSettings({ ...settings, address: e.target.value })} /></div>
              <div className="grid grid-cols-2 gap-3">
                <div><Label>Τηλέφωνο</Label><Input value={settings.phone || ""} onChange={(e) => setSettings({ ...settings, phone: e.target.value })} /></div>
                <div><Label>Email γραφείου</Label><Input value={settings.email || ""} onChange={(e) => setSettings({ ...settings, email: e.target.value })} /></div>
              </div>
              <div className="grid grid-cols-2 gap-3">
                <div><Label>IBAN</Label><Input className="font-mono" value={settings.iban || ""} onChange={(e) => setSettings({ ...settings, iban: e.target.value })} /></div>
                <div><Label>Τράπεζα</Label><Input value={settings.bank_name || ""} onChange={(e) => setSettings({ ...settings, bank_name: e.target.value })} /></div>
              </div>
              <div>
                <Label>Περίοδος Δήλωσης ΦΠΑ</Label>
                <Select value={settings.vat_period} onValueChange={(v) => setSettings({ ...settings, vat_period: v })}>
                  <SelectTrigger><SelectValue /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="monthly">Μηνιαία</SelectItem>
                    <SelectItem value="quarterly">Τριμηνιαία</SelectItem>
                  </SelectContent>
                </Select>
              </div>
            </>
          )}

          {step === 1 && (
            <>
              <h2 className="font-heading text-xl">Αποστολή Email (SMTP)</h2>
              <p className="text-sm text-muted-foreground">
                Χρειάζεται ώστε να στέλνετε τιμολόγια & υπενθυμίσεις στους πελάτες. Για Gmail: δημιουργήστε ένα{" "}
                <a href="https://myaccount.google.com/apppasswords" target="_blank" rel="noreferrer" className="underline text-[hsl(var(--gold))]">App Password</a>. Μπορείτε να το παραλείψετε και να το ρυθμίσετε αργότερα.
              </p>
              <div className="grid grid-cols-2 gap-3">
                <div><Label>SMTP Host</Label><Input value={settings.smtp_host || ""} onChange={(e) => setSettings({ ...settings, smtp_host: e.target.value })} placeholder="smtp.gmail.com" /></div>
                <div><Label>Θύρα</Label><Input type="number" value={settings.smtp_port || 587} onChange={(e) => setSettings({ ...settings, smtp_port: Number(e.target.value) })} /></div>
              </div>
              <div><Label>Email αποστολέα</Label><Input type="email" value={settings.smtp_user || ""} onChange={(e) => setSettings({ ...settings, smtp_user: e.target.value })} data-testid="onb-smtp-user" /></div>
              <div><Label>App Password</Label><Input type="password" value={settings.smtp_password || ""} onChange={(e) => setSettings({ ...settings, smtp_password: e.target.value })} data-testid="onb-smtp-pass" /></div>
              <div><Label>Όνομα αποστολέα</Label><Input value={settings.smtp_from_name || ""} onChange={(e) => setSettings({ ...settings, smtp_from_name: e.target.value })} placeholder={settings.company_name} /></div>
              <Button variant="outline" onClick={testSmtp} disabled={testingSmtp || !settings.smtp_user || !settings.smtp_password} data-testid="onb-test-smtp">
                {testingSmtp ? "Δοκιμή..." : "Δοκιμή αποστολής email"}
              </Button>
            </>
          )}

          {step === 2 && (
            <>
              <h2 className="font-heading text-xl">Σύνδεση myDATA / ΑΑΔΕ</h2>
              <p className="text-sm text-muted-foreground">
                Καταχωρήστε τα διαπιστευτήριά σας για ζωντανή διαβίβαση τιμολογίων. Για sandbox: <a className="underline" href="https://mydata-register.azurewebsites.net" target="_blank" rel="noreferrer">mydata-register.azurewebsites.net</a>.
                Μπορείτε να παραλείψετε αυτό το βήμα — μέχρι τότε η διαβίβαση λειτουργεί σε προσομοίωση (mock).
              </p>
              <div><Label>AADE User ID</Label><Input value={settings.aade_user_id || ""} onChange={(e) => setSettings({ ...settings, aade_user_id: e.target.value })} data-testid="onb-aade-user" /></div>
              <div><Label>AADE Subscription Key</Label><Input type="password" value={settings.aade_subscription_key || ""} onChange={(e) => setSettings({ ...settings, aade_subscription_key: e.target.value })} data-testid="onb-aade-key" /></div>
              <div>
                <Label>Περιβάλλον</Label>
                <Select value={settings.aade_environment || "sandbox"} onValueChange={(v) => setSettings({ ...settings, aade_environment: v })}>
                  <SelectTrigger><SelectValue /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="sandbox">Sandbox (δοκιμαστικό)</SelectItem>
                    <SelectItem value="production">Παραγωγή</SelectItem>
                  </SelectContent>
                </Select>
              </div>
              <Button variant="outline" onClick={testAade} disabled={testingAade || !settings.aade_user_id || !settings.aade_subscription_key} data-testid="onb-test-aade">
                {testingAade ? "Έλεγχος..." : "Δοκιμή σύνδεσης ΑΑΔΕ"}
              </Button>
            </>
          )}

          {step === 3 && (
            <>
              <h2 className="font-heading text-xl">Εισαγωγή Πελατολογίου</h2>
              <p className="text-sm text-muted-foreground">
                Ανεβάστε το υφιστάμενο πελατολόγιό σας από Excel (.xlsx) ή CSV. Στήλες που αναγνωρίζουμε:
                <span className="font-medium text-foreground"> Όνομα, ΑΦΜ, ΔΟΥ, Email, Τηλέφωνο, Διεύθυνση, Τύπος</span>.
              </p>
              <Button variant="outline" onClick={downloadTemplate} data-testid="onb-template-button">Λήψη προτύπου CSV</Button>
              <div className="border border-dashed border-[hsl(var(--gold))]/40 rounded-xl p-6 text-center">
                <input ref={fileRef} type="file" accept=".csv,.xlsx,.xlsm" className="hidden"
                  onChange={(e) => importClients(e.target.files?.[0])} data-testid="onb-import-file" />
                <Button onClick={() => fileRef.current?.click()} disabled={importing} data-testid="onb-import-button">
                  <UploadSimple size={16} className="mr-1.5" /> {importing ? "Επεξεργασία..." : "Επιλογή αρχείου"}
                </Button>
                <p className="text-xs text-muted-foreground mt-2">Μπορείτε να το παραλείψετε — προσθέτετε πελάτες ανά πάσα στιγμή.</p>
              </div>
              {importResult && (
                <div className="text-sm p-4 rounded-xl bg-[hsl(var(--success))]/10 border border-[hsl(var(--success))]/30" data-testid="onb-import-result">
                  <div className="text-[hsl(var(--success))] font-medium">✓ {importResult.message}</div>
                  <div className="text-xs text-muted-foreground mt-1">Σύνολο γραμμών: {importResult.total_rows}</div>
                  {importResult.errors?.length > 0 && (
                    <details className="mt-2 text-xs">
                      <summary className="cursor-pointer">Σφάλματα ({importResult.errors.length})</summary>
                      <ul className="mt-1 text-[hsl(var(--destructive))]">
                        {importResult.errors.map((e, i) => <li key={i}>· {e}</li>)}
                      </ul>
                    </details>
                  )}
                </div>
              )}
            </>
          )}

          {done && (
            <div className="text-center py-8">
              <Confetti size={64} className="mx-auto text-[hsl(var(--gold))] mb-4" />
              <h2 className="font-brand text-3xl font-bold">Είστε έτοιμοι!</h2>
              <p className="text-sm text-muted-foreground mt-2">Καλή δουλειά με το Nomos One 🌟</p>
              <Button className="mt-6" onClick={finish} disabled={saving} data-testid="onb-finish-button">Είσοδος στην πλατφόρμα</Button>
            </div>
          )}
        </div>

        {!done && (
          <div className="flex items-center justify-between mt-6">
            <Button variant="ghost" onClick={() => setStep(Math.max(0, step - 1))} disabled={step === 0} data-testid="onb-back-button">
              <ArrowLeft size={16} className="mr-1.5" /> Πίσω
            </Button>
            <div className="flex gap-2">
              <Button variant="ghost" onClick={() => setStep(step + 1)} data-testid="onb-skip-button">Παράλειψη</Button>
              <Button onClick={next} disabled={saving} data-testid="onb-next-button">
                {step === STEPS.length - 1 ? "Τέλος" : "Επόμενο"} <ArrowRight size={16} className="ml-1.5" />
              </Button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
