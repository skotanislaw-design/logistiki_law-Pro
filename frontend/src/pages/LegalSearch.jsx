import { useState } from "react";
import axios, { API, errMsg } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Badge } from "@/components/ui/badge";
import { MagnifyingGlass, Scales, BookOpen, Warning, Lightbulb, ArrowUpRight } from "@phosphor-icons/react";
import { toast } from "sonner";

const COURTS = ["", "Άρειος Πάγος", "Ολομέλεια ΑΠ", "Εφετείο", "Πρωτοδικείο", "Ειρηνοδικείο", "Συμβούλιο της Επικρατείας", "Διοικητικό Εφετείο", "Διοικητικό Πρωτοδικείο"];
const AREAS = ["", "Αστικό", "Ενοχικό", "Οικογενειακό", "Κληρονομικό", "Εργατικό", "Εμπορικό", "Ποινικό", "Διοικητικό", "Φορολογικό", "Δικονομικό"];

export default function LegalSearch() {
  const [query, setQuery] = useState("");
  const [court, setCourt] = useState("");
  const [area, setArea] = useState("");
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState(null);

  const search = async () => {
    if (!query.trim() || query.length < 4) return toast.error("Δώστε πιο συγκεκριμένο ερώτημα.");
    setLoading(true); setResult(null);
    try {
      const { data } = await axios.post(`${API}/legal-search/query`, { query, court, legal_area: area });
      setResult(data);
      toast.success("Η αναζήτηση ολοκληρώθηκε");
    } catch (e) { toast.error(errMsg(e)); }
    finally { setLoading(false); }
  };

  const r = result?.result;

  return (
    <div className="space-y-8" data-testid="legal-search-page">
      <div>
        <div className="lux-overline mb-1.5">Nomos One · AI Έρευνα</div>
        <h1 className="font-brand text-4xl font-bold tracking-wide">Νομολογιακή Αναζήτηση</h1>
        <p className="text-sm text-muted-foreground mt-1">
          AI-assisted προσανατολισμός σε ελληνική νομολογία — προτείνει, δεν αντικαθιστά τη νομική βιβλιοθήκη
        </p>
      </div>

      <div className="lux-card p-6 space-y-4">
        <div>
          <Label>Νομικό ερώτημα</Label>
          <Textarea
            rows={3}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="π.χ. Ευθύνη μεταφορέα για κλοπή εμπορεύματος κατά τη μεταφορά. Ποια είναι η νομολογιακή πρακτική;"
            data-testid="ls-query"
          />
        </div>
        <div className="grid grid-cols-2 gap-3">
          <div>
            <Label>Δικαστήριο (προαιρετικό)</Label>
            <Select value={court} onValueChange={setCourt}>
              <SelectTrigger data-testid="ls-court"><SelectValue placeholder="Όλα" /></SelectTrigger>
              <SelectContent>{COURTS.map((c) => <SelectItem key={c || "any"} value={c}>{c || "Όλα"}</SelectItem>)}</SelectContent>
            </Select>
          </div>
          <div>
            <Label>Κλάδος δικαίου (προαιρετικό)</Label>
            <Select value={area} onValueChange={setArea}>
              <SelectTrigger data-testid="ls-area"><SelectValue placeholder="Όλοι" /></SelectTrigger>
              <SelectContent>{AREAS.map((a) => <SelectItem key={a || "any"} value={a}>{a || "Όλοι"}</SelectItem>)}</SelectContent>
            </Select>
          </div>
        </div>
        <Button onClick={search} disabled={loading} className="w-full" data-testid="ls-search-button">
          <MagnifyingGlass size={16} className="mr-1.5" /> {loading ? "Αναζήτηση με AI..." : "Αναζήτηση"}
        </Button>
      </div>

      {r && (
        <div className="space-y-4">
          {r.topic_summary && (
            <div className="lux-card p-5">
              <div className="lux-overline">Νομικό Θέμα</div>
              <p className="mt-2 text-sm" data-testid="ls-summary">{r.topic_summary}</p>
            </div>
          )}

          {r.applicable_law?.length > 0 && (
            <div className="lux-card p-5">
              <div className="lux-overline flex items-center gap-1.5"><BookOpen size={12} /> Εφαρμοστέες Διατάξεις</div>
              <ul className="mt-2 space-y-2">
                {r.applicable_law.map((l, i) => (
                  <li key={i} className="text-sm">
                    <Badge variant="outline" className="mr-2 font-mono">{l.code}</Badge>
                    {l.description}
                  </li>
                ))}
              </ul>
            </div>
          )}

          {r.key_precedents?.length > 0 && (
            <div className="lux-card p-5">
              <div className="lux-overline flex items-center gap-1.5"><Scales size={12} className="text-[hsl(var(--gold))]" /> Νομολογιακές Αρχές</div>
              <div className="mt-3 space-y-3">
                {r.key_precedents.map((p, i) => (
                  <div key={i} className="border-l-2 border-[hsl(var(--gold))]/40 pl-3" data-testid={`ls-precedent-${i}`}>
                    <div className="text-xs font-medium text-[hsl(var(--gold))]">{p.court} {p.number && `· ${p.number}`}</div>
                    <p className="text-sm mt-1">{p.principle}</p>
                  </div>
                ))}
              </div>
            </div>
          )}

          {r.practical_tips?.length > 0 && (
            <div className="lux-card p-5">
              <div className="lux-overline flex items-center gap-1.5"><Lightbulb size={12} className="text-[hsl(var(--gold))]" /> Πρακτικές Οδηγίες</div>
              <ul className="mt-2 space-y-1 text-sm">
                {r.practical_tips.map((t, i) => <li key={i}>· {t}</li>)}
              </ul>
            </div>
          )}

          {r.risks_defenses?.length > 0 && (
            <div className="lux-card p-5">
              <div className="lux-overline flex items-center gap-1.5 text-[hsl(var(--destructive))]"><Warning size={12} /> Κίνδυνοι / Αντιρρήσεις</div>
              <ul className="mt-2 space-y-1 text-sm">
                {r.risks_defenses.map((t, i) => <li key={i}>· {t}</li>)}
              </ul>
            </div>
          )}

          {r.recommended_actions?.length > 0 && (
            <div className="lux-card p-5">
              <div className="lux-overline">Προτεινόμενα Επόμενα Βήματα</div>
              <ul className="mt-2 space-y-1 text-sm">
                {r.recommended_actions.map((t, i) => <li key={i} className="flex items-start gap-2"><ArrowUpRight size={14} className="text-[hsl(var(--gold))] mt-0.5" /> {t}</li>)}
              </ul>
            </div>
          )}

          <div className="lux-card p-4 border-[hsl(var(--gold))]/25 bg-[hsl(var(--gold))]/5">
            <p className="text-xs text-muted-foreground">
              <strong>Αποποίηση Ευθύνης:</strong> {r.disclaimer || "Επιβεβαιώστε τη νομολογία σε Ισοκράτη/Νομική Βιβλιοθήκη πριν από κάθε κατάθεση."}
            </p>
            <div className="mt-2 flex gap-2 flex-wrap">
              <a href={`https://www.areiospagos.gr/nomologia/apofaseis.asp`} target="_blank" rel="noreferrer" className="text-xs underline text-[hsl(var(--gold))]">Άρειος Πάγος</a>
              <a href={`https://www.ste.gr/portal/page/portal/StE/ProsvasiSeApofaseis`} target="_blank" rel="noreferrer" className="text-xs underline text-[hsl(var(--gold))]">Συμβούλιο Επικρατείας</a>
              <a href={`https://solon.gov.gr`} target="_blank" rel="noreferrer" className="text-xs underline text-[hsl(var(--gold))]">ΣΟΛΩΝ</a>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
