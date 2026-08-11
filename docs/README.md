# Documentation

Start with the [project README](../README.md) — setup, keys, and what works
today. These are the deeper documents.

| Document | Read it when… |
| -------- | ------------- |
| **[ARCHITECTURE.md](ARCHITECTURE.md)** | You're changing the code. Every module, the data model, the request flows, and the decisions that look arbitrary but aren't. |
| **[WHATSAPP_SETUP.md](WHATSAPP_SETUP.md)** | You're connecting WhatsApp. Both routes compared, and the full Meta Cloud API setup. |
| **[PHONE-SETUP.md](PHONE-SETUP.md)** | You want a real phone number. Call forwarding, virtual numbers, what's actually free in India and what isn't. |
| **[GSM-GATEWAY.md](GSM-GATEWAY.md)** | You're building the free route: your own SIM in an old Android, bridged to Asterisk over Bluetooth. Step by step, with checkpoints. |
| **[ASTERISK.md](ASTERISK.md)** | You're installing Asterisk and want a softphone calling into it. The software layer only. |
| **[ASTERISK-PLAN.md](ASTERISK-PLAN.md)** | You're deciding whether to build the realtime audio loop. Phase-by-phase effort and risk. |

Component READMEs live next to their code:
[wa-bridge](../wa-bridge/README.md).

---

## The short version

**Works now:** browser calls, WhatsApp auto-reply, agents, voices, CRM,
analytics.

**Doesn't yet:** live audio on phone calls. Asterisk answers and logs the call;
streaming the audio through the AI is 7–10 days of work
([why](ASTERISK-PLAN.md#6-phases-with-honest-cost)).

**Costs nothing:** Asterisk, the AI stack on free tiers, and incoming calls in
India. **Costs money:** any phone number that customers can dial.

**Needs Linux:** Asterisk. Not Windows, not macOS — `chan_mobile` depends on
BlueZ, and WSL2 has no Bluetooth passthrough.
