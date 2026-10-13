import { useEffect, useState, useRef } from "react";
import axios, { API, errMsg } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { DisclaimerBanner } from "@/components/DisclaimerBanner";
import { DownloadSimple, UploadSimple, Database, ShieldCheck } from "@phosphor-icons/react";
import { TwoFactorSettings } from "@/components/TwoFactorSettings";
import { BankConnection } from "@/components/BankConnection";
import { toast } from "sonner";
import { MAX_WITHHOLDING_PERCENT, WITHHOLDING_CATEGORIES, parseRatePercent, percentToRate } from "@/lib/withholding";
import { todayAthens } from "@/lib/utils";

// Μια επαναφορά «ολοκληρώθηκε» ακόμη κι όταν κάποια έγγραφα δεν επανήλθαν, γραμμές του ελεγκτικού ίχνους διαφέρουν από
// το αντίγραφο, υπάρχοντα έγγραφα κρατήθηκαν αντί να αντικατασταθούν ή οι έλεγχοι μετά την επαναφορά (αρίθμηση
// τιμολογίων, πελατολογικοί λογαριασμοί) βρήκαν πρόβλημα. Τότε ο διαχειριστής πρέπει να το δει ως προειδοποίηση, όχι ως επιτυχία.
const sumCounts = (counts) => Object.values(counts || {}).reduce((sum, n) => sum + (Number(n) || 0), 0);
const differingRows = (result) => sumCounts(result?.differing);
const keptRows = (result) => sumCounts(result?.kept);
// `ok: false` = βρέθηκε πρόβλημα· `ok: null` = ο έλεγχος δεν ολοκληρώθηκε (πρέπει να τρέξει με το χέρι).
const checkProblem = (check) => !!check && (check.ok === false || check.ok === null);
const restoreNeedsAttention = (result) =>
  (result?.failed || 0) > 0 || differingRows(result) > 0 || keptRows(result) > 0
  || checkProblem(result?.checks?.numbering) || checkProblem(result?.checks?.trust_ledger);

const RESTORE_CONFIRM_MERGE =
  "Η επαναφορά ΠΡΟΣΘΕΤΕΙ από το αντίγραφο μόνο όσα έγγραφα λείπουν. Δεν αντικαθιστά υπάρχοντα τιμολόγια, πληρωμές, "
  + "τραπεζικές κινήσεις, κινήσεις πελατολογικού λογαριασμού, περιόδους ΦΠΑ ή ελεγκτικό ίχνος, και οι μετρητές αρίθμησης "
  + "δεν γυρίζουν πίσω. Για να γυρίσετε ολόκληρη τη βάση σε παλαιότερη κατάσταση, επαναφέρετε σε κενή βάση. Συνέχεια;";
const RESTORE_CONFIRM_OVERWRITE =
  "ΑΝΤΙΚΑΤΑΣΤΑΣΗ ΔΕΔΟΜΕΝΩΝ ΕΡΓΑΣΙΑΣ: πελάτες, υποθέσεις, δικάσιμοι, εργαζόμενοι, πρότυπα σύνταξης και ρυθμίσεις γραφείου "
  + "που διαφέρουν από το αντίγραφο θα αντικατασταθούν από την εκδοχή του αντιγράφου και οι αλλαγές που έγιναν μετά θα "
  + "χαθούν. Τιμολόγια, πληρωμές, τραπεζικές κινήσεις, πελατολογικοί λογαριασμοί, περίοδοι ΦΠΑ και ελεγκτικό ίχνος ΔΕΝ "
  + "αντικαθίστανται ποτέ. Συνέχεια;";

const RATE_ERROR = `Δώστε ποσοστό παρακράτησης από 0 έως ${MAX_WITHHOLDING_PERCENT} (π.χ. 20 ή 12,5). Οι ρυθμίσεις δεν αποθηκεύονται μέχρι να διορθωθεί.`;

