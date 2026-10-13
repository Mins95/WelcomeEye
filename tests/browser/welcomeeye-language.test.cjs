/* Actual card and editor DOM with independent presentation languages. */
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const {before,after,test}=require('node:test');
const {chromium}=require('playwright');
const source=fs.readFileSync(path.resolve(__dirname,'../../custom_components/welcomeeye_local/frontend/welcomeeye-card.js'),'utf8');
let browser;
before(async()=>{const channel=process.env.WELCOMEEYE_BROWSER_CHANNELS?.split(',')[0];browser=await chromium.launch({headless:true,...(channel?{channel}:{})});});
after(async()=>{await browser?.close();});
test('English and Spanish card and editor use the entry choice without changing HA or custom names',async t=>{
  const page=await browser.newPage();const errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  t.after(async()=>{await page.close();assert.deepEqual(errors,[]);});
  await page.setContent('<!doctype html><div id="mount"></div>');
  await page.addScriptTag({content:source});
  await page.evaluate(()=>{
    window.calls=[];
    window.hass={language:'fr',states:{'camera.front':{state:'idle',attributes:{
      welcomeeye_player:true,welcomeeye_language:'es',welcomeeye_multichannel_available:true,
      welcomeeye_capabilities:{camera:true,manual_snapshot:true,downstream_audio:true}}}},
      connection:{connected:true},callWS:async msg=>{calls.push(msg);return {outputs:{},channels:[
        {channel:1,entity_id:'camera.front'},{channel:2,entity_id:'camera.second'}],language:'es'};}};
    window.card=document.createElement('welcomeeye-card');card.hass=hass;card.setConfig({entity:'camera.front',channel_1_name:'Rue',strike_1_name:'Maison'});
    window.editor=document.createElement('welcomeeye-card-editor');editor.hass=hass;editor.setConfig({entity:'camera.front',channel_1_name:'Rue',strike_1_name:'Maison'});
    document.querySelector('#mount').append(card,editor);
  });
  await page.waitForFunction(()=>card.shadowRoot.querySelector('.sound span').textContent==='Sonido desactivado');
  assert.equal(await page.evaluate(()=>editor.shadowRoot.querySelector('.channel-1-name-row span').textContent),'Nombre de la entrada 1');
  assert.equal(await page.evaluate(()=>editor.shadowRoot.querySelector('.channel-1-name').value),'Rue');
  assert.equal(await page.evaluate(()=>card.shadowRoot.querySelector('.channel-1').textContent),'Rue');
  await page.evaluate(()=>{hass.states['camera.front'].attributes.welcomeeye_language='en';card.hass=hass;editor.hass=hass;});
  await page.waitForFunction(()=>card.shadowRoot.querySelector('.sound span').textContent==='Sound off');
  assert.equal(await page.evaluate(()=>editor.shadowRoot.querySelector('.channel-1-name-row span').textContent),'Entrance 1 name');
  assert.equal(await page.evaluate(()=>editor.shadowRoot.querySelector('.strike-1-name').value),'Maison');
  assert.equal(await page.evaluate(()=>hass.language),'fr');
  assert.equal(await page.evaluate(()=>calls.some(c=>c.type==='welcomeeye_local/player_offer')),false);
});
