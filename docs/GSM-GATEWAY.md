# Your SIM → Android → Asterisk → AI agent

Setup for the hardware you actually have: a **spare laptop for Ubuntu** and a
**dedicated Android phone** holding the SIM.

```mermaid
flowchart LR
    C["📞 Caller"]
    S["SIM in Android<br/>GSM_PHONE_1"]
    B["Bluetooth HFP"]
    A["Ubuntu laptop<br/>Asterisk + chan_mobile"]
    P["Our backend<br/>STT → LLM → TTS"]

    C -->|"GSM"| S -->|"audio"| B --> A -->|"ARI"| P
    P -->|"cloned voice"| A --> B --> S --> C

    style A fill:#7c5cff22,stroke:#7c5cff
    style P fill:#2ee6a822,stroke:#2ee6a8
```

Every command is labelled with where it runs.

---

## Read this before you start

**Install Ubuntu 22.04 LTS, not 24.04.** `chan_mobile` has documented trouble
with the newer BlueZ/DBus stack in 24.04 — unstable pairing, devices that don't
appear, HFP that connects but carries no audio. 22.04's older BlueZ is the
safer bet. Neither is guaranteed; this is the risky part of the whole approach.

**One call at a time.** Bluetooth HFP carries a single call. A second caller
hears busy. That is a protocol limit, not a setting.

**Give it two days.** If two-way audio isn't stable by then, stop and switch —
either a voice-capable USB GSM dongle, or a SIP trunk from Exotel/Plivo. Both
are covered in [PHONE-SETUP.md](PHONE-SETUP.md). Sinking a week into Bluetooth
HFP is the classic way to lose a deadline on this.

---

## Phase 0 — the phone and SIM

Do all of this **before** touching Linux. Each one is a thing that silently
breaks the setup later.

- [ ] Dedicated phone, not your personal one. It lives on a charger, permanently.
- [ ] SIM inserted; **disable the SIM PIN** (Settings → Security → SIM lock).
      A PIN prompt after a reboot takes the gateway offline until someone types it.
- [ ] Make and receive a normal call. Confirm mic, speaker and signal.
- [ ] Turn **off battery optimisation** for Bluetooth and phone/system services.
      Android will otherwise kill the Bluetooth link after a few idle hours.
- [ ] Rename the phone's Bluetooth to `GSM_PHONE_1` so it's obvious in scans.
- [ ] Disable "Do Not Disturb" and any call-blocking or spam-filter app.
- [ ] Note whether calls arrive over **VoLTE** — some phones route VoLTE audio
      in a way HFP doesn't pick up. If audio fails later, test with VoLTE off.

---

## Phase 1 — Ubuntu

Install **Ubuntu 22.04 LTS** on the spare laptop, copy this repo across, then:

```bash
cd voiceflow-ai/asterisk && chmod +x setup-ubuntu.sh && ./setup-ubuntu.sh
```

*[Ubuntu]* — this does Phase 1 and most of Phase 2 for you: installs Asterisk
and `asterisk-mobile`, copies the config in (backing up anything already
there), generates real passwords for ARI and both SIP extensions, writes the
ARI password into `backend/.env`, and stops the laptop suspending on lid-close.
Safe to run twice. It then prints the pairing steps, which stay manual.

