import { useState, useRef } from "react";
import axios, { API, dateFmt, errMsg } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { UploadSimple, ScanSmiley, WarningCircle, Sparkle } from "@phosphor-icons/react";
import { toast } from "sonner";

const CONFIDENCE_STYLE = {
  high: "text-[hsl(var(--success))] border-[hsl(var(--success))]/40",
  medium: "text-[hsl(var(--gold))] border-[hsl(var(--gold))]/40",
  low: "text-[hsl(var(--destructive))] border-[hsl(var(--destructive))]/40",
};

export default function Ocr() {
  const fileRef = useRef();
  const [file, setFile] = useState(null);
  const [analyzing, setAnalyzing] = useState(false);
  const [result, setResult] = useState(null);

  const analyze = async (f) => {
    if (!f) return;
    if (f.size > 8 * 1024 * 1024) return toast.error("Το αρχείο υπερβαίνει τα 8 MB.");
    setFile(f); setResult(null); setAnalyzing(true);
    try {
      const fd = new FormData();
      fd.append("file", f);
      const { data } = await axios.post(`${API}/ocr/analyze`, fd, { headers: { "Content-Type": "multipart/form-data" } });
      setResult(data);
      toast.success("Η ανάλυση ολοκληρώθηκε");
    } catch (e) { toast.error(errMsg(e)); }
    finally { setAnalyzing(false); }
  };

  const e = result?.extracted || {};

  return (
    <div className="space-y-8" data-testid="ocr-page">
      <div>
        <div className="lux-overline mb-1.5">Nomos One · AI Vision</div>
        <h1 className="font-brand text-4xl font-bold tracking-wide">OCR Νομικών Εγγράφων</h1>
        <p className="text-sm text-muted-foreground mt-1">
          Ανεβάστε δικαστική απόφαση, δικόγραφο ή εξώδικο → αυτόματη εξαγωγή δεδομένων
        </p>
      </div>

      <div className="lux-card p-8 text-center border-dashed border-[hsl(var(--gold))]/30" data-testid="ocr-upload-zone">
        <ScanSmiley size={48} className="mx-auto text-[hsl(var(--gold))]/50 mb-3" />
        <input
          ref={fileRef}
          type="file"
          accept="image/*,.pdf"
          className="hidden"
          onChange={(e) => analyze(e.target.files?.[0])}
          data-testid="ocr-file-input"
        />
        <Button onClick={() => fileRef.current?.click()} disabled={analyzing} data-testid="ocr-upload-button">
          <UploadSimple size={16} className="mr-1.5" /> {analyzing ? "Ανάλυση με AI..." : "Επιλογή εγγράφου"}
        </Button>
        <p className="text-xs text-muted-foreground mt-2">Υποστηρίζονται: PDF, PNG, JPG, WEBP · έως 8 MB</p>
        {file && !analyzing && <p className="text-xs mt-2">✓ {file.name}</p>}
      </div>

      {result && (
        <div className="space-y-4">
          <div className="lux-card p-6 space-y-4">
            <div className="flex items-start justify-between">
              <div>
                <div className="lux-overline">Αναγνωρίστηκε ως</div>
                <div className="mt-1 font-heading text-xl">{e.document_type || "Άγνωστος τύπος"}</div>
              </div>
              {e.confidence && (
                <Badge variant="outline" className={CONFIDENCE_STYLE[e.confidence]}>
                  Πιθανότητα: {e.confidence === "high" ? "Υψηλή" : e.confidence === "medium" ? "Μεσαία" : "Χαμηλή"}
                </Badge>
              )}
            </div>

            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              {e.court && <Field label="Δικαστήριο" value={e.court} testid="ocr-court" />}
              {e.decision_number && <Field label="Αριθμός Απόφασης" value={e.decision_number} testid="ocr-decision" />}
              {e.case_number && <Field label="Αριθμός Υπόθεσης" value={e.case_number} />}
              {e.gak && <Field label="ΓΑΚ" value={e.gak} />}
              {e.eak && <Field label="ΕΑΚ" value={e.eak} />}
              {e.date && <Field label="Ημερομηνία" value={dateFmt(e.date)} />}
              {e.amount && <Field label="Ποσό" value={e.amount} />}
              {e.parties?.plaintiff && <Field label="Ενάγων / Αιτών" value={e.parties.plaintiff} />}
              {e.parties?.defendant && <Field label="Εναγόμενος / Καθ'ου" value={e.parties.defendant} />}
            </div>

            {e.subject && (
              <div className="border-t border-border/50 pt-3">
                <div className="lux-overline">Αντικείμενο</div>
                <p className="text-sm mt-1">{e.subject}</p>
              </div>
            )}

            {e.summary && (
              <div className="border-t border-border/50 pt-3">
                <div className="lux-overline flex items-center gap-1.5"><Sparkle size={12} /> Σύνοψη AI</div>
                <p className="text-sm mt-1 leading-relaxed" data-testid="ocr-summary">{e.summary}</p>
              </div>
            )}

            {e.key_facts?.length > 0 && (
              <div className="border-t border-border/50 pt-3">
                <div className="lux-overline">Σημαντικά Γεγονότα</div>
                <ul className="text-sm mt-1 space-y-1">
                  {e.key_facts.map((f, i) => <li key={i}>· {f}</li>)}
                </ul>
              </div>
            )}

            {e.next_actions?.length > 0 && (
              <div className="border-t border-border/50 pt-3">
                <div className="lux-overline text-[hsl(var(--gold))]">Προτεινόμενες Ενέργειες</div>
                <ul className="text-sm mt-1 space-y-1">
                  {e.next_actions.map((a, i) => <li key={i}>→ {a}</li>)}
                </ul>
              </div>
            )}
          </div>

          <div className="text-xs text-muted-foreground flex items-start gap-2 p-3">
            <WarningCircle size={14} className="shrink-0 mt-0.5" />
            <span>Η αυτόματη εξαγωγή είναι βοηθητική και ενδέχεται να περιέχει σφάλματα. Πάντα ελέγξτε τα δεδομένα με το πρωτότυπο έγγραφο.</span>
          </div>
        </div>
      )}
    </div>
  );
}

function Field({ label, value, testid }) {
  return (
    <div>
      <div className="lux-overline text-[10px]">{label}</div>
      <div className="mt-0.5 text-sm font-medium" data-testid={testid}>{value}</div>
    </div>
  );
}
