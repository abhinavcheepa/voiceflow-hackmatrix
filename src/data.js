// Mock data only — no backend yet. Shapes here are what the FastAPI/Postgres
// layer in the architecture slide is expected to return.

export const stats = {
  totalCalls: 1284,
  avgDuration: "2m 14s",
  successRate: 94.2,
  whatsappResponseRate: 98.6,
};

// Calls answered per hour, 09:00 → 21:00. Drives the dashboard bar chart.
export const callsByHour = [
  { hour: "09", calls: 42 },
  { hour: "10", calls: 78 },
  { hour: "11", calls: 96 },
  { hour: "12", calls: 88 },
  { hour: "13", calls: 54 },
  { hour: "14", calls: 71 },
  { hour: "15", calls: 103 },
  { hour: "16", calls: 118 },
  { hour: "17", calls: 134 },
  { hour: "18", calls: 121 },
  { hour: "19", calls: 92 },
  { hour: "20", calls: 63 },
  { hour: "21", calls: 38 },
];

export const languages = [
  { name: "Hindi", share: 46, color: "#7c5cff" },
  { name: "English", share: 27, color: "#2ee6a8" },
  { name: "Hinglish", share: 15, color: "#ff8a3d" },
  { name: "Marathi", share: 7, color: "#4fa8ff" },
  { name: "Tamil", share: 5, color: "#ff5c8a" },
];

export const calls = [
  {
    id: "CL-2941",
    name: "Ramesh Kulkarni",
    phone: "+91 98220 41xxx",
    language: "Marathi",
    intent: "Appointment booking",
    duration: "1m 52s",
    outcome: "Booked",
    time: "2 min ago",
  },
  {
    id: "CL-2940",
    name: "Priya Sharma",
    phone: "+91 99110 87xxx",
    language: "Hindi",
    intent: "Price enquiry",
    duration: "3m 07s",
    outcome: "Lead captured",
    time: "11 min ago",
  },
  {
    id: "CL-2939",
    name: "Unknown",
    phone: "+91 70007 59xxx",
    language: "Hinglish",
    intent: "Store timings",
    duration: "0m 41s",
    outcome: "Answered",
    time: "26 min ago",
  },
  {
    id: "CL-2938",
    name: "Ayesha Khan",
    phone: "+91 90045 12xxx",
    language: "Hindi",
    intent: "Reschedule visit",
    duration: "2m 20s",
    outcome: "Booked",
    time: "48 min ago",
  },
  {
    id: "CL-2937",
    name: "Suresh Iyer",
    phone: "+91 98404 66xxx",
    language: "Tamil",
    intent: "Complaint",
    duration: "4m 35s",
    outcome: "Escalated",
    time: "1 hr ago",
  },
  {
    id: "CL-2936",
    name: "Neha Gupta",
    phone: "+91 88266 30xxx",
    language: "English",
    intent: "Product availability",
    duration: "1m 09s",
    outcome: "Answered",
    time: "1 hr ago",
  },
  {
    id: "CL-2935",
    name: "Vikram Patel",
    phone: "+91 97129 55xxx",
    language: "Hindi",
    intent: "Bulk order",
    duration: "5m 12s",
    outcome: "Lead captured",
    time: "2 hr ago",
  },
  {
    id: "CL-2934",
    name: "Farhan Ali",
    phone: "+91 93150 78xxx",
    language: "Hinglish",
    intent: "Missed call callback",
    duration: "0m 58s",
    outcome: "Answered",
    time: "2 hr ago",
  },
];

