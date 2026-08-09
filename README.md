# VoiceFlow AI

**Your voice. Your accent. Answering every customer.**

A self-hosted AI voice agent for Indian small businesses. It answers every
inbound call and WhatsApp message 24/7, in whatever language the customer
speaks, replying in a clone of the owner's own voice and writing style.

**Team name — Voice Flow**

---

## Problem Statement

Small and medium businesses in India lose customers every single day for five
specific, connected reasons:

1. **Missed calls = missed revenue.** After hours, during a rush, or when staff
   are busy, the call goes unanswered — and the customer simply dials a
   competitor next.
2. **Human call agents don't scale.** Hiring and training is expensive, and a
   small business cannot staff 24/7 support no matter how many leads it loses
   by not doing so.
3. **Global voice AI is English-first.** Vapi, Bland AI and Retell AI have weak
   Hindi and Indian-accent support, bill per minute in USD, and move customer
   data out of the country.
4. **WhatsApp replies are still manual.** It is the channel Indian customers
   actually prefer, yet every reply is typed by hand — so a slow response is a
   lost customer.
5. **Nothing lives in one place.** Call logs, WhatsApp chats and customer
   records sit in separate systems, so owners have no single view of what is
   actually happening in their business.

## Solution Overview

VoiceFlow AI answers each of the five problems above directly:

| Problem                     | What VoiceFlow AI does                                                                                                              |
| --------------------------- | ----------------------------------------------------------------------------------------------------------------------------------- |
| Missed calls                | An AI voice agent picks up every inbound call instantly, 24/7 — understands the query, answers it, or books and logs the lead.        |
| Can't hire agents           | One agent handles unlimited concurrent calls, so a clinic or shop gets round-the-clock coverage without hiring or training anyone.    |
| English-first tools         | Multilingual transcription with auto language detection, so the agent answers in whatever language the caller actually used.          |
| Data leaving the country    | Telephony, call records, transcripts and the CRM all live on our own infrastructure — only the live audio leg goes to the voice provider. |
| Manual WhatsApp             | The same agent auto-replies the moment a message lands — text in your writing style, voice notes in your cloned voice.                |
| Five disconnected tools     | Calls, WhatsApp conversations and customer records unified in one dashboard and CRM.                                                  |

**What makes it different:** the agent doesn't use a generic robotic voice. It
replies in the owner's cloned voice, accent and conversational style — and if a
customer sends a WhatsApp voice note, it answers with a voice note too.

**Pipeline:** customer calls or messages → multilingual STT + language
detection → LLM orchestration and persona engine → voice-cloned TTS or
style-matched text → reply on the same channel → everything logged to the
dashboard.

## Live Demo

🔗 _Coming soon — link will be added here once deployed._

Until then, run it locally with the setup steps below.

## Technology Stack

**Frontend (this repo)**

- React 19 + Vite
- React Router
- Tailwind CSS v4
- lucide-react

**Backend (`backend/`)**

- FastAPI (Python 3.12)
- SQLite via stdlib `sqlite3` — swap the DSN for PostgreSQL when scale needs it
- httpx for the Vapi REST calls

**AI / Voice**

- **Vapi** — runs the realtime conversation loop: speech-to-text, LLM
  response, and text-to-speech, with barge-in and turn-taking handled for us
- Multilingual transcription with automatic language detection, so the agent
  replies in whatever language the caller used
- Cloned-voice output via a voice provider (ElevenLabs voice id), so replies
  come back in the owner's own voice

**Telephony — our own**

Vapi handles the conversation, **not** the phone line. Our own dialer / PBX
(Asterisk or FreeSWITCH) owns the call and hands the audio leg to Vapi over
SIP. On each inbound call Vapi asks this backend which assistant to use
(`assistant-request` webhook) and we answer with the persona defined in
[`backend/vapi.py`](backend/vapi.py). That keeps numbers, routing and call
records on our infrastructure.

**Channels**

- WhatsApp Business API (conversations are stored and displayed; auto-send
  not wired yet)

## Architecture

Full developer documentation — every file, folder and diagram — is in
[`backend/ARCHITECTURE.md`](backend/ARCHITECTURE.md).

```
Caller ──► our PBX / dialer ──SIP──► Vapi (STT → LLM → cloned-voice TTS)
                                       │
                                       │ webhooks
                                       ▼
                          FastAPI backend  ──►  SQLite  ──►  React dashboard
```

