# VoiceFlow AI Architecture & Security Model

## System Overview
- **Backend**: FastAPI (Python 3.10+), SQLite via `sqlite3` with `PRAGMA foreign_keys = ON`, WAL mode (`journal_mode = WAL`), busy timeout (5000ms), and isolated connection factories.
- **Frontend**: React 19 + Vite + Tailwind CSS v4.
- **WhatsApp Bridge**: Node.js (`whatsapp-web.js` + Express) communicating with FastAPI over authenticated HTTP webhooks.
- **Telephony**: Asterisk ARI / Vapi integration for PSTN routing.

---

## WebCall Security Gate & Resource Protection (`backend/webcall_gate.py`)

### 1. State Storage & Lifecycle
- **Current Architecture**: State is maintained in-memory within the `WebCallGate` singleton:
  - `_tickets`: Ephemeral single-use tickets (`secrets.token_urlsafe(32)`) with 30s TTL.
  - `_ip_history_ticket`: Sliding window timestamps for `/api/web-call/ticket` requests.
  - `_ip_history_ws`: Sliding window timestamps for `/api/web-call/ws` handshake attempts.
  - `_active_calls`: Set of active call session IDs enforcing the concurrency cap.
  - `_daily_seconds_used`: Daily aggregated call duration against the global budget.
  - `_ip_daily_seconds_used`: Per-IP aggregated call duration preventing single-IP budget exhaustion.
- **Server Restart Behavior**:
  - In-memory state resets upon restart.
  - In-flight calls drop cleanly when the process terminates; active slots reset to 0.
  - Expired and pending tickets are dropped cleanly; browsers obtain a fresh ticket on reload.
- **Multi-Worker / Clustered Scaling Consideration**:
  - Running multiple Uvicorn workers (`--workers N`) or multi-container clusters partitions the in-memory gate across processes.
  - **Proposed Horizontal Scaling Option**: Move state to an atomic external store:
    - Redis (`SETEX` for tickets with atomic `DEL`/`GETDEL`, Redis sliding-window sorted sets for rate limits, `INCRBYFLOAT` for daily budget).
    - Or centralized SQLite table with `BEGIN IMMEDIATE` transactions in WAL mode.

---

### 2. Origin Validation vs. True Authentication
> [!IMPORTANT]
> **Origin Validation is NOT Client Authentication**:
> Browsers enforce the `Origin` header during cross-origin WebSocket requests, making `ALLOWED_ORIGINS` effective against browser-based Cross-Site WebSocket Hijacking (CSWSH).
> However, non-browser clients (curl, Python, automated bots) can easily spoof arbitrary `Origin` headers.
>
> Therefore, the real security and cost protection boundaries are:
> 1. **Cryptographic Single-Use Tickets**: Tickets must be minted prior to WebSocket upgrade and can only be consumed once.
> 2. **Subprotocol / In-Frame Delivery**: Tickets are negotiated strictly via WebSocket subprotocols (`vf-ticket.<token>`) or the first JSON frame, never via URL query parameters, preventing leakage into web server access logs and browser history.
> 3. **Sliding-Window Rate Limiting**: Independent IP limits on both ticket minting and WebSocket handshakes.
> 4. **Concurrency & Frame Safeguards**: Global concurrency cap, 1MB maximum frame size, and 25 frames/second rate cap.
> 5. **Global & Per-IP Daily Budgets**: Hard limits on consumed audio minutes to strictly cap LLM/TTS billing.

---

### 3. Customer Persona & Tool Isolation
- Web-call sessions for inbound callers execute strictly with customer persona context:
  - `tools_ctx = {"channel": "call", "agent_id": ..., "squad": []}` (strictly lacking `"owner": True`).
- All sensitive management tools (`send_whatsapp`, `start_chat`, `auto_reply_settings`, contact management) verify `ctx.get("owner")` in `tools.py` and immediately return `"Tool '{name}' is not available."` if called by a customer session.


---

### 4. WebSocket Close Codes & Frontend Handling
The backend and frontend ([WebCall.jsx](file:///c:/Users/PC/Desktop/all%20hacathon%20project/voiceflow-hackmatrix/src/pages/WebCall.jsx)) use unified 4000-series application close codes (with 4400-series and 1009 fallback compatibility):
- **`4001` / `4401`**: Invalid or expired single-use ticket. Frontend displays: *"Session expired or invalid ticket. Please click Start call again."*
- **`4002` / `4402`**: Daily web-call budget exhausted (global or per-IP). Frontend displays: *"Daily web-call budget reached. Please try again tomorrow."*
- **`4003` / `4403`**: Forbidden Origin header. Frontend displays: *"Access from this domain is forbidden (Origin not allowed)."*
- **`4008` / `4408`**: Rate limit exceeded on ticket issue or WS connect. Frontend displays: *"Too many calls from your connection. Please wait a few minutes."*
- **`4009` / `4409`**: Server busy (maximum concurrent calls reached). Frontend displays: *"All call lines are busy right now. Please try again in a moment."*
- **`4010` / `4410`**: Hard maximum call duration reached (5 minutes). Frontend displays: *"Call ended: maximum call duration reached (5 minutes)."*
- **`4011` / `1009`**: Frame size or frame rate cap exceeded (>1MB frame or >25 frames/second). Frontend displays: *"Audio frame exceeded maximum allowed size or rate limit."*

---

### 5. Trusted Proxies (ngrok / Caddy) & Demo Day Overrides
- **Trusted Proxy IP Resolution**: Configured via `TRUSTED_PROXIES` (default: `127.0.0.1,::1,localhost`). When running behind a local tunnel (e.g. ngrok, Caddy reverse proxy), `127.0.0.1` is trusted and the client's real public IP is safely extracted from the leftmost hop of `X-Forwarded-For`. If an untrusted peer connects directly, `X-Forwarded-For` is ignored to prevent IP spoofing.
- **Demo Mode Override**: Set `WEBCALL_DEMO_MODE=true` in `.env` for hackathon demos (defaults to `false`). This automatically raises defaults:
  - `DAILY_WEBCALL_MINUTES_BUDGET`: 120.0 -> 300.0 global minutes.
  - `PER_IP_DAILY_MINUTES`: 15.0 -> 30.0 minutes per IP (strictly below the global 300m limit).
  - `RATE_LIMIT_PER_IP`: 5 -> 50 calls per 10 minutes.
