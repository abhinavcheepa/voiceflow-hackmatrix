# VoiceFlow AI — Empty Endpoints & Webhooks Audit Report

**Date:** 2026-10-11  
**Project:** VoiceFlow AI (`voiceflow-ai`)  
**Scope:** `backend/` (FastAPI), `wa-bridge/` (Node.js Express), `asterisk/`, configuration (`.env`).

---

## 1. Executive Summary

Yeh document VoiceFlow AI ke sabhi **Empty Webhooks**, **Unconfigured/Inactive Webhook URLs**, **Stubbed Tool Call Handlers**, aur **503-Blocked API Endpoints** ka complete analysis aur list provide karta hai.

Is codebase me "Empty" teen categories me aate hain:
1. **Unconfigured / Blank Webhook URLs (.env level)**: Webhooks jinke public endpoints ya tokens `.env` me empty hain.
2. **Code-Level Stubbed / Incomplete Handlers (Webhook level)**: Webhook endpoints jo active toh hain lekin unke andar specific events ya tools unimplemented ("Not available right now" / no-op) hain.
3. **Runtime Inactive / Blocked Endpoints (API level)**: API routes jo missing credentials ki wajah se 503 return karte hain ya unhandled rehte hain.

---

## 2. Category 1: Empty & Unconfigured Webhooks (.env Level)

Neeche diye gaye webhooks aur unke related parameters currently `.env` me empty hain:

| Webhook / Parameter | Current Status in `.env` | Impact / Reason | Required Action |
|---|---|---|---|
| **`PUBLIC_URL`** | `PUBLIC_URL=` *(EMPTY)* | Public HTTPS URL empty hone se Vapi aur Meta dono webhooks tak reach nahi kar sakte. | `ngrok http 8000` chala kar HTTPS URL paste karein. |
| **Vapi Webhook Server URL** (`<PUBLIC_URL>/api/vapi/webhook`) | *Not configured on Vapi* | `vapi.py` me `config["server"]` tabhi set hota hai jab `PUBLIC_URL` present ho. Currently Vapi assistant ko webhook URL nahi jaata. | `PUBLIC_URL` set karein. |
| **Meta WhatsApp Webhook** (`<PUBLIC_URL>/api/whatsapp/webhook`) | *Not configured on Meta* | Meta Developer Dashboard me verify callback URL empty rehta hai. | Meta app me callback URL & `WHATSAPP_VERIFY_TOKEN` verify karein. |
| **`VAPI_PRIVATE_KEY`** | `VAPI_PRIVATE_KEY=` *(EMPTY)* | Vapi API calls fail hote hain. | `vapi.ai` se Private Key daalein. |
| **`VAPI_ASSISTANT_ID`** | `VAPI_ASSISTANT_ID=` *(EMPTY)* | Custom dashboard assistant ID set nahi hai (fallback to code-defined assistant). | Optional agar code assistant use karna ho. |
| **`VAPI_PHONE_NUMBER_ID`** | `VAPI_PHONE_NUMBER_ID=` *(EMPTY)* | Vapi rented phone number ID empty hai. | Telephony number attach karne ke liye required. |
| **`WHATSAPP_TOKEN`** | `WHATSAPP_TOKEN=` *(EMPTY)* | Meta Cloud API permanent system user token missing hai. | Meta Cloud API switch karne par required. |
| **`WHATSAPP_PHONE_NUMBER_ID`** | `WHATSAPP_PHONE_NUMBER_ID=` *(EMPTY)* | Meta WhatsApp Business phone number ID missing hai. | Meta API calls ke liye required. |
| **`VAPI_SIP_URI`** | `VAPI_SIP_URI=` *(EMPTY)* | Asterisk PBX channel Vapi bridge leg empty hai (calls greet karke hangup ho jayengi). | SIP trunk URI configure karein. |
| **`OWNER_PHONE`** | `OWNER_PHONE=` *(EMPTY)* | Vapi tool `transferCall` generate nahi hota kyunki destination phone empty hai. | Owner ka phone number daalein. |
| **`ASTERISK_ARI_PASSWORD`** | `ASTERISK_ARI_PASSWORD=` *(EMPTY)* | Asterisk ARI connection inactive rehta hai. | Asterisk `ari.conf` ka password daalein. |
| **`FALLBACK_BASE_URL` & `FALLBACK_API_KEY`** | *EMPTY* | LLM 3rd tier fallback endpoints configured nahi hain. | Backup LLM (e.g. Nvidia NIM) URL & key daalein. |