| Vapi webhook         | What the backend does                                    |
| -------------------- | -------------------------------------------------------- |
| `assistant-request`  | Returns the assistant config — persona, prompt, voice     |
| `status-update`      | Tracks ringing / in-progress / ended                      |
| `end-of-call-report` | Saves duration, transcript, recording, outcome, language  |
| `tool-calls`         | Runs assistant functions (e.g. `book_appointment`)        |

### API endpoints

| Method | Route                   | Purpose                                |
| ------ | ----------------------- | -------------------------------------- |
| GET    | `/health`               | Liveness + whether Vapi is configured  |
| GET    | `/api/stats`            | The four dashboard tiles               |
| GET    | `/api/calls?limit=`     | Call log                               |
| GET    | `/api/calls-by-hour`    | Call-volume chart                      |
| GET    | `/api/languages`        | Language mix                           |
| GET    | `/api/conversations`    | WhatsApp inbox with messages           |
| POST   | `/api/calls/outbound`   | Place a call through Vapi              |
| POST   | `/api/vapi/sip-number`  | One-time: register our SIP endpoint    |
| POST   | `/api/vapi/webhook`     | Vapi events (secured by `X-Vapi-Secret`) |

The frontend falls back to the mock data in [`src/data.js`](src/data.js) if the
backend is unreachable, so the dashboard still renders during a demo.

## Team Members

| Name                | Role      |
| ------------------- | --------- |
| **Abhinav Cheepa**  | Team Lead |
| **Raghav Goyal**    | Member    |

## API Keys Required

Everything goes in `backend/.env` (copy from `backend/.env.example`). `.env` is
gitignored — never commit real keys.

| Key                   | Required?               | Where to get it                                                                                     |
| --------------------- | ----------------------- | --------------------------------------------------------------------------------------------------- |
| `VAPI_PRIVATE_KEY`    | **Yes**                 | vapi.ai → Dashboard → Settings → API Keys → **Private Key**                                          |
| `VAPI_WEBHOOK_SECRET` | **Yes**                 | Invent any random string. Paste the same value into Vapi → Assistant → Server URL → Secret            |
| `PUBLIC_URL`          | Yes, for real calls     | Public HTTPS URL of this backend. Locally use `ngrok http 8000` and paste the https URL               |
| `VOICE_ID`            | For the cloned voice    | elevenlabs.io → clone the owner's voice → copy the Voice ID. Add your ElevenLabs key inside Vapi      |
| `VAPI_ASSISTANT_ID`   | Optional                | Only if you build the assistant in Vapi's dashboard instead of `backend/vapi.py`                      |
| `VAPI_PHONE_NUMBER_ID`| Optional                | Only if you rent a number from Vapi. Not needed — we use our own telephony                            |

No OpenAI / Deepgram / ElevenLabs keys are needed in this repo: those providers
are configured once inside the Vapi dashboard, and Vapi bills them.

Until `VAPI_PRIVATE_KEY` is set, the dashboard and all read endpoints work
normally — only the outbound-call and SIP-registration routes return 503.

## Setup Instructions

**Requirements:** Node.js 18+ and Python 3.11+.

**1. Backend**

```bash
cd backend
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
uvicorn main:app --reload --port 8000
```

On first run it creates `voiceflow.db` and seeds a demo dataset so the
dashboard isn't empty. Set `SEED_DEMO_DATA=false` in `.env` to start clean.

**2. Frontend** (in a second terminal)

```bash
npm install
npm run dev
```

Open **http://localhost:5174**. The API is expected at `http://localhost:8000`
— override with `VITE_API_URL` if you host it elsewhere.

**3. Connect your telephony (when you're ready for real calls)**

Expose the backend, then register a SIP endpoint for your PBX to dial:

```bash
curl -X POST http://localhost:8000/api/vapi/sip-number -H "Content-Type: application/json" -d "{\"sip_uri\":\"sip:voiceflow@sip.vapi.ai\"}"
```

Point your Asterisk/FreeSWITCH dialplan at that URI. Inbound calls will hit the
`assistant-request` webhook and be answered by the assistant in `backend/vapi.py`.

**Build for production**

```bash
npm run build
```

## Routes

| Route               | What it is                                                            |
| ------------------- | --------------------------------------------------------------------- |
| `/`                 | Landing page — problem, solution, features, architecture, impact       |
| `/app`              | Dashboard — total calls, avg duration, success rate, WhatsApp response |
| `/app/calls`        | Call log with search + outcome filter                                  |
| `/app/whatsapp`     | Inbox with text and cloned-voice-note replies                          |
| `/app/voice-studio` | Voice-clone profile, persona tone, preview clips                       |
"# voiceflow-hackmatrix" 
