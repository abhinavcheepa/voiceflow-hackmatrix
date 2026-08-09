# Asterisk setup — Phases 1 to 5

Goal of this document: a softphone on your laptop dials extension **1001**,
Asterisk answers, hands the channel to our Python code, and you hear a greeting.
That proves the whole telephony path before any AI audio work starts.

**Out of scope here:** the realtime audio loop (ExternalMedia RTP, streaming
STT, barge-in). That's Phases 6-11 — see [ASTERISK-PLAN.md](ASTERISK-PLAN.md).
Until then the conversation still runs on Vapi.

Every command is labelled with the terminal it belongs in. Do not mix them.

---

## Why WSL2 and not Windows or Docker

Asterisk has no supported Windows build. Two options remain:

**WSL2 + Ubuntu — recommended.** SIP uses UDP 5060 and RTP uses a range of ~200
UDP ports. WSL2 forwards localhost traffic to Windows, so a softphone running on
Windows can reach Asterisk inside WSL without extra config.

**Docker — works, but harder here.** Asterisk needs `--network host` to expose
that whole UDP port range cleanly, and Docker Desktop for Windows does not
support `--network host` the way Linux does. You end up mapping 200 ports by
hand or debugging one-way audio. Use it only if you already know Docker
networking well.

This machine has WSL2 with only the `docker-desktop` distro, so Ubuntu needs
installing first.

---

## Phase 1 — Install Asterisk

```powershell
wsl --install -d Ubuntu-24.04
```

*[PowerShell]* — ~1.5 GB. It will ask for a username and password; those are
your Linux account, unrelated to Windows. Reboot if it asks.

```bash
sudo apt update && sudo apt upgrade -y
```

*[WSL / Ubuntu]*

```bash
sudo apt install -y asterisk asterisk-modules asterisk-config
```

*[WSL / Ubuntu]* — Ubuntu 24.04 ships Asterisk 20 (LTS), which is what these
configs target. Building from source is unnecessary for this.

```bash
asterisk -V
```

*[WSL / Ubuntu]* — expect `Asterisk 20.x.x`.

### Verify it runs

```bash
sudo systemctl start asterisk && sudo systemctl status asterisk --no-pager
```

*[WSL / Ubuntu]* — expect `active (running)`.

> **WSL note:** WSL2 doesn't run systemd by default on older setups. If
> `systemctl` errors, either enable it (`sudo nano /etc/wsl.conf`, add
> `[boot]` and `systemd=true`, then `wsl --shutdown` from *[PowerShell]*), or
> run Asterisk in the foreground with `sudo asterisk -cvvv`.

---

## Phase 2 — Install our config

The config files live in the repo at `voiceflow-ai/asterisk/`. Copy them in:

```bash
cd /mnt/c/Users/abhin/Desktop/voiceflow-ai/asterisk
sudo cp pjsip.conf extensions.conf ari.conf http.conf rtp.conf logger.conf /etc/asterisk/
```

*[WSL / Ubuntu]*

### Set the three passwords

```bash
sudo nano /etc/asterisk/pjsip.conf   # CHANGE_ME_1001, CHANGE_ME_1002
sudo nano /etc/asterisk/ari.conf     # CHANGE_ME_ARI
```

*[WSL / Ubuntu]* — generate them with:

```bash
openssl rand -base64 18
```

*[WSL / Ubuntu]* — do this now, not later. A default SIP password on a box that
later gets a public IP is how toll fraud starts.

Put the ARI password into `backend/.env` as `ASTERISK_ARI_PASSWORD`.

```bash
sudo systemctl restart asterisk
```

*[WSL / Ubuntu]*

### Confirm the extensions loaded

```
pjsip show endpoints
```

*[Asterisk CLI]* — get there with `sudo asterisk -rvvv` from *[WSL / Ubuntu]*.
Expect `1001` and `1002`, both `Unavailable` (nothing has registered yet).

```
dialplan show voiceflow-internal
```

*[Asterisk CLI]* — expect extensions `1001` and `600`.

---

## Phase 3 — Register a softphone

Install **MicroSIP** (Windows, free) or **Zoiper**.

Find the WSL IP:

```bash
hostname -I
```

*[WSL / Ubuntu]* — usually `172.x.x.x`. `localhost` often works too, since WSL2
forwards it.

Configure the softphone:

| Field | Value |
| ----- | ----- |
| SIP server / domain | the WSL IP, or `localhost` |
| Username | `1002` |
| Password | whatever you set for `CHANGE_ME_1002` |
| Transport | UDP |
| Port | 5060 |

```
pjsip show endpoint 1002
```

*[Asterisk CLI]* — expect `Not in use` instead of `Unavailable`. That means it
registered.

### Test 3a — echo test