---

## 3. Category 2: Stubbed / Incomplete Webhook Logic (Code Level)

In endpoints par incoming requests toh aate hain, par logic incomplete ya stubbed hai:

### A. Vapi Webhook: `POST /api/vapi/webhook` (`backend/main.py:841`)
* **Tool-calls Stub (`_run_tools`):**
  Vapi assistant ke paas sirf `book_appointment` ka handler implemented hai.
  Jabki `backend/tools.py` me 6 comprehensive tools defined hain:
  - `book_appointment` -> ✅ **Implemented** (records booking in DB)
  - `escalate_to_human` -> ❌ **Empty / Stub** (Returns: `{"result": "Not available right now."}`)
  - `create_bill` -> ❌ **Empty / Stub** (Returns: `{"result": "Not available right now."}`)
  - `add_bill_item` -> ❌ **Empty / Stub** (Returns: `{"result": "Not available right now."}`)
  - `check_stock` -> ❌ **Empty / Stub** (Returns: `{"result": "Not available right now."}`)
  - `add_stock_entry` -> ❌ **Empty / Stub** (Returns: `{"result": "Not available right now."}`)
* **Unhandled Webhook Message Types:**
  Vapi ke message types me se sirf `assistant-request`, `status-update`, `end-of-call-report`, aur `tool-calls` handled hain.
  Baaki sabhi webhook events (e.g., `transcript`, `speech-update`, `hang`, `conversation-update`) silently ignore hokar empty fallback `{"ok": True}` return karte hain.

### B. WhatsApp Meta Webhook: `GET /api/whatsapp/webhook` & `POST /api/whatsapp/webhook` (`backend/main.py:184-207`)
* Jab `.env` me `WHATSAPP_PROVIDER=web` hota hai, yeh Meta webhooks completely **dormant/unused** rehte hain kyunki messages local bridge `POST /api/whatsapp/web/incoming` par aate hain.

### C. WhatsApp Web Bridge: `GET /qr` (`wa-bridge/index.js:491`)
* Jab bridge already connected ho ya QR code generate na hua ho, yeh endpoint `404 {"status": "starting"}` (empty QR) return karta hai.

---

## 4. Category 3: Blocked / 503 HTTP Endpoints (Credential Dependent)

Ye API endpoints code me defined hain lekin empty env credentials ki wajah se runtime par fail / blocked ho jaate hain:

| HTTP Method & Endpoint | Trigger / Function | Failure Mode |
|---|---|---|
| `POST /api/calls/outbound` | `vapi.create_call(number)` | **HTTP 503**: `RuntimeError("VAPI_PRIVATE_KEY is not set...")` |
| `POST /api/vapi/sip-number` | `vapi.register_sip_number(sip_uri)` | **HTTP 503**: `RuntimeError("VAPI_PRIVATE_KEY is not set...")` |
| `POST /api/campaigns/{campaign_id}/start` | Campaign background dialer | **HTTP 503**: `HTTPException(503, "No telephony configured — set VAPI_PRIVATE_KEY or Asterisk")` |
| `POST /api/whatsapp/sync` | WhatsApp Chat sync | **HTTP 400**: Agar provider `meta` ho. **HTTP 503**: Agar bridge disconnected ho. |
| `POST /api/whatsapp/logout` | WhatsApp Web logout | **HTTP 400**: Agar provider `meta` ho. |

---

## 5. Master Inventory: All Endpoints Status Matrix

### Backend Endpoints (`backend/main.py`)