export default function Settings() {
  const [form, setForm] = useState(null);
  const [testingEmail, setTestingEmail] = useState(false);
  const [testingAade, setTestingAade] = useState(false);
  const [exportingBackup, setExportingBackup] = useState(false);
  const [importingBackup, setImportingBackup] = useState(false);
  const [importingClients, setImportingClients] = useState(false);
  const [importResult, setImportResult] = useState(null);
  const [backupResult, setBackupResult] = useState(null);
  // Προαιρετική, ρητή επιλογή: αντικατάσταση και των υπαρχόντων δεδομένων εργασίας. Η προεπιλογή είναι «μόνο προσθήκη».
  const [overwriteWorking, setOverwriteWorking] = useState(false);
  // Το κείμενο του ποσοστού κρατιέται χωριστά ώστε να πληκτρολογείται «12,5» χωρίς να "τρώγεται" η υποδιαστολή.
  const [rateText, setRateText] = useState(null);
  const backupFileRef = useRef();
  const clientsFileRef = useRef();

  const exportBackup = async () => {
    setExportingBackup(true);
    try {
      const res = await axios.get(`${API}/backup/export`, { responseType: "blob" });
      const url = URL.createObjectURL(res.data);
      const a = document.createElement("a");
      a.href = url;
      a.download = `nomos-one-backup-${todayAthens()}.zip`;
      document.body.appendChild(a); a.click(); a.remove();
      URL.revokeObjectURL(url);
      toast.success("Το backup κατέβηκε επιτυχώς");
    } catch (e) {
      toast.error(errMsg(e));
    } finally { setExportingBackup(false); }
  };

  const importBackup = async (file) => {
    if (!file) return;
    const mode = overwriteWorking ? "overwrite" : "merge";
    if (!window.confirm(mode === "overwrite" ? RESTORE_CONFIRM_OVERWRITE : RESTORE_CONFIRM_MERGE)) return;
    setImportingBackup(true); setBackupResult(null);
    try {
      const fd = new FormData();
      fd.append("file", file);
      const { data } = await axios.post(`${API}/backup/import`, fd, {
        params: { mode }, headers: { "Content-Type": "multipart/form-data" },
      });
      setBackupResult(data);
      (restoreNeedsAttention(data) ? toast.warning : toast.success)(data.message);
      // Η επαναφορά μπορεί να άλλαξε τις ρυθμίσεις· μια φόρμα που δείχνει ακόμη τις παλιές τιμές θα τις ξαναέγραφε
      // πάνω στις επανελθούσες με το επόμενο «Αποθήκευση».
      try {
        const fresh = await axios.get(`${API}/settings`);
        setForm(fresh.data); setRateText(null);
      } catch (e) {
        toast.error(`Οι ρυθμίσεις δεν ανανεώθηκαν μετά την επαναφορά — ανανεώστε τη σελίδα πριν τις αποθηκεύσετε. ${errMsg(e)}`);
      }
    } catch (e) { toast.error(errMsg(e)); }
    finally {
      setImportingBackup(false);
      setOverwriteWorking(false);   // η αντικατάσταση ζητείται ξεχωριστά για κάθε επαναφορά, δεν μένει αναμμένη
    }
  };

  const importClients = async (file) => {
    if (!file) return;
    setImportingClients(true); setImportResult(null);
    try {
      const fd = new FormData();
      fd.append("file", file);
      const { data } = await axios.post(`${API}/import/clients`, fd, { headers: { "Content-Type": "multipart/form-data" } });
      setImportResult(data);
      toast.success(data.message);
    } catch (e) { toast.error(errMsg(e)); }
    finally { setImportingClients(false); }
  };

  const downloadClientsTemplate = async () => {
    try {
      const res = await axios.get(`${API}/import/clients/template`, { responseType: "blob" });
      const url = URL.createObjectURL(res.data);
      const a = document.createElement("a");
      a.href = url; a.download = "clients_template.csv"; document.body.appendChild(a); a.click(); a.remove();
      URL.revokeObjectURL(url);
    } catch (e) { toast.error(errMsg(e)); }
  };

  const testAade = async () => {
    setTestingAade(true);
    try {
      const { data } = await axios.post(`${API}/mydata/test-connection`);
      toast.success(data.message);
    } catch (e) {
      toast.error(errMsg(e));
    } finally {
      setTestingAade(false);
    }
  };

  const sendTestEmail = async () => {
    setTestingEmail(true);
    try {
      const { data } = await axios.post(`${API}/settings/test-email`);
      toast.success(data.message);
    } catch (e) {
      toast.error(errMsg(e));
    } finally {
      setTestingEmail(false);
    }
  };

  useEffect(() => {
    axios.get(`${API}/settings`).then((r) => setForm(r.data)).catch((e) => toast.error(errMsg(e)));
  }, []);

  const handleSubmit = async () => {
    try {
      const { data } = await axios.put(`${API}/settings`, form);
      setForm(data);
      setRateText(null);
      toast.success("Οι ρυθμίσεις αποθηκεύτηκαν");
    } catch (e) {
      toast.error(errMsg(e));
    }
  };

  const chooseWithholdingCategory = (value) => {
    const category = WITHHOLDING_CATEGORIES.find((c) => c.value === value);
    setRateText(null);
    setForm({ ...form, withholding_category: value, withholding_rate: category ? category.rate : form.withholding_rate });
  };

  const changeWithholdingRate = (text) => {
    setRateText(text);
    const percent = parseRatePercent(text);
    if (percent !== null) setForm({ ...form, withholding_rate: percentToRate(percent) });
  };

  if (!form) return <div className="text-muted-foreground">Φόρτωση...</div>;

  const ratePercent = rateText ?? String(Math.round((form.withholding_rate ?? 0) * 10000) / 100);
  const rateInvalid = rateText !== null && parseRatePercent(rateText) === null;

  return (
    <div className="space-y-6 max-w-2xl" data-testid="settings-page">
      <div>
        <div className="lux-overline mb-1.5">Nomos One · Γραφείο</div>
        <h1 className="font-brand text-4xl font-bold tracking-wide">Ρυθμίσεις</h1>
        <p className="text-sm text-muted-foreground mt-1">Στοιχεία εταιρείας & σύνδεση myDATA / ΑΑΔΕ</p>
      </div>

      <DisclaimerBanner />

      <TwoFactorSettings />

      <BankConnection />


      <div className="lux-card p-6 space-y-4">
        <h3 className="font-heading text-lg font-medium">Στοιχεία Εταιρείας</h3>
        <div>
          <Label>Επωνυμία</Label>
          <Input value={form.company_name} onChange={(e) => setForm({ ...form, company_name: e.target.value })} className="mt-1.5" data-testid="settings-company-name" />
        </div>
        <div className="grid grid-cols-2 gap-3">
          <div><Label>ΑΦΜ</Label><Input value={form.afm} onChange={(e) => setForm({ ...form, afm: e.target.value })} className="mt-1.5" /></div>
          <div><Label>ΔΟΥ</Label><Input value={form.doy} onChange={(e) => setForm({ ...form, doy: e.target.value })} className="mt-1.5" /></div>
        </div>
        <div>
          <Label>Διεύθυνση</Label>
          <Input value={form.address || ""} onChange={(e) => setForm({ ...form, address: e.target.value })} className="mt-1.5" data-testid="settings-address" />
        </div>
        <div className="grid grid-cols-2 gap-3">
          <div><Label>Τηλέφωνο</Label><Input value={form.phone || ""} onChange={(e) => setForm({ ...form, phone: e.target.value })} className="mt-1.5" data-testid="settings-phone" /></div>
          <div><Label>Email</Label><Input value={form.email || ""} onChange={(e) => setForm({ ...form, email: e.target.value })} className="mt-1.5" data-testid="settings-email" /></div>
        </div>
        <div>
          <Label>Περίοδος Δήλωσης ΦΠΑ</Label>
          <Select value={form.vat_period} onValueChange={(v) => setForm({ ...form, vat_period: v })}>
            <SelectTrigger className="mt-1.5"><SelectValue /></SelectTrigger>
            <SelectContent>
              <SelectItem value="monthly">Μηνιαία</SelectItem>
              <SelectItem value="quarterly">Τριμηνιαία</SelectItem>
            </SelectContent>
          </Select>
        </div>
      </div>

      <div className="lux-card p-6 space-y-4">
        <h3 className="font-heading text-lg font-medium">Στοιχεία Πληρωμών</h3>
        <p className="text-sm text-muted-foreground">Εμφανίζονται στα PDF τιμολογίων που εκδίδετε.</p>
        <div>
          <Label>IBAN</Label>
          <Input value={form.iban || ""} onChange={(e) => setForm({ ...form, iban: e.target.value })} className="mt-1.5 font-mono" placeholder="GR00 0000 0000 0000 0000 0000 000" data-testid="settings-iban" />
        </div>
        <div>
          <Label>Τράπεζα</Label>
          <Input value={form.bank_name || ""} onChange={(e) => setForm({ ...form, bank_name: e.target.value })} className="mt-1.5" placeholder="π.χ. Εθνική Τράπεζα" data-testid="settings-bank" />
        </div>
      </div>

      <div className="lux-card p-6 space-y-4" data-testid="settings-withholding">
        <h3 className="font-heading text-lg font-medium">Παρακράτηση φόρου</h3>
        <p className="text-sm text-muted-foreground">
          Εφαρμόζεται σε τιμολόγια παροχής υπηρεσιών προς επιχειρήσεις, όταν είναι ενεργή (Autopilot → Κανόνες). Η κατηγορία και το
          ποσοστό διαβιβάζονται στο myDATA· ορίστε τα σε συνεννόηση με τον λογιστή σας.
        </p>
        <div className="grid grid-cols-2 gap-3">
          <div>
            <Label>Κατηγορία myDATA</Label>
            <Select value={form.withholding_category || ""} onValueChange={chooseWithholdingCategory}>
              <SelectTrigger className="mt-1.5" data-testid="settings-withholding-category">
                <SelectValue placeholder="Επιλέξτε κατηγορία" />
              </SelectTrigger>
              <SelectContent>
                {WITHHOLDING_CATEGORIES.map((c) => <SelectItem key={c.value} value={c.value}>{c.label}</SelectItem>)}
              </SelectContent>
            </Select>
          </div>
          <div>
            <Label>Ποσοστό παρακράτησης (%)</Label>
            <Input type="number" min="0" max={MAX_WITHHOLDING_PERCENT} step="0.01" value={ratePercent} className="mt-1.5"
              aria-invalid={rateInvalid || undefined} aria-describedby={rateInvalid ? "settings-withholding-rate-error" : undefined}
              onChange={(e) => changeWithholdingRate(e.target.value)} data-testid="settings-withholding-rate" />
          </div>
        </div>
        {rateInvalid && (
          <p id="settings-withholding-rate-error" role="alert" className="text-xs text-[hsl(var(--destructive))]"
            data-testid="settings-withholding-rate-error">{RATE_ERROR}</p>
        )}
        {form.withholding_category === "2" && (
          <p className="text-xs text-[hsl(var(--warning))]" data-testid="settings-withholding-legacy">
            Η κατηγορία «2» (δικαιώματα) δεν αφορά αμοιβές υπηρεσιών· τα νέα γραφεία ξεκινούν με «3». Δεν έχει αλλάξει αυτόματα —
            επιβεβαιώστε με τον λογιστή ποια ισχύει για εσάς.
          </p>
        )}
      </div>

      <div className="lux-card p-6 space-y-4">
        <h3 className="font-heading text-lg font-medium">Αποστολή Email (SMTP)</h3>
        <p className="text-sm text-muted-foreground">
          Τα τιμολόγια αποστέλλονται στους πελάτες από το δικό σας email. Για Gmail: ενεργοποιήστε την Επαλήθευση 2 βημάτων και δημιουργήστε ένα{" "}
          <a href="https://myaccount.google.com/apppasswords" target="_blank" rel="noreferrer" className="underline text-[hsl(var(--gold))]">App Password</a>.
        </p>
        <div className="grid grid-cols-2 gap-3">
          <div>
            <Label>Διακομιστής SMTP</Label>
            <Input value={form.smtp_host || ""} onChange={(e) => setForm({ ...form, smtp_host: e.target.value })} className="mt-1.5" placeholder="smtp.gmail.com" data-testid="settings-smtp-host" />
          </div>
          <div>
            <Label>Θύρα</Label>
            <Input type="number" value={form.smtp_port || 587} onChange={(e) => setForm({ ...form, smtp_port: Number(e.target.value) })} className="mt-1.5" data-testid="settings-smtp-port" />
          </div>
        </div>
        <div>
          <Label>Email αποστολέα</Label>
          <Input type="email" value={form.smtp_user || ""} onChange={(e) => setForm({ ...form, smtp_user: e.target.value })} className="mt-1.5" placeholder="grafeio@example.gr" data-testid="settings-smtp-user" />
        </div>
        <div>
          <Label>App Password</Label>
          <Input type="password" value={form.smtp_password || ""} onChange={(e) => setForm({ ...form, smtp_password: e.target.value })} className="mt-1.5" placeholder={form.smtp_password ? "Αποθηκευμένο — αφήστε κενό για να μην αλλάξει" : "16ψήφιος κωδικός εφαρμογής"} data-testid="settings-smtp-password" />
        </div>
        <div>
          <Label>Όνομα αποστολέα</Label>
          <Input value={form.smtp_from_name || ""} onChange={(e) => setForm({ ...form, smtp_from_name: e.target.value })} className="mt-1.5" placeholder="π.χ. Σκοτάνης & Συνεργάτες" data-testid="settings-smtp-from-name" />
        </div>
        <Button variant="outline" size="sm" onClick={sendTestEmail} disabled={testingEmail} data-testid="settings-test-email-button">
          {testingEmail ? "Αποστολή..." : "Αποστολή δοκιμαστικού email"}
        </Button>
      </div>

      <div className="lux-card p-6 space-y-4">
        <div className="flex items-center justify-between">
          <h3 className="font-heading text-lg font-medium">Διαπιστευτήρια ΑΑΔΕ / myDATA</h3>
          <Badge variant={form.mydata_connected ? "default" : "secondary"} data-testid="settings-mydata-badge">
            {form.mydata_connected ? "Live σύνδεση" : "Προσομοίωση (Mock)"}
          </Badge>
        </div>
        <p className="text-sm text-muted-foreground">
          Καταχωρήστε τα διαπιστευτήριά σας από το{" "}
          <a href="https://www1.aade.gr/saadeapps2/bookkeeper-web" target="_blank" rel="noreferrer" className="underline">myDATA portal της ΑΑΔΕ</a>{" "}
          (ή sandbox κλειδιά από το <a href="https://mydata-register.azurewebsites.net" target="_blank" rel="noreferrer" className="underline">mydata-register</a>).
          Μόλις αποθηκευτούν, ενεργοποιείται αυτόματα η ζωντανή διαβίβαση παραστατικών.
        </p>
        <div>
          <Label>AADE User ID</Label>
          <Input value={form.aade_user_id} onChange={(e) => setForm({ ...form, aade_user_id: e.target.value })} className="mt-1.5" data-testid="settings-aade-user" placeholder="Δεν έχει καταχωρηθεί" />
        </div>
        <div>
          <Label>AADE Subscription Key</Label>
          <Input type="password" value={form.aade_subscription_key} onChange={(e) => setForm({ ...form, aade_subscription_key: e.target.value })} className="mt-1.5" data-testid="settings-aade-key" placeholder="Δεν έχει καταχωρηθεί" />
        </div>
        <div>
          <Label>Περιβάλλον myDATA</Label>
          <Select value={form.aade_environment || "sandbox"} onValueChange={(v) => setForm({ ...form, aade_environment: v })}>
            <SelectTrigger className="mt-1.5" data-testid="settings-aade-env"><SelectValue /></SelectTrigger>
            <SelectContent>
              <SelectItem value="sandbox">Sandbox (δοκιμαστικό — mydataapidev.aade.gr)</SelectItem>
              <SelectItem value="production">Παραγωγή (mydatapi.aade.gr)</SelectItem>
            </SelectContent>
          </Select>
        </div>
        <Button variant="outline" size="sm" onClick={testAade} disabled={testingAade} data-testid="settings-test-aade-button">
          {testingAade ? "Έλεγχος..." : "Δοκιμή σύνδεσης ΑΑΔΕ"}
        </Button>
        <p className="text-[11px] text-muted-foreground/70">Αποθηκεύστε πρώτα τις ρυθμίσεις και μετά πατήστε «Δοκιμή σύνδεσης ΑΑΔΕ».</p>
      </div>

      <Button onClick={handleSubmit} disabled={rateInvalid} data-testid="save-settings-button">Αποθήκευση Ρυθμίσεων</Button>

      <div className="lux-card p-6 space-y-4">
        <div className="flex items-center gap-2">
          <UploadSimple size={20} className="text-[hsl(var(--gold))]" />
          <h3 className="font-heading text-lg font-medium">Εισαγωγή Πελατολογίου</h3>
        </div>
        <p className="text-sm text-muted-foreground">
          Ανεβάστε πελατολόγιο από .xlsx ή .csv. Στήλες: <span className="text-foreground">Όνομα, ΑΦΜ, ΔΟΥ, Email, Τηλέφωνο, Διεύθυνση, Τύπος</span>.
          Οι διπλοεγγραφές (με ίδιο ΑΦΜ ή Όνομα) παραλείπονται.
        </p>
        <div className="flex flex-wrap gap-2">
          <Button variant="outline" onClick={downloadClientsTemplate} data-testid="download-clients-template">
            <DownloadSimple size={16} className="mr-1.5" /> Λήψη προτύπου
          </Button>
          <input ref={clientsFileRef} type="file" accept=".csv,.xlsx,.xlsm" className="hidden"
            onChange={(e) => { const file = e.target.files?.[0]; e.target.value = ""; importClients(file); }}
            data-testid="import-clients-input" />
          <Button onClick={() => clientsFileRef.current?.click()} disabled={importingClients} data-testid="import-clients-button">
            <UploadSimple size={16} className="mr-1.5" /> {importingClients ? "Εισαγωγή..." : "Ανέβασμα αρχείου"}
          </Button>
        </div>
        {importResult && (
          <div className="text-sm p-3 rounded-xl bg-[hsl(var(--success))]/5 border border-[hsl(var(--success))]/25" data-testid="import-result">
            <div className="text-[hsl(var(--success))]">✓ {importResult.message}</div>
            <div className="text-xs text-muted-foreground mt-1">Σύνολο γραμμών: {importResult.total_rows}</div>
            {importResult.errors?.length > 0 && (
              <details className="mt-1 text-xs">
                <summary className="cursor-pointer text-[hsl(var(--destructive))]">Σφάλματα ({importResult.errors.length})</summary>
                <ul className="mt-1">{importResult.errors.map((e, i) => <li key={i}>· {e}</li>)}</ul>
              </details>
            )}
          </div>
        )}
      </div>

      <div className="lux-card p-6 space-y-4">
        <div className="flex items-center gap-2">
          <Database size={20} className="text-[hsl(var(--gold))]" />
          <h3 className="font-heading text-lg font-medium">Backup & Επαναφορά</h3>
        </div>
        <p className="text-sm text-muted-foreground">
          Πλήρες αντίγραφο ασφαλείας των δεδομένων σας σε αρχείο ZIP — περιλαμβάνει και το ελεγκτικό ίχνος, τις τραπεζικές κινήσεις και τις περιόδους ΦΠΑ. Προτείνεται εβδομαδιαία λήψη και αποθήκευση σε δεύτερο μέσο.
        </p>
        <div className="text-xs text-muted-foreground rounded-xl border border-border/60 p-3 space-y-1" data-testid="backup-caveats">
          <div className="font-medium text-foreground">Τι δεν περιλαμβάνεται στο αντίγραφο</div>
          <ul className="list-disc pl-4 space-y-0.5">
            <li>
              <span className="text-foreground">Αρχεία που έχουν ανέβει</span> (φωτογραφίες αποδείξεων εξόδων, έγγραφα): βρίσκονται στον φάκελο
              αποθήκευσης της εγκατάστασης και χρειάζονται δικό τους αντίγραφο.
            </li>
            <li>
              <span className="text-foreground">Χρήστες, κωδικοί πρόσβασης και σύνδεσμοι της πύλης πελατών</span>: δεν μεταφέρονται σε αρχείο.
              Μετά από επαναφορά οι πελάτες χρειάζονται νέες προσκλήσεις.
            </li>
            <li>
              <span className="text-foreground">Μυστικά των ρυθμίσεων</span> (κωδικός SMTP, κλειδί ΑΑΔΕ, GoCardless): είναι κρυπτογραφημένα και
              διαβάζονται μόνο με το ίδιο SECRETS_KEY της εγκατάστασης. Κρατήστε το κλειδί ασφαλές και ξεχωριστά από το αντίγραφο.
            </li>
          </ul>
        </div>
        <p className="text-xs text-muted-foreground" data-testid="backup-restore-rules">
          <span className="text-foreground">Πώς λειτουργεί η επαναφορά:</span> προσθέτει μόνο όσα έγγραφα λείπουν και δεν
          αντικαθιστά ποτέ τιμολόγια, πληρωμές, τραπεζικές κινήσεις, πελατολογικό λογαριασμό, περιόδους ΦΠΑ και ελεγκτικό ίχνος.
          Για να γυρίσετε ολόκληρη τη βάση σε παλαιότερη κατάσταση, επαναφέρετε σε κενή βάση.
        </p>
        <label className="flex items-start gap-2 text-xs text-muted-foreground cursor-pointer" htmlFor="backup-overwrite-toggle">
          <input id="backup-overwrite-toggle" type="checkbox" className="mt-0.5" checked={overwriteWorking}
            onChange={(e) => setOverwriteWorking(e.target.checked)} data-testid="backup-overwrite-toggle" />
          <span>
            <span className="text-foreground">Αντικατάσταση δεδομένων εργασίας</span> (προαιρετικό): τα υπάρχοντα στοιχεία
            πελατών, υποθέσεων, δικασίμων, εργαζομένων, προτύπων σύνταξης και οι ρυθμίσεις του γραφείου αντικαθίστανται από την
            εκδοχή του αντιγράφου. Τα βιβλία (τιμολόγια, πληρωμές, τράπεζα, πελατολογικός, ΦΠΑ, ελεγκτικό ίχνος) δεν αλλάζουν ποτέ.
          </span>
        </label>
        <div className="flex flex-wrap gap-2">
          <Button variant="outline" onClick={exportBackup} disabled={exportingBackup} data-testid="export-backup-button">
            <DownloadSimple size={16} className="mr-1.5" /> {exportingBackup ? "Δημιουργία..." : "Λήψη Backup (.zip)"}
          </Button>
          <input ref={backupFileRef} type="file" accept=".zip" className="hidden"
            onChange={(e) => { const file = e.target.files?.[0]; e.target.value = ""; importBackup(file); }}
            data-testid="import-backup-input" />
          <Button variant="ghost" onClick={() => backupFileRef.current?.click()} disabled={importingBackup} data-testid="import-backup-button">
            <UploadSimple size={16} className="mr-1.5" />
            {importingBackup ? "Επαναφορά..." : overwriteWorking ? "Επαναφορά με αντικατάσταση" : "Επαναφορά από Backup"}
          </Button>
        </div>
        {backupResult && (
          <div role={restoreNeedsAttention(backupResult) ? "alert" : "status"}
            className={`text-sm p-3 rounded-xl border ${restoreNeedsAttention(backupResult)
              ? "bg-[hsl(var(--warning))]/5 border-[hsl(var(--warning))]/40"
              : "bg-[hsl(var(--success))]/5 border-[hsl(var(--success))]/25"}`}
            data-testid="backup-result" data-attention={restoreNeedsAttention(backupResult) ? "true" : "false"}>
            {restoreNeedsAttention(backupResult) ? (
              <div className="text-[hsl(var(--warning))] font-medium" data-testid="backup-attention">
                ⚠ Η επαναφορά ολοκληρώθηκε, αλλά χρειάζεται έλεγχο.
                {(backupResult.failed || 0) > 0 && ` Έγγραφα που δεν επαναφέρθηκαν: ${backupResult.failed}.`}
                {differingRows(backupResult) > 0 && ` Γραμμές ελεγκτικού ίχνους που διαφέρουν από το αντίγραφο: ${differingRows(backupResult)}.`}
                {keptRows(backupResult) > 0 && ` Υπάρχοντα στοιχεία που διέφεραν από το αντίγραφο και δεν άλλαξαν: ${keptRows(backupResult)}.`}
                {backupResult.checks?.numbering?.ok === false && " Ο έλεγχος αρίθμησης τιμολογίων βρήκε προβλήματα."}
                {backupResult.checks?.numbering?.ok === null && " Ο έλεγχος αρίθμησης τιμολογίων δεν ολοκληρώθηκε."}
                {backupResult.checks?.trust_ledger?.ok === false
                  && ` Πελατολογικοί λογαριασμοί που δεν συμφωνούν με τις κινήσεις τους: ${backupResult.checks.trust_ledger.inconsistent?.length ?? 0}.`}
                {backupResult.checks?.trust_ledger?.ok === null && " Ο έλεγχος των πελατολογικών λογαριασμών δεν ολοκληρώθηκε."}
              </div>
            ) : (
              <div className="text-[hsl(var(--success))]">✓ {backupResult.message}</div>
            )}
            {backupResult.mode === "overwrite" && (
              <div className="text-xs text-muted-foreground mt-1" data-testid="backup-mode">
                Τρόπος επαναφοράς: αντικατάσταση δεδομένων εργασίας
                {sumCounts(backupResult.replaced) > 0 && ` (αντικαταστάθηκαν ${sumCounts(backupResult.replaced)} στοιχεία)`}.
              </div>
            )}
            {backupResult.checks?.numbering?.ok === true && (
              <div className="text-xs text-muted-foreground mt-1" data-testid="backup-check-numbering">
                ✓ Έλεγχος αρίθμησης τιμολογίων: δεν βρέθηκε πρόβλημα.
              </div>
            )}
            {backupResult.checks?.trust_ledger?.ok === true && (
              <div className="text-xs text-muted-foreground mt-1" data-testid="backup-check-trust">
                ✓ Πελατολογικοί λογαριασμοί: τα υπόλοιπα συμφωνούν με τις κινήσεις τους
                {backupResult.checks.trust_ledger.accounts > 0 && ` (${backupResult.checks.trust_ledger.accounts} λογαριασμοί)`}.
              </div>
            )}
            {(backupResult.notes || []).map((note, index) => (
              <div key={`${index}:${note}`} className="text-xs text-muted-foreground mt-1" data-testid="backup-note">{note}</div>
            ))}
            {backupResult.errors?.length > 0 && (
              <details className="mt-1 text-xs">
                <summary className="cursor-pointer text-[hsl(var(--destructive))]">Έγγραφα που δεν επαναφέρθηκαν ({backupResult.failed ?? backupResult.errors.length})</summary>
                <ul className="mt-1">{backupResult.errors.map((e, i) => <li key={i}>· {e}</li>)}</ul>
              </details>
            )}
          </div>
        )}
        <div className="flex items-start gap-2 text-xs text-muted-foreground border-t border-border/50 pt-3">
          <ShieldCheck size={14} className="shrink-0 mt-0.5 text-[hsl(var(--gold))]" />
          <span>
            GDPR: Στην καρτέλα κάθε πελάτη μπορείτε να κατεβάσετε σε ZIP όλα τα δεδομένα του
            (Άρθρο 15 &amp; 20 ΓΚΠΔ — Data Subject Access Request).
          </span>
        </div>
      </div>
    </div>
  );
}
