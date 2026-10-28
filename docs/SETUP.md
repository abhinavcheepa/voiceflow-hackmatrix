# Running VoiceFlow AI on another machine

Everything runs locally. There is no hosted service to sign into — a new machine
needs the code, two runtimes, and your API keys.

---

## 1. What must already be on the machine

| Need | Version | Check with |
|---|---|---|
| Python | 3.10 or newer | `python --version` |
| Node.js | 18 or newer | `node --version` |
| Git | any | `git --version` |
| Google Chrome | any recent | only for WhatsApp via `wa-bridge` |

On Windows, install Python from python.org and tick **"Add python.exe to PATH"**.
The Microsoft Store build puts files somewhere the venv step cannot use.

---

## 2. Get the code

```bash
git clone https://github.com/abhinavcheepa/voiceflow-hackmatrix.git voiceflow-ai
```

Copying the folder on a USB stick works too — but delete `backend/venv`,
`node_modules`, and `wa-bridge/node_modules` first. They contain absolute paths
from the old machine and will not run on the new one. Steps 3 and 4 rebuild them.

---

## 3. Backend

**Windows (PowerShell)**

```powershell
cd voiceflow-ai
python -m venv backend\venv
backend\venv\Scripts\python.exe -m pip install -r backend\requirements.txt
```

**macOS / Linux**

```bash
cd voiceflow-ai
python3 -m venv backend/venv
backend/venv/bin/python -m pip install -r backend/requirements.txt
```

`start.ps1` looks for the venv at exactly `backend/venv` — a different name or a
global install and it will refuse to start.

---

## 4. Frontend and the WhatsApp bridge

```bash
npm install
npm install --prefix wa-bridge
```

Skip the second line if you are not using WhatsApp.

---

## 5. Keys

```bash
cp backend/.env.example backend/.env
```

(PowerShell: `Copy-Item backend\.env.example backend\.env`)

Open `backend/.env` and fill in what you actually need. The server starts with
all of it blank; each integration switches itself on when its keys appear.

| To get | Set | Where it comes from |
|---|---|---|
| Replies + transcription | `GROQ_API_KEY` | console.groq.com → API Keys (free) |
| Voice | nothing | `VOICE_PROVIDER=edge` needs no key at all |
| Voice cloning | `CARTESIA_API_KEY` + `VOICE_PROVIDER=cartesia` | play.cartesia.ai (paid) |
| WhatsApp, quick | `WHATSAPP_PROVIDER=web`, `WA_BRIDGE_TOKEN` | invent any random string |
| WhatsApp, official | `WHATSAPP_PROVIDER=meta`, `WHATSAPP_TOKEN`, `WHATSAPP_PHONE_NUMBER_ID`, `WHATSAPP_VERIFY_TOKEN` | developers.facebook.com (needs business KYC) |
| Phone calls | `VAPI_PRIVATE_KEY`, `VAPI_WEBHOOK_SECRET` | vapi.ai → Settings → API Keys |

**Only `GROQ_API_KEY` is needed for a working demo.** With that one key, web
calling and the dashboard run fully.

Never commit `backend/.env`. It is gitignored — keep it that way.

---

## 6. Start it

**Windows**

```powershell
.\start.ps1
```

**macOS / Linux** — two terminals:

```bash
cd backend && venv/bin/python -m uvicorn main:app --port 8000
```

```bash
npm run dev
```

Then open **http://localhost:5174**.

`start.ps1 -Stop` shuts everything down. `start.ps1 -NoBridge` skips WhatsApp.

---

## 7. Check it actually works

```bash
curl http://127.0.0.1:8000/health
```

`/health` reports which integrations came up. Then in the dashboard:

1. **Web call** → press the green button → allow the microphone.
2. You should hear the greeting within a few seconds.
3. Speak. Watch the **mic level bar** — your voice has to cross the centre line
   to register. If it never does, the mic is too quiet: raise its input volume
   in the OS sound settings, or move closer.
4. A reply takes about 5-6 seconds: transcribe, think, speak.

---

## 8. WhatsApp on the new machine

The WhatsApp session does **not** transfer. `wa-bridge/.session/` is tied to the
machine that created it and is gitignored — copying it will not work and leaking
it would hand over control of the number.

```bash
cd wa-bridge && npm start
```

Then open **Settings → WhatsApp** in the dashboard and scan the QR with the
phone whose number the agent should answer from.

> Driving WhatsApp Web this way is against WhatsApp's terms and numbers do get
> banned. Use a dedicated SIM, never a personal number. Keep the allowlist on
> until you have tested it.

---

## 9. Moving your existing data

Optional. Everything the dashboard shows lives in one file:

```
backend/voiceflow.db
```

Copy it across to keep your contacts, conversations, agents and call history.
Stop the backend on both machines first — SQLite does not like being copied
mid-write. Leave it behind and the new machine starts with empty tables and
creates its own.

---

## Common failures

| What you see | Cause | Fix |
|---|---|---|
| "Backend venv missing" | venv not at `backend/venv` | redo step 3 |
| Agent never speaks, no audio | `edge-tts` missing or too old — Microsoft 403s stale clients | `backend/venv/Scripts/python -m pip install -U edge-tts` |
| Call connects, ignores your speech | mic too quiet | watch the level bar; raise mic volume |
| `402 quota_exceeded` | Cartesia credits gone | set `VOICE_PROVIDER=edge` |
| Replies fail, calls stay silent | `GROQ_API_KEY` missing or revoked | issue a new key |
| Dashboard loads, every panel empty | backend not running | check `curl 127.0.0.1:8000/health` |
| WhatsApp stuck "starting" | orphan Chrome holding `.session` | close Chrome, `npm start` again |

---

## Security note

The dashboard API has **no authentication**. Anyone who can reach port 8000 can
read every conversation and send WhatsApp messages as you. That is fine on your
own laptop. Do not put this on a public IP or expose it through ngrok beyond the
specific webhook paths.
