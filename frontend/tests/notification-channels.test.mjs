import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import ts from 'typescript';

// Exercise the existing page-local helpers without mounting pages that fetch data.
function pageHelpers(page) {
  const source = readFileSync(new URL(`../src/pages/${page}.tsx`, import.meta.url), 'utf8');
  const ast = ts.createSourceFile(page + '.tsx', source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  const names = ['ChannelChecks', 'channelHelpContent', 'channelSetupMeta', 'normalizeChannels', 'sanitizeChannels'];
  const functions = ast.statements.filter((node) => ts.isFunctionDeclaration(node) && names.includes(node.name?.text));
  assert.equal(functions.length, names.length);
  const code = ts.transpileModule(functions.map((node) => node.getText(ast)).join('\n'), {
    compilerOptions: { jsx: ts.JsxEmit.React, target: ts.ScriptTarget.ES2022 },
  }).outputText;
  const icon = () => null;
  return { source, ...new Function('React', 'Mail', 'Send', code + `\nreturn { ${names.join(', ')} };`)(React, icon, icon) };
}

for (const page of ['SenteroPage', 'SettingsPage']) {
  test(`${page}: only email and Telegram are offered, including for legacy contacts`, () => {
    const helpers = pageHelpers(page);
    const available = { email: true, telegram: true, whatsapp: true };
    const html = renderToStaticMarkup(React.createElement(helpers.ChannelChecks, {
      value: ['email', 'telegram', 'whatsapp'], available, onChange() {},
    }));
    assert.match(html, /E-Mail/);
    assert.match(html, /Telegram/);
    assert.doesNotMatch(html, /whatsapp/i);
    assert.equal((html.match(/type="checkbox"/g) || []).length, 2);
    assert.doesNotMatch(helpers.source, /WhatsApp|setSetupChannel\('whatsapp'\)|setHelpChannel\('whatsapp'\)/);
    // Removing controls must not silently discard an existing contact preference.
    assert.deepEqual(helpers.sanitizeChannels(helpers.normalizeChannels('["email","whatsapp"]'), available), ['email', 'whatsapp']);
  });

  test(`${page}: supported setup and help remain available`, () => {
    const helpers = pageHelpers(page);
    for (const channel of ['email', 'telegram']) {
      assert.ok(helpers.channelSetupMeta(channel).fields.length);
      assert.ok(helpers.channelHelpContent(channel).sections.length);
    }
    assert.match(JSON.stringify(helpers.channelHelpContent('email')), /Mit Microsoft verbinden/);
    assert.match(JSON.stringify(helpers.channelHelpContent('telegram')), /BotFather/);
  });
}
