/* Native DOM lifecycle and late results with mocked HA; no network/device I/O. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {before, after, test} = require('node:test');
const {chromium} = require('playwright');
const source = fs.readFileSync(path.resolve(__dirname, '../../custom_components/welcomeeye_local/frontend/welcomeeye-card.js'), 'utf8');
const channel = process.env.WELCOMEEYE_BROWSER_CHANNELS?.split(',')[0];
let browser;
before(async () => {browser = await chromium.launch({headless:true,...(channel ? {channel} : {})});});
after(async () => {await browser?.close();});

async function fixture(t) {
  const context = await browser.newContext();
  const requests = [], errors = [];
  await context.route('**/*', route => {requests.push(route.request().url());return route.abort();});
  const page = await context.newPage();
  page.on('pageerror', error => errors.push(error.message));
  t.after(async () => {
    await context.close();
    assert.deepEqual(requests, []);
    assert.deepEqual(errors, []);
  });
  await page.setContent('<!doctype html><meta charset="utf-8"><div id="mount"></div>');
  await page.addScriptTag({content:source});
  await page.evaluate(() => {
    window.calls = [];
    window.settings = {channels:[{channel:1,entity_id:'camera.primary'},{channel:2,entity_id:'camera.secondary'}],outputs:{}};
    const capabilities = {camera:true,live_media:true,manual_snapshot:true};
    window.hass = {
      states:{'camera.primary':{state:'idle',attributes:{welcomeeye_player:true,welcomeeye_channel:1,welcomeeye_capabilities:capabilities}},
        'camera.secondary':{state:'idle',attributes:{welcomeeye_player:true,welcomeeye_channel:2,welcomeeye_capabilities:capabilities}}},
      connection:Object.assign(new EventTarget(),{connected:true}),
      callWS:async message => {
        window.calls.push(message);
        if (message.type==='call_service') return new Promise((resolve,reject)=>{window.finishPhoto=resolve;window.failPhoto=reject;});
        return window.settings;
      },
      callService:async () => {throw new Error('No physical command is permitted in this test');},
    };
    window.card = document.createElement('welcomeeye-card');
    card.hass = hass;card.setConfig({entity:'camera.primary'});
  });
  return page;
}

test('native DOM removal cancels metadata before its queued WebSocket call', async t => {
  const page = await fixture(t);
  const count = await page.evaluate(async () => {
    document.querySelector('#mount').append(card);
    card.remove();
    for (let index=0;index<12;++index) await Promise.resolve();
    return window.calls.length;
  });
  assert.equal(count, 0);
});

for (const failed of [false,true]) {
  test(`native camera switch ignores a late photo ${failed ? 'error' : 'success'} from the previous entry`, async t => {
    const page = await fixture(t);
    await page.evaluate(() => document.querySelector('#mount').append(card));
    await page.waitForFunction(() => card._channels?.length===2);
    const result = await page.evaluate(async failed => {
      const photo = card._snapshot();
      await card._selectChannel(2);
      const selectedStatus = card._status;
      if (failed) window.failPhoto(new Error('late photo failure'));
      else window.finishPhoto({response:{'camera.primary':{saved:true}}});
      await photo;
      return {entity:card._entity(),status:card._status,selectedStatus,busy:card._snapshotBusy,
        photoCalls:window.calls.filter(item=>item.type==='call_service').map(item=>item.target.entity_id)};
    }, failed);
    assert.equal(result.entity, 'camera.secondary');
    assert.equal(result.status, result.selectedStatus);
    assert.equal(result.busy, false);
    assert.deepEqual(result.photoCalls, ['camera.primary']);
  });
}
