import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";

export function ImagePreviewDialog({ src, open, onOpenChange }) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl" data-testid="receipt-image-dialog">
        <DialogHeader><DialogTitle>Απόδειξη</DialogTitle></DialogHeader>
        {src ? (
          <img src={src} alt="Απόδειξη" className="w-full rounded-sm border border-border" />
        ) : (
          <p className="text-sm text-muted-foreground">Δεν υπάρχει διαθέσιμη εικόνα για αυτό το έξοδο.</p>
        )}
      </DialogContent>
    </Dialog>
  );
}
