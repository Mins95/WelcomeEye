/* Actual browser custom panel with synthetic HA replies only. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {before, after, test} = require('node:test');
const {chromium} = require('playwright');
const source = fs.readFileSync(path.resolve(__dirname,'../../custom_components/welcomeeye_local/frontend/welcomeeye-photos.js'),'utf8');
let browser;
const channel = process.env.WELCOMEEYE_BROWSER_CHANNELS?.split(',')[0];
before(async()=>{browser=await chromium.launch({headless:true,...(channel ? {channel} : {})});});
after(async()=>{await browser?.close();});

async function fixture(t, {admin=true}={}) {
  const page=await browser.newPage(); const errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  t.after(async()=>{await page.close();assert.deepEqual(errors,[]);});
  await page.setContent('<!doctype html><div id="mount"></div>');
  await page.addScriptTag({content:source});
  await page.evaluate(admin=>{
    window.calls=[]; window.rows=[1,2,3].map(n=>({id:String(n).padStart(64,'0'),name:`photo_${n}.jpg`,channel:n===3?2:1,day:'2026-10-13',media_content_id:`media-source://welcomeeye_local/synthetic/${n}`}));
    window.hass={language:'fr',user:{is_admin:admin},connection:{},hassUrl:x=>x,
      callWS:async msg=>{
        calls.push(msg);
        if(msg.type==='media_source/resolve_media') return {url:'data:image/gif;base64,R0lGODlhAQABAAD/ACwAAAAAAQABAAACADs='};
        if(msg.operation==='devices') return [{entry_id:'owner',name:'Appareil <script>test</script>'}];
        if(msg.operation==='list') return {days:rows.length?[{day:'2026-10-13',count:rows.length}]:[],photos:msg.day?rows:[],count:rows.length,retention_days:0};
        if(msg.operation==='prepare') {window.pending=msg.scope==='selection'?rows.filter(p=>msg.ids.includes(p.id)):rows.slice();return {token:'private-confirmation',count:pending.length};}
        if(msg.operation==='delete') {rows=rows.filter(p=>!pending.some(d=>d.id===p.id));return {requested:pending.length,deleted:pending.length,missing:0,failed:0};}
        if(msg.operation==='cancel') return {cancelled:true};
        throw new Error('Unexpected operation');
      }};
    window.panel=document.createElement('welcomeeye-photos');panel.hass=hass;document.querySelector('#mount').append(panel);
  },admin);
  if(admin) await page.waitForFunction(()=>panel.photos.length===3&&!panel.busy);
  return page;
}

test('photo panel selects multiple, confirms exact count and refreshes immediately',async t=>{
  const page=await fixture(t); let text;
  page.on('dialog',async dialog=>{text=dialog.message();await dialog.accept();});
  await page.getByRole('checkbox').first().check();
  await page.getByRole('checkbox').nth(1).check();
  assert.equal(await page.evaluate(()=>panel.selected.size),2);
  await page.getByRole('button',{name:'Supprimer la sélection',exact:true}).click();
  await page.waitForFunction(()=>!panel.busy&&panel.photos.length===1);
  assert.match(text,/2 photo/);
  assert.equal(await page.evaluate(()=>calls.filter(c=>c.operation==='delete').length),1);
  assert.equal(await page.evaluate(()=>panel.selected.size),0);
  assert.equal(await page.locator('script').count(),1,'custom names are rendered as text');
});

test('cancelled confirmation never deletes; device and day actions each ask',async t=>{
  const page=await fixture(t);
  page.on('dialog',async dialog=>{await dialog.dismiss();});
  await page.getByRole('button',{name:'Supprimer toutes les photos de cet appareil'}).click();
  await page.waitForFunction(()=>!panel.busy&&calls.some(c=>c.operation==='cancel'));
  assert.equal(await page.evaluate(()=>calls.some(c=>c.operation==='delete')),false);
  await page.getByRole('button',{name:'Supprimer cette journée',exact:true}).click();
  await page.waitForFunction(()=>!panel.busy&&calls.filter(c=>c.operation==='cancel').length===2);
  assert.deepEqual(await page.evaluate(()=>calls.filter(c=>c.operation==='prepare').map(c=>c.scope)),['device','day']);
});

test('select all selects the current day; expired confirmation does not retry',async t=>{
  const page=await fixture(t); page.on('dialog',d=>d.accept());
  await page.getByRole('button',{name:'Tout sélectionner',exact:true}).click();
  assert.equal(await page.evaluate(()=>panel.selected.size),3);
  await page.evaluate(()=>{const old=hass.callWS;hass.callWS=async msg=>{if(msg.operation==='delete'){calls.push(msg);throw {code:'confirmation_invalid_or_expired'};}return old(msg);};});
  await page.getByRole('button',{name:'Supprimer la sélection',exact:true}).click();
  await page.waitForFunction(()=>!panel.busy&&panel.message.includes('expiré'));
  assert.equal(await page.evaluate(()=>calls.filter(c=>c.operation==='delete').length),1);
  assert.equal(await page.evaluate(()=>rows.length),3);
});

test('non-admin has no photo requests and no delete controls',async t=>{
  const page=await fixture(t,{admin:false});
  assert.equal(await page.getByRole('button',{name:/Supprimer/}).count(),0);
  assert.equal(await page.evaluate(()=>calls.length),0);
});

test('old empty folders remain until the user confirms cleanup and refresh immediately',async t=>{
  const page=await fixture(t); let dialogs=0;
  await page.evaluate(async()=>{
    rows=[]; window.emptyFolders=2;
    const old=hass.callWS;
    hass.callWS=async msg=>{
      if(msg.operation==='list'){calls.push(msg);return {days:[],photos:[],count:0,retention_days:0,empty_directories:emptyFolders};}
      if(msg.operation==='prepare'){calls.push(msg);if(msg.scope!=='empty')throw Error('Cleanup must be folder-only');return {token:'cleanup',count:0,empty_directories:emptyFolders};}
      if(msg.operation==='delete'){calls.push(msg);emptyFolders=0;return {deleted:0,missing:0,failed:0,directories_removed:3,directory_errors:0};}
      return old(msg);
    };
    await panel.refresh();
  });
  assert.equal(await page.evaluate(()=>emptyFolders),2);
  page.on('dialog',async dialog=>{
    dialogs++; assert.match(dialog.message(),/2 dossier/);
    dialogs===1 ? await dialog.dismiss() : await dialog.accept();
  });
  await page.getByRole('button',{name:'Nettoyer les dossiers vides',exact:true}).click();
  await page.waitForFunction(()=>!panel.busy);
  assert.equal(await page.evaluate(()=>emptyFolders),2);
  assert.equal(await page.evaluate(()=>calls.some(c=>c.operation==='delete')),false);
  await page.getByRole('button',{name:'Nettoyer les dossiers vides',exact:true}).click();
  await page.waitForFunction(()=>!panel.busy&&panel.emptyDirectories===0);
  assert.equal(await page.evaluate(()=>calls.filter(c=>c.operation==='delete').length),1);
  assert.equal(await page.getByRole('button',{name:'Nettoyer les dossiers vides',exact:true}).isDisabled(),true);
});

test('cleanup is visible with remaining photos and never prepares JPEG deletion',async t=>{
  const page=await fixture(t); let confirmed;
  await page.evaluate(async()=>{
    window.emptyFolders=2;
    const old=hass.callWS;
    hass.callWS=async msg=>{
      if(msg.operation==='list'){const result=await old(msg);return {...result,empty_directories:emptyFolders};}
      if(msg.operation==='prepare'){
        calls.push(msg);
        if(msg.scope!=='empty')throw Error('Cleanup must be folder-only');
        return {token:'empty-only',count:0,empty_directories:emptyFolders};
      }
      if(msg.operation==='delete'){calls.push(msg);emptyFolders=0;return {deleted:0,missing:0,failed:0,directories_removed:2,directory_errors:0};}
      return old(msg);
    };
    await panel.refresh();
  });
  assert.equal(await page.getByRole('button',{name:'Nettoyer les dossiers vides',exact:true}).isEnabled(),true);
  assert.equal(await page.getByRole('button',{name:'Supprimer toutes les photos de cet appareil',exact:true}).isEnabled(),true);
  page.on('dialog',async dialog=>{confirmed=dialog.message();await dialog.accept();});
  await page.getByRole('button',{name:'Nettoyer les dossiers vides',exact:true}).click();
  await page.waitForFunction(()=>!panel.busy&&panel.emptyDirectories===0);
  assert.match(confirmed,/2 dossier/);
  assert.doesNotMatch(confirmed,/photo/);
  assert.equal(await page.evaluate(()=>rows.length),3);
  assert.equal(await page.evaluate(()=>panel.photos.length),3);
  assert.deepEqual(await page.evaluate(()=>calls.filter(c=>c.operation==='prepare').map(c=>c.scope)),['empty']);
  assert.equal(await page.evaluate(()=>calls.filter(c=>c.operation==='delete').length),1);
});

test('large day scrolls inside the HA viewport and pages reset scroll',async t=>{
  const page=await fixture(t);
  await page.evaluate(async()=>{
    document.querySelector('#mount').style.cssText='height:400px;overflow:hidden';
    rows=Array.from({length:100},(_,n)=>({...rows[0],id:String(n).padStart(64,'0'),name:`photo_${n}.jpg`}));
    await panel.refresh();
  });
  assert.equal(await page.evaluate(()=>panel.clientHeight),400);
  assert.equal(await page.evaluate(()=>panel.scrollHeight>panel.clientHeight),true);
  await page.getByRole('button',{name:'Suivant',exact:true}).click();
  assert.equal(await page.evaluate(()=>panel.page),1);
  assert.equal(await page.evaluate(()=>panel.scrollTop),0);
});

test('removing a panel before prepare returns prevents a late deletion',async t=>{
  const page=await fixture(t);
  await page.evaluate(()=>{const old=hass.callWS;hass.callWS=msg=>msg.operation==='prepare'?new Promise(resolve=>window.finish=()=>resolve({token:'late',count:3})):old(msg);panel.deletePhotos('device');});
  await page.waitForFunction(()=>typeof window.finish==='function');
  await page.evaluate(()=>{panel.parentNode.removeChild(panel);finish();});
  await page.waitForTimeout(30);
  assert.equal(await page.evaluate(()=>calls.some(c=>c.operation==='delete')),false);
});