| # | Method | Endpoint Path | Functionality | Status / Implementation State |
|---|---|---|---|---|
| 1 | GET | `/health` | Integration health check | ✅ Fully Implemented |
| 2 | GET | `/api/stats` | Dashboard metrics | ✅ Fully Implemented |
| 3 | GET | `/api/calls` | Call history list | ✅ Fully Implemented |
| 4 | GET | `/api/calls-by-hour` | Analytics hourly calls | ✅ Fully Implemented |
| 5 | GET | `/api/languages` | Language distribution | ✅ Fully Implemented |
| 6 | GET | `/api/conversations` | WhatsApp chat list | ✅ Fully Implemented |
| 7 | POST | `/api/calls/outbound` | Outbound phone call trigger | ⚠️ **Blocked (503)** - `VAPI_PRIVATE_KEY` empty |
| 8 | POST | `/api/vapi/sip-number` | Register SIP trunk | ⚠️ **Blocked (503)** - `VAPI_PRIVATE_KEY` empty |
| 9 | GET | `/api/whatsapp/webhook` | Meta Webhook Verification | ⚠️ **Dormant** - `WHATSAPP_PROVIDER=web` |
| 10 | POST | `/api/whatsapp/webhook` | Meta Webhook Inbound | ⚠️ **Dormant** - `WHATSAPP_PROVIDER=web` |
| 11 | POST | `/api/whatsapp/web/incoming` | WA Bridge Webhook Receiver | ✅ Fully Implemented |
| 12 | GET | `/api/whatsapp/link` | WA Connection Status | ✅ Fully Implemented |
| 13 | GET | `/api/whatsapp/auto-reply` | Auto-reply configuration | ✅ Fully Implemented |
| 14 | PUT | `/api/whatsapp/allowlist` | Auto-reply number allowlist | ✅ Fully Implemented |
| 15 | PUT | `/api/whatsapp/auto-reply` | Auto-reply master toggle | ✅ Fully Implemented |
| 16 | PUT | `/api/conversations/{phone}/auto-reply` | Per-contact auto-reply | ✅ Fully Implemented |
| 17 | POST | `/api/whatsapp/sync` | Sync chats from WA Web | ⚠️ Depends on bridge connection |
| 18 | POST | `/api/whatsapp/logout` | Disconnect WA Web session | ✅ Fully Implemented |
| 19 | POST | `/api/whatsapp/send` | Manual WhatsApp reply | ✅ Fully Implemented |
| 20 | GET | `/api/voice/profile` | Active voice profile & style | ✅ Fully Implemented |
| 21 | GET | `/api/voice/library` | Curated voice options | ✅ Fully Implemented |
| 22 | PUT | `/api/voice/tuning` | Speed & pitch tuning | ✅ Fully Implemented |
| 23 | POST | `/api/voice/select` | Select active voice | ✅ Fully Implemented |
| 24 | POST | `/api/voice/clone` | Upload audio to clone voice | ✅ Fully Implemented (Cartesia) |
| 25 | DELETE | `/api/voice/clone` | Delete custom cloned voice | ✅ Fully Implemented |
| 26 | GET | `/api/kb` | List knowledge documents | ✅ Fully Implemented |
| 27 | POST | `/api/kb` | Upload reference document | ✅ Fully Implemented |
| 28 | DELETE | `/api/kb/{doc_id}` | Delete knowledge document | ✅ Fully Implemented |
| 29 | GET | `/api/bookings` | List appointments & orders | ✅ Fully Implemented |
| 30 | DELETE | `/api/bookings/{booking_id}` | Cancel/delete booking | ✅ Fully Implemented |
| 31 | POST | `/api/voice/preview` | Audio preview generation | ✅ Fully Implemented |
| 32 | PUT | `/api/voice/style` | Update writing persona | ✅ Fully Implemented |
| 33 | GET | `/api/agents` | List multi-agent personas | ✅ Fully Implemented |
| 34 | POST | `/api/agents` | Create new agent | ✅ Fully Implemented |
| 35 | PUT | `/api/agents/{agent_id}` | Update agent prompt/name | ✅ Fully Implemented |
| 36 | DELETE | `/api/agents/{agent_id}` | Delete agent | ✅ Fully Implemented |
| 37 | GET | `/api/contacts` | Contact CRM list | ✅ Fully Implemented |
| 38 | GET | `/api/contacts/{contact_id}` | Single contact detail | ✅ Fully Implemented |
| 39 | DELETE | `/api/contacts/{contact_id}` | Delete contact | ✅ Fully Implemented |
| 40 | PUT | `/api/contacts/{contact_id}` | Update contact notes/status | ✅ Fully Implemented |
| 41 | GET | `/api/campaigns` | Outbound campaigns list | ✅ Fully Implemented |
| 42 | GET | `/api/campaigns/{id}/targets` | Campaign numbers list | ✅ Fully Implemented |
| 43 | POST | `/api/campaigns` | Create campaign batch | ✅ Fully Implemented |
| 44 | POST | `/api/campaigns/{id}/start` | Start campaign auto-dialer | ⚠️ **Blocked (503)** - Telephony keys empty |
| 45 | POST | `/api/campaigns/{id}/stop` | Pause campaign auto-dialer | ✅ Fully Implemented |
| 46 | GET | `/api/analytics` | Summary analytics | ✅ Fully Implemented |
| 47 | GET | `/api/stock` | Inventory list | ✅ Fully Implemented |
| 48 | POST | `/api/stock` | Add item to inventory | ✅ Fully Implemented |
| 49 | PUT | `/api/stock/{stock_id}` | Update stock item | ✅ Fully Implemented |
| 50 | DELETE | `/api/stock/{stock_id}` | Remove stock item | ✅ Fully Implemented |
| 51 | GET | `/api/stock/{id}/entries` | Item in/out entries history | ✅ Fully Implemented |
| 52 | POST | `/api/stock/{id}/entries` | Create stock in/out entry | ✅ Fully Implemented |
| 53 | GET | `/api/bills` | Invoices list | ✅ Fully Implemented |
| 54 | GET | `/api/bills/{bill_id}` | Single invoice detail | ✅ Fully Implemented |
| 55 | DELETE | `/api/bills/{bill_id}` | Delete bill | ✅ Fully Implemented |
| 56 | GET | `/api/learned` | Auto-learned Q&A list | ✅ Fully Implemented |
| 57 | PUT | `/api/learned/{id}` | Approve/reject learned Q&A | ✅ Fully Implemented |
| 58 | DELETE | `/api/learned/{id}` | Delete learned Q&A | ✅ Fully Implemented |
| 59 | WS | `/api/web-call/ws` | Real-time web browser call | ✅ Fully Implemented |
| 60 | POST | `/api/vapi/webhook` | Vapi Events & Tool Calls Webhook | ⚠️ **Partial/Stubbed Tools** - Tools 2-6 return "Not available" |

