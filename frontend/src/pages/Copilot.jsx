import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import axios, { API, money, errMsg } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Sparkle, PaperPlaneRight, FileDoc, FilePdf, ArrowSquareOut, ArrowsClockwise, Scales, Microphone, Stop } from "@phosphor-icons/react";
import { toast } from "sonner";

const SUGGESTIONS = [
  "Πόσο ΦΠΑ χρωστάω αυτόν τον μήνα;",
  "Ποια τιμολόγια είναι ανεξόφλητα;",
  "Φτιάξε τιμολόγιο 500€ για νομικές υπηρεσίες",
  "Σύνταξε επιστολή όχλησης προς πελάτη για οφειλή",
];

function newSessionId() {
  return `nomos-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}

function DocumentCard({ doc }) {
  const download = async (kind) => {
    try {
      const res = await axios.post(`${API}/documents/${kind}`, doc, { responseType: "blob" });
      const url = URL.createObjectURL(res.data);
      const a = document.createElement("a");
      a.href = url;
      a.download = kind === "word" ? "nomos-one-letter.docx" : "nomos-one-letter.pdf";
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch (e) {
      toast.error(errMsg(e));
    }
  };
  return (
    <div className="mt-3 border border-[hsl(var(--gold))]/25 rounded-sm bg-background/50 p-4 space-y-2" data-testid="copilot-document-card">
      {doc.recipient && <p className="text-xs text-muted-foreground">Προς: {doc.recipient}</p>}
      <p className="text-sm font-semibold text-[hsl(var(--gold))]">{doc.subject}</p>
      <p className="text-xs text-muted-foreground line-clamp-4 whitespace-pre-line">{doc.body}</p>
      <div className="flex gap-2 pt-1">
        <Button size="sm" variant="outline" onClick={() => download("word")} data-testid="copilot-download-word">
          <FileDoc size={15} className="mr-1.5" /> Word
        </Button>
        <Button size="sm" variant="outline" onClick={() => download("pdf")} data-testid="copilot-download-pdf">
          <FilePdf size={15} className="mr-1.5" /> PDF
        </Button>
      </div>
    </div>
  );
}

function InvoiceCard({ invoice }) {
  return (
    <div className="mt-3 border border-[hsl(var(--gold))]/25 rounded-sm bg-background/50 p-4 flex items-center justify-between" data-testid="copilot-invoice-card">
      <div>
        <p className="text-sm font-semibold text-[hsl(var(--gold))] font-mono">{invoice.number}</p>
        <p className="text-xs text-muted-foreground">{invoice.client_name} · {money(invoice.total)}</p>
      </div>
      <Link to="/invoices" className="text-xs flex items-center gap-1 text-[hsl(var(--gold))] hover:underline" data-testid="copilot-invoice-link">
        Προβολή <ArrowSquareOut size={13} />
      </Link>
    </div>
  );
}

export default function Copilot() {
  const [sessionId, setSessionId] = useState(() => sessionStorage.getItem("copilot_session") || newSessionId());
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState("");
  const [thinking, setThinking] = useState(false);
  const [recording, setRecording] = useState(false);
  const [transcribing, setTranscribing] = useState(false);
  const bottomRef = useRef(null);
  const mediaRef = useRef({ recorder: null, chunks: [] });

  useEffect(() => {
    sessionStorage.setItem("copilot_session", sessionId);
    axios.get(`${API}/copilot/history/${sessionId}`).then((r) => setMessages(r.data)).catch(() => {});
  }, [sessionId]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, thinking]);

  const send = async (text) => {
    const message = (text || input).trim();
    if (!message || thinking) return;
    setInput("");
    setMessages((m) => [...m, { role: "user", content: message }]);
    setThinking(true);
    try {
      const { data } = await axios.post(`${API}/copilot/chat`, { session_id: sessionId, message });
      setMessages((m) => [...m, { role: "assistant", content: data.reply, invoice: data.invoice, document: data.document }]);
    } catch (e) {
      toast.error(errMsg(e));
      setMessages((m) => m.slice(0, -1));
    } finally {
      setThinking(false);
    }
  };

  const startRecording = async () => {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const recorder = new MediaRecorder(stream);
      mediaRef.current.chunks = [];
      recorder.ondataavailable = (e) => mediaRef.current.chunks.push(e.data);
      recorder.onstop = async () => {
        stream.getTracks().forEach((t) => t.stop());
        const blob = new Blob(mediaRef.current.chunks, { type: "audio/webm" });
        setTranscribing(true);
        const fd = new FormData();
        fd.append("audio", blob, "voice.webm");
        try {
          const { data } = await axios.post(`${API}/copilot/voice`, fd, { headers: { "Content-Type": "multipart/form-data" } });
          setTranscribing(false);
          send(data.transcript);
        } catch (e) {
          setTranscribing(false);
          toast.error(errMsg(e));
        }
      };
      recorder.start();
      mediaRef.current.recorder = recorder;
      setRecording(true);
    } catch {
      toast.error("Δεν δόθηκε πρόσβαση στο μικρόφωνο");
    }
  };

  const stopRecording = () => {
    mediaRef.current.recorder?.stop();
    setRecording(false);
  };

  const reset = () => {
    const id = newSessionId();
    sessionStorage.setItem("copilot_session", id);
    setSessionId(id);
    setMessages([]);
  };

  return (
    <div className="flex flex-col h-[calc(100vh-180px)]" data-testid="copilot-page">
      <div className="flex items-start justify-between animate-in-up">
        <div>
          <div className="lux-overline mb-1.5">Nomos One · AI Εργαλεία</div>
          <h1 className="font-brand text-4xl font-bold tracking-wide">Nomos AI</h1>
          <p className="text-sm text-muted-foreground mt-1">Ο ψηφιακός νομικός-λογιστής σου — ρώτα, δώσε εντολή, σύνταξε</p>
        </div>
        <Button variant="outline" size="sm" onClick={reset} data-testid="copilot-new-session">
          <ArrowsClockwise size={15} className="mr-1.5" /> Νέα συνομιλία
        </Button>
      </div>

      <div className="flex-1 overflow-y-auto mt-6 pr-1 space-y-4">
        {messages.length === 0 && !thinking && (
          <div className="h-full flex flex-col items-center justify-center text-center animate-in-up">
            <div className="w-16 h-16 rounded-sm bg-[hsl(var(--gold))]/10 border border-[hsl(var(--gold))]/25 flex items-center justify-center mb-5">
              <Scales size={30} className="text-[hsl(var(--gold))]" />
            </div>
            <h2 className="font-brand text-2xl font-semibold tracking-wide">Πώς μπορώ να βοηθήσω;</h2>
            <p className="text-sm text-muted-foreground mt-1 mb-8 max-w-md">
              Γνωρίζω τα οικονομικά της εταιρείας σε πραγματικό χρόνο. Μπορώ να απαντήσω, να εκδώσω τιμολόγια και να συντάξω έγγραφα.
            </p>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 max-w-2xl w-full stagger">
              {SUGGESTIONS.map((s) => (
                <button
                  key={s}
                  onClick={() => send(s)}
                  className="lux-card p-4 text-left text-sm text-foreground/80 hover:text-foreground"
                  data-testid="copilot-suggestion"
                >
                  {s}
                </button>
              ))}
            </div>
          </div>
        )}

        {messages.map((m, i) => (
          <div key={i} className={`flex ${m.role === "user" ? "justify-end" : "justify-start"}`}>
            <div
              className={`max-w-[75%] px-4 py-3 text-sm whitespace-pre-line leading-relaxed ${
                m.role === "user"
                  ? "bg-[hsl(var(--gold))]/12 border border-[hsl(var(--gold))]/30 rounded-2xl rounded-br-md"
                  : "bg-card/70 backdrop-blur-md border border-[hsl(var(--gold))]/10 rounded-2xl rounded-bl-md"
              }`}
              data-testid={`copilot-message-${m.role}`}
            >
              {m.role === "assistant" && (
                <div className="flex items-center gap-1.5 text-[10px] tracking-[0.2em] text-[hsl(var(--gold))] mb-1.5">
                  <Sparkle size={11} /> NOMOS AI
                </div>
              )}
              {m.content.replace(/\*\*/g, "")}
              {m.invoice && <InvoiceCard invoice={m.invoice} />}
              {m.document && <DocumentCard doc={m.document} />}
            </div>
          </div>
        ))}

        {thinking && (
          <div className="flex justify-start">
            <div className="bg-card/70 backdrop-blur-md border border-[hsl(var(--gold))]/10 rounded-2xl rounded-bl-md px-4 py-3 text-sm text-muted-foreground animate-pulse" data-testid="copilot-thinking">
              <span className="flex items-center gap-2"><Sparkle size={14} className="text-[hsl(var(--gold))]" /> Το Nomos AI σκέφτεται...</span>
            </div>
          </div>
        )}
        <div ref={bottomRef} />
      </div>

      <div className="mt-4">
        <form
          onSubmit={(e) => { e.preventDefault(); send(); }}
          className="flex gap-2 items-center"
        >
          <Button
            type="button"
            variant={recording ? "destructive" : "outline"}
            size="icon"
            onClick={recording ? stopRecording : startRecording}
            disabled={thinking || transcribing}
            className={recording ? "animate-pulse" : ""}
            title={recording ? "Διακοπή εγγραφής" : "Φωνητική εντολή"}
            data-testid="copilot-mic-button"
          >
            {recording ? <Stop size={17} weight="fill" /> : <Microphone size={17} />}
          </Button>
          <Input
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder={
              recording ? "🔴 Ακούω... μίλησε και πάτα Stop"
              : transcribing ? "Αναγνώριση ομιλίας..."
              : "Ρώτησε, δώσε εντολή ή πάτα το μικρόφωνο... π.χ. «Φτιάξε τιμολόγιο 800€ για τον Παπαδόπουλο»"
            }
            className="flex-1"
            disabled={recording || transcribing}
            data-testid="copilot-input"
          />
          <Button type="submit" disabled={thinking || recording || transcribing || !input.trim()} data-testid="copilot-send-button">
            <PaperPlaneRight size={17} />
          </Button>
        </form>
        <p className="text-[11px] text-muted-foreground/60 mt-2 text-center">
          Το Nomos AI παρέχει πληροφόρηση, όχι δεσμευτική φορολογική ή νομική συμβουλή. Τελικός έλεγχος από πιστοποιημένο επαγγελματία.
        </p>
      </div>
    </div>
  );
}
