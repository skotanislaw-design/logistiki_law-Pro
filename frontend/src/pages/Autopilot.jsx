import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import axios, { API, money, dateFmt, errMsg } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Switch } from "@/components/ui/switch";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useAuth } from "@/context/AuthContext";
import { Robot, CheckCircle, WarningCircle, Info, XCircle, Play, EnvelopeSimple, ArrowsClockwise, FilePdf } from "@phosphor-icons/react";
import { toast } from "sonner";
import { formatDateTime } from "@/lib/utils";

const LEVEL = {
  info: { icon: Info, cls: "text-muted-foreground" },
  success: { icon: CheckCircle, cls: "text-[hsl(var(--success))]" },
  warning: { icon: WarningCircle, cls: "text-[hsl(var(--warning))]" },
  critical: { icon: XCircle, cls: "text-destructive" },
};

const ENTITY_LINK = {
  invoice: () => "/invoices",
  bank_tx: () => "/cashflow",
  expense: () => "/expenses",
  vat_period: () => "/vat",
  settings: () => "/settings",
  deadline: () => "/tax-calendar",
};

function Capability({ ok, label, hint }) {
  return (
    <div className="flex items-center gap-2 text-sm" title={hint}>
      <span className={`w-2 h-2 rounded-full ${ok ? "bg-[hsl(var(--success))]" : "bg-destructive/70"}`} />
      <span className={ok ? "" : "text-muted-foreground"}>{label}</span>
    </div>
  );
}

function Stat({ label, value, testid }) {
  return (
    <div className="lux-card p-4 flex flex-col gap-1" data-testid={testid}>
      <span className="text-[10px] uppercase tracking-[0.18em] text-muted-foreground">{label}</span>
      <span className="font-mono text-2xl">{value}</span>
    </div>
  );
}

