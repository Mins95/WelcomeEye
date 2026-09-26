/* Dependency-free card tests. All peers, media, services and timers are mocks. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../../custom_components/welcomeeye_local/frontend/welcomeeye-card.js'), 'utf8');
const deferred = () => {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve=yes; reject=no; });
  return {promise, resolve, reject};
};
const tick = async () => { for (let i=0; i<8; ++i) await Promise.resolve(); };

class Events {
  constructor() { this.listeners = new Map(); }
  addEventListener(type, listener) {
    if (!this.listeners.has(type)) this.listeners.set(type, new Set());
    this.listeners.get(type).add(listener);
  }
  removeEventListener(type, listener) { this.listeners.get(type)?.delete(listener); }
  dispatch(type, event={}) {
    for (const listener of this.listeners.get(type) || []) listener(event);
    this['on'+type]?.(event);
  }
}
class Element extends Events {
  constructor() {
    super(); this.isConnected=true; this.muted=true;
    this.attrs={}; this.children=[];
    this.classList={toggle() {}};
  }
  attachShadow() { this.shadowRoot=new Shadow(); }
  setAttribute(key, value) { this.attrs[key]=value; }
  play() { return Promise.resolve(); }
  prepend(element) { this.children.unshift(element); }
  remove() { this.removed=true; }
}
class Shadow {
  constructor() { this.nodes=new Map(); }
  querySelector(selector) {
    if (!this.nodes.has(selector)) this.nodes.set(selector, new Element());
    return this.nodes.get(selector);
  }
}
class Stream {
  constructor(tracks=[]) { this.tracks=tracks; }
  addTrack(track) { this.tracks.push(track); }
  getTracks() { return this.tracks; }
  getAudioTracks() { return this.tracks; }
}
const track = () => ({enabled:true, stopped:false, stop() {this.stopped=true;}});

function harness({webrtc=true, hls=true}={}) {
  const timers=new Map(), registered=new Map(), peers=[];
  let timerId=0, unsubscribeCount=0;
  const settings={configuration:{},microphone_allowed:true,buttons:{strike:'button.strike',gate:'button.gate'}};
  class Peer extends Events {
    constructor() {
      super(); this.connectionState='new'; this.iceGatheringState='complete';
      this.sender={track:null, replaceTrack: async value => {this.sender.track=value;}};
      peers.push(this);
    }
    addTransceiver() { return {sender:this.sender}; }
    createDataChannel() {
      this.channel={readyState:'open',sent:[],send(value) {this.sent.push(JSON.parse(value));}};
      return this.channel;
    }
    async createOffer() { return {type:'offer',sdp:'mock'}; }
    async setLocalDescription(description) { this.localDescription=description; }
    async setRemoteDescription(description) { this.remoteDescription=description; }
    close() { this.closed=true; this.connectionState='closed'; }
    state(value) { this.connectionState=value; this.dispatch('connectionstatechange'); }
  }
  const document = Object.assign(new Events(), {hidden:false,createElement:()=>new Element()});
  const window = Object.assign(new Events(), {isSecureContext:true});
  const navigator={mediaDevices:{getUserMedia:async()=>new Stream([track()])}};
  if (hls) registered.set('ha-hls-player', Element);
  const customElements={
    get:name=>registered.get(name),
    define(name, value) { if (registered.has(name)) throw new Error('Duplicate element'); registered.set(name,value); },
    whenDefined:name=>registered.has(name) ? Promise.resolve() : new Promise(()=>{})
  };
  const context=vm.createContext({
    HTMLElement:Element,document,window,navigator,customElements,MediaStream:Stream,
    ...(webrtc ? {RTCPeerConnection:Peer} : {}),
    setTimeout:(callback,ms)=>{timers.set(++timerId,{callback,ms});return timerId;},
    clearTimeout:id=>timers.delete(id),
    setInterval:(callback,ms)=>{timers.set(++timerId,{callback,ms,interval:true});return timerId;},
    clearInterval:id=>timers.delete(id)
  });
  vm.runInContext(source,context);
  const Card=registered.get('welcomeeye-card');
  const card=new Card();
  const calls=[];
  const hass={
    states:{
      'camera.front':{state:'idle',attributes:{welcomeeye_player:true,ring_image_capture_entity_id:'switch.ring_photo'}},
      'switch.ring_photo':{state:'off',attributes:{}}
    },
    callWS:async message=>{calls.push(message);return settings;},
    callService:async (...args)=>{calls.push(args);},
    connection:{subscribeMessage:async callback=>{
      hass.offerCallback=callback;
      return ()=>{unsubscribeCount++;};
    }}
  };
  card.hass=hass;
  card.setConfig({entity:'camera.front'});
  card.connectedCallback();
  return {card,hass,calls,settings,timers,peers,context,document,window,navigator,registered,
    q:selector=>card.shadowRoot.querySelector(selector),
    get unsubscribeCount() {return unsubscribeCount;},
    async live() {await card._open();peers[0].state('connected');return peers[0];},
    fire(ms) {
      for (const [id,timer] of [...timers]) if (timer.ms===ms) {
        if (!timer.interval) timers.delete(id);
        timer.callback();
      }
    }
  };
}

test('config validation and duplicate resource loading preserve element registration',()=>{
  const h=harness();
  for (const config of [null, {}, {entity:7}, {entity:'light.front'}, {entity:'camera.'}]) {
    assert.throws(()=>h.card.setConfig(config),/caméra WelcomeEye/);
  }
  const original=h.registered.get('welcomeeye-card');
  vm.runInContext(source,h.context);
  assert.equal(h.registered.get('welcomeeye-card'),original);
  assert.equal(h.window.customCards.length,1);
});

test('missing, unknown and unavailable cameras disable open and snapshot actions',async()=>{
  const h=harness();
  for (const state of ['unknown','unavailable',null]) {
    if (state) h.hass.states['camera.front'].state=state;
    else delete h.hass.states['camera.front'];
    h.card.hass=h.hass;
    assert.equal(h.q('.open').disabled,true);
    assert.equal(h.q('.snapshot').disabled,true);
    await h.card._open(); await h.card._snapshot();
  }
  assert.equal(h.calls.length,0);
});

test('snapshot targets the backend once and preserves live media, audio and microphone',async()=>{
  const h=harness(), peer=await h.live(), wait=deferred();
  const local=new Stream([track()]);
  h.card._local=local;h.card._mic=true;h.card._video.muted=false;
  h.hass.callWS=message=>{h.calls.push(message);return wait.promise;};
  const capturing=h.card._snapshot();
  await h.card._snapshot();
  assert.equal(h.q('.snapshot').disabled,true);
  const call=h.calls.at(-1);
  assert.equal(call.type,'call_service');
  assert.equal(call.service,'capture_snapshot');
  assert.equal(call.target.entity_id,'camera.front');
  assert.equal(call.service_data.save_to_media,true);
  assert.equal(call.return_response,true);
  assert.equal(h.calls.filter(c=>c.service==='capture_snapshot').length,1);
  wait.resolve({response:{'camera.front':{saved:true,media_content_id:'media-source://test'}}});
  await capturing;
  assert.equal(h.card._status,'Photo enregistrée');
  assert.equal(h.card._pc,peer);
  assert.equal(peer.closed,undefined);
  assert.equal(h.card._mic,true);
  assert.equal(h.card._local,local);
  assert.equal(h.card._video.muted,false);
  h.card._close();
});

test('closed viewer snapshot reports partial disk failure and authorization errors precisely',async()=>{
  const h=harness();
  h.hass.callWS=async()=>({response:{'camera.front':{saved:false,save_error:'PermissionError'}}});
  await h.card._snapshot();
  assert.match(h.card._status,/Photo capturée.*Médias impossible.*PermissionError/);
  assert.equal(h.card._error,true);
  assert.equal(h.peers.length,0);
  h.hass.callWS=async()=>{throw {code:'unauthorized'};};
  await h.card._snapshot();
  assert.match(h.card._status,/autorisation Home Assistant insuffisante/);
  assert.equal(h.card._snapshotBusy,false);
});

test('snapshot never claims a saved image without a saved response',async()=>{
  const h=harness();
  h.hass.callWS=async()=>({});
  await h.card._snapshot();
  assert.match(h.card._status,/non confirmée/);
  const wait=deferred();
  h.hass.callWS=()=>wait.promise;
  const saving=h.card._snapshot();
  h.card.setConfig({entity:'camera.other'});
  const message=h.card._status;
  wait.resolve({response:{'camera.front':{saved:true}}});
  await saving;
  assert.equal(h.card._status,message);
});

test('automatic capture remains outside the card while manual Photo stays available',()=>{
  const h=harness();
  assert.doesNotMatch(h.card.shadowRoot.innerHTML,/auto-photo|Auto photo/);
  assert.match(h.card.shadowRoot.innerHTML,/action snapshot/);
  h.hass.states['switch.ring_photo'].state='on';h.card.hass=h.hass;
  assert.equal(h.calls.length,0);
  assert.equal(h.q('.snapshot').disabled,false);
});

test('pending open is single flight and close prevents a late viewer from being created',async()=>{
  const h=harness(), wait=deferred();let calls=0;
  h.hass.callWS=()=>{calls++;return wait.promise;};
  const opening=h.card._open();await h.card._open();
  assert.equal(calls,1);assert.equal(h.q('.open').hidden,true);
  h.card._close();wait.resolve(h.settings);await opening;
  assert.equal(h.peers.length,0);assert.equal(h.card._opening,false);
});

test('pending ICE wait and listener are released immediately on close',async()=>{
  const h=harness();
  h.context.RTCPeerConnection.prototype.createOffer=async function() {
    this.iceGatheringState='gathering';return {type:'offer',sdp:'mock'};
  };
  const opening=h.card._open();await tick();
  const peer=h.peers[0];
  assert.equal(peer.listeners.get('icegatheringstatechange').size,1);
  h.card._close();await opening;
  assert.equal(h.timers.size,0);
  assert.equal(peer.listeners.get('icegatheringstatechange').size,0);
  assert.equal(peer.closed,true);
  assert.equal(h.hass.offerCallback,undefined);
});

test('late subscription cleanup is handled even when unsubscribe rejects',async()=>{
  const h=harness(), wait=deferred(), entered=deferred();let unsubscribed=0;
  h.hass.connection.subscribeMessage=()=>{entered.resolve();return wait.promise;};
  const opening=h.card._open();await entered.promise;h.card._close();
  wait.resolve(()=>{unsubscribed++;return Promise.reject(new Error('connection already gone'));});
  await opening;await tick();
  assert.equal(unsubscribed,1);assert.equal(h.card._pc,null);
});

test('microphone permission refusal releases pending state and sends no enable command',async()=>{
  const h=harness(), peer=await h.live();
  h.navigator.mediaDevices.getUserMedia=async()=>{throw {name:'NotAllowedError'};};
  await h.card._toggleMicrophone();
  assert.match(h.card._status,/Autorisez le microphone/);
  assert.equal(h.card._micPending,false);
  assert.equal(peer.channel.sent.some(value=>value.enabled===true),false);
  h.card._close();
});

test('microphone honors HA permissions and secure-context requirements',async()=>{
  const h=harness();await h.live();let calls=0;
  h.navigator.mediaDevices.getUserMedia=async()=>{calls++;return new Stream([track()]);};
  h.settings.microphone_allowed=false;await h.card._toggleMicrophone();
  assert.equal(calls,0);
  h.settings.microphone_allowed=true;h.window.isSecureContext=false;
  await h.card._toggleMicrophone();assert.equal(calls,0);assert.match(h.card._status,/HTTPS/);
  h.card._close();
});

test('microphone acquired after closing is stopped and never installed',async()=>{
  const h=harness(), peer=await h.live(), wait=deferred(), input=track();
  h.navigator.mediaDevices.getUserMedia=()=>wait.promise;
  const enabling=h.card._toggleMicrophone();h.card._close();
  wait.resolve(new Stream([input]));await enabling;
  assert.equal(input.stopped,true);
  assert.equal(peer.sender.track,null);
  assert.equal(peer.channel.sent.some(value=>value.enabled===true),false);
});

test('data channel acknowledgement gates microphone and channel failure cannot break cleanup',async()=>{
  const h=harness(), peer=await h.live();
  await h.card._toggleMicrophone();
  const input=h.card._local.getTracks()[0];
  assert.equal(input.enabled,false);assert.equal(h.card._micPending,true);
  peer.channel.onmessage({data:'null'});peer.channel.onmessage({data:'invalid'});
  peer.channel.onmessage({data:JSON.stringify({type:'microphone',enabled:true,id:h.card._micId})});
  assert.equal(input.enabled,true);assert.equal(h.card._mic,true);
  peer.channel.send=()=>{throw new Error('closed while sending');};
  h.card._close();await tick();
  assert.equal(input.stopped,true);assert.equal(peer.closed,true);
  assert.equal(h.timers.size,0);assert.equal(h.unsubscribeCount,1);
});

test('canceling a pending microphone attachment serializes detach before the next attachment',async()=>{
  const h=harness(), peer=await h.live(), wait=deferred(), entered=deferred(), replacements=[];
  peer.sender.replaceTrack=value=>{
    replacements.push(value);
    if (replacements.length===1) {entered.resolve();return wait.promise.then(()=>{peer.sender.track=value;});}
    peer.sender.track=value;return Promise.resolve();
  };
  const first=h.card._toggleMicrophone();await entered.promise;
  const firstTrack=h.card._local.getTracks()[0];
  await h.card._toggleMicrophone();
  const second=h.card._toggleMicrophone();await tick();
  assert.equal(firstTrack.stopped,true);
  assert.equal(replacements.length,1);
  wait.resolve();await first;await second;
  assert.equal(replacements[1],null);
  assert.equal(replacements[2],h.card._local.getTracks()[0]);
  assert.equal(peer.sender.track,replacements[2]);
  assert.equal(peer.channel.sent.filter(message=>message.enabled===true).length,1);
  h.card._close();await tick();
});

test('WebRTC disconnect disables controls, reconnect leaves microphone off, and never replays output',async()=>{
  const h=harness(), peer=await h.live(), wait=deferred();
  h.hass.callService=(...args)=>{h.calls.push(args);return wait.promise;};
  const output=h.card._output('strike');await h.card._output('strike');
  peer.state('disconnected');
  assert.equal(h.card._connected,false);assert.equal(h.q('.strike').disabled,true);
  peer.state('disconnected');
  assert.equal([...h.timers.values()].filter(timer=>timer.ms===3000).length,1);
  peer.state('connected');
  assert.equal(h.card._mic,false);
  wait.reject(new Error('Confirmation inconnue'));await output;
  assert.equal(h.calls.filter(Array.isArray).length,1);
  assert.equal([...h.timers.values()].filter(timer=>timer.ms===3000).length,0);
  h.card._close();
});

test('output completion from a closed viewer does not overwrite current status',async()=>{
  const h=harness();await h.live();const wait=deferred();
  h.hass.callService=()=>wait.promise;
  const output=h.card._output('gate');h.card._close();
  const status=h.card._status;wait.resolve();await output;
  assert.equal(h.card._status,status);
  assert.equal(h.card._outputBusy,null);
});

test('hidden page and detached card close peers, remove listeners, and do not reopen automatically',async()=>{
  const h=harness();await h.live();
  h.document.hidden=true;h.document.dispatch('visibilitychange');await tick();
  assert.equal(h.peers[0].closed,true);assert.equal(h.unsubscribeCount,1);
  await h.card._open();assert.equal(h.peers.length,1);
  h.document.hidden=false;h.document.dispatch('visibilitychange');assert.equal(h.peers.length,1);
  h.card.disconnectedCallback();h.card.connectedCallback();
  assert.equal(h.document.listeners.get('visibilitychange').size,1);
  assert.equal(h.window.listeners.get('pagehide').size,1);
  await h.card._open();assert.equal(h.peers.length,2);
  h.window.dispatch('pagehide');assert.equal(h.peers[1].closed,true);
  h.card.disconnectedCallback();
  assert.equal(h.document.listeners.get('visibilitychange').size,0);
  assert.equal(h.window.listeners.get('pagehide').size,0);
});

test('WebRTC failure closes its peer before HLS and close removes the HLS player',async()=>{
  const h=harness(), peer=await h.live();
  peer.state('failed');await tick();
  assert.equal(peer.closed,true);assert.equal(h.unsubscribeCount,1);
  const player=h.card._hls;assert.ok(player);
  player.dispatch('load');assert.equal(h.card._connected,true);
  assert.equal(h.q('.mic').disabled,true);
  h.card._close();assert.equal(player.removed,true);
  player.dispatch('load');assert.equal(h.card._connected,false);
  assert.equal(h.timers.size,0);
});

test('browser without WebRTC can use HLS and canceled HLS loading cannot open later',async()=>{
  const h=harness({webrtc:false});await h.card._open();
  assert.ok(h.card._hls);h.card._close();
  const pending=harness({webrtc:false,hls:false});
  const opening=pending.card._open();await tick();
  assert.equal(pending.card._fallbackPending,true);
  pending.card._close();await opening;
  assert.equal(pending.card._hls,null);assert.equal(pending.timers.size,0);
});

test('fullscreen unavailable, denied, and exit errors are handled without unhandled rejection',async()=>{
  const h=harness();await h.card._fullscreen();assert.match(h.card._status,/Plein écran indisponible/);
  h.card.requestFullscreen=()=>Promise.reject(new Error('denied'));
  await h.card._fullscreen();assert.equal(h.card._error,true);
  h.document.fullscreenElement=h.card;h.document.exitFullscreen=()=>Promise.reject(new Error('denied'));
  await h.card._fullscreen();assert.equal(h.card._error,true);
  let exited=false;h.document.exitFullscreen=()=>{exited=true;};await h.card._fullscreen();assert.equal(exited,true);
});

test('late HLS helpers cannot create a camera card after the viewer was closed',async()=>{
  const h=harness({webrtc:false,hls:false}), wait=deferred();let created=0;
  h.window.loadCardHelpers=()=>wait.promise;
  const opening=h.card._open();await tick();h.card._close();await opening;
  wait.resolve({createCardElement(){created++;}});await tick();
  assert.equal(created,0);assert.equal(h.timers.size,0);
});


test('capabilities hide unsupported controls and block direct handlers', async () => {
  const h=harness();
  h.hass.states['camera.front'].attributes.welcomeeye_capabilities={camera:true,live_media:true,downstream_audio:true,talkback:false,strike:false,gate:false,manual_snapshot:false};
  h.card.hass=h.hass;
  for (const control of ['mic','strike','gate','snapshot']) assert.equal(h.q('.'+control).hidden,true);
  assert.equal(h.q('.sound').hidden,false);
  await h.card._toggleMicrophone(); await h.card._output('strike'); await h.card._output('gate'); await h.card._snapshot();
  assert.equal(h.calls.length,0);
});

test('R001 and V1 retain all supported card controls', () => {
  const h=harness();
  h.hass.states['camera.front'].attributes.welcomeeye_capabilities={camera:true,live_media:true,downstream_audio:true,talkback:true,strike:true,gate:true,manual_snapshot:true,local_ring:false};
  h.card.hass=h.hass;
  for (const control of ['sound','mic','strike','gate','snapshot']) assert.equal(h.q('.'+control).hidden,false);
});

test('unsupported camera capability cannot open live media', async () => {
  const h=harness();
  h.hass.states['camera.front'].attributes.welcomeeye_capabilities={camera:false};
  h.card.hass=h.hass;
  await h.card._open();
  assert.equal(h.calls.length,0);
});
