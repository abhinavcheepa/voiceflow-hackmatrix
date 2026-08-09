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
| English-first tools         | Built for India — Sarvam AI for native Hindi TTS/STT plus Groq for fast LLM responses, giving natural sub-second conversation.        |
| Data leaving the country    | Entirely self-hosted on the business's own backend and database. No per-minute USD billing, no customer data handed to cloud vendors. |
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

**Backend**

- FastAPI (Python)
- Celery for background jobs
- PostgreSQL

**AI / Voice**

- Faster-Whisper — multilingual speech-to-text + language detection
- Groq / Llama — LLM orchestration, intent understanding, persona engine
- Sarvam AI — native Hindi/Indic text-to-speech
- Voice cloning model — replies in the owner's own voice

**Channels & Infrastructure**

- WhatsApp Business API
- Telephony: Exotel / Asterisk (VPS-based rollout)
- Fully self-hosted — no per-minute USD billing, data stays in India

> Note: this repository is the frontend — landing page plus dashboard. Every
> screen renders from mock data in [`src/data.js`](src/data.js); the shapes
> there are what the backend API is expected to return.

## Team Members

| Name                | Role      |
| ------------------- | --------- |
| **Abhinav Cheepa**  | Team Lead |
| **Raghav Goyal**    | Member    |

## Setup Instructions

**Requirements:** Node.js 18 or newer.

```bash
git clone <repo-url>
cd voiceflow-ai
npm install
npm run dev
```

Open **http://localhost:5174** in your browser.

**Other commands**

```bash
npm run build
```

```bash
npm run preview
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
