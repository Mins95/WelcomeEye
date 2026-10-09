/* Real-browser module fetching against local HTTP mocks; no HA/device access. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const http = require('node:http');
const path = require('node:path');
const {before, after, test} = require('node:test');
const {chromium} = require('playwright');

const root = path.resolve(__dirname, '../..');
const assets = path.join(root, 'custom_components/welcomeeye_local/frontend');
const loader = fs.readFileSync(path.join(assets, 'welcomeeye-loader.js'));
const card = fs.readFileSync(path.join(assets, 'welcomeeye-card.js'));
const channels = process.env.WELCOMEEYE_BROWSER_CHANNELS?.split(',') || [''];
const browsers = new Map();

before(async () => {
  for (const channel of channels) {
    const browser = await chromium.launch({headless: true, ...(channel ? {channel} : {})});
    browsers.set(channel, browser);
    console.log(`Browser: ${channel || 'Chromium'} ${browser.version()}`);
  }
});
after(async () => { await Promise.all([...browsers.values()].map(browser => browser.close())); });

async function fixture(t, channel, options = {}) {
  const state = {requests: [], failures: options.failures || 0, empty: !!options.empty, delays: options.delays || []};
  const timers = new Set();
  const server = http.createServer((request, response) => {
    const url = new URL(request.url, 'http://localhost');
    response.setHeader('Cache-Control', 'no-store');
    if (url.pathname.endsWith('/welcomeeye-loader.js')) {
      response.setHeader('Content-Type', 'text/javascript');
      response.end(loader);
    } else if (url.pathname.endsWith('/welcomeeye-card.js')) {
      state.requests.push(url.search);
      const index = state.requests.length - 1;
      const failed = state.failures-- > 0;
      const content = state.empty ? '/* successful HTTP response but no registration */' : card;
      const finish = () => {
        response.statusCode = failed ? 503 : 200;
        response.setHeader('Content-Type', 'text/javascript');
        response.end(failed ? '/* temporary frontend failure */' : content);
      };
      if (state.delays[index]) {
        const timer = setTimeout(() => {timers.delete(timer);finish();}, state.delays[index]);
        timers.add(timer);
      } else finish();
    } else {
      response.setHeader('Content-Type', 'text/html');
      response.end('<!doctype html><meta charset="utf-8"><title>Local WelcomeEye module test</title>');
    }
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const base = `http://127.0.0.1:${server.address().port}`;
  const context = await browsers.get(channel).newContext();
  if (options.fastTimeout) await context.addInitScript(() => {
    const original = window.setTimeout.bind(window);
    window.setTimeout = (fn, ms, ...args) => original(fn, ms === 15000 ? 100 : ms, ...args);
  });
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  t.after(async () => {
    for (const timer of timers) clearTimeout(timer);
    await context.close();
    server.closeAllConnections();
    await new Promise(resolve => server.close(resolve));
    assert.deepEqual(errors, [], 'no unhandled browser exception');
  });
  await page.goto(base);
  return {state, page, base};
}