export default function Autopilot() {
  const { user } = useAuth();
  const isAdmin = user?.role === "admin";
  const [status, setStatus] = useState(null);
  const [items, setItems] = useState([]);
  const [runs, setRuns] = useState([]);
  const [vatPeriods, setVatPeriods] = useState([]);
  const [settings, setSettings] = useState(null);
  const [busy, setBusy] = useState(false);
  const [preview, setPreview] = useState(null);

  const load = useCallback(async () => {
    try {
      const [s, n, r, v, st] = await Promise.all([
        axios.get(`${API}/automation/status`),
        axios.get(`${API}/automation/notifications`),
        axios.get(`${API}/automation/runs?limit=40`),
        axios.get(`${API}/automation/vat-periods`),
        axios.get(`${API}/settings`),
      ]);
      setStatus(s.data); setItems(n.data); setRuns(r.data); setVatPeriods(v.data); setSettings(st.data);
    } catch (e) {
      toast.error(errMsg(e));
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const patch = async (fields) => {
    try {
      const { data } = await axios.patch(`${API}/settings`, fields);
      setSettings(data);
      toast.success("Οι ρυθμίσεις Autopilot αποθηκεύτηκαν");
      load();
    } catch (e) {
      toast.error(errMsg(e));
    }
  };

  const resolve = async (id, decision = "done") => {
    try {
      await axios.post(`${API}/automation/notifications/${id}/resolve`, { decision });
      setItems((prev) => prev.filter((n) => n.id !== id));
      load();
    } catch (e) {
      toast.error(errMsg(e));
    }
  };

  const runNow = async (jobs) => {
    setBusy(true);
    try {
      const { data } = await axios.post(`${API}/automation/run`, { jobs });
      const errors = data.results.filter((r) => r.status === "error");
      if (errors.length) toast.warning(`Ολοκληρώθηκε με ${errors.length} σφάλματα — δείτε το ιστορικό.`);
      else toast.success("Ο κύκλος Autopilot ολοκληρώθηκε");
      load();
    } catch (e) {
      toast.error(errMsg(e));
    } finally {
      setBusy(false);
    }
  };

  const sendDigest = async () => {
    setBusy(true);
    try {
      const { data } = await axios.post(`${API}/automation/digest/send-now`);
      toast.success(data.message);
      load();
    } catch (e) {
      toast.error(errMsg(e));
    } finally {
      setBusy(false);
    }
  };

  const loadPreview = async () => {
    try {
      const { data } = await axios.get(`${API}/automation/digest/preview`);
      setPreview(data);
    } catch (e) {
      toast.error(errMsg(e));
    }
  };

  const downloadVatReturn = async (period) => {
    try {
      const res = await axios.get(`${API}/automation/vat-periods/${period}/return.pdf`, { responseType: "blob" });
      const url = URL.createObjectURL(new Blob([res.data], { type: "application/pdf" }));
      const link = document.createElement("a");
      link.href = url;
      link.download = `FPA-${period}.pdf`;
      link.click();
      URL.revokeObjectURL(url);
    } catch (e) {
      toast.error(errMsg(e));
    }
  };

  const dismissInfo = async () => {
    try {
      await axios.post(`${API}/automation/notifications/dismiss-informational`);
      load();
    } catch (e) {
      toast.error(errMsg(e));
    }
  };

  if (!status || !settings) return <div className="animate-pulse text-muted-foreground" data-testid="autopilot-loading">Φόρτωση...</div>;

  const decisions = items.filter((n) => n.requires_action);
  const feed = items.filter((n) => !n.requires_action);
  const week = status.last_7_days || {};

  return (
    <div className="space-y-8" data-testid="autopilot-page">
      <div className="flex flex-wrap items-start justify-between gap-4 animate-in-up">
        <div>
          <div className="lux-overline mb-1.5">Nomos One · Autopilot</div>
          <h1 className="font-brand text-4xl font-bold tracking-wide flex items-center gap-3">
            <Robot size={34} className="text-[hsl(var(--gold))]" /> Autopilot
          </h1>
          <p className="text-sm text-muted-foreground mt-1">
            Το γραφείο σας «τρέχει» μόνο του. Εσείς απαντάτε μόνο σε ό,τι χρειάζεται απόφαση.
          </p>
        </div>
        <div className="flex items-center gap-3">
          <div className="flex items-center gap-2 lux-card px-4 py-2">
            <Label htmlFor="autopilot-toggle" className="text-sm">Ενεργό</Label>
            <Switch id="autopilot-toggle" checked={!!settings.autopilot_enabled} disabled={!isAdmin}
              onCheckedChange={(v) => patch({ autopilot_enabled: v })} data-testid="autopilot-toggle" />
          </div>
          {isAdmin && (
            <Button onClick={() => runNow(null)} disabled={busy} data-testid="autopilot-run-now">
              <Play size={16} className="mr-1.5" /> Εκτέλεση τώρα
            </Button>
          )}
        </div>
      </div>

      <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-7 gap-4 stagger">
        <Stat label="Αποφάσεις" value={status.open_items} testid="autopilot-open-items" />
        <Stat label="Emails 7ημ." value={week.emails_sent || 0} testid="autopilot-emails" />
        <Stat label="Υπενθυμίσεις 7ημ." value={week.reminders_sent || 0} testid="autopilot-reminders" />
        <Stat label="Εξοφλήσεις 7ημ." value={week.reconciled || 0} testid="autopilot-reconciled" />
        <Stat label="myDATA 7ημ." value={week.mydata_synced || 0} testid="autopilot-mydata" />
        <Stat label="Έξοδα ΑΑΔΕ 7ημ." value={week.expenses_from_mydata || 0} testid="autopilot-mydata-expenses" />
        <Stat label="Κινήσεις τράπεζας 7ημ." value={week.bank_transactions || 0} testid="autopilot-bank-transactions" />
      </div>

      <div className="lux-card p-5 flex flex-wrap gap-x-8 gap-y-2 items-center" data-testid="autopilot-capabilities">
        <span className="text-xs uppercase tracking-[0.15em] text-muted-foreground">Δυνατότητες</span>
        <Capability ok={status.capabilities.email} label="Email (SMTP)" hint="Απαιτείται για αποστολή τιμολογίων, υπενθυμίσεων και digest" />
        <Capability ok={status.capabilities.mydata} label="myDATA / ΑΑΔΕ" hint="Χωρίς credentials τα παραστατικά μένουν σε αναμονή" />
        <Capability ok={status.capabilities.stripe} label="Stripe πληρωμές" hint="Προσθέτει payment link στις υπενθυμίσεις" />
        <Capability ok={status.capabilities.ai} label="AI / OCR" hint="Για αυτόματη καταχώρηση αποδείξεων από email" />
        <Capability ok={status.capabilities.imap} label="Εισερχόμενα email (IMAP)" hint="Σαρώνει το γραμματοκιβώτιο για παραστατικά" />
        <Capability ok={status.capabilities.mydata_expenses} label="Έξοδα από ΑΑΔΕ" hint="Κατεβάζει τα παραστατικά που διαβίβασαν οι προμηθευτές — ακριβέστερο από OCR" />
        <Capability ok={status.capabilities.openbanking} label="Τραπεζικές κινήσεις (PSD2)" hint="Οι κινήσεις έρχονται μόνες τους και συμφωνούνται — χωρίς CSV" />
        <span className="ml-auto text-xs text-muted-foreground">Κύκλος κάθε {status.cycle_minutes}′ · Digest: {status.last_digest_at ? dateFmt(status.last_digest_at) : "ποτέ"}</span>
        {(!status.capabilities.email || !status.capabilities.mydata) && (
          <Link to="/settings" className="text-xs text-[hsl(var(--gold))] underline underline-offset-4">Συμπλήρωση ρυθμίσεων →</Link>
        )}
      </div>

      <Tabs defaultValue="decisions">
        <TabsList>
          <TabsTrigger value="decisions" data-testid="tab-decisions">Χρειάζονται απόφαση ({decisions.length})</TabsTrigger>
          <TabsTrigger value="feed" data-testid="tab-feed">Τι έγινε ({feed.length})</TabsTrigger>
          <TabsTrigger value="vat" data-testid="tab-vat">Περίοδοι ΦΠΑ</TabsTrigger>
          <TabsTrigger value="rules" data-testid="tab-rules">Κανόνες</TabsTrigger>
          <TabsTrigger value="runs" data-testid="tab-runs">Ιστορικό</TabsTrigger>
        </TabsList>

        <TabsContent value="decisions" className="space-y-3 mt-4">
          {decisions.length === 0 && (
            <div className="lux-card p-8 text-center text-muted-foreground" data-testid="autopilot-no-decisions">
              <CheckCircle size={36} className="mx-auto mb-2 text-[hsl(var(--success))]" />
              Τίποτα δεν περιμένει εσάς. Το Autopilot τα χειρίζεται όλα.
            </div>
          )}
          {decisions.map((n) => {
            const L = LEVEL[n.level] || LEVEL.info;
            const link = ENTITY_LINK[n.entity]?.(n);
            return (
              <div key={n.id} className="lux-card p-4 flex gap-4 items-start" data-testid={`decision-${n.type}`}>
                <L.icon size={22} className={`${L.cls} shrink-0 mt-0.5`} />
                <div className="flex-1 min-w-0">
                  <div className="font-medium">{n.title}</div>
                  {n.message && <div className="text-sm text-muted-foreground mt-0.5">{n.message}</div>}
                  <div className="text-[11px] text-muted-foreground mt-1">{dateFmt(n.created_at)}{link && <> · <Link to={link} className="underline underline-offset-4">Άνοιγμα</Link></>}</div>
                </div>
                <div className="flex flex-wrap gap-2 shrink-0">
                  {(n.actions?.length ? n.actions : [{ key: "done", label: "Το τακτοποίησα" }]).map((a) => (
                    <Button key={a.key} size="sm" variant={a.key === "confirm" || a.key === "done" ? "default" : "outline"}
                      onClick={() => resolve(n.id, a.key)} data-testid={`decision-action-${a.key}`}>{a.label}</Button>
                  ))}
                </div>
              </div>
            );
          })}
        </TabsContent>

        <TabsContent value="feed" className="space-y-2 mt-4">
          <div className="flex justify-end">
            {feed.length > 0 && <Button variant="ghost" size="sm" onClick={dismissInfo} data-testid="autopilot-dismiss-feed">Καθαρισμός</Button>}
          </div>
          {feed.length === 0 && <div className="lux-card p-6 text-center text-muted-foreground">Καμία πρόσφατη ενέργεια.</div>}
          {feed.map((n) => {
            const L = LEVEL[n.level] || LEVEL.info;
            return (
              <div key={n.id} className="lux-card px-4 py-3 flex gap-3 items-start" data-testid={`feed-${n.type}`}>
                <L.icon size={18} className={`${L.cls} shrink-0 mt-0.5`} />
                <div className="min-w-0 flex-1">
                  <div className="text-sm">{n.title}</div>
                  {n.message && <div className="text-xs text-muted-foreground">{n.message}</div>}
                </div>
                <span className="text-[11px] text-muted-foreground shrink-0">{dateFmt(n.created_at)}</span>
              </div>
            );
          })}
        </TabsContent>

        <TabsContent value="vat" className="mt-4">
          <div className="lux-card overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="text-xs uppercase tracking-wider text-muted-foreground">
                <tr className="border-b border-white/5">
                  <th className="text-left p-3">Περίοδος</th><th className="text-right p-3">Εκροές</th><th className="text-right p-3">Εισροές</th>
                  <th className="text-right p-3">ΦΠΑ</th><th className="text-left p-3">Προθεσμία</th><th className="p-3">Κατάσταση</th>
                </tr>
              </thead>
              <tbody>
                {vatPeriods.length === 0 && <tr><td colSpan={6} className="p-6 text-center text-muted-foreground">Δεν έχει κλείσει ακόμη περίοδος.</td></tr>}
                {vatPeriods.map((v) => (
                  <tr key={v.period} className="border-b border-white/5" data-testid={`vat-period-${v.period}`}>
                    <td className="p-3 font-mono">{v.period}</td>
                    <td className="p-3 text-right font-mono">{money(v.output_vat)}</td>
                    <td className="p-3 text-right font-mono">{money(v.input_vat)}</td>
                    <td className={`p-3 text-right font-mono ${v.vat_due > 0 ? "text-destructive" : "text-[hsl(var(--success))]"}`}>{money(v.vat_due)}</td>
                    <td className="p-3">{dateFmt(v.due_date)}</td>
                    <td className="p-3 text-center">
                      <div className="flex items-center justify-center gap-2">
                        <Button size="sm" variant="ghost" onClick={() => downloadVatReturn(v.period)}
                          title="Έντυπο Φ2 σε PDF, ανά κωδικό" data-testid={`vat-return-${v.period}`}>
                          <FilePdf size={15} className="mr-1" /> Φ2
                        </Button>
                        {v.status === "submitted" ? <Badge>Υποβλήθηκε</Badge> : (
                          <Button size="sm" variant="outline" onClick={async () => { await axios.post(`${API}/automation/vat-periods/${v.period}/submitted`); load(); }}>Υποβλήθηκε</Button>
                        )}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </TabsContent>

        <TabsContent value="rules" className="mt-4 space-y-5">
          <div className="lux-card p-6 space-y-5">
            <h3 className="font-heading text-lg font-medium">Τι κάνει μόνο του</h3>
            <div className="gold-rule">◆</div>
            {[
              ["auto_email_on_issue", "Αποστολή PDF στον πελάτη μόλις εκδοθεί τιμολόγιο"],
              ["auto_reminders", "Αυτόματες υπενθυμίσεις ανείσπρακτων"],
              ["auto_mydata_retry", "Επανάληψη διαβίβασης στο myDATA μέχρι να πετύχει"],
              ["mydata_expense_sync", "Λήψη εξόδων από το myDATA (παραστατικά προμηθευτών)"],
              ["withholding_enabled", "Παρακράτηση φόρου σε τιμολόγια προς επιχειρήσεις (ποσοστό και κατηγορία: Ρυθμίσεις)"],
            ].map(([key, label]) => (
              <div key={key} className="flex items-center justify-between gap-4">
                <Label htmlFor={key} className="text-sm">{label}</Label>
                <Switch id={key} checked={!!settings[key]} disabled={!isAdmin} onCheckedChange={(v) => patch({ [key]: v })} data-testid={`rule-${key}`} />
              </div>
            ))}
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
              {[
                ["reminder_after_days", "1η υπενθύμιση μετά (ημέρες)"],
                ["reminder_interval_days", "Επόμενες κάθε (ημέρες)"],
                ["reminder_max", "Μέγιστες υπενθυμίσεις"],
                ["auto_reconcile_min_confidence", "Auto-εξόφληση από τράπεζα ≥ %"],
                ["stale_draft_days", "Ειδοποίηση για πρόχειρα μετά (ημέρες)"],
                ["withholding_min_net", "Παρακράτηση από καθαρή αξία ≥ €"],
              ].map(([key, label]) => (
                <div key={key}>
                  <Label className="text-xs">{label}</Label>
                  <Input type="number" className="mt-1.5" defaultValue={settings[key]} disabled={!isAdmin}
                    onBlur={(e) => Number(e.target.value) !== settings[key] && patch({ [key]: Number(e.target.value) })}
                    data-testid={`rule-${key}`} />
                </div>
              ))}
            </div>
          </div>

          <div className="lux-card p-6 space-y-4">
            <h3 className="font-heading text-lg font-medium">Πώς σας ενημερώνει</h3>
            <div className="gold-rule">◆</div>
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
              <div>
                <Label className="text-xs">Συχνότητα digest</Label>
                <Select value={settings.digest_frequency} onValueChange={(v) => patch({ digest_frequency: v })} disabled={!isAdmin}>
                  <SelectTrigger className="mt-1.5" data-testid="rule-digest-frequency"><SelectValue /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="daily">Καθημερινά</SelectItem>
                    <SelectItem value="weekly">Κάθε Δευτέρα</SelectItem>
                    <SelectItem value="off">Ποτέ</SelectItem>
                  </SelectContent>
                </Select>
              </div>
              <div>
                <Label className="text-xs">Ώρα αποστολής</Label>
                <Input type="number" min={0} max={23} className="mt-1.5" defaultValue={settings.digest_hour} disabled={!isAdmin}
                  onBlur={(e) => Number(e.target.value) !== settings.digest_hour && patch({ digest_hour: Number(e.target.value) })} />
              </div>
              <div>
                <Label className="text-xs">Παραλήπτης (κενό = SMTP χρήστης)</Label>
                <Input type="email" className="mt-1.5" defaultValue={settings.digest_email || ""} disabled={!isAdmin}
                  onBlur={(e) => e.target.value !== (settings.digest_email || "") && patch({ digest_email: e.target.value })} data-testid="rule-digest-email" />
              </div>
            </div>
            <div className="flex flex-wrap gap-2">
              <Button variant="outline" size="sm" onClick={loadPreview} data-testid="digest-preview"><EnvelopeSimple size={15} className="mr-1.5" /> Προεπισκόπηση</Button>
              {isAdmin && <Button variant="outline" size="sm" onClick={sendDigest} disabled={busy} data-testid="digest-send-now"><ArrowsClockwise size={15} className="mr-1.5" /> Αποστολή τώρα</Button>}
            </div>
            {preview && (
              <div className="border border-[hsl(var(--gold))]/20 rounded-lg overflow-hidden">
                <div className="px-3 py-2 text-xs text-muted-foreground bg-white/5">{preview.subject} → {preview.recipient || "(δεν έχει οριστεί παραλήπτης)"}</div>
                <iframe title="digest" srcDoc={preview.html} className="w-full h-[520px] bg-white" />
              </div>
            )}
          </div>

          <div className="lux-card p-6 space-y-4">
            <h3 className="font-heading text-lg font-medium">Παραστατικά από email</h3>
            <div className="gold-rule">◆</div>
            <p className="text-sm text-muted-foreground">
              Προωθείτε (ή ζητάτε από προμηθευτές να στέλνουν) αποδείξεις σε ένα γραμματοκιβώτιο. Το Autopilot τις διαβάζει με OCR και καταχωρεί το έξοδο.
            </p>
            <div className="flex items-center justify-between">
              <Label htmlFor="imap_enabled" className="text-sm">Ενεργοποίηση σάρωσης IMAP</Label>
              <Switch id="imap_enabled" checked={!!settings.imap_enabled} disabled={!isAdmin} onCheckedChange={(v) => patch({ imap_enabled: v })} data-testid="rule-imap" />
            </div>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
              {[["imap_host", "IMAP host", "imap.gmail.com"], ["imap_user", "Email", "paraistatika@grafeio.gr"], ["imap_password", "App password", ""], ["imap_folder", "Φάκελος", "INBOX"]].map(([key, label, ph]) => (
                <div key={key}>
                  <Label className="text-xs">{label}</Label>
                  <Input type={key === "imap_password" ? "password" : "text"} className="mt-1.5" placeholder={ph} defaultValue={settings[key] || ""} disabled={!isAdmin}
                    onBlur={(e) => e.target.value !== (settings[key] || "") && patch({ [key]: e.target.value })} data-testid={`rule-${key}`} />
                </div>
              ))}
            </div>
          </div>
        </TabsContent>

        <TabsContent value="runs" className="mt-4">
          <div className="lux-card overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="text-xs uppercase tracking-wider text-muted-foreground">
                <tr className="border-b border-white/5"><th className="text-left p-3">Εργασία</th><th className="p-3">Κατάσταση</th><th className="text-left p-3">Αποτέλεσμα</th><th className="text-left p-3">Πότε</th></tr>
              </thead>
              <tbody>
                {runs.length === 0 && <tr><td colSpan={4} className="p-6 text-center text-muted-foreground">Δεν έχει τρέξει ακόμη κύκλος.</td></tr>}
                {runs.map((r) => (
                  <tr key={r.id} className="border-b border-white/5">
                    <td className="p-3">{r.label}</td>
                    <td className="p-3 text-center"><Badge variant={r.status === "ok" ? "default" : r.status === "error" ? "destructive" : "secondary"}>{r.status}</Badge></td>
                    <td className="p-3 font-mono text-xs text-muted-foreground">{r.error || Object.entries(r.stats || {}).map(([k, v]) => `${k}=${v}`).join("  ")}</td>
                    <td className="p-3 text-xs text-muted-foreground">{formatDateTime(r.started_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </TabsContent>
      </Tabs>
    </div>
  );
}
