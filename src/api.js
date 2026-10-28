import { useCallback, useEffect, useState } from "react";

// Empty = same origin: Vite proxies /api to the backend (vite.config.js). That
// is what lets a phone on the Wi-Fi use the app — "localhost:8000" on a phone
// is the phone. Set VITE_API_URL only to point at a backend elsewhere.
export const API = import.meta.env.VITE_API_URL ?? "";

export function getApiToken() {
  return localStorage.getItem("voiceflow_api_token") || import.meta.env.VITE_API_TOKEN || "";
}

export function setApiToken(token) {
  if (token) {
    localStorage.setItem("voiceflow_api_token", token);
  } else {
    localStorage.removeItem("voiceflow_api_token");
  }
  window.dispatchEvent(new CustomEvent("voiceflow:token-change"));
}

export async function request(path, options = {}) {
  const token = getApiToken();
  const headers = { ...options.headers };
  if (token) {
    headers["Authorization"] = `Bearer ${token}`;
  }

  const r = await fetch(`${API}${path}`, { ...options, headers });
  if (r.status === 401) {
    window.dispatchEvent(new CustomEvent("voiceflow:unauthorized"));
  }
  if (!r.ok) {
    const detail = await r.json().catch(() => null);
    throw new Error(detail?.detail ?? `${r.status} ${r.statusText}`);
  }
  return r.status === 204 ? null : r.json();
}

export const get = (path) => request(path);

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

export const del = (path) =>
  request(path, {
    method: "DELETE",
  });

/**
 * GET `path`, polling every `pollMs` or reacting to SSE events and tab visibility.
 * Immediately refetches on reconnect or visibilitychange.
 */
export function useApi(path, initial, pollMs = 15000) {
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

    const onVisibility = () => {
      if (document.visibilityState === "visible") {
        load();
      }
    };
    document.addEventListener("visibilitychange", onVisibility);

    const onRefresh = () => {
      load();
    };
    window.addEventListener("voiceflow:refresh", onRefresh);

    let timer = null;
    if (pollMs) {
      timer = setInterval(() => {
        if (document.visibilityState === "visible") {
          load(ctrl.signal);
        }
      }, pollMs);
    }

    return () => {
      document.removeEventListener("visibilitychange", onVisibility);
      window.removeEventListener("voiceflow:refresh", onRefresh);
      if (timer) clearInterval(timer);
      ctrl.abort();
    };
  }, [load, pollMs]);

  return { data, error, loading, reload: () => load() };
}
