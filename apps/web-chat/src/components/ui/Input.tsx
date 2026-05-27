"use client";

import { InputHTMLAttributes, forwardRef } from "react";

interface InputProps extends InputHTMLAttributes<HTMLInputElement> {
  label?: string;
  error?: string | null;
}

export const Input = forwardRef<HTMLInputElement, InputProps>(function Input(
  { label, error, className = "", id, ...rest },
  ref,
) {
  const inputId = id ?? `inp-${Math.random().toString(36).slice(2, 8)}`;
  return (
    <div className="flex flex-col gap-1.5">
      {label ? (
        <label
          htmlFor={inputId}
          className="text-xs font-medium uppercase tracking-wider text-text-secondary"
        >
          {label}
        </label>
      ) : null}
      <input
        ref={ref}
        id={inputId}
        className={`rounded-md border border-border bg-bg-secondary px-3.5 py-2.5 text-sm text-text-primary placeholder:text-text-muted focus:border-border-strong focus:outline-none focus:ring-2 focus:ring-border-strong/40 ${className}`}
        {...rest}
      />
      {error ? (
        <span className="text-xs text-status-danger">{error}</span>
      ) : null}
    </div>
  );
});