---

### Bridge Endpoints (`wa-bridge/index.js`)

| # | Method | Endpoint Path | Functionality | Status |
|---|---|---|---|---|
| 1 | GET | `/status` | Connection state & diagnostics | ✅ Fully Implemented |
| 2 | GET | `/qr` | Raw PNG QR code or clean HTML for browser | ✅ **Fixed** (Handles HTML & auto-refresh gracefully) |
| 3 | POST | `/send/text` | Send WhatsApp text message | ✅ Fully Implemented |
| 4 | POST | `/send/audio` | Send WhatsApp voice note | ✅ Fully Implemented |
| 5 | POST | `/sync` | Historical chats fetcher | ✅ Fully Implemented (fallback for changes) |
| 6 | GET | `/debug/contact/:id` | Low-level contact inspection | ✅ Diagnostic Only |
| 7 | POST | `/logout` | Terminate session | ✅ Fully Implemented |

---

## 6. Non-Vapi Fixes Completed (Summary of Changes)

1. **Knowledge Base BM25 Fallback (`backend/kb.py`)**:
   - Pehle `rank_bm25` module missing hone par KB retrieval completely crash ho raha tha (`ModuleNotFoundError`).
   - Pure-Python BM25 Okapi fallback implement kiya gaya.
   - Sabhi 6 KB unit tests (`backend/test_kb.py`) ab **100% pass** hain.
2. **WhatsApp Web Bridge Liveness & QR (`wa-bridge/index.js`)**:
   - `lastCheckAt` timestamp `/status` aur `/qr` request par actively update hota hai, jisse dashboard par `quietMinutes` accurate rehta hai.
   - `/qr` endpoint ab browser direct visit par friendly HTML page + auto-refresh provide karta hai jab QR available ya already connected ho, bajaye raw 404 crash ke.
3. **Outbound Campaign Telephony Fallback (`backend/main.py`)**:
   - `POST /api/campaigns/{campaign_id}/start` ab Vapi na hone par Asterisk dialer par fallback karne ke liye ready hai instead of strict Vapi blocking.
4. **Environment Cleanup (`backend/.env`)**:
   - Corrupted/duplicated base URL comments clean kiye gaye.
5. **Full Test Suite Status**:
   - `42/42 tests passed` (100% green).

