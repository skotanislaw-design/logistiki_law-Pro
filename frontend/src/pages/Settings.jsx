import { useEffect, useState } from "react";
import axios, { API, errMsg } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { DisclaimerBanner } from "@/components/DisclaimerBanner";
import { toast } from "sonner";

export default function Settings() {
  const [form, setForm] = useState(null);
  const [testingEmail, setTestingEmail] = useState(false);

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
      toast.success("Οι ρυθμίσεις αποθηκεύτηκαν");
    } catch (e) {
      toast.error(errMsg(e));
    }
  };

  if (!form) return <div className="text-muted-foreground">Φόρτωση...</div>;

  return (
    <div className="space-y-6 max-w-2xl" data-testid="settings-page">
      <div>
        <h1 className="font-heading text-3xl font-bold tracking-tight">Ρυθμίσεις</h1>
        <p className="text-sm text-muted-foreground mt-1">Στοιχεία εταιρείας & σύνδεση myDATA / ΑΑΔΕ</p>
      </div>

      <DisclaimerBanner />

      <div className="border border-border rounded-sm p-5 bg-card space-y-4">
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

      <div className="border border-border rounded-sm p-5 bg-card space-y-4">
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

      <div className="border border-border rounded-sm p-5 bg-card space-y-4">
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
          <Input type="password" value={form.smtp_password || ""} onChange={(e) => setForm({ ...form, smtp_password: e.target.value })} className="mt-1.5" placeholder="16ψήφιος κωδικός εφαρμογής" data-testid="settings-smtp-password" />
        </div>
        <div>
          <Label>Όνομα αποστολέα</Label>
          <Input value={form.smtp_from_name || ""} onChange={(e) => setForm({ ...form, smtp_from_name: e.target.value })} className="mt-1.5" placeholder="π.χ. Σκοτάνης & Συνεργάτες" data-testid="settings-smtp-from-name" />
        </div>
        <Button variant="outline" size="sm" onClick={sendTestEmail} disabled={testingEmail} data-testid="settings-test-email-button">
          {testingEmail ? "Αποστολή..." : "Αποστολή δοκιμαστικού email"}
        </Button>
      </div>

      <div className="border border-border rounded-sm p-5 bg-card space-y-4">
        <h3 className="font-heading text-lg font-medium">Διαπιστευτήρια ΑΑΔΕ / myDATA</h3>
        <p className="text-sm text-muted-foreground">
          Καταχωρήστε τα πραγματικά διαπιστευτήρια σας από το <a href="https://www.aade.gr" target="_blank" rel="noreferrer" className="underline">aade.gr</a> για να ενεργοποιήσετε πραγματική σύνδεση. Χωρίς αυτά, το σύστημα λειτουργεί σε προσομοίωση (mock).
        </p>
        <div>
          <Label>AADE User ID</Label>
          <Input value={form.aade_user_id} onChange={(e) => setForm({ ...form, aade_user_id: e.target.value })} className="mt-1.5" data-testid="settings-aade-user" placeholder="Δεν έχει καταχωρηθεί" />
        </div>
        <div>
          <Label>AADE Subscription Key</Label>
          <Input type="password" value={form.aade_subscription_key} onChange={(e) => setForm({ ...form, aade_subscription_key: e.target.value })} className="mt-1.5" data-testid="settings-aade-key" placeholder="Δεν έχει καταχωρηθεί" />
        </div>
      </div>

      <Button onClick={handleSubmit} data-testid="save-settings-button">Αποθήκευση Ρυθμίσεων</Button>
    </div>
  );
}
