import { useEffect, useRef } from "react";

interface Props {
  open: boolean;
  title: string;
  message: React.ReactNode;
  confirmLabel?: string;
  danger?: boolean;
  busy?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}

/** Used for every destructive or fleet-wide action. */
export default function ConfirmDialog({ open, title, message, confirmLabel = "Confirm", danger, busy, onConfirm, onCancel }: Props) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const d = ref.current;
    if (!d) return;
    if (open && !d.open) d.showModal();
    if (!open && d.open) d.close();
  }, [open]);
  return (
    <dialog ref={ref} className="dialog" onCancel={(e) => { e.preventDefault(); onCancel(); }}>
      <h3>{title}</h3>
      <div className="dialog-body">{message}</div>
      <div className="dialog-actions">
        <button onClick={onCancel} disabled={busy}>Cancel</button>
        <button className={danger ? "danger" : "primary"} onClick={onConfirm} disabled={busy} autoFocus>
          {busy ? "Working…" : confirmLabel}
        </button>
      </div>
    </dialog>
  );
}
