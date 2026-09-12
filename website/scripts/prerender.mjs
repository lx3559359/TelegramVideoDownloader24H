import { readFile, writeFile } from 'node:fs/promises';
import { createServer } from 'vite';
import React from 'react';
import { renderToString } from 'react-dom/server';

// Use the same component as the browser so crawlers and visitors see identical content.
const server = await createServer({ server: { middlewareMode: true }, appType: 'custom' });
try {
  const { App } = await server.ssrLoadModule('/src/App.jsx');
  const file = new URL('../dist/client/index.html', import.meta.url);
  const template = await readFile(file, 'utf8');
  if (!template.includes('<div id="root"></div>')) throw new Error('Missing prerender root');
  await writeFile(file, template.replace('<div id="root"></div>', () => `<div id="root">${renderToString(React.createElement(App))}</div>`));
  console.log('Prerendered complete landing page for search engines and no-JavaScript visitors.');
} finally {
  await server.close();
}
