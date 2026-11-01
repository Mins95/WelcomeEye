/* Real DOM and images, synthetic HA only. Every HTTP request is intercepted. */
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const {before,after,test}=require('node:test');
const {chromium}=require('playwright');
const source=fs.readFileSync(path.resolve(__dirname,'../../custom_components/welcomeeye_local/frontend/welcomeeye-card.js'),'utf8');
const channel=process.env.WELCOMEEYE_BROWSER_CHANNELS?.split(',')[0];
let browser;
before(async()=>{browser=await chromium.launch({headless:true,...(channel?{channel}:{})});});
after(async()=>{await browser?.close();});

async function fixture(t,config={}) {
  const context=await browser.newContext({viewport:{width:460,height:980}}),page=await context.newPage(),errors=[],requests=[];
  await context.route('**/*',async route=>{
    const url=new URL(route.request().url());requests.push(url.pathname);
    if(url.origin!=='https://ha.test') throw new Error('Unexpected destination');
    if(url.pathname==='/') return route.fulfill({contentType:'text/html',body:'<!doctype html><html lang="fr"><style>body{margin:16px;background:#e5e9ed;--primary-text-color:#202b34;--secondary-text-color:#54616e;--card-background-color:#fff;--secondary-background-color:#f3f5f8;--divider-color:#d6dde5;--primary-color:#177c77}#mount{max-width:430px;margin:auto}</style><div id="mount"></div></html>'});
    if(url.pathname.startsWith('/media/')) return route.fulfill({contentType:'image/svg+xml',body:'<svg xmlns="http://www.w3.org/2000/svg" width="640" height="480"><rect width="640" height="480" fill="#426e78"/><rect y="280" width="640" height="200" fill="#28474e"/><path d="M120 290V170L270 80L420 170V290" fill="#a3b9b3"/><rect x="240" y="190" width="65" height="100" fill="#355762"/><path d="M0 340H640M40 310V440M100 310V440M160 310V440M220 310V440M280 310V440M340 310V440M400 310V440M460 310V440M520 310V440M580 310V440" stroke="#dce8dc" stroke-width="5"/><text x="320" y="465" text-anchor="middle" fill="white" font-family="sans-serif" font-size="18">Image de démonstration</text></svg>'});
    throw new Error('No camera or external network request is permitted: '+url.pathname);
  });
  page.on('pageerror',error=>errors.push(error.message));
  t.after(async()=>{await context.close();assert.deepEqual(errors,[]);assert.ok(requests.every(p=>p==='/'||p.startsWith('/media/')));});
  await page.goto('https://ha.test/');await page.addScriptTag({content:source});
  await page.evaluate(config=>{
    window.calls=[];
    const caps={camera:true,live_media:true,downstream_audio:true,talkback:true,manual_snapshot:true,strike:true,gate:true};
    window.settings={photo_history_supported:true,images:{ring:'image.last_ring'},configuration:{},microphone_allowed:true,
      channels:[{entity_id:'camera.front',channel:1},{entity_id:'camera.side',channel:2}],
      outputs:{strike_1:{entity_id:'button.strike_1',channel:1,output:1},gate_1:{entity_id:'button.gate_1',channel:1,output:2},
        strike_2:{entity_id:'button.strike_2',channel:2,output:1},gate_2:{entity_id:'button.gate_2',channel:2,output:2}}};
    window.hass={language:'fr',user:{is_admin:true},states:{
      'camera.front':{state:'idle',attributes:{welcomeeye_player:true,welcomeeye_channel:1,welcomeeye_multichannel_available:true,welcomeeye_capabilities:caps}},
      'camera.side':{state:'idle',attributes:{welcomeeye_player:true,welcomeeye_channel:2,welcomeeye_multichannel_available:true,welcomeeye_capabilities:caps}},
      'image.last_ring':{state:'2026-01-01T12:00:00Z',attributes:{captured_at:'2026-01-01T12:00:00Z'}}},
      hassUrl:p=>'https://ha.test'+p,connection:Object.assign(new EventTarget(),{connected:true}),
      callService:async(...args)=>{calls.push({service:args});},
      callWS:async message=>{
        calls.push(message);
        if(message.type==='welcomeeye_local/player_config')return settings;
        if(message.type==='welcomeeye_local/player_photos') {
          const channel=message.entity_id==='camera.side'?2:1;
          return {photos:Array.from({length:3},(_,i)=>({id:String(i),name:`2026-01-01_12-00-0${i}_ring_1.jpg`,day:'2026-01-01',channel,
            media_content_id:`media-source://welcomeeye_local/local/WelcomeEye/${channel}/${i}.jpg`}))};
        }
        if(message.type==='media_source/resolve_media')return {url:'/media/local/WelcomeEye/photo.jpg?authSig=demo'};
        throw new Error('Unexpected action');
      }};
    window.card=document.createElement('welcomeeye-card');card.hass=hass;
    card.setConfig({entity:'camera.front',name:'WelcomeEye',channel_1_name:'Rue',channel_2_name:'Jardin',
      strike_1_name:'Portillon rue',gate_1_name:'Portail rue',strike_2_name:'Portillon jardin',gate_2_name:'Portail jardin',...config});
    document.querySelector('#mount').append(card);
  },config);
  await page.waitForFunction(()=>card._settingsEntity==='camera.front');
  return page;
}

