import type { ReactNode } from "react";
import { STATUS_LABEL, tone } from "../services/format";

/** A status as plain coloured text (no badge). */
export function State({ status }: { status: string }) {
  const t = tone(status);
  return <span className={`state ${t}`}>{STATUS_LABEL[status] ?? status}</span>;
}

export function Note({ tone: t, children }: { tone?: "bad" | "warn" | "ok"; children: ReactNode }) {
  return <p className={`note-block ${t ?? ""}`} role={t === "bad" ? "alert" : undefined}>{children}</p>;
}

export function Empty({ children }: { children: ReactNode }) {
  return <p className="empty">{children}</p>;
}

export function Section({ title, note, first, children }: { title?: string; note?: ReactNode; first?: boolean; children: ReactNode }) {
  return (
    <section className={`sec ${first ? "first" : ""}`}>
      {title && <h2>{title}</h2>}
      {note && <p className="note">{note}</p>}
      {children}
    </section>
  );
}
