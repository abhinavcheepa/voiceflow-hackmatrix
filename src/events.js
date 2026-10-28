import { API, post } from "./api";

let eventSource = null;
let reconnectTimer = null;
let backoffMs = 1000;
let connecting = false;
const MAX_BACKOFF = 15000;

export async function connectEvents() {
  if (eventSource || connecting) return;
  connecting = true;

  try {
    // Request a 60-second single-use ticket via POST with Authorization header
    // The real API token never appears in the EventSource URL or proxy logs.
    let ticket = "";
    try {
      const ticketRes = await post("/api/events/ticket", {});
      ticket = ticketRes?.ticket || "";
    } catch {
      // 401 will automatically trigger "voiceflow:unauthorized" via api.js
      scheduleReconnect();
      return;
    }

    const url = `${API}/api/events${ticket ? `?ticket=${encodeURIComponent(ticket)}` : ""}`;
    eventSource = new EventSource(url);

    eventSource.onopen = () => {
      backoffMs = 1000;
      window.dispatchEvent(new CustomEvent("voiceflow:refresh"));
      window.dispatchEvent(new CustomEvent("voiceflow:sse-status", { detail: { connected: true } }));
    };

    eventSource.addEventListener("call_update", (e) => {
      try {
        const data = JSON.parse(e.data);
        window.dispatchEvent(new CustomEvent("voiceflow:call_update", { detail: data }));
        window.dispatchEvent(new CustomEvent("voiceflow:refresh"));
      } catch (err) {
        console.warn("SSE parse error", err);
      }
    });

    eventSource.addEventListener("message_update", (e) => {
      try {
        const data = JSON.parse(e.data);
        window.dispatchEvent(new CustomEvent("voiceflow:message_update", { detail: data }));
        window.dispatchEvent(new CustomEvent("voiceflow:refresh"));
      } catch (err) {
        console.warn("SSE parse error", err);
      }
    });

    eventSource.addEventListener("status_change", (e) => {
      try {
        const data = JSON.parse(e.data);
        window.dispatchEvent(new CustomEvent("voiceflow:status_change", { detail: data }));
        window.dispatchEvent(new CustomEvent("voiceflow:refresh"));
      } catch (err) {
        console.warn("SSE parse error", err);
      }
    });

    eventSource.onerror = () => {
      window.dispatchEvent(new CustomEvent("voiceflow:sse-status", { detail: { connected: false } }));
      if (eventSource) {
        eventSource.close();
        eventSource = null;
      }
      scheduleReconnect();
    };
  } catch (err) {
    console.warn("Could not initiate EventSource", err);
    scheduleReconnect();
  } finally {
    connecting = false;
  }
}

function scheduleReconnect() {
  clearTimeout(reconnectTimer);
  reconnectTimer = setTimeout(() => {
    backoffMs = Math.min(backoffMs * 1.5, MAX_BACKOFF);
    connectEvents();
  }, backoffMs);
}

export function disconnectEvents() {
  clearTimeout(reconnectTimer);
  if (eventSource) {
    eventSource.close();
    eventSource = null;
  }
}

// Global visibility listener: if app was backgrounded and woke up,
// verify SSE connection and trigger fresh load
if (typeof document !== "undefined") {
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") {
      if (!eventSource || eventSource.readyState === EventSource.CLOSED) {
        disconnectEvents();
        connectEvents();
      } else {
        window.dispatchEvent(new CustomEvent("voiceflow:refresh"));
      }
    }
  });
}