test('saved poster and inline history never acquire media; all fixed outputs stay under both sources',async t=>{
  const page=await fixture(t);await page.waitForFunction(()=>!!card._posterUrl);
  assert.equal(await page.locator('welcomeeye-card .poster').isVisible(),true);
  assert.match(await page.locator('welcomeeye-card .poster-label').textContent(),/Rue/);
  await page.locator('welcomeeye-card .history').click();
  await page.waitForFunction(()=>!card._photoBusy && card.shadowRoot.querySelectorAll('.history-photo').length===3);
  await page.locator('welcomeeye-card .history-photo').first().click();
  assert.equal(await page.locator('welcomeeye-card .history-photo.expanded').count(),1);
  await page.locator('welcomeeye-card .channel-2').click();
  await page.waitForFunction(()=>card._entity()==='camera.side' && !!card._posterUrl);
  assert.match(await page.locator('welcomeeye-card .poster-label').textContent(),/Jardin/);
  assert.equal(await page.locator('welcomeeye-card .output-tools button:visible').count(),4);
  assert.equal(await page.evaluate(()=>calls.some(c=>c.service||c.type.includes('offer')||c.type==='call_service')),false);
  await page.locator('welcomeeye-card .gate').click();
  assert.deepEqual(await page.evaluate(()=>calls.filter(c=>c.service).map(c=>c.service)),[['button','press',{entity_id:'button.gate_1'}]]);
});

test('native graphical editor persists presentation options, independent toggles and custom labels',async t=>{
  const page=await fixture(t);
  await page.evaluate(()=>{
    window.editor=document.createElement('welcomeeye-card-editor');editor.hass=hass;
    editor.setConfig({...card._config});editor.addEventListener('config-changed',e=>{window.edited=e.detail.config;editor.setConfig(edited);});
    document.querySelector('#mount').replaceChildren(editor);
  });
  await page.locator('welcomeeye-card-editor .option-layout').selectOption('compact');
  await page.locator('welcomeeye-card-editor .option-appearance').selectOption('theme');
  await page.locator('welcomeeye-card-editor .option-auto_close').selectOption('60');
  await page.locator('welcomeeye-card-editor .option-autoplay').check();
  await page.locator('welcomeeye-card-editor .option-show_last_photo').uncheck();
  await page.locator('welcomeeye-card-editor .gate-2-name').fill('Garage');
  const edited=await page.evaluate(()=>edited);
  assert.equal(edited.layout,'compact');assert.equal(edited.appearance,'theme');assert.equal(edited.auto_close,60);
  assert.equal(edited.autoplay,true);assert.equal(edited.show_last_photo,false);assert.equal(edited.gate_2_name,'Garage');
  assert.match(await page.locator('welcomeeye-card-editor .independent-audio').textContent(),/indépendants/);
  assert.match(await page.locator('welcomeeye-card-editor .autoplay-warning').textContent(),/interrompre/);
  assert.equal(await page.evaluate(()=>calls.some(c=>c.service||c.type.includes('offer'))),false);
});

test('real intersection observer never autoplays in preview or after manual close',async t=>{
  const page=await fixture(t,{autoplay:false});
  await page.evaluate(()=>{
    window.opens=0;card._open=()=>{opens++;card._autoplayAttempted=true;};
    card.preview=true;card.setConfig({...card._config,autoplay:true});
  });
  await page.waitForFunction(()=>card._inView===true);
  assert.equal(await page.evaluate(()=>opens),0);
  await page.evaluate(()=>{card.preview=false;card._maybeAutoplay();});
  assert.equal(await page.evaluate(()=>opens),1);
  await page.evaluate(()=>{card._close();card._maybeAutoplay();card.hass=hass;});
  assert.equal(await page.evaluate(()=>opens),1);
});

test('mobile layouts stay inside the card, render safe names and produce optional local previews',async t=>{
  const page=await fixture(t);await page.waitForFunction(()=>!!card._posterUrl);
  const output=process.env.WELCOMEEYE_PREVIEW_OUTPUT;
  if(output)fs.mkdirSync(output,{recursive:true});
  for(const layout of ['full','compact']) {
    await page.evaluate(layout=>card.setConfig({...card._config,layout,appearance:layout==='compact'?'theme':'welcomeeye'}),layout);
    await page.setViewportSize({width:390,height:980});
    const fits=await page.evaluate(()=>{
      const cardBox=card.getBoundingClientRect();
      return [...card.shadowRoot.querySelectorAll('.toolbar button')].filter(b=>!b.hidden).every(b=>b.getBoundingClientRect().right<=cardBox.right);
    });
    assert.equal(fits,true);
    if(output)await page.locator('welcomeeye-card').screenshot({path:path.join(output,`card-${layout}.png`)});
  }
  await page.evaluate(()=>card.setConfig({...card._config,gate_1_name:'<img src=x onerror=alert(1)>'}));
  assert.match(await page.locator('welcomeeye-card .gate span').textContent(),/<img/);
  assert.equal(await page.locator('welcomeeye-card .gate img').count(),0);
});
