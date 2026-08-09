import { Link } from "react-router-dom";
import {
  ArrowRight,
  PhoneIncoming,
  MessageSquare,
  Mic,
  Languages,
  BrainCircuit,
  Database,
  Server,
  ShieldCheck,
  Phone,
  Mail,
} from "lucide-react";
import { Logo, SectionTitle, Waveform } from "./ui.jsx";
import { problems, solutions, features, impact } from "./data.js";

function Nav() {
  return (
    <header className="sticky top-0 z-50 border-b border-line/60 bg-ink/80 backdrop-blur-xl">
      <div className="mx-auto flex h-16 max-w-6xl items-center justify-between px-5">
        <Logo />
        <nav className="hidden items-center gap-8 text-sm text-dim md:flex">
          <a href="#problem" className="transition hover:text-white">
            Problem
          </a>
          <a href="#solution" className="transition hover:text-white">
            Solution
          </a>
          <a href="#features" className="transition hover:text-white">
            Features
          </a>
          <a href="#architecture" className="transition hover:text-white">
            Architecture
          </a>
        </nav>
        <Link
          to="/app"
          className="flex items-center gap-1.5 rounded-full bg-violet px-4 py-2 text-sm font-medium text-white transition hover:bg-violet-soft"
        >
          Open dashboard <ArrowRight className="size-4" />
        </Link>
      </div>
    </header>
  );
}

function Hero() {
  return (
    <section className="glow relative overflow-hidden">
      <div className="mx-auto max-w-6xl px-5 pt-20 pb-24 text-center">
        <span className="inline-flex items-center gap-2 rounded-full border border-line bg-panel px-3.5 py-1.5 text-xs text-dim">
          <span className="size-1.5 rounded-full bg-mint" />
          Self-hosted · Built for Indian SMBs
        </span>

        <h1 className="mx-auto mt-7 max-w-4xl text-4xl font-semibold leading-[1.1] tracking-tight sm:text-6xl">
          Your voice. Your accent.
          <br />
          <span className="bg-gradient-to-r from-violet-soft via-white to-mint bg-clip-text text-transparent">
            Answering every customer.
          </span>
        </h1>

        <p className="mx-auto mt-6 max-w-2xl text-base leading-relaxed text-dim sm:text-lg">
          VoiceFlow AI picks up every call and WhatsApp message for your business — 24/7, in
          whatever language the customer speaks, replying in a clone of your own voice and writing
          style.
        </p>

        <div className="mt-9 flex flex-wrap items-center justify-center gap-3">
          <Link
            to="/app"
            className="flex items-center gap-2 rounded-full bg-violet px-6 py-3 text-sm font-medium text-white transition hover:bg-violet-soft"
          >
            See the live dashboard <ArrowRight className="size-4" />
          </Link>
          <a
            href="#architecture"
            className="rounded-full border border-line bg-panel px-6 py-3 text-sm font-medium text-dim transition hover:text-white"
          >
            How it works
          </a>
        </div>

        <div className="mt-16 grid gap-4 text-left sm:grid-cols-3">
          <LiveCallCard />
          <WhatsAppCard />
          <CloneCard />
        </div>
      </div>
    </section>
  );
}

function LiveCallCard() {
  return (
    <div className="rounded-2xl border border-line bg-panel p-5">
      <div className="flex items-center gap-2 text-xs text-mint">
        <span className="size-1.5 animate-pulse rounded-full bg-mint" />
        LIVE CALL · 00:42
      </div>
      <p className="mt-4 text-sm text-dim">Customer · Hindi</p>
      <p className="mt-1 text-[15px]">“Kya aaj shaam ka appointment mil jaayega?”</p>
      <p className="mt-4 text-sm text-violet-soft">Your cloned voice</p>
      <p className="mt-1 text-[15px]">“Bilkul! 7:30 baje ka slot khaali hai.”</p>
      <Waveform bars={24} className="mt-5 h-10" />
    </div>
  );
}

function WhatsAppCard() {
  return (
    <div className="rounded-2xl border border-line bg-panel p-5">
      <div className="flex items-center gap-2 text-xs text-dim">
        <MessageSquare className="size-3.5" /> WHATSAPP
      </div>
      <div className="mt-4 space-y-2.5">
        <p className="w-fit rounded-2xl rounded-tl-sm bg-panel-2 px-3.5 py-2 text-sm">
          उद्या सकाळी येऊ शकतो का?
        </p>
        <div className="ml-auto flex w-fit items-center gap-2 rounded-2xl rounded-tr-sm bg-violet/20 px-3.5 py-2 ring-1 ring-violet/30">
          <Mic className="size-3.5 text-violet-soft" />
          <Waveform bars={9} className="h-4" />
          <span className="text-xs text-dim">0:09</span>
        </div>
      </div>
      <p className="mt-4 text-xs text-dim">
        Voice note in, voice note out — answered in your cloned voice, in Marathi.
      </p>
    </div>
  );
}

