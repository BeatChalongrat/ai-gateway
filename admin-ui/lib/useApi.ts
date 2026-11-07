"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError } from "./api";
import { useT } from "./i18n";

interface UseApiResult<T> {
  data: T | null;
  loading: boolean;
  error: string | null;
  refetch: () => void;
}

export function useApi<T = any>(path: string): UseApiResult<T> {
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [tick, setTick] = useState(0);

  // Held in a ref so `t` (whose identity changes when the locale changes)
  // never lands in the effect's dependency list — switching language must
  // not re-issue every in-flight request.
  const t = useT();
  const tRef = useRef(t);
  tRef.current = t;

  const refetch = useCallback(() => setTick((n) => n + 1), []);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);

    api
      .get(path)
      .then((result) => {
        if (!cancelled) {
          setData(result);
        }
      })
      .catch((err) => {
        if (!cancelled) {
          // The status stays a raw HTTP code (or the localized "timeout"
          // marker when the request never got one) so it is still greppable
          // against the gateway logs.
          setError(
            err instanceof ApiError
              ? tRef.current("ui.api.error", {
                  status: err.status || tRef.current("ui.api.timeout"),
                })
              : err.message,
          );
        }
      })
      .finally(() => {
        if (!cancelled) {
          setLoading(false);
        }
      });

    return () => { cancelled = true; };
  }, [path, tick]);

  return { data, loading, error, refetch };
}
