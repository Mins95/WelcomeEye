#!/usr/bin/env node
/* Real-browser editor regression. Uses a local HA fixture; all network is denied.
 * Run with an installed Playwright and browser, for example:
 * node tools/verify_card_editor_browser.cjs --playwright /path/to/playwright \
 *   --channel msedge --output /path/to/artifacts --baseline v0.4.4-rc.1
 * This checks actual browser inputs and layout, not a live HA installation.
 */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {execFileSync} = require('node:child_process');

function option(name, fallback) {
  const index = process.argv.indexOf('--'+name);
  if (index === -1) return fallback;
  if (!process.argv[index+1] || process.argv[index+1].startsWith('--')) throw new Error('Missing --'+name+' value');
  return process.argv[index+1];
}
const root = path.resolve(__dirname, '..');
const output = path.resolve(option('output', path.join(root, 'artifacts', 'card-editor-browser')));
const baseline = option('baseline', 'v0.4.4-rc.1');
const relative = 'custom_components/welcomeeye_local/frontend/welcomeeye-card.js';
const source = fs.readFileSync(path.join(root, relative), 'utf8');
const baselineSource = execFileSync('git', ['show', baseline+':'+relative], {cwd:root,encoding:'utf8'});
const {chromium} = require(option('playwright', 'playwright'));
const channel = option('channel', undefined);
const fields = ['channel_1_name','channel_2_name','strike_1_name','strike_2_name','gate_1_name','gate_2_name'];
const config = {type:'custom:welcomeeye-card',entity:'camera.connect2',name:'WelcomeEye Connect 2',
  channel_1_name:'Rue',channel_2_name:'Jardin',strike_1_name:'Portillon maison',
  strike_2_name:'Portillon jardin',gate_1_name:'Grand portail',gate_2_name:'Portail garage'};
const report = {baseline,fixture:'Native browser DOM, simulated HA state; no device or live HA',checks:[],screenshots:[]};
const check = (name, detail) => report.checks.push({name,passed:true,...(detail || {})});

async function fixture(browser, script, heading) {
  const context = await browser.newContext({viewport:{width:1240,height:1100},deviceScaleFactor:1});
  const attemptedRequests = [];
  await context.route('**/*', route => {attemptedRequests.push(route.request().url());return route.abort();});
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror',error=>errors.push(error.message));
  await page.setContent(`<!doctype html><html lang="fr"><head><meta charset="utf-8"><style>
    :root{--primary-text-color:#20252b;--secondary-text-color:#596875;--primary-color:#027fa9;--divider-color:#aab5be;--secondary-background-color:#f4f7f9;--primary-font-family:Arial,system-ui}
    body{margin:0;padding:32px;background:#eaf0f4;font-family:Arial,system-ui;color:#20252b}
    h1{font-size:24px;margin:0 0 8px}.fixture-note{margin:0 0 24px;color:#596875;font-size:14px}
    main{display:grid;grid-template-columns:1fr 1fr;gap:24px;align-items:start}
    section{padding:24px;border-radius:14px;background:white;box-shadow:0 2px 12px #0001}
    h2{font-size:17px;margin:0 0 22px}
  </style></head><body><h1></h1><p class="fixture-note">Vérification locale dans un navigateur · aucun appareil connecté</p>
  <main><section><h2>Modifier la carte</h2><div id="editor"></div></section><section><h2>Aperçu de la carte</h2><div id="preview"></div></section></main></body></html>`);
  await page.locator('h1').evaluate((node,text)=>{node.textContent=text;},heading);
  await page.evaluate(() => {
    // Only the unrelated entity picker is a stub. Name controls are production
    // card DOM, and no HA text-input custom element is registered.
    customElements.define('ha-entity-picker', class extends HTMLElement {
      constructor() {
        super();this.attachShadow({mode:'open'}).innerHTML='<style>:host{display:block;margin-bottom:16px}label{display:grid;gap:8px;font:14px Arial}input{box-sizing:border-box;width:100%;height:48px;padding:12px;border:1px solid #aab5be;border-radius:8px;background:#f4f7f9;color:#20252b}</style><label>Caméra WelcomeEye<input readonly></label>';
      }
      set value(value) {this.shadowRoot.querySelector('input').value=value || '';}
    });
    window.actions=[];
    window.settings={channels:[{channel:1,entity_id:'camera.connect2',label:'Entrée 1'},{channel:2,entity_id:'camera.secondary',label:'Entrée 2'}],
      outputs:{strike_1:{entity_id:'button.strike1',channel:1,output:1,validation_status:'existing'},gate_1:{entity_id:'button.gate1',channel:1,output:2,validation_status:'existing'},
        strike_2:{entity_id:'button.strike2',channel:2,output:1,validation_status:'existing'},gate_2:{entity_id:'button.gate2',channel:2,output:2,validation_status:'existing'}}};
    const attributes={welcomeeye_player:true,welcomeeye_channel:1,welcomeeye_multichannel_available:true,
      welcomeeye_capabilities:{camera:true,live_media:true,downstream_audio:true,talkback:true,strike:true,gate:true,manual_snapshot:true}};
    window.hass={states:{'camera.connect2':{state:'idle',attributes},'camera.secondary':{state:'idle',attributes:{...attributes,welcomeeye_channel:2}}},
      callWS:async message=>{window.actions.push(message);return window.settings;},
      callService:async(...args)=>{window.actions.push(args);throw new Error('No action may be invoked by an editor test');},
      connection:Object.assign(new EventTarget(),{connected:true})};
  });
  await page.addScriptTag({content:script});
  await page.evaluate(initial => {
    window.card=document.createElement('welcomeeye-card');card.hass=hass;card.setConfig(initial);document.querySelector('#preview').append(card);
    window.mountEditor=saved => {
      const editor=document.createElement('welcomeeye-card-editor');editor.hass=hass;editor.setConfig(saved);
      editor.addEventListener('config-changed',event=>{
        window.savedConfig=JSON.parse(JSON.stringify(event.detail.config));
        editor.setConfig(window.savedConfig);card.setConfig(window.savedConfig);
      });
      document.querySelector('#editor').replaceChildren(editor);window.editor=editor;return editor;
    };
    window.savedConfig=JSON.parse(JSON.stringify(initial));mountEditor(initial);
  },config);
  return {context,page,attemptedRequests,errors};
}
async function screenshot(page, name) {
  const destination=path.join(output,name);
  await page.screenshot({path:destination,fullPage:true});report.screenshots.push(destination);
}

