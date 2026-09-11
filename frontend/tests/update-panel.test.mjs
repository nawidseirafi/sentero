import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import ts from 'typescript';

const source = readFileSync(new URL('../src/components/UpdatePanel.tsx', import.meta.url), 'utf8');
const ast = ts.createSourceFile('UpdatePanel.tsx', source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
const code = ts.transpileModule(ast.statements.filter((node) => !ts.isImportDeclaration(node)).map((node) => node.getText(ast)).join('\n'), {
  compilerOptions: { jsx: ts.JsxEmit.React, module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText;
const stale = {
  status: 'update_available', state: 'update_available', current_version: '0.4.6', latest_version: '0.4.7', update_available: true,
  steps: [{ key: 'done', label: 'Fertig', status: 'success' }],
  install: { status: 'success', target_version: '0.4.6' },
};

function panel(api) {
  const state = [structuredClone(stale), '', ''];
  let cursor = 0;
  const useState = () => {
    const index = cursor++;
    return [state[index], (value) => { state[index] = value; }];
  };
  const icon = () => null;
  const exports = {};
  new Function('exports', 'React', 'useState', 'useEffect', 'api', 'window',
    'Activity', 'CheckCircle2', 'History', 'RefreshCw', 'ShieldAlert', code)(
    exports, React, useState, () => {}, api, { setTimeout: (callback) => { callback(); } },
    icon, icon, icon, icon, icon,
  );
  const render = () => { cursor = 0; return exports.UpdatePanel({}); };
  const nodes = (node) => !node || typeof node !== 'object' ? [] : [node, ...React.Children.toArray(node.props?.children).flatMap(nodes)];
  return {
    state, outcome: exports.updateAttemptOutcome,
    html: () => renderToStaticMarkup(render()),
    click: () => nodes(render()).find((node) => node.type === 'button' && node.props.className === 'button primary').props.onClick(),
  };
}

test('old successful steps are not rendered as an active installation', () => {
  const ui = panel({});
  assert.doesNotMatch(ui.html(), /update-wizard|Abgeschlossen/);
  assert.match(ui.html(), /Update installieren/);
  assert.equal(ui.outcome(stale, '0.4.7'), false);
});

test('HTTP rejection stops immediately without pretending installation is running', async () => {
  let calls = 0;
  const ui = panel({
    senteroInstallUpdate: async () => { calls++; throw Object.assign(new Error('Nur Administratoren'), { status: 403 }); },
    senteroUpdateStatus: async () => { throw new Error('must not poll'); },
  });
  await ui.click();
  assert.equal(calls, 1);
  assert.equal(ui.state[1], '');
  assert.match(ui.html(), /Nur Administratoren/);
  assert.doesNotMatch(ui.html(), /update-wizard/);
});

test('lost request with unchanged available status is a failure, not a 15 minute wait', async () => {
  let polls = 0;
  const ui = panel({
    senteroInstallUpdate: async () => { throw new Error('Verbindung fehlgeschlagen'); },
    senteroUpdateStatus: async () => { polls++; return structuredClone(stale); },
  });
  await ui.click();
  assert.equal(polls, 1);
  assert.equal(ui.state[1], '');
  assert.match(ui.html(), /Verbindung fehlgeschlagen/);
  assert.doesNotMatch(ui.html(), /update-wizard/);
});

test('click sends install request and follows only the requested target', async () => {
  let calls = 0;
  const ui = panel({
    senteroInstallUpdate: async () => {
      calls++;
      return { ...stale, status: 'running', steps: [], install: { status: 'running', target_version: '0.4.7' } };
    },
    senteroUpdateStatus: async () => ({
      ...stale, status: 'success', current_version: '0.4.7',
      install: { status: 'success', target_version: '0.4.7', finished_at: new Date().toISOString() },
    }),
  });
  await ui.click();
  assert.equal(calls, 1);
  assert.equal(ui.state[0].install.target_version, '0.4.7');
  assert.equal(ui.state[2], '');
  assert.match(ui.html(), /Update erfolgreich/);
});

test('API client points installation at the matching POST endpoint', () => {
  const client = readFileSync(new URL('../src/shared/api/client.ts', import.meta.url), 'utf8');
  assert.match(client, /senteroInstallUpdate:.*\/api\/sentero\/system\/update\/install.*method: 'POST'/);
});
