// Self-hosted fonts (replaces the dead propsmadness.com hotlinks).
// Inter = UI (variable, 100-900), Chakra Petch = data/SVG labels (static 400/500/600/700).
// Mirrors propsmadness's own font setup.
import '@fontsource-variable/inter/wght.css';
import '@fontsource/chakra-petch/latin-400.css';
import '@fontsource/chakra-petch/latin-500.css';
import '@fontsource/chakra-petch/latin-600.css';
import '@fontsource/chakra-petch/latin-700.css';

import React from 'react';
import ReactDOM from 'react-dom/client';
import App from './App';

const rootElement = document.getElementById('root');
if (!rootElement) {
  throw new Error("Could not find root element to mount to");
}

const root = ReactDOM.createRoot(rootElement);
root.render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);