(async()=>{
  fs.mkdirSync(output,{recursive:true});
  const browser=await chromium.launch({headless:true,...(channel ? {channel} : {})});
  report.browser=browser.version();
  try {
    const old=await fixture(browser,baselineSource,'Avant · '+baseline+' · champs de noms absents');
    const before=await old.page.evaluate(()=>({
      customTextfieldRegistered:!!customElements.get('ha-textfield'),
      textfieldCount:editor.shadowRoot.querySelectorAll('ha-textfield').length,
      nativeNameInputs:editor.shadowRoot.querySelectorAll('input[type="text"]').length,
      renderedTextfields:[...editor.shadowRoot.querySelectorAll('ha-textfield')].filter(node=>node.getBoundingClientRect().width>0 && node.getBoundingClientRect().height>0).length
    }));
    assert.equal(before.customTextfieldRegistered,false);assert.equal(before.textfieldCount,6);
    assert.equal(before.nativeNameInputs,0);assert.equal(before.renderedTextfields,0);
    check('Released baseline reproduces missing editable name fields',before);
    await screenshot(old.page,'01-before-missing-name-fields.png');
    assert.deepEqual(old.errors,[]);assert.deepEqual(old.attemptedRequests,[]);await old.context.close();

    const fixed=await fixture(browser,source,'Après · champs de noms accessibles');
    const page=fixed.page;
    assert.equal(await page.locator('welcomeeye-card-editor input[type="text"]').count(),6);
    for (const label of ['Nom entrée 1','Nom entrée 2','Nom portillon 1','Nom portillon 2','Nom portail 1','Nom portail 2']) {
      const control=page.getByRole('textbox',{name:label,exact:true});
      assert.equal(await control.count(),1);assert.equal(await control.isVisible(),true);
      assert.equal(await control.isEditable(),true);
    }
    check('All six name fields are visible, editable and accessible by their native label');
    const edits={channel_1_name:'Accès rue',channel_2_name:'Accès jardin',strike_1_name:'Portillon côté rue',strike_2_name:'Portillon côté jardin',gate_1_name:'Portail voitures',gate_2_name:'Portail arrière'};
    for (const [key,value] of Object.entries(edits)) await page.locator('welcomeeye-card-editor .'+key.replaceAll('_','-')).fill(value);
    const afterEditing=await page.evaluate(()=>window.savedConfig);
    for (const [key,value] of Object.entries(edits)) assert.equal(afterEditing[key],value);
    assert.equal(afterEditing.entity,config.entity);assert.equal(afterEditing.name,config.name);assert.equal(afterEditing.type,config.type);
    const labels=await page.evaluate(()=>({
      channel1:card.shadowRoot.querySelector('.channel-1').textContent,channel2:card.shadowRoot.querySelector('.channel-2').textContent,
      strike1:card.shadowRoot.querySelector('.strike span').textContent,strike2:card.shadowRoot.querySelector('.strike-2 span').textContent,
      gate1:card.shadowRoot.querySelector('.gate span').textContent,gate2:card.shadowRoot.querySelector('.gate-2 span').textContent
    }));
    assert.deepEqual(labels,{channel1:edits.channel_1_name,channel2:edits.channel_2_name,strike1:edits.strike_1_name,strike2:edits.strike_2_name,gate1:edits.gate_1_name,gate2:edits.gate_2_name});
    check('Edits persist and render as six independent names without changing camera or title');

    const focus=await page.evaluate(()=>{
      const input=editor.shadowRoot.querySelector('.strike-1-name');input.focus();input.setSelectionRange(4,9);
      editor.setConfig({...savedConfig});
      return {sameNode:input===editor.shadowRoot.querySelector('.strike-1-name'),focused:editor.shadowRoot.activeElement===input,start:input.selectionStart,end:input.selectionEnd};
    });
    assert.deepEqual(focus,{sameNode:true,focused:true,start:4,end:9});check('Config echo preserves focused input and selection',focus);
    const lengthInput=page.getByRole('textbox',{name:'Nom portail 2',exact:true});
    await lengthInput.fill('');await lengthInput.pressSequentially('x'.repeat(70));
    assert.equal((await lengthInput.inputValue()).length,64);
    assert.equal(await page.evaluate(()=>savedConfig.gate_2_name.length),64);
    check('Browser typing enforces the 64-character limit');await lengthInput.fill(edits.gate_2_name);

    const unsafe='<img src=x onerror=alert(1)>';
    await page.getByRole('textbox',{name:'Nom portillon 2',exact:true}).fill(unsafe);
    assert.equal(await page.locator('welcomeeye-card .strike-2 span').textContent(),unsafe);
    assert.equal(await page.locator('welcomeeye-card img').count(),0);
    check('Markup-like names remain plain text');await page.getByRole('textbox',{name:'Nom portillon 2',exact:true}).fill(edits.strike_2_name);
    await screenshot(page,'02-after-six-editable-name-fields.png');

    await page.evaluate(()=>{hass.states['camera.connect2'].attributes.welcomeeye_multichannel_available=false;editor.hass=hass;});
    for (const key of fields.filter(key=>key.includes('_2_'))) assert.equal(await page.locator('welcomeeye-card-editor .'+key.replaceAll('_','-')+'-row').isVisible(),false);
    for (const key of fields.filter(key=>key.includes('_1_'))) assert.equal(await page.locator('welcomeeye-card-editor .'+key.replaceAll('_','-')).isVisible(),true);
    await page.getByRole('textbox',{name:'Nom portillon 1',exact:true}).fill('Portillon renommé');
    for (const key of fields.filter(key=>key.includes('_2_'))) assert.equal(await page.evaluate(key=>savedConfig[key],key),edits[key]);
    await screenshot(page,'03-single-panel-secondary-labels-hidden.png');
    await page.evaluate(()=>{hass.states['camera.connect2'].attributes.welcomeeye_multichannel_available=true;mountEditor(JSON.parse(JSON.stringify(savedConfig)));});
    for (const key of fields.filter(key=>key.includes('_2_'))) {
      const field=page.locator('welcomeeye-card-editor .'+key.replaceAll('_','-'));
      assert.equal(await field.isVisible(),true);assert.equal(await field.inputValue(),edits[key]);
    }
    assert.equal(await page.getByRole('textbox',{name:'Nom portillon 1',exact:true}).inputValue(),'Portillon renommé');
    check('Secondary labels and inputs hide together; hidden names survive edits and editor recreation');
    const actions=await page.evaluate(()=>actions);
    assert.equal(actions.every(action=>!Array.isArray(action) && action.type==='welcomeeye_local/player_config'),true);
    assert.deepEqual(fixed.errors,[]);assert.deepEqual(fixed.attemptedRequests,[]);
    check('No browser errors, network requests, media requests or physical service calls',{metadataFixtureCalls:actions.length});
    await fixed.context.close();
  } finally {await browser.close();}
  fs.writeFileSync(path.join(output,'browser-verification.json'),JSON.stringify(report,null,2)+'\n');
  process.stdout.write(JSON.stringify(report,null,2)+'\n');
})().catch(error=>{console.error(error);process.exitCode=1;});
