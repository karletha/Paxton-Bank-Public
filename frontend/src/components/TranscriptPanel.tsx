import React, { useEffect, useRef } from 'react';
import { MessagesSquare, Bot, User, Briefcase, AlertTriangle, Trash2, Send } from 'lucide-react';

export interface TranscriptMessage {
  id: string;
  speaker: string;
  role?: 'ai' | 'banker' | 'participant';
  text: string;
  timestamp: string;
  state: 'idle' | 'listening' | 'thinking' | 'speaking' | 'paused' | 'handoff';
  isRestrictedTopic?: boolean;
}

interface TranscriptTurnProps {
  message: TranscriptMessage;
  isActive?: boolean;
}

export const TranscriptTurn: React.FC<TranscriptTurnProps> = ({ message, isActive = false }) => {
  const isKairos = message.role === 'ai' || /kairos|bridget/.test(message.speaker.toLowerCase());
  const isBanker = message.role === 'banker';
  const isProspect = !isKairos && !isBanker;

  // Choose correct theme and icon per speaker
  const getSpeakerStyles = () => {
    if (isKairos) {
      return {
        nameColor: 'text-sky-700 font-bold',
        Icon: Bot,
        iconColor: 'text-sky-600',
        bg: 'bg-[#003B70] text-white border border-white/10 shadow-md',
        align: 'self-start w-5/6',
      };
    } else if (isProspect) {
      return {
        nameColor: 'text-gray-700',
        Icon: User,
        iconColor: 'text-amber-500',
        bg: 'bg-white border border-gray-200 text-gray-800 shadow-sm',
        align: 'self-end w-5/6',
      };
    } else {
      // Banker
      return {
        nameColor: 'text-gray-700',
        Icon: Briefcase,
        iconColor: 'text-slate-500',
        bg: 'bg-gray-100 border border-gray-200 text-gray-800 shadow-sm',
        align: 'self-start w-5/6',
      };
    }
  };

  const styles = getSpeakerStyles();
  const { Icon } = styles;

  return (
    <div id={`turn-${message.id}`} className={`flex flex-col gap-1.5 transition-all duration-300 ${styles.align} ${isActive ? 'scale-[1.02]' : ''}`}>
      {/* Turn Header */}
      <div className={`flex items-center gap-1.5 text-xs text-gray-500 ${isProspect ? 'justify-end pr-2' : 'pl-2'}`}>
        {!isProspect && <Icon size={15} className={styles.iconColor} />}
        <span className={`font-semibold ${styles.nameColor}`}>{message.speaker}</span>
        <span className="text-[10px] text-gray-400 font-mono">• {message.timestamp}</span>
        {isProspect && <Icon size={15} className={styles.iconColor} />}
      </div>

      {/* Bubble Message */}
      <div className={`rounded-2xl p-4 text-sm leading-relaxed transition-all relative ${styles.bg}`}>
        {message.isRestrictedTopic && (
          <div className="mb-2 bg-red-500/20 border border-red-400/40 rounded-lg p-2 text-xs font-bold text-red-100 flex items-center gap-1.5">
            <AlertTriangle size={14} className="text-red-300" />
            <span>Policy interceptor override triggered</span>
          </div>
        )}
        <p className="whitespace-pre-wrap">{message.text}</p>
      </div>
    </div>
  );
};

interface TranscriptPanelProps {
  messages: TranscriptMessage[];
  currentStepIndex?: number;
  onSendCustomText?: (text: string) => void;
  onClearTranscript?: () => void;
}

export const TranscriptPanel: React.FC<TranscriptPanelProps> = ({
  messages,
  onSendCustomText,
  onClearTranscript,
}) => {
  const containerRef = useRef<HTMLDivElement>(null);
  const [inputText, setInputText] = React.useState('');

  useEffect(() => {
    if (containerRef.current) {
      containerRef.current.scrollTop = containerRef.current.scrollHeight;
    }
  }, [messages]);

  const handleSend = (e: React.FormEvent) => {
    e.preventDefault();
    if (!inputText.trim()) return;
    if (onSendCustomText) {
      onSendCustomText(inputText);
    }
    setInputText('');
  };

  return (
    <div className="flex-1 bg-white border border-gray-200 rounded-xl flex flex-col shadow-sm min-h-[580px] max-h-[580px] overflow-hidden">
      {/* Header with Clear / Reset control */}
      <div className="px-5 py-4 border-b border-gray-100 flex justify-between items-center shrink-0">
        <div>
          <div className="mb-1 text-[10px] font-bold uppercase tracking-wider text-gray-400">Live meeting</div>
          <h3 className="flex items-center gap-2 text-[13px] font-bold text-[#00254A]">
            <MessagesSquare size={14} className="text-sky-600" /> Live transcript
            <span className="rounded-full bg-sky-50 px-2.5 py-0.5 text-[10.5px] font-bold text-[#00254A]">
              {messages.length} turns
            </span>
          </h3>
        </div>

        {onClearTranscript && (
          <button
            onClick={onClearTranscript}
            className="text-xs bg-gray-100 hover:bg-gray-200 text-gray-600 font-semibold px-2.5 py-1 rounded-lg flex items-center gap-1 transition-all cursor-pointer"
            title="Clear transcript messages"
          >
            <Trash2 size={13} /> Clear
          </button>
        )}
      </div>

      {/* Transcript Turns Body */}
      <div
        ref={containerRef}
        className="flex-grow overflow-y-auto p-5 flex flex-col gap-5"
        style={{ minHeight: '220px' }}
      >
        {messages.length === 0 ? (
          <div className="flex-grow flex flex-col items-center justify-center p-8 text-center text-gray-400">
            <MessagesSquare size={36} className="mb-2 text-sky-400/60 animate-pulse" />
            <p className="text-sm font-medium text-gray-600">Ready for live treasury briefing</p>
            <p className="text-xs text-gray-400 mt-1">Spoken voice and banker turns will appear here in real time.</p>
          </div>
        ) : (
          messages.map((message, idx) => (
            <TranscriptTurn
              key={message.id}
              message={message}
              isActive={idx === messages.length - 1}
            />
          ))
        )}
      </div>

      {/* Input Message Container */}
      <form onSubmit={handleSend} className="p-4 bg-gray-50 border-t border-gray-200 shrink-0">
        <div className="relative">
          <input
            id="message-panel-input"
            type="text"
            value={inputText}
            onChange={(e) => setInputText(e.target.value)}
            className="w-full bg-white border border-gray-200 rounded-full py-3 pl-5 pr-12 text-sm text-gray-900 focus:border-sky-500 focus:outline-none transition-colors placeholder-gray-400 shadow-sm"
            placeholder="Type a message or response for Bridget..."
          />
          <button
            id="message-panel-submit"
            type="submit"
            className="absolute right-2 top-1.5 p-2 bg-sky-500 hover:bg-sky-600 text-white rounded-full transition-colors flex items-center justify-center cursor-pointer shadow-sm"
            title="Send message"
          >
            <Send size={16} />
          </button>
        </div>
      </form>
    </div>
  );
};
