# WhatsApp setup

Two ways to connect WhatsApp. They are not equivalent — pick deliberately.

| | **WhatsApp Web** (`WHATSAPP_PROVIDER=web`) | **Meta Cloud API** (`WHATSAPP_PROVIDER=meta`) |
| --- | --- | --- |
| Setup time | ~2 minutes, scan a QR | 30 min, plus KYC approval |
| Needs a business | No | **Yes** — registration certificate + PAN/GST |
| Within WhatsApp's terms | **No** | Yes |
| Ban risk | Real. Numbers get banned. | None |
| Good for | Demos, validating the idea | Anything a real business depends on |

**Use WhatsApp Web to prove it works. Move to Meta Cloud API before it
matters.** Setup for the Web route is in
[wa-bridge/README.md](../wa-bridge/README.md); this document covers Meta.

---

## Meta Cloud API — six steps

### 1. Create the app

[developers.facebook.com](https://developers.facebook.com) → **My Apps** →
**Create App** → type **Business** → add the **WhatsApp** product.

### 2. You get a free test number

Immediately, no KYC: one test sender number and up to **5 recipient numbers**
you verify by OTP. Enough to demo the whole thing — you do not need a real
number to start.

The catch: the test number can only message those 5 verified recipients.

### 3. Get a permanent token

The token on the **API Setup** screen expires in **24 hours**. Do not build on
it — the agent will simply stop replying tomorrow with a 401.

For a permanent one: **Business Settings** → **System Users** → add a system
user → **Generate token** → select your app → grant:

- `whatsapp_business_messaging`
- `whatsapp_business_management`

That token does not expire.

### 4. Make the backend reachable

Meta posts webhooks to a public HTTPS URL. In development:

```bash
ngrok http 8000
```

Copy the `https://` URL. It changes every time ngrok restarts, and the webhook
must be updated to match.

### 5. Configure the webhook

**WhatsApp** → **Configuration** → **Webhook** → Edit:

| Field | Value |
| ----- | ----- |
| Callback URL | `<your-public-url>/api/whatsapp/webhook` |
| Verify token | the `WHATSAPP_VERIFY_TOKEN` already in `backend/.env` |

Save — Meta calls your URL immediately and expects the challenge echoed back.
The backend handles that. A failure here means the URL is wrong or the backend
isn't running.

Then **subscribe to the `messages` field.** This step is separate from saving
the URL and it is the one people miss — without it the webhook verifies fine
and no message ever arrives.

### 6. Fill in `.env`

```
WHATSAPP_PROVIDER=meta
WHATSAPP_TOKEN=<the permanent token>
WHATSAPP_PHONE_NUMBER_ID=<from API Setup>
WHATSAPP_VERIFY_TOKEN=<same value you gave Meta>
```

Restart the backend. `GET /health` should show `"whatsapp": true`.

---

## The 24-hour rule

Meta only allows free-form messages **within 24 hours of the customer's last
message**. Outside that window, only pre-approved templates.

This does not affect the agent: it always replies to something a customer just
sent, so it is always inside the window.

It **does** affect campaigns. Messaging old leads out of the blue needs
templates submitted to Meta for approval, which takes a day or two.

---

## Going to production

The test number is not enough for real customers. You need a real number, which
means business verification:

- A Certificate of Incorporation (MCA) **or** an Udyam certificate (MSME)
- Business PAN or GST certificate

**Udyam registration is free, online, and issued immediately** — it needs only
Aadhaar and PAN. For a small team without a registered company, that is usually
the fastest route to a document Meta will accept.

The number you register must **not** already be active on WhatsApp. Migrating a
number that is in use means deleting its existing WhatsApp account first.

---

## Troubleshooting

| Symptom | Cause | Fix |
| ------- | ----- | --- |
| Webhook verification fails | Backend down, or wrong URL | Check the backend is running and the path ends `/api/whatsapp/webhook` |
| Verified, but no messages arrive | `messages` field not subscribed | Configuration → Webhook fields → subscribe to `messages` |
| Worked yesterday, 401 today | Temporary 24-hour token | Generate a System User token (step 3) |
| "Recipient not in allowed list" | Test number restriction | Add and verify the recipient, or move to a real number |
| Replies stop after a while | Outside the 24-hour window | Expected. Needs a template |
| `"whatsapp": false` in `/health` | Token or phone number id missing | Check `.env`, restart the backend |

---

## Switching between the two

The provider is one line in `.env`. Nothing else changes — the agent, prompts,
allowlist, toggles and dashboard are identical either way, because
`whatsapp.py` dispatches to whichever provider is configured.

```
WHATSAPP_PROVIDER=web    # local bridge, QR code
WHATSAPP_PROVIDER=meta   # Cloud API
```