Dial **600** from the softphone. You should hear your own voice back with a
short delay.

**If this fails, stop here.** Echo failing means RTP is broken, and no amount of
AI work will produce audio. See Troubleshooting.

---

## Phase 4 — Answer a call

Dial **1001**. Asterisk answers, then tries to hand the channel to Stasis. With
the gateway not running yet the call will drop immediately — that's correct at
this stage.

```
core show channels
```

*[Asterisk CLI]* — run it during the call to see the live channel.

---

## Phase 5 — Connect ARI

Check ARI is up:

```bash
curl -u voiceflow:YOUR_ARI_PASSWORD http://127.0.0.1:8088/ari/asterisk/info
```

*[WSL / Ubuntu]* — expect JSON with the Asterisk version. A 401 means the
password doesn't match `ari.conf`; connection refused means `http.conf` didn't
load or Asterisk wasn't restarted.

Now start the backend. **It must run inside WSL**, because `ASTERISK_ARI_URL`
points at `127.0.0.1:8088` which is Asterisk's loopback, not Windows'.

```bash
cd /mnt/c/Users/abhin/Desktop/voiceflow-ai/backend
python3 -m venv venv-linux && ./venv-linux/bin/pip install -r requirements.txt
./venv-linux/bin/uvicorn main:app --port 8000
```

*[WSL / Ubuntu]* — a separate venv from the Windows one; they can't be shared.

Expect in the log:

```
INFO  voiceflow.ari  ARI connected to http://127.0.0.1:8088/ari as app voiceflow-ai
```

```bash
curl -s http://127.0.0.1:8000/health
```

*[WSL / Ubuntu]* — `"asterisk": true`.

### Test 5a — the full Phase 1-5 path

Dial **1001** from the softphone. Expected:

1. Call connects
2. You hear the greeting (`hello-world` by default)
3. Backend log shows `call started: PJSIP/1002-... from 1002`
4. Call hangs up after ~6 seconds
5. The call appears in the dashboard at `/app/calls`

That's the telephony adapter working end to end.

### Optional — greet in the cloned voice

Generate a clip with the Cartesia voice, convert to the format Asterisk wants,
and drop it in:

```bash
sudo apt install -y ffmpeg
ffmpeg -i greeting.mp3 -ar 8000 -ac 1 -c:a pcm_s16le /usr/share/asterisk/sounds/en/voiceflow-greeting.wav
```

*[WSL / Ubuntu]* — then set `ASTERISK_GREETING=sound:voiceflow-greeting` in
`.env` and restart the backend. Asterisk wants 8 kHz mono for telephony.

---

## Troubleshooting

| Symptom | Cause | Fix |
| ------- | ----- | --- |
| Softphone won't register | Wrong IP, or Asterisk not running | `hostname -I` in *[WSL]*; `sudo systemctl status asterisk` |
| Registers, but 401 on call | Password mismatch | Re-check `pjsip.conf`, then `pjsip reload` in *[Asterisk CLI]* |
| Call connects, **no audio either way** | RTP ports blocked | Check `rtp.conf` range; allow UDP 10000-10200 in Windows Firewall |
| **One-way audio** | NAT | Set `external_media_address` and `local_net` in `pjsip.conf` |
| Echo test (600) works, 1001 doesn't | ARI not connected | `curl` the ARI info endpoint; check backend log |
| `ARI disconnected — retrying` | Wrong password or URL | Compare `.env` against `ari.conf` |
| Backend says `asterisk: false` | `ASTERISK_ARI_URL` or password blank | Fill both in `.env` |

Useful while debugging:

```
pjsip set logger on
core set verbose 5
core show channels
```

*[Asterisk CLI]* — `pjsip set logger on` prints full SIP traffic **including
credentials**, so turn it off again when you're done.

```bash
sudo tail -f /var/log/asterisk/full
```

*[WSL / Ubuntu]*

---

## Before this box ever gets a public IP

Phases 1-5 are localhost-only and safe. The moment a SIP trunk or public IP is
involved, all of these are mandatory:

- No `allowguest`, no anonymous endpoints — already the case in our `pjsip.conf`
- Strong per-endpoint passwords, none of the `CHANGE_ME` defaults
- `fail2ban` with the `asterisk` jail enabled — SIP scanners find you in hours
- Firewall: allow 5060 only from your trunk provider's IPs
- ARI stays on `127.0.0.1`, or goes behind TLS
- A hard cap on concurrent calls and call duration (`MAX_CALL_MS` in the dialplan)
- No outbound dialplan pattern broader than the numbers you actually need

**Toll fraud is the risk that costs real money.** An exposed Asterisk with a weak
password gets used to route international calls, and bills of ₹1-5 lakh over a
weekend are routine. The telco holds the account owner liable, not the attacker.
