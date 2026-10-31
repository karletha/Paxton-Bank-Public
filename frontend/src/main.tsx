import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import App from './App.tsx';
import { ProspectShareView } from './views/ProspectShareView.tsx';
import './index.css';

// ?share=<room> renders the PROSPECT-SAFE shared canvas instead of the banker
// cockpit — opened in a second tab by the cockpit's share button and
// screen-shared in Zoom. ?source=<crm|news|market|kyc|dossier> renders a
// styled data-source viewer (Client Profile links — never raw API JSON).
// Everything else renders the full cockpit.
import { SourceViewer } from './views/SourceViewer.tsx';

const params = new URLSearchParams(window.location.search);
const shareRoom = params.get('share');
const source = params.get('source');

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    {shareRoom ? <ProspectShareView room={shareRoom} />
      : source ? <SourceViewer source={source} company={params.get('company') || 'Meridian Components Inc.'} />
      : <App />}
  </StrictMode>,
);
