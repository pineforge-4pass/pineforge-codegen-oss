"use client";

import type { HTMLInputTypeAttribute, ReactNode } from "react";
import { HONEYPOT_FIELD } from "@/lib/validate";

/**
 * A labelled field. The label text is the field's accessible name, verbatim;
 * hints and errors are tied to the control with aria-describedby, and an
 * error sets aria-invalid.
 */
export function Field({
  name,
  label,
  error,
  hint,
  type = "text",
  autoComplete,
  multiline = false,
  required = false,
  defaultValue,
  mono = false,
  children,
}: {
  name: string;
  label: string;
  error?: string;
  hint?: ReactNode;
  type?: HTMLInputTypeAttribute;
  autoComplete?: string;
  multiline?: boolean;
  required?: boolean;
  defaultValue?: string;
  mono?: boolean;
  /** A <select>'s options; renders a select instead of an input. */
  children?: ReactNode;
}) {
  const id = `field-${name}`;
  const hintId = hint ? `hint-${name}` : undefined;
  const errorId = error ? `error-${name}` : undefined;
  const describedBy = [hintId, errorId].filter(Boolean).join(" ") || undefined;
  const common = {
    id,
    name,
    "aria-invalid": error ? true : undefined,
    "aria-describedby": describedBy,
    "aria-required": required || undefined,
    className: "input" + (mono ? " mono" : ""),
    defaultValue,
  } as const;
  return (
    <div>
      <label htmlFor={id} className="field-label">
        {label}
      </label>
      {hint ? (
        <span id={hintId} className="field-hint">
          {hint}
        </span>
      ) : null}
      {children ? (
        <select {...common}>{children}</select>
      ) : multiline ? (
        <textarea {...common} rows={6} />
      ) : (
        <input {...common} type={type} autoComplete={autoComplete} />
      )}
      {error ? <FieldError id={`error-${name}`} name={name} message={error} /> : null}
    </div>
  );
}

export function FieldError({ id, name, message }: { id: string; name: string; message: string }) {
  return (
    <p id={id} data-testid={`error-${name}`} className="field-error">
      {message}
    </p>
  );
}

/** The honeypot: off screen, out of the tab order, never autofilled. */
export function Honeypot({ label }: { label: string }) {
  return (
    <div className="hp" aria-hidden="true">
      <label htmlFor="f-x7q">{label}</label>
      <input id="f-x7q" type="text" name={HONEYPOT_FIELD} tabIndex={-1} autoComplete="off" defaultValue="" />
    </div>
  );
}
