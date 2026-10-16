import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import "./index.css";

import Landing from "./Landing.jsx";
import AppShell from "./AppShell.jsx";
import Dashboard from "./pages/Dashboard.jsx";
import Calls from "./pages/Calls.jsx";
import WhatsApp from "./pages/WhatsApp.jsx";
import VoiceStudio from "./pages/VoiceStudio.jsx";
import WebCall from "./pages/WebCall.jsx";
import Agents from "./pages/Agents.jsx";
import Contacts from "./pages/Contacts.jsx";
import Campaigns from "./pages/Campaigns.jsx";
import Bookings from "./pages/Bookings.jsx";
import Analytics from "./pages/Analytics.jsx";
import Stock from "./pages/Stock.jsx";
import Bills from "./pages/Bills.jsx";
import Learning from "./pages/Learning.jsx";

createRoot(document.getElementById("root")).render(
  <StrictMode>
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<Landing />} />
        <Route path="/app" element={<AppShell />}>
          <Route index element={<Dashboard />} />
          <Route path="calls" element={<Calls />} />
          <Route path="web-call" element={<WebCall />} />
          <Route path="whatsapp" element={<WhatsApp />} />
          <Route path="bookings" element={<Bookings />} />
          <Route path="agents" element={<Agents />} />
          <Route path="contacts" element={<Contacts />} />
          <Route path="campaigns" element={<Campaigns />} />
          <Route path="analytics" element={<Analytics />} />
          <Route path="voice-studio" element={<VoiceStudio />} />
          <Route path="stock" element={<Stock />} />
          <Route path="bills" element={<Bills />} />
          <Route path="learning" element={<Learning />} />
        </Route>
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </BrowserRouter>
  </StrictMode>,
);

