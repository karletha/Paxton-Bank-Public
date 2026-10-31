// @ts-nocheck
/**
 * Start a REAL Zoom meeting through the Bridget engine: the bridge creates the
 * meeting on the authorized Zoom account and seats the Bridget advisor — the only
 * AI participant — via ONE self-hosted Zoom SDK bot (~5s warm join). The client
 * is a live human. No fabricated meeting numbers:
 * if creation fails, the error is shown — never a fake fallback room.
 */
import React, { useState } from 'react';

interface ZoomMeetingModalProps {
  isOpen: boolean;
  onClose: () => void;
  onStart: (company: string, bankerName: string) => Promise<void> | void;
}

const COMPANIES = ['Meridian Components Inc.', 'Bluewater Logistics Group', 'Helioform Materials'];



export const ZoomMeetingModal: React.FC<ZoomMeetingModalProps> = ({ isOpen, onClose, onStart }) => {
  const [company, setCompany] = useState(COMPANIES[0]);
  const [bankerName, setBankerName] = useState(localStorage.getItem('kairos_banker') || 'Alex Drummond');
  const [starting, setStarting] = useState(false);

  if (!isOpen) return null;

  const handleStart = async () => {
    setStarting(true);
    localStorage.setItem('kairos_banker', bankerName.trim());
    try { await onStart(company, bankerName.trim() || 'Alex Drummond'); } finally { setStarting(false); }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm" onClick={onClose}>
      <div className="bg-white rounded-2xl shadow-2xl w-full max-w-lg p-6 m-4" onClick={e => e.stopPropagation()}>
        <div className="flex items-center justify-between mb-4">
          <div>
            <h2 className="text-lg font-bold text-[#00254A]">Start a live meeting</h2>
            <p className="text-xs text-gray-500 mt-0.5">Creates the meeting and brings Bridget in automatically.</p>
          </div>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-600 cursor-pointer text-xl leading-none">×</button>
        </div>

        <label className="block text-[10px] font-extrabold uppercase tracking-widest text-gray-500 mb-1.5">Your name (Banker Host)</label>
        <input value={bankerName} onChange={e => setBankerName(e.target.value)} placeholder="e.g. Karletha Paxton"
          className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm mb-4 bg-white" />
        <label className="block text-[10px] font-extrabold uppercase tracking-widest text-gray-500 mb-1.5">Prospect company</label>
        <select value={company} onChange={e => setCompany(e.target.value)}
          className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm mb-4 bg-white cursor-pointer">
          {COMPANIES.map(c => <option key={c}>{c}</option>)}
        </select>

        <div className="rounded-xl border border-sky-200 bg-sky-50 px-4 py-3 mb-5">
          <div className="text-sm font-bold text-[#00254A]">Kira (Bridget) joins as your live AI Advisor</div>
          <div className="text-xs text-gray-500">You and your client are live humans in the meeting; Kira streams real-time 24fps video and 24kHz HD Gemini 3.5 Flash Live audio.</div>
        </div>

        <div className="rounded-lg bg-slate-50 border border-slate-200 p-3 text-[11px] text-slate-600 mb-5">
          <b>Live 3-Way Setup:</b> Copy the client invite link after starting and open it in an Incognito window (or send to your client) so both sides see each other and Kira live.
        </div>

        <button onClick={handleStart} disabled={starting}
          className="w-full bg-sky-500 hover:bg-sky-400 disabled:opacity-60 text-white font-bold text-sm py-3 rounded-xl shadow transition-all cursor-pointer">
          {starting ? 'Creating meeting & seating AI…' : 'Create meeting'}
        </button>
      </div>
    </div>
  );
};
