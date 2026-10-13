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

for (const width of [360,800]) {
  test(`native ${width}px toolbar keeps authorized fixed-target commands on both views without empty grid cells`, async t => {
    const page = await fixture(t);
    await page.setViewportSize({width,height:900});
    await page.evaluate(() => {
      window.services=[];
      hass.callService=async (...args)=>{window.services.push(args);};
      settings.outputs={
        strike_1:{entity_id:'button.front_strike',channel:1,output:1},
        gate_1:{entity_id:'button.front_gate',channel:1,output:2},
        strike_2:{entity_id:'button.rear_strike',channel:2,output:1},
      };
      card.setConfig({entity:'camera.primary',channel_1_name:'Rue',channel_2_name:'Jardin',
        strike_1_name:'Maison',gate_1_name:'Garage',strike_2_name:'Bureau',gate_2_name:'Absent'});
      document.querySelector('#mount').append(card);
    });
    await page.waitForFunction(() => card._channels?.length===2);
    const result = await page.evaluate(async () => {
      const read=()=>{
        const toolbar=card.shadowRoot.querySelector('.toolbar'),bounds=toolbar.getBoundingClientRect();
        return Array.from(toolbar.querySelectorAll('button')).map(button=>{
          const box=button.getBoundingClientRect();
          return {name:button.textContent.trim(),hidden:button.hidden,width:box.width,
            contained:!box.width || (box.left>=bounds.left && box.right<=bounds.right+1)};
        });
      };
      const primary=read();await card._selectChannel(2);const secondary=read();
      await card._settingsRequest?.promise;
      await card.shadowRoot.querySelector('.gate').onclick();
      delete settings.outputs.strike_1;delete settings.outputs.strike_2;
      card._applySettings(settings,card._entity());const gateOnly=read();
      return {primary,secondary,gateOnly,entity:card._entity(),services:window.services,
        mediaCalls:window.calls.filter(item=>item.type==='welcomeeye_local/player_offer')};
    });
    assert.deepEqual(result.primary,result.secondary);
    assert.deepEqual(result.primary.filter(item=>!item.hidden).map(item=>item.name),['Maison','Garage','Bureau','Photo']);
    assert.equal(result.primary.find(item=>item.name==='Absent').width,0);
    assert.ok(result.primary.every(item=>item.contained));
    assert.deepEqual(result.gateOnly.filter(item=>!item.hidden).map(item=>item.name),['Garage','Photo']);
    assert.ok(result.gateOnly.filter(item=>item.hidden).every(item=>item.width===0));
    assert.equal(result.entity,'camera.secondary');
    assert.deepEqual(result.services,[['button','press',{entity_id:'button.front_gate'}]]);
    assert.deepEqual(result.mediaCalls,[]);
  });
}