If you would rather do it by hand, the rest of this section is what the script
runs.

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y asterisk asterisk-mobile bluez bluez-tools
```

*[Ubuntu]* — `asterisk-mobile` is the package that carries `chan_mobile`. It is
not installed by the base `asterisk` package.

```bash
sudo asterisk -rx "module show like chan_mobile"
```

*[Ubuntu]* — expect `chan_mobile.so ... Running`. If it says "0 modules
loaded", the package didn't install or the module is blocked in
`/etc/asterisk/modules.conf`.

Keep the laptop's lid-close behaviour set to "do nothing", or it suspends and
the gateway dies:

```bash
sudo sed -i 's/^#HandleLidSwitch=.*/HandleLidSwitch=ignore/' /etc/systemd/logind.conf && sudo systemctl restart systemd-logind
```

*[Ubuntu]*

---

## Phase 2 — Pair the phone

```bash
hciconfig
```

*[Ubuntu]* — note the adapter MAC (`BD Address`). This goes in `mobile.conf` as
the **adapter** address. It is the laptop's, not the phone's — mixing these up
is the most common mistake here.

```bash
bluetoothctl
```

*[Ubuntu]* — then, inside the `bluetoothctl` prompt:

```
power on
agent on
default-agent
scan on
```

*[bluetoothctl]* — wait for `GSM_PHONE_1` to appear, then:

```
pair AA:BB:CC:DD:EE:FF
trust AA:BB:CC:DD:EE:FF
connect AA:BB:CC:DD:EE:FF
info AA:BB:CC:DD:EE:FF
quit
```

*[bluetoothctl]* — accept the pairing prompt on the phone. `trust` matters:
without it the phone won't reconnect on its own after a reboot.

In `info`, look for **`Handsfree Audio Gateway`** in the UUID list. If it isn't
there, this phone will not work as a gateway — try another handset before
spending more time.

---

## Phase 3 — Asterisk sees the phone

```bash
sudo asterisk -rx "mobile search"
```

*[Ubuntu]* — this is the step that gives you the two values `mobile.conf` needs:

```
Address            Name           Usable   Type       Port
AA:BB:CC:DD:EE:FF  GSM_PHONE_1    Yes      Phone      4
```

**`Usable: Yes` and a `Port` number.** Copy that port — it is *not* always 1,
and using a tutorial's value is the usual reason the device never connects.

Now install the config from this repo:

```bash
sudo cp /path/to/voiceflow-ai/asterisk/mobile.conf /etc/asterisk/mobile.conf
sudo nano /etc/asterisk/mobile.conf
```

*[Ubuntu]* — fill in the adapter MAC, the phone MAC and the port from above.

```bash
sudo cp /path/to/voiceflow-ai/asterisk/extensions.conf /etc/asterisk/extensions.conf
sudo cp /path/to/voiceflow-ai/asterisk/{ari.conf,http.conf,rtp.conf,logger.conf} /etc/asterisk/
sudo asterisk -rx "module reload chan_mobile"
sudo asterisk -rx "mobile show devices"
```

*[Ubuntu]* — expect `State: Free`. `Init` or `Disconnected` means the RFCOMM
channel is wrong or the phone dropped the link.

### Test 3a — a real call, no AI yet

Call the SIM from another phone. On the Asterisk console:

```bash
sudo asterisk -rvvv
```

*[Ubuntu]* — expect `VoiceFlow: GSM call from +91…` and the call answering.

**If you get this far, the hard part is done.** Everything after it is software
we already have working.

---

## Phase 4 — Hand the call to the AI

The backend runs on the same Ubuntu laptop, because `ASTERISK_ARI_URL` points at
Asterisk's loopback.

```bash
cd /path/to/voiceflow-ai/backend
python3 -m venv venv && ./venv/bin/pip install -r requirements.txt
```

*[Ubuntu]*

Set the ARI password in `/etc/asterisk/ari.conf` and the same value in
`backend/.env` as `ASTERISK_ARI_PASSWORD`, then:

```bash
./venv/bin/uvicorn main:app --port 8000
```

*[Ubuntu]* — expect `ARI connected ... as app voiceflow-ai` in the log, and
`"asterisk": true` from `/health`.

Call the SIM again. The agent answers, in the voice selected in Voice Studio.

---

## What still needs building

Answering, logging and hanging up work today via
[asterisk_ari.py](../backend/asterisk_ari.py). What does **not** exist yet is
the realtime audio loop — streaming the caller's speech out of Asterisk and
synthesised audio back in over ARI ExternalMedia.

Until that lands, a GSM call is answered and logged but the conversation itself
isn't running on this path. The web call at `/app/web-call` runs the full
pipeline today and is the honest thing to demo.

Scope and estimates for that work: [ASTERISK-PLAN.md](ASTERISK-PLAN.md),
sections 6 and 9.

---

## Troubleshooting

| Symptom | Cause | Fix |
| ------- | ----- | --- |
| `mobile search` finds nothing | Phone not discoverable, or BlueZ not running | Re-open the phone's Bluetooth screen; `sudo systemctl status bluetooth` |
| Found but `Usable: No` | Phone doesn't offer Handsfree Audio Gateway | Try a different handset — this one can't do it |
| Device stuck in `Init` | Wrong RFCOMM port | Use the port from `mobile search`, not `1` |
| Connects, drops after hours | Android battery optimisation | Disable it for Bluetooth **and** phone/system services |
| Call answers, no audio | HFP audio not routing | Test with VoLTE off; check `mobile show devices` mid-call |
| Works, dies after reboot | Phone not trusted | `trust <MAC>` in bluetoothctl |
| Second caller gets busy | HFP carries one call | Expected. Needs a SIP trunk, not Bluetooth |

Useful while debugging:

```bash
sudo asterisk -rx "mobile show devices"
sudo journalctl -u bluetooth -f
sudo tail -f /var/log/asterisk/full
```

*[Ubuntu]*

---

## Security

The laptop stays on your LAN. Do not port-forward SIP or ARI to the internet.
`ari.conf` already binds to localhost.

The one thing worth saying plainly: this gateway can place outbound GSM calls on
your SIM. Anyone who reaches ARI can run up your phone bill. Keep the ARI
password strong, keep the box off the public internet, and leave the
`voiceflow-outbound` dialplan pattern as narrow as it is.