for (const channel of channels) {
  const name = channel || 'Chromium';

  test(`${name}: reproduces shared failed module URL and its cached rejection`, async t => {
    const {state, page} = await fixture(t, channel, {failures: 1});
    const result = await page.evaluate(async () => {
      const url = '/welcomeeye_local/welcomeeye-card.js?v=old&card=2';
      const first = await Promise.allSettled([import(url), import(url)]);
      const repeated = await Promise.allSettled([import(url)]);
      const missing = !customElements.get('welcomeeye-card');
      await import(url + '&retry=fresh');
      return {first: first.map(item => item.status), repeated: repeated[0].status, missing,
        recovered: !!customElements.get('welcomeeye-card')};
    });
    assert.deepEqual(result, {first: ['rejected', 'rejected'], repeated: 'rejected', missing: true, recovered: true});
    assert.equal(state.requests.length, 2, 'one failed shared request, then one fresh URL');
  });

  test(`${name}: a slow import outlives the old eight-second popup timeout`, async t => {
    const {page} = await fixture(t, channel, {delays: [8250]});
    const result = await page.evaluate(async () => {
      const pending = import('/welcomeeye_local/welcomeeye-card.js?old_popup=1');
      const outcome = await Promise.race([pending.then(() => 'loaded'),
        new Promise(resolve => setTimeout(() => resolve('timeout'), 8000))]);
      const missingAtTimeout = !customElements.get('welcomeeye-card');
      await pending;
      return {outcome, missingAtTimeout, loadedLater: !!customElements.get('welcomeeye-card')};
    });
    assert.deepEqual(result, {outcome: 'timeout', missingAtTimeout: true, loadedLater: true});
  });

  test(`${name}: independent loader recovers an already failed Lovelace module`, async t => {
    const {state, page} = await fixture(t, channel, {failures: 2});
    const result = await page.evaluate(async () => {
      await import('/welcomeeye_local/welcomeeye-card.js?v=test&card=hash').catch(() => {});
      const loader = await import('/welcomeeye_local/welcomeeye-loader.js?v=test&card=hash');
      await loader.loadWelcomeEyeCard();
      return !!customElements.get('welcomeeye-card');
    });
    assert.equal(result, true);
    assert.equal(state.requests.length, 3);
    assert.equal(new Set(state.requests).size, 3, 'failed URLs are never reused');
  });

  test(`${name}: duplicate bootstrap and popup consumers share one pending load`, async t => {
    const {state, page} = await fixture(t, channel, {delays: [150]});
    const result = await page.evaluate(async () => {
      const [a, b] = await Promise.all([
        import('/welcomeeye_local/welcomeeye-loader.js?v=test'),
        import('/welcomeeye_local/welcomeeye-loader.js?v=test&popup=1'),
      ]);
      const first = a.loadWelcomeEyeCard(), second = b.loadWelcomeEyeCard();
      const samePromise = first === second && first === window.loadWelcomeEyeCard();
      await first;
      return samePromise;
    });
    assert.equal(result, true);
    assert.equal(state.requests.length, 1);
  });

  test(`${name}: load slower than eight seconds succeeds without an early error`, async t => {
    const {state, page} = await fixture(t, channel, {delays: [8250]});
    const result = await page.evaluate(async () => {
      const loader = await import('/welcomeeye_local/welcomeeye-loader.js?v=slow');
      await loader.loadWelcomeEyeCard();
      return !!customElements.get('welcomeeye-card');
    });
    assert.equal(result, true);
    assert.equal(state.requests.length, 1);
  });

  test(`${name}: timeout recovery tolerates a late first module registration`, async t => {
    const {state, page} = await fixture(t, channel, {delays: [450], fastTimeout: true});
    await page.evaluate(async () => {
      const loader = await import('/welcomeeye_local/welcomeeye-loader.js?v=timeout');
      await loader.loadWelcomeEyeCard();
      await new Promise(resolve => setTimeout(resolve, 500));
    });
    assert.equal(state.requests.length, 2);
    assert.equal(await page.evaluate(() => !!customElements.get('welcomeeye-card')), true);
  });

  test(`${name}: three failures stop automatic requests; an explicit retry recovers`, async t => {
    const {state, page} = await fixture(t, channel, {failures: 3});
    const failed = await page.evaluate(async () => {
      const loader = await import('/welcomeeye_local/welcomeeye-loader.js?v=offline');
      return loader.loadWelcomeEyeCard().then(() => '', error => error.message);
    });
    assert.match(failed, /non chargée après 3 tentatives/);
    assert.equal(state.requests.length, 3);
    await new Promise(resolve => setTimeout(resolve, 600));
    assert.equal(state.requests.length, 3, 'no background retry loop');
    await page.evaluate(() => window.loadWelcomeEyeCard());
    assert.equal(state.requests.length, 4);
    assert.equal(new Set(state.requests).size, 4);
  });

  test(`${name}: a module response without registration fails clearly and can retry`, async t => {
    const {state, page} = await fixture(t, channel, {empty: true});
    const message = await page.evaluate(async () => {
      const loader = await import('/welcomeeye_local/welcomeeye-loader.js?v=empty');
      return loader.loadWelcomeEyeCard().catch(error => error.message);
    });
    assert.match(message, /n'a pas enregistré welcomeeye-card/);
    assert.equal(state.requests.length, 3);
    state.empty = false;
    await page.evaluate(() => window.loadWelcomeEyeCard());
    assert.equal(state.requests.length, 4);
  });

  test(`${name}: an already registered card is preserved without another fetch`, async t => {
    const {state, page} = await fixture(t, channel);
    const unchanged = await page.evaluate(async () => {
      class ExistingCard extends HTMLElement {}
      customElements.define('welcomeeye-card', ExistingCard);
      const loader = await import('/welcomeeye_local/welcomeeye-loader.js?v=existing');
      return await loader.loadWelcomeEyeCard() === ExistingCard;
    });
    assert.equal(unchanged, true);
    assert.equal(state.requests.length, 0);
  });

  const popupPath = process.env.WELCOMEEYE_POPUP_FILE;
  test(`${name}: personal popup uses bounded recovery and releases its click guard`, {skip: !popupPath}, async t => {
    const source = fs.readFileSync(popupPath, 'utf8');
    const block = source.split('                  code: |')[1].split(/\r?\n/).slice(1);
    const lines = [];
    for (const line of block) {
      if (line.trim() && !line.startsWith(' '.repeat(20))) break;
      lines.push(line.slice(20));
    }
    const code = lines.join('\n');
    assert.ok(code.includes('welcomeeye-loader.js'));
    const {state, page} = await fixture(t, channel, {failures: 3});
    await page.evaluate(() => {
      window.popupResults = [];
      window.browser_mod = {service: async (name, data) => window.popupResults.push(data.content)};
    });
    await page.evaluate(code => new Function(code)(), code);
    await page.waitForFunction(() => window.popupResults.length === 1);
    const failure = await page.evaluate(() => ({result: window.popupResults[0], locked: window.__welcomeeyePopupLoading}));
    assert.equal(failure.result.type, 'markdown');
    assert.match(failure.result.content, /Chargement WelcomeEye impossible/);
    assert.match(failure.result.content, /3 tentatives/);
    assert.equal(failure.locked, false);
    assert.equal(state.requests.length, 3);
    await page.evaluate(code => new Function(code)(), code);
    await page.waitForFunction(() => window.popupResults.length === 2);
    assert.equal(await page.evaluate(() => window.popupResults[1].type), 'custom:welcomeeye-card');
    assert.equal(state.requests.length, 4);
  });
}
