import { useState } from "react";
import axios, { API, errMsg } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Sparkle, FileDoc, FilePdf } from "@phosphor-icons/react";
import { toast } from "sonner";
import { todayAthens } from "@/lib/utils";

const GREEK_MONTHS = ["", "Ιανουαρίου", "Φεβρουαρίου", "Μαρτίου", "Απριλίου", "Μαΐου", "Ιουνίου", "Ιουλίου", "Αυγούστου", "Σεπτεμβρίου", "Οκτωβρίου", "Νοεμβρίου", "Δεκεμβρίου"];

export default function Documents() {
  const [form, setForm] = useState({ recipient: "", subject: "", body: "" });
  const [instruction, setInstruction] = useState("");
  const [drafting, setDrafting] = useState(false);
  const [downloading, setDownloading] = useState("");

  const aiDraft = async () => {
    if (!instruction.trim()) return toast.error("Γράψε μια οδηγία για τη σύνταξη");
    setDrafting(true);
    try {
      const { data } = await axios.post(`${API}/documents/ai-draft`, { instruction });
      setForm({ recipient: data.recipient || "", subject: data.subject || "", body: data.body || "" });
      toast.success("Το έγγραφο συντάχθηκε — έλεγξε και κατέβασέ το");
    } catch (e) {
      toast.error(errMsg(e));
    } finally {
      setDrafting(false);
    }
  };

  const download = async (kind) => {
    if (!form.body.trim()) return toast.error("Το περιεχόμενο είναι κενό");
    setDownloading(kind);
    try {
      const res = await axios.post(`${API}/documents/${kind}`, form, { responseType: "blob" });
      const url = URL.createObjectURL(res.data);
      const a = document.createElement("a");
      a.href = url;
      a.download = kind === "word" ? "nomos-one-letter.docx" : "nomos-one-letter.pdf";
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
      toast.success(kind === "word" ? "Το Word δημιουργήθηκε" : "Το PDF δημιουργήθηκε");
    } catch (e) {
      toast.error(errMsg(e));
    } finally {
      setDownloading("");
    }
  };

  // Η ημερομηνία της επιστολής είναι η σημερινή στην Αθήνα — όπως ακριβώς θα τυπωθεί στο Word/PDF του server.
  const [letterYear, letterMonth, letterDay] = todayAthens().split("-").map(Number);

  return (
    <div className="space-y-8" data-testid="documents-page">
      <div className="animate-in-up">
        <div className="lux-overline mb-1.5">Nomos One · AI Εργαλεία</div>
        <h1 className="font-brand text-4xl font-bold tracking-wide">Έγγραφα</h1>
        <p className="text-sm text-muted-foreground mt-1">Επιστολές & έγγραφα σε Word/PDF με το letterhead της εταιρείας</p>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 stagger">
        <div className="space-y-5">
          <div className="lux-card p-6 space-y-3">
            <h3 className="font-heading text-base font-medium flex items-center gap-2">
              <Sparkle size={17} className="text-[hsl(var(--gold))]" /> Σύνταξη με AI
            </h3>
            <p className="text-xs text-muted-foreground">Περιέγραψε τι θες και το Nomos AI θα συντάξει το έγγραφο.</p>
            <div className="flex gap-2">
              <Input
                value={instruction}
                onChange={(e) => setInstruction(e.target.value)}
                placeholder='π.χ. "Επιστολή όχλησης προς Παπαδόπουλο για οφειλή 1.200€"'
                data-testid="documents-ai-instruction"
              />
              <Button onClick={aiDraft} disabled={drafting} data-testid="documents-ai-draft-button">
                {drafting ? "Σύνταξη..." : "Σύνταξη"}
              </Button>
            </div>
          </div>

          <div className="lux-card p-6 space-y-4">
            <div>
              <Label>Παραλήπτης</Label>
              <Input value={form.recipient} onChange={(e) => setForm({ ...form, recipient: e.target.value })} className="mt-1.5" placeholder="π.χ. κ. Ιωάννη Παπαδόπουλο" data-testid="documents-recipient" />
            </div>
            <div>
              <Label>Θέμα</Label>
              <Input value={form.subject} onChange={(e) => setForm({ ...form, subject: e.target.value })} className="mt-1.5" placeholder="Θέμα εγγράφου" data-testid="documents-subject" />
            </div>
            <div>
              <Label>Περιεχόμενο</Label>
              <Textarea value={form.body} onChange={(e) => setForm({ ...form, body: e.target.value })} rows={12} className="mt-1.5" placeholder="Το κείμενο της επιστολής..." data-testid="documents-body" />
            </div>
            <div className="flex gap-2">
              <Button onClick={() => download("word")} disabled={downloading === "word"} data-testid="documents-download-word">
                <FileDoc size={16} className="mr-1.5" /> {downloading === "word" ? "Δημιουργία..." : "Λήψη Word"}
              </Button>
              <Button variant="outline" onClick={() => download("pdf")} disabled={downloading === "pdf"} data-testid="documents-download-pdf">
                <FilePdf size={16} className="mr-1.5" /> {downloading === "pdf" ? "Δημιουργία..." : "Λήψη PDF"}
              </Button>
            </div>
          </div>
        </div>

        <div className="lux-card p-2 overflow-hidden">
          <div className="bg-white text-black rounded-[2px] h-full p-8 overflow-y-auto max-h-[720px]" data-testid="documents-preview">
            <img src="/letterhead-logo.png" alt="letterhead" className="w-40 mx-auto mb-3" />
            <div className="h-px bg-[#c9a227] mb-6" />
            <p className="text-right text-xs text-gray-500 mb-4">Αθήνα, {letterDay} {GREEK_MONTHS[letterMonth]} {letterYear}</p>
            {form.recipient && <p className="text-sm font-semibold text-[#0a0f1e] mb-2">Προς: {form.recipient}</p>}
            {form.subject && <p className="text-sm font-bold text-[#0a0f1e] mb-4">Θέμα: {form.subject}</p>}
            <div className="text-[13px] leading-relaxed text-gray-800 whitespace-pre-line text-justify min-h-[180px]">
              {form.body || <span className="text-gray-400">Η προεπισκόπηση του εγγράφου θα εμφανιστεί εδώ...</span>}
            </div>
            <p className="text-[13px] mt-8">Με εκτίμηση,</p>
            <p className="text-[13px] font-bold text-[#0a0f1e]">Σκοτάνης & Συνεργάτες</p>
          </div>
        </div>
      </div>
    </div>
  );
}