function CloneCard() {
  return (
    <div className="rounded-2xl border border-line bg-panel p-5">
      <div className="flex items-center gap-2 text-xs text-dim">
        <Mic className="size-3.5" /> ONE-TIME SETUP
      </div>
      <p className="mt-4 text-[15px]">
        Record a 60-second voice sample once. That trains your voice-clone profile — every call and
        voice note afterwards sounds like you.
      </p>
      <div className="mt-5 h-1.5 overflow-hidden rounded-full bg-panel-2">
        <div className="h-full w-full rounded-full bg-gradient-to-r from-violet to-mint" />
      </div>
      <p className="mt-2 text-xs text-mint">Voice profile trained · 100%</p>
    </div>
  );
}

function Problem() {
  return (
    <section id="problem" className="border-t border-line/60 py-24">
      <div className="mx-auto max-w-6xl px-5">
        <SectionTitle
          eyebrow="The problem"
          title="Indian SMBs lose leads every single day"
          sub="Clinics, real-estate offices, salons, D2C brands and local services don't lose customers because of a bad product — they lose them because of how calls and WhatsApp messages get handled."
        />
        <div className="mt-14 grid gap-4 md:grid-cols-2 lg:grid-cols-3">
          {problems.map((p) => (
            <div key={p.title} className="rounded-2xl border border-line bg-panel p-6">
              <h3 className="text-[15px] font-semibold text-saffron">{p.title}</h3>
              <p className="mt-3 text-sm leading-relaxed text-dim">{p.body}</p>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}

function Solution() {
  return (
    <section id="solution" className="glow border-t border-line/60 py-24">
      <div className="mx-auto max-w-6xl px-5">
        <SectionTitle
          eyebrow="The solution"
          title="One AI agent that actually represents the owner"
          sub="VoiceFlow AI closes each of those gaps directly — on the phone, on WhatsApp, and in a single place to see all of it."
        />
        <div className="mt-14 grid gap-4 md:grid-cols-2 lg:grid-cols-3">
          {solutions.map((s, i) => (
            <div key={s.title} className="rounded-2xl border border-line bg-panel p-6">
              <span className="grid size-9 place-items-center rounded-xl bg-mint/10 text-sm font-semibold text-mint ring-1 ring-mint/25">
                {i + 1}
              </span>
              <h3 className="mt-4 text-[15px] font-semibold">{s.title}</h3>
              <p className="mt-2.5 text-sm leading-relaxed text-dim">{s.body}</p>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}

function Features() {
  return (
    <section id="features" className="border-t border-line/60 py-24">
      <div className="mx-auto max-w-6xl px-5">
        <SectionTitle eyebrow="Key features" title="What makes it feel like you" />
        <div className="mt-14 grid gap-px overflow-hidden rounded-2xl border border-line bg-line sm:grid-cols-2 lg:grid-cols-3">
          {features.map((f) => (
            <div key={f.n} className="bg-panel p-7">
              <span className="text-sm font-semibold text-violet-soft">{f.n}</span>
              <h3 className="mt-3 text-[17px] font-semibold tracking-tight">{f.title}</h3>
              <p className="mt-3 text-sm leading-relaxed text-dim">{f.body}</p>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}

const flow = [
  {
    icon: PhoneIncoming,
    title: "Customer reaches out",
    body: "Voice call in any language · WhatsApp text · WhatsApp voice note.",
    tint: "text-saffron",
  },
  {
    icon: Languages,
    title: "Multilingual STT + language detection",
    body: "Faster-Whisper transcribes and detects the language. Text messages skip this step entirely.",
    tint: "text-mint",
  },
  {
    icon: BrainCircuit,
    title: "LLM orchestration + persona engine",
    body: "Groq / Llama reads intent in any language and answers in your tone, using your business knowledge.",
    tint: "text-violet-soft",
  },
  {
    icon: Mic,
    title: "Voice cloning TTS · personalised text",
    body: "Sarvam plus your cloned voice model speaks the reply; the text generator matches your writing style.",
    tint: "text-violet-soft",
  },
  {
    icon: MessageSquare,
    title: "Reply on the same channel",
    body: "Voice-call reply · WhatsApp voice note in your cloned voice · WhatsApp text in your style.",
    tint: "text-mint",
  },
  {
    icon: Database,
    title: "Backend & data",
    body: "FastAPI + Celery + PostgreSQL logs every interaction and feeds the dashboard/CRM.",
    tint: "text-saffron",
  },
];

function Architecture() {
  return (
    <section id="architecture" className="border-t border-line/60 py-24">
      <div className="mx-auto max-w-6xl px-5">
        <SectionTitle
          eyebrow="Architecture"
          title="From a ringing phone to a reply in your voice"
          sub="Every stage runs on infrastructure the business owns."
        />
        <ol className="mx-auto mt-14 max-w-3xl">
          {flow.map((s, i) => (
            <li key={s.title} className="relative flex gap-5 pb-8 last:pb-0">
              {i < flow.length - 1 && (
                <span className="absolute top-12 left-[23px] h-[calc(100%-2.5rem)] w-px bg-line" />
              )}
              <span className="grid size-12 shrink-0 place-items-center rounded-2xl border border-line bg-panel">
                <s.icon className={`size-5 ${s.tint}`} />
              </span>
              <div className="pt-2">
                <h3 className="text-[15px] font-semibold">{s.title}</h3>
                <p className="mt-1.5 text-sm leading-relaxed text-dim">{s.body}</p>
              </div>
            </li>
          ))}
        </ol>

        <div className="mx-auto mt-10 flex max-w-3xl flex-wrap gap-3 text-xs text-dim">
          {[
            [Server, "Self-hosted"],
            [ShieldCheck, "Data stays in India"],
            [Database, "PostgreSQL + Celery"],
          ].map(([Icon, label]) => (
            <span
              key={label}
              className="flex items-center gap-2 rounded-full border border-line bg-panel px-3.5 py-1.5"
            >
              <Icon className="size-3.5" /> {label}
            </span>
          ))}
        </div>
      </div>
    </section>
  );
}

function Impact() {
  return (
    <section className="glow border-t border-line/60 py-24">
      <div className="mx-auto max-w-6xl px-5">
        <SectionTitle
          eyebrow="Conclusion & impact"
          title="An AI agent that genuinely represents the owner"
          sub="Not just automated replies — every customer, in any language, hears the owner's own voice, accent and tone."
        />
        <div className="mt-14 grid gap-4 sm:grid-cols-3">
          {impact.map((s) => (
            <div key={s.big} className="rounded-2xl border border-line bg-panel p-8 text-center">
              <p className="bg-gradient-to-b from-white to-violet-soft bg-clip-text text-5xl font-semibold text-transparent">
                {s.big}
              </p>
              <p className="mt-3 text-sm text-dim">{s.label}</p>
            </div>
          ))}
        </div>
        <p className="mx-auto mt-10 max-w-3xl rounded-2xl border border-line bg-panel px-6 py-4 text-center text-sm text-dim">
          <span className="font-medium text-white">Next:</span> VPS-based telephony rollout
          (Exotel / Asterisk) · multi-persona voice cloning for teams · broader SMB pilot
        </p>
      </div>
    </section>
  );
}

function Footer() {
  return (
    <footer className="border-t border-line/60 py-14">
      <div className="mx-auto max-w-6xl px-5">
        <div className="flex flex-wrap items-start justify-between gap-8">
          <div>
            <Logo />
            <p className="mt-4 max-w-sm text-sm leading-relaxed text-dim">
              Team VoiceFlow · Madhav Institute of Technology &amp; Science, Gwalior (M.P.), India
            </p>
          </div>
          <div className="text-sm text-dim">
            <p className="font-medium text-white">Team</p>
            <p className="mt-2">Abhinav Cheepa — Team Leader</p>
            <p className="mt-1">Raghav Goyal</p>
            <p className="mt-3 flex items-center gap-2">
              <Mail className="size-3.5" /> abhinavcheepa7@gmail.com
            </p>
            <p className="mt-1 flex items-center gap-2">
              <Phone className="size-3.5" /> +91 70007 59702
            </p>
          </div>
        </div>
        <p className="mt-12 text-xs text-dim">© {new Date().getFullYear()} VoiceFlow AI</p>
      </div>
    </footer>
  );
}

export default function Landing() {
  return (
    <>
      <Nav />
      <main>
        <Hero />
        <Problem />
        <Solution />
        <Features />
        <Architecture />
        <Impact />
      </main>
      <Footer />
    </>
  );
}
