const BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8080";
const ADMIN_KEY = process.env.NEXT_PUBLIC_ADMIN_API_KEY || "";

export class ApiError extends Error {
  status: number;
  body: any;
  constructor(status: number, body: any) {
    super(`API error ${status}`);
    this.status = status;
    this.body = body;
  }
}

// Dedup: if the same GET is already in-flight, reuse its promise
const _inflight = new Map<string, Promise<any>>();

async function request(path: string, options: RequestInit = {}) {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(options.headers as Record<string, string>),
  };
  if (ADMIN_KEY) {
    headers["Authorization"] = `Bearer ${ADMIN_KEY}`;
  }

  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), 20000);

  try {
    const res = await fetch(`${BASE_URL}${path}`, {
      ...options,
      headers,
      signal: controller.signal,
    });
    clearTimeout(timeoutId);

    if (!res.ok) {
      let body;
      try { body = await res.json(); } catch { body = await res.text(); }
      throw new ApiError(res.status, body);
    }
    if (res.status === 204) return null;
    return res.json();
  } catch (err: any) {
    clearTimeout(timeoutId);
    if (err.name === "AbortError") {
      throw new ApiError(0, { error: { message: "Request timed out" } });
    }
    throw err;
  }
}

function deduplicatedGet(path: string): Promise<any> {
  const existing = _inflight.get(path);
  if (existing) return existing;

  const promise = request(path).finally(() => {
    _inflight.delete(path);
  });
  _inflight.set(path, promise);
  return promise;
}

export const api = {
  get: (path: string) => deduplicatedGet(path),
  post: (path: string, data: any) => request(path, { method: "POST", body: JSON.stringify(data) }),
  put: (path: string, data: any) => request(path, { method: "PUT", body: JSON.stringify(data) }),
  patch: (path: string, data: any) => request(path, { method: "PATCH", body: JSON.stringify(data) }),
  delete: (path: string) => request(path, { method: "DELETE" }),
};

export async function fetchJson(path: string) {
  return api.get(path);
}