export const conversations = [
  {
    id: 1,
    name: "Priya Sharma",
    phone: "+91 99110 87xxx",
    language: "Hindi",
    unread: 0,
    last: "Ji bilkul, main aapke liye slot book kar deti hoon.",
    time: "2m",
    messages: [
      { from: "them", type: "text", text: "Namaste, kya aaj evening ka slot mil sakta hai?", time: "6:41 PM" },
      {
        from: "us",
        type: "voice",
        seconds: 8,
        text: "Namaste Priya ji! Aaj 7:30 baje ka slot khaali hai — book kar doon?",
        time: "6:41 PM",
      },
      { from: "them", type: "text", text: "Haan please book kar dijiye", time: "6:42 PM" },
      { from: "us", type: "text", text: "Ji bilkul, main aapke liye slot book kar deti hoon.", time: "6:42 PM" },
    ],
  },
  {
    id: 2,
    name: "Ramesh Kulkarni",
    phone: "+91 98220 41xxx",
    language: "Marathi",
    unread: 2,
    last: "🎤 Voice note · 0:12",
    time: "14m",
    messages: [
      { from: "them", type: "voice", seconds: 12, text: "उद्या सकाळी येऊ शकतो का?", time: "6:29 PM" },
      {
        from: "us",
        type: "voice",
        seconds: 9,
        text: "नक्कीच! उद्या सकाळी ११ वाजता तुमची वेळ ठरवली आहे.",
        time: "6:29 PM",
      },
    ],
  },
  {
    id: 3,
    name: "Neha Gupta",
    phone: "+91 88266 30xxx",
    language: "English",
    unread: 0,
    last: "Sure — sending you the catalogue now.",
    time: "1h",
    messages: [
      { from: "them", type: "text", text: "Do you have the new collection in stock?", time: "5:18 PM" },
      { from: "us", type: "text", text: "Sure — sending you the catalogue now.", time: "5:18 PM" },
    ],
  },
  {
    id: 4,
    name: "Suresh Iyer",
    phone: "+91 98404 66xxx",
    language: "Tamil",
    unread: 1,
    last: "ஆர்டர் எப்போ வரும்?",
    time: "3h",
    messages: [{ from: "them", type: "text", text: "ஆர்டர் எப்போ வரும்?", time: "3:52 PM" }],
  },
];

// Landing-page copy, lifted from the deck so the page and the slides never drift.
export const problems = [
  {
    title: "Missed calls = missed revenue",
    body: "After hours, during a rush, or when staff are busy — the call goes unanswered and the customer simply dials a competitor next.",
  },
  {
    title: "Human call agents don't scale",
    body: "Hiring and training is expensive, and a small business can't staff 24/7 support no matter how many leads it loses.",
  },
  {
    title: "Global voice AI is English-first",
    body: "Vapi, Bland and Retell have weak Hindi and Indian-accent support, bill per minute in USD, and move customer data out of the country.",
  },
  {
    title: "WhatsApp replies are still manual",
    body: "It's the channel Indian customers actually prefer, yet every reply is typed by hand — so a slow response is a lost customer.",
  },
  {
    title: "Nothing lives in one place",
    body: "Call logs, WhatsApp chats and customer records sit in separate systems, so owners have no single view of what's happening.",
  },
];

export const solutions = [
  {
    title: "No more missed calls",
    body: "An AI voice agent picks up every inbound call instantly, 24/7 — understands the query, answers it, or books and logs the lead automatically.",
  },
  {
    title: "No agent hiring needed",
    body: "One agent handles unlimited concurrent calls. A clinic or shop gets round-the-clock coverage without hiring or training anyone.",
  },
  {
    title: "Built for India, not adapted for it",
    body: "Sarvam AI for native Hindi TTS/STT plus Groq for fast LLM responses — natural conversation with sub-second replies.",
  },
  {
    title: "Data stays with the business",
    body: "Entirely self-hosted on your own backend and database. No per-minute USD billing, no customer data handed to third-party cloud vendors.",
  },
  {
    title: "WhatsApp goes from manual to instant",
    body: "The same agent auto-replies to WhatsApp the moment a message lands, so leads are engaged while they're still interested.",
  },
  {
    title: "One dashboard, not five tools",
    body: "Calls, WhatsApp conversations and customer records unified in a single CRM — the owner finally sees the full picture.",
  },
];

export const features = [
  {
    n: "01",
    title: "Your Voice. Your Accent. Your Style.",
    body: "The agent replies to every customer in your cloned voice, accent and conversational behaviour — not a generic robotic voice. Customers feel they're talking to you.",
  },
  {
    n: "02",
    title: "Understands Any Language",
    body: "Whatever language or dialect the customer speaks, the AI understands it — and replies in that same language, in your cloned voice.",
  },
  {
    n: "03",
    title: "WhatsApp Voice Notes, In Your Voice",
    body: "If a customer sends a voice note instead of text, the AI answers with a voice note too — cloned in your voice, not a flat text reply.",
  },
  {
    n: "04",
    title: "Writes Like You Do",
    body: "WhatsApp text replies are generated to match your own writing style, tone and phrasing — not generic bot-speak.",
  },
  {
    n: "05",
    title: "Real-Time Dashboard",
    body: "Track total calls, average call duration, success rate and WhatsApp response rate live, in one place.",
  },
  {
    n: "06",
    title: "Self-Hosted & Cost-Effective",
    body: "Full control over customer data and no per-minute USD billing — unlike Vapi, Bland AI or Retell AI.",
  },
];

export const impact = [
  { big: "24/7", label: "Availability — no missed calls, ever" },
  { big: "Any", label: "Language understood & answered" },
  { big: "₹0", label: "Per-agent hiring cost to scale support" },
];
