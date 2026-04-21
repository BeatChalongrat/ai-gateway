"use client";

import { InputHTMLAttributes, forwardRef, useState } from "react";

interface Props extends InputHTMLAttributes<HTMLInputElement> {
  label?: string;
  error?: string;
  showToggle?: boolean; // for password inputs
}

export const Input = forwardRef<HTMLInputElement, Props>(function Input(
  { label, error, showToggle, type = "text", className = "", id, ...rest },
  ref
) {
  const [reveal, setReveal] = useState(false);
  const realType = showToggle && type === "password" ? (reveal ? "text" : "password") : type;
  const inputId = id ?? label?.toLowerCase().replace(/\s+/g, "-");

  return (
    <div className="w-full">
      {label ? (
        <label
          htmlFor={inputId}
          className="block text-xs font-semibold uppercase tracking-wider text-gray-600 mb-1.5"
        >
          {label}
        </label>
      ) : null}
      <div className="relative">
        <input
          ref={ref}
          id={inputId}
          type={realType}
          className={[
            "w-full h-10 px-3 rounded-lg bg-white border text-sm",
            "placeholder:text-gray-400 text-gray-900",
            "focus:outline-none focus:ring-2 focus:ring-brand-blue/30 focus:border-brand-blue",
            error ? "border-brand-red" : "border-gray-300",
            showToggle && type === "password" ? "pr-16" : "",
            className,
          ].join(" ")}
          {...rest}
        />
        {showToggle && type === "password" ? (
          <button
            type="button"
            onClick={() => setReveal((v) => !v)}
            tabIndex={-1}
            className="absolute right-2 top-1/2 -translate-y-1/2 text-xs font-medium text-brand-navy hover:text-brand-blue px-1"
          >
            {reveal ? "Hide" : "Show"}
          </button>
        ) : null}
      </div>
      {error ? <p className="text-xs text-brand-red mt-1">{error}</p> : null}
    </div>
  );
});
