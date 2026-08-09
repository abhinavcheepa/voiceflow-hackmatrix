import { useCallback, useEffect, useState } from "react";

export const API = import.meta.env.VITE_API_URL ?? "http://localhost:8000";

async function request(path, options) {
  const r = await fetch(`${API}${path}`, options);
  if (!r.ok) {
    const detail = await r.json().catch(() => null);
    throw new Error(detail?.detail ?? `${r.status} ${r.statusText}`);
  }
  return r.status === 204 ? null : r.json();
}

export const post = (path, body) =>
  request(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });

export const put = (path, body) =>
  request(path, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });

/**
 * GET `path`, polling every `pollMs` so the dashboard reflects live calls and
 * messages without a refresh. `initial` is the shape to render before the
 * first response lands — an empty list or a zeroed object, never fake data.
 */
export function useApi(path, initial, pollMs = 10000) {
  const [data, setData] = useState(initial);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(
    (signal) =>
      request(path, { signal })
        .then((d) => {
          setData(d);
          setError(null);
        })
        .catch((e) => {
          if (e.name !== "AbortError") setError(e.message);
        })
        .finally(() => setLoading(false)),
    [path],
  );

  useEffect(() => {
    const ctrl = new AbortController();
    load(ctrl.signal);
    if (!pollMs) return () => ctrl.abort();
    const timer = setInterval(() => load(ctrl.signal), pollMs);
    return () => {
      clearInterval(timer);
      ctrl.abort();
    };
  }, [load, pollMs]);

  return { data, error, loading, reload: () => load() };
}
