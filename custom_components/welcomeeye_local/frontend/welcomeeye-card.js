/* WelcomeEye intercom card. No credentials, media or SDP are persisted. */
(() => {
const icons = {
  play: '<path d="m9 5 11 7-11 7z"/>',
  close: '<path d="m6 6 12 12M6 18 18 6"/>',
  mic: '<rect x="9" y="2" width="6" height="13" rx="3"/><path d="M5 10v2a7 7 0 0 0 14 0v-2M12 19v3M8 22h8"/>',
  sound: '<path d="M3 9h4l5-4v14l-5-4H3zM16 8a6 6 0 0 1 0 8M19 5a10 10 0 0 1 0 14"/>',
  strike: '<path d="M4 21h16M7 21V3h10v18M13 12h1"/>',
  gate: '<path d="M3 21V4m18 17V4M3 8h18M3 17h18M8 8v9m8-9v9M12 8v13"/>',
  camera: '<path d="M3 6h4l2-3h6l2 3h4v15H3z"/><circle cx="12" cy="13" r="4"/>',
  full: '<path d="M9 3H3v6m12-6h6v6M3 15v6h6m6 0h6v-6"/>'
};
const svg = (name) => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${icons[name]}</svg>`;
const NAME_FIELDS = ['channel_1_name','channel_2_name','strike_1_name','strike_2_name','gate_1_name','gate_2_name'];
const OUTPUT_TARGETS = {strike_1:{channel:1,output:1,selector:'.strike'},gate_1:{channel:1,output:2,selector:'.gate'},
  strike_2:{channel:2,output:1,selector:'.strike-2'},gate_2:{channel:2,output:2,selector:'.gate-2'}};

class WelcomeEyeCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({mode: 'open'});
    this._generation = 0;
    this._actionGeneration = 0;
    this._micId = 0;
    this._settingsRevision = 0;
    this._status = 'Prêt à ouvrir la vidéo';
    this._visibility = () => { if (document.hidden) this._close(); else this._refreshSettings(); };
    this._pagehide = () => this._close();
    this._connectionReady = () => {this._invalidateSettings();this._refreshSettings();};
    this.shadowRoot.innerHTML = `
      <style>
        :host{display:block;--accent:#75e5c2;font-family:var(--primary-font-family,system-ui)}
        ha-card{display:block;overflow:hidden;border-radius:22px;background:#111d25;color:#f4f8fa;box-shadow:0 8px 32px #0002}
        header{display:flex;align-items:center;justify-content:space-between;padding:18px 20px;gap:12px}
        h2{margin:0;font-size:17px;letter-spacing:.2px;font-weight:600}.badge{font-size:10px;letter-spacing:1.6px;color:#a3b4bf;display:flex;align-items:center;gap:7px}
        .badge:before{content:'';width:6px;height:6px;border-radius:50%;background:#657780}.badge.live{color:var(--accent)}.badge.live:before{background:var(--accent)}
        .channels{display:flex;gap:8px;padding:0 20px 14px}.channels button{flex:1;padding:9px;border:1px solid #ffffff35;border-radius:10px;color:inherit;background:#1c2c36}.channels button[aria-pressed=true]{border-color:var(--accent);color:var(--accent)}
        .screen{position:relative;background:#081116;aspect-ratio:4/3;display:grid;place-items:center}
        video{width:100%;height:100%;position:absolute;object-fit:contain}.open{z-index:1;border:1px solid #ffffff35;background:#21333dd9;border-radius:50%;width:72px;height:72px;display:grid;place-items:center;color:var(--accent)}
        .open svg{width:30px;height:30px;fill:currentColor;stroke:none;margin-left:4px}.screen-tools{position:absolute;top:12px;right:12px;display:flex;gap:8px}.screen-tools button{padding:9px;background:#081116bf;border:1px solid #ffffff22;border-radius:12px;color:white}
        ha-hls-player{position:absolute;inset:0;width:100%;height:100%}.screen-tools{z-index:2}
        .toolbar{display:grid;grid-template-columns:repeat(auto-fit,minmax(72px,1fr));gap:6px;padding:16px 10px 10px}
        button{font:inherit;cursor:pointer;touch-action:manipulation}button:focus-visible{outline:2px solid var(--accent);outline-offset:3px}button:disabled{opacity:.35;cursor:default}
        .action{min-width:0;min-height:68px;padding:8px 2px;border:1px solid #ffffff13;border-radius:14px;background:#1c2c36;color:#d1dce2;display:flex;align-items:center;justify-content:center;flex-direction:column;gap:7px;font-size:12px}
        .action span{max-width:100%;overflow-wrap:anywhere;line-height:1.2}
        svg{width:23px;height:23px}.action[aria-pressed=true]{background:#75e5c21c;color:var(--accent);border-color:#75e5c27a}.action.working{opacity:.65}
        .status{margin:0;padding:4px 18px 17px;min-height:32px;color:#a7bac6;font-size:12px;line-height:1.5}.status.error{color:#ffc0af}.hint{color:#718793;font-size:10px;letter-spacing:.5px;text-align:right;padding:0 18px 12px}
        [hidden]{display:none!important}:host(:fullscreen){background:#081116;display:grid;place-items:center} :host(:fullscreen) ha-card{width:min(100vw,1100px)}
        @media(max-width:360px){header{padding:15px}.toolbar{gap:4px;padding:12px 8px}.action{font-size:11px}}
      </style>
      <ha-card>
        <header><h2>WelcomeEye</h2><span class="badge">INTERPHONE</span></header>
        <div class="channels" hidden><button class="channel-1" aria-pressed="true">Entrée 1</button><button class="channel-2" aria-pressed="false">Entrée 2</button></div>
        <div class="screen"><video playsinline autoplay muted></video><button class="open" aria-label="Ouvrir la vidéo">${svg('play')}</button>
          <div class="screen-tools"><button class="full" title="Plein écran" aria-label="Plein écran">${svg('full')}</button><button class="close" hidden title="Fermer et libérer la vidéo" aria-label="Fermer la vidéo">${svg('close')}</button></div>
        </div>
        <div class="toolbar">
          <button class="action sound" aria-pressed="false">${svg('sound')}<span>Son coupé</span></button>
          <button class="action mic" aria-pressed="false">${svg('mic')}<span>Micro coupé</span></button>
          <button class="action strike">${svg('strike')}<span>Gâche</span></button>
          <button class="action gate">${svg('gate')}<span>Portail</span></button>
          <button class="action strike-2" hidden>${svg('strike')}<span>Portillon 2 · essai</span></button>
          <button class="action gate-2" hidden>${svg('gate')}<span>Portail 2 · essai</span></button>
          <button class="action snapshot" title="Enregistrer une photo fraîche dans Médias">${svg('camera')}<span>Photo</span></button>
        </div><p class="status" role="status" aria-live="polite"></p><div class="hint">PHILIPS WELCOMEEYE · LOCAL</div>
      </ha-card>`;
    this._video = this.shadowRoot.querySelector('video');
    this.shadowRoot.querySelector('.open').onclick = () => this._open();
    this.shadowRoot.querySelector('.close').onclick = () => this._close();
    this.shadowRoot.querySelector('.full').onclick = () => this._fullscreen();
    this.shadowRoot.querySelector('.sound').onclick = () => {
      if (!this._connected) return;
      const generation = this._generation;
      const player = this._hls || this._video;
      player.muted = !player.muted;
      if (!this._hls) Promise.resolve(this._video.play()).catch(() => {
        if (generation === this._generation) this._message('Lecture du son bloquée par le navigateur', true);
      });
      this._render();
    };
    this.shadowRoot.querySelector('.mic').onclick = () => this._toggleMicrophone();
    this.shadowRoot.querySelector('.snapshot').onclick = () => this._snapshot();
    for (const channel of [1,2]) this.shadowRoot.querySelector('.channel-'+channel).onclick = () => this._selectChannel(channel);
    for (const [target,descriptor] of Object.entries(OUTPUT_TARGETS)) this.shadowRoot.querySelector(descriptor.selector).onclick = () => this._output(target);
  }
  setConfig(config) {
    if (!config || typeof config.entity !== 'string' || !/^camera\.[a-z0-9_]+$/.test(config.entity)) throw new Error('Choisissez une caméra WelcomeEye');
    for (const field of NAME_FIELDS) {
      if (config[field] !== undefined && (typeof config[field] !== 'string' || config[field].length > 64)) throw new Error('Nom d’entrée invalide (64 caractères maximum)');
    }
    if (this._config?.entity !== config.entity) {
      ++this._actionGeneration;
      this._close();
      this._settings = null;
      this._channels = null;
      this._selectedEntity = config.entity;
      this._settingsEntity = null;
      this._invalidateSettings();
    }
    this._config = {...config};
    this.shadowRoot.querySelector('h2').textContent = config.name || 'WelcomeEye';
    this._render();
    this._refreshSettings();
  }
  set hass(hass) { this._hass = hass; this._watchConnection(); if (this._hls) this._hls.hass = hass; this._render(); this._refreshSettings(); }
  getCardSize() { return 6; }
  static getStubConfig(hass) {
    return {entity: Object.keys(hass.states).find(id => id.startsWith('camera.') && hass.states[id].attributes.welcomeeye_player), name: 'WelcomeEye'};
  }
  static getConfigElement() { return document.createElement('welcomeeye-card-editor'); }
  connectedCallback() {
    this._watchConnection();
    document.addEventListener('visibilitychange', this._visibility);
    window.addEventListener('pagehide', this._pagehide);
    this._render();
    this._refreshSettings();
  }
  disconnectedCallback() {
    document.removeEventListener('visibilitychange', this._visibility);
    window.removeEventListener('pagehide', this._pagehide);
    this._metadataConnection?.removeEventListener?.('ready',this._connectionReady);
    this._metadataConnection=null;
    this._invalidateSettings();
    ++this._actionGeneration;
    this._close();
  }
  _cameraAvailable() {
    const camera = this._hass?.states[this._entity()];
    return !!camera && this._supports('camera') && camera.state !== 'unavailable' && camera.state !== 'unknown';
  }
  _supports(capability) {
    const capabilities = this._hass?.states[this._entity()]?.attributes?.welcomeeye_capabilities;
    // Older 0.4.2 backends have no matrix attribute; preserve their existing card.
    return capabilities === undefined ? true : capabilities[capability] === true;
  }
  _entity() { return this._selectedEntity || this._config?.entity; }
  _channelNumber() { return this._channels?.find(item=>item.entity_id===this._entity())?.channel || 1; }
  _channelName(number) { return this._config?.['channel_'+number+'_name'] || this._channels?.find(item=>item.channel===number)?.label || 'Entrée '+number; }
  _videoLabel(capitalized=true) { return (capitalized ? 'Vidéo' : 'vidéo')+(this._channels?.length===2 ? ' '+this._channelName(this._channelNumber()) : ''); }
  _outputName(target) { return this._config?.[target+'_name'] || (target.startsWith('strike') ? 'Portillon ' : 'Portail ')+OUTPUT_TARGETS[target].channel; }
  _outputConfig(target) {
    const expected=OUTPUT_TARGETS[target];if (!expected) return null;
    const settings=this._settings;
    if (settings?.outputs !== undefined) {
      const value=settings.outputs?.[target];
      return value && value.channel===expected.channel && value.output===expected.output && /^button\.[a-z0-9_]+$/.test(value.entity_id) ? value : null;
    }
    // Older releases expose only the two primary output entities.
    const name=target.split('_')[0], entity_id=settings?.buttons?.[name];
    return expected.channel===1 && entity_id && (this._supports(name) || settings.buttons_channel===1)
      ? {entity_id,channel:1,output:expected.output,validation_status:'existing'} : null;
  }
  _invalidateSettings() {
    clearTimeout(this._settingsRetryTimer);this._settingsRetryTimer=null;this._settingsRetryCount=0;
    clearTimeout(this._settingsRequest?.timeout);this._settingsRequest?.cancel?.();
    this._settingsRevision++;this._settingsKey=null;this._settingsAttemptKey=null;this._settingsObservedKey=null;this._settingsRequest=null;
  }
  _watchConnection() {
    const connection=this._hass?.connection;
    if (connection===this._metadataConnection) return;
    this._metadataConnection?.removeEventListener?.('ready',this._connectionReady);
    this._metadataConnection=connection;
    connection?.addEventListener?.('ready',this._connectionReady);
    this._invalidateSettings();
  }
  _metadataKey(entity=this._entity()) {
    const states=this._hass?.states || {};
    const related=Object.entries(states).filter(([id,state])=>
      (id.startsWith('camera.') && state.attributes?.welcomeeye_player && [1,2].includes(state.attributes.welcomeeye_channel)) ||
      (id.startsWith('button.') && Object.hasOwn(OUTPUT_TARGETS,state.attributes?.welcomeeye_output_target || '')))
      .map(([id,state])=>[id,!['unavailable','unknown'].includes(state.state),state.attributes.welcomeeye_channel,
        state.attributes.welcomeeye_multichannel_available,state.attributes.welcomeeye_output_target]).sort((a,b)=>a[0].localeCompare(b[0]));
    return JSON.stringify([entity,!!states[entity],this._hass?.connection?.connected,related]);
  }
  _applySettings(settings, entity, key=this._metadataKey(entity)) {
    clearTimeout(this._settingsRetryTimer);this._settingsRetryTimer=null;this._settingsRetryCount=0;
    this._settings=settings; this._settingsEntity=entity;
    this._settingsKey=key;
    this._channels=Array.isArray(settings.channels) ? settings.channels.filter(item=>[1,2].includes(item.channel) && /^camera\.[a-z0-9_]+$/.test(item.entity_id)) : [];
    this._render();
  }
  _refreshSettings() {
    const entity=this._entity();
    if (!this.isConnected || document.hidden) return;
    if (!this._config || !this._hass || this._hass.connection?.connected===false || ![1,2].includes(this._hass.states[entity]?.attributes?.welcomeeye_channel)) {
      if (this._settingsObservedKey!=null) this._invalidateSettings();
      return;
    }
    const key=this._metadataKey(entity);
    if (this._settingsObservedKey!==key) {this._invalidateSettings();this._settingsObservedKey=key;}
    if (this._settingsKey===key || this._settingsRequest?.key===key || this._settingsAttemptKey===key) return;
    this._settingsAttemptKey=key;
    const request={entity,key,generation:this._actionGeneration,revision:this._settingsRevision};this._settingsRequest=request;
    const deadline=new Promise((_,reject)=>{
      request.cancel=()=>reject(new Error('metadata_cancelled'));
      request.timeout=setTimeout(()=>reject(new Error('metadata_timeout')),5000);
    });
    request.promise=Promise.race([deadline,Promise.resolve().then(()=>{
      if (request!==this._settingsRequest || request.generation!==this._actionGeneration || request.revision!==this._settingsRevision
          || entity!==this._entity() || !this.isConnected || document.hidden || this._hass.connection?.connected===false) throw new Error('metadata_cancelled');
      return this._hass.callWS({type:'welcomeeye_local/player_config',entity_id:entity});
    })]).then(settings=>{
      if (request===this._settingsRequest && request.generation===this._actionGeneration && request.revision===this._settingsRevision && entity===this._entity()) this._applySettings(settings,entity,key);
    }).catch(error=>{
      if (request!==this._settingsRequest || request.revision!==this._settingsRevision || entity!==this._entity()) return;
      if (error?.message==='metadata_cancelled') {this._settingsAttemptKey=null;return;}
      // Only read-only metadata is retried. Never replay media or output actions.
      const forbidden=['unauthorized','not_allowed','forbidden'].includes(error?.code);
      const delay=[1000,3000][this._settingsRetryCount || 0];
      if (!forbidden && delay!==undefined) {
        this._settingsRetryCount=(this._settingsRetryCount || 0)+1;
        this._settingsRetryTimer=setTimeout(()=>{
          this._settingsRetryTimer=null;
          if (request.revision!==this._settingsRevision || key!==this._metadataKey() || entity!==this._entity()) return;
          this._settingsAttemptKey=null;this._refreshSettings();
        },delay);
      } else if (!this._connected && !this._opening) {
        this._message(forbidden ? 'Accès aux commandes non autorisé.' : 'Réglages de la carte indisponibles. Rechargez le tableau de bord.',true);
      }
    }).finally(()=>{clearTimeout(request.timeout);if (this._settingsRequest===request) {this._settingsRequest=null;this._refreshSettings();}});
  }
  async _selectChannel(number) {
    const target=this._channels?.find(item=>item.channel===number);
    if (!target || target.entity_id===this._entity() || this._switching || this._outputBusy) return;
    const reopen=!!(this._pc || this._opening || this._hls || this._fallbackPending);
    const wasHls=!!this._hls;
    this._switching=true;
    const closing=this._close(), generation=this._generation;
    this._message('Sélection de '+this._channelName(number)+'…');
    try {
      if (!(await closing)) throw new Error('Fermeture du lecteur non confirmée ; aucun changement effectué.');
      if (generation!==this._generation || !this.isConnected || document.hidden) return;
      // HA owns its HLS lease: removing the browser player does not acknowledge
      // upstream media release. A later explicit attempt is still checked by HA.
      if (wasHls) throw new Error('Lecteur Home Assistant fermé. Fermez les autres lecteurs et attendez la libération du flux avant de sélectionner une autre entrée.');
      this._selectedEntity=target.entity_id;this._settings=null;this._settingsEntity=null;this._invalidateSettings();
      this._message(this._channelName(number)+' sélectionnée');
      if (reopen) await this._open(); else this._refreshSettings();
    } catch (error) {if (generation===this._generation) this._message(error.message,true);}
    finally {this._switching=false;this._render();}
  }
  async _fullscreen() {
    try {
      if (document.fullscreenElement || document.webkitFullscreenElement) {
        const exit = document.exitFullscreen || document.webkitExitFullscreen;
        if (!exit) throw new Error();
        await exit.call(document);
      } else {
        const enter = this.requestFullscreen || this.webkitRequestFullscreen;
        if (!enter) throw new Error();
        await enter.call(this);
      }
    } catch { this._message('Plein écran indisponible dans ce navigateur', true); }
  }
  _message(text, error=false) {
    this._statusMessage=typeof text==='function' ? text : null;
    this._status=this._statusMessage ? this._statusMessage() : text;
    this._error=error; this._render();
  }
  _render() {
    const q = s => this.shadowRoot.querySelector(s);
    const available = this._cameraAvailable();
    const active = !!(this._opening || this._pc || this._hls || this._fallbackPending);
    const muted = (this._hls || this._video).muted;
    for (const [control, capability] of Object.entries({sound:'downstream_audio',mic:'talkback',snapshot:'manual_snapshot'})) {
      q('.'+control).hidden = !this._supports(capability);
    }
    const multiple=this._channels?.length===2;
    q('.channels').hidden=!multiple;
    for (const channel of [1,2]) {
      q('.channel-'+channel).textContent=this._channelName(channel);
      q('.channel-'+channel).setAttribute('aria-label','Sélectionner '+this._channelName(channel));
      q('.channel-'+channel).setAttribute('aria-pressed',String(channel===this._channelNumber()));
      q('.channel-'+channel).disabled=!!(this._switching || this._outputBusy);
    }
    q('.open').setAttribute('aria-label','Ouvrir la '+this._videoLabel(false));
    q('.close').setAttribute('aria-label','Fermer la '+this._videoLabel(false));
    q('.open').hidden = active;
    q('.open').disabled = !available || !!this._switching;
    q('.close').hidden = !active;
    q('.badge').textContent = this._connected ? (this._hls ? 'VIA HOME ASSISTANT' : 'EN DIRECT') : active ? 'CONNEXION' : 'INTERPHONE';
    q('.badge').classList.toggle('live', !!this._connected);
    q('.sound').disabled = !this._connected;
    q('.sound').setAttribute('aria-pressed', String(!muted));
    q('.sound span').textContent = muted ? 'Son coupé' : 'Son actif';
    q('.mic').disabled = !this._connected || !this._settings?.microphone_allowed || this._channel?.readyState !== 'open';
    q('.mic').setAttribute('aria-pressed', String(!!this._mic));
    q('.mic span').textContent = this._hls ? 'Micro indisponible' : this._micPending ? 'Annuler micro' : this._mic ? 'Micro actif' : 'Micro coupé';
    for (const [target,descriptor] of Object.entries(OUTPUT_TARGETS)) {
      const output=this._outputConfig(target), button=q(descriptor.selector), name=this._outputName(target);
      const trial=output?.validation_status==='hardware_pending';
      const selected=descriptor.channel===this._channelNumber();
      button.hidden=!selected || (this._settings ? !output : descriptor.channel===2 || !this._supports(target.split('_')[0]));
      button.disabled=button.hidden || !available || !this._connected || !!this._outputBusy || !!this._switching || !output;
      button.classList.toggle('working',this._outputBusy===target);
      q(descriptor.selector+' span').textContent=name+(trial ? ' · essai' : '');
      button.setAttribute('aria-label',(trial ? 'Tester ' : 'Commander ')+name+' — '+this._channelName(descriptor.channel));
      button.title=trial ? name+' : essai non validé sur le matériel, vérifiez le résultat sur place' : name;
    }
    const snapshotNeedsLive=this._hass?.states[this._entity()]?.attributes?.welcomeeye_snapshot_requires_live===true;
    q('.snapshot').disabled = !available || !!this._snapshotBusy || (snapshotNeedsLive && !this._connected);
    q('.snapshot').title=snapshotNeedsLive && !this._connected ? 'Ouvrez le direct pour prendre une photo' : 'Enregistrer une photo fraîche dans Médias';
    q('.snapshot').classList.toggle('working', !!this._snapshotBusy);
    q('.snapshot span').textContent = this._snapshotBusy ? 'Capture…' : 'Photo';
    if (this._statusMessage) this._status=this._statusMessage();
    q('.status').textContent = this._status;
    q('.status').classList.toggle('error', !!this._error);
  }
  async _open() {
    if (this._opening || this._pc || this._hls || this._fallbackPending || !this._cameraAvailable() || !this.isConnected || document.hidden) return;
    const generation = ++this._generation;
    this._opening = true;
    this._message(()=> 'Connexion '+this._videoLabel(false)+'…');
    try {
      if (this._closePromise && !(await this._closePromise)) throw new Error('Fermeture précédente non confirmée. Rechargez la carte.');
      if (generation !== this._generation || !this.isConnected || document.hidden) return;
      const entity=this._entity();
      const settingsKey=this._metadataKey(entity);
      const settings = await this._hass.callWS({type:'welcomeeye_local/player_config',entity_id:entity});
      if (generation !== this._generation || !this.isConnected) return;
      this._applySettings(settings,entity,settingsKey);
      if (typeof RTCPeerConnection !== 'function') { await this._fallback(); return; }
      const pc = this._pc = new RTCPeerConnection(settings.configuration);
      this._remote = new MediaStream();
      this._video.srcObject = this._remote;
      this._video.muted = true;
      pc.addTransceiver('video', {direction:'recvonly'});
      this._audioSender = pc.addTransceiver('audio', {direction:'sendrecv'}).sender;
      const dc = this._channel = pc.createDataChannel('welcomeeye-control');
      dc.onopen = () => { if (generation === this._generation) this._render(); };
      dc.onclose = () => {
        if (generation !== this._generation) return;
        this._stopMicrophone(false);
        this._message('Micro coupé : canal de contrôle fermé',true);
      };
      dc.onmessage = event => {
        let message;
        try { message=JSON.parse(event.data); } catch { return; }
        if (generation !== this._generation || !message || message.type !== 'microphone' || message.id !== this._micId) return;
        if (!message.enabled || message.error) {
          this._stopMicrophone(false);
          this._message(message.error || 'Micro coupé', !!message.error);
          return;
        }
        this._micPending=false; this._mic=true;
        clearInterval(this._heartbeat); clearTimeout(this._micTimeout);
        for (const track of this._local?.getTracks() || []) track.enabled=true;
        this._heartbeat=setInterval(() => {
          try {
            if (dc.readyState === 'open') dc.send(JSON.stringify({type:'heartbeat'}));
            else this._stopMicrophone(false);
          } catch { this._stopMicrophone(false); this._message('Micro coupé : canal de contrôle fermé',true); }
        },1000);
        this._message('Micro actif — vous pouvez parler');
      };
      pc.ontrack = event => {
        if (generation !== this._generation) return;
        this._remote.addTrack(event.track);
        Promise.resolve(this._video.play()).catch(() => {});
      };
      pc.onconnectionstatechange = () => {
        if (generation !== this._generation) return;
        if (pc.connectionState === 'connected') {
          clearTimeout(this._connectTimeout); clearTimeout(this._disconnectTimeout);
          this._connected=true; this._message(()=>this._videoLabel()+' en direct · micro coupé');
        } else if (pc.connectionState === 'failed') {
          this._fallback();
        } else if (pc.connectionState === 'closed') {
          this._close(); this._message('Connexion interrompue. Rouvrez la vidéo pour réessayer.',true);
        } else if (pc.connectionState === 'disconnected') {
          this._connected=false;
          this._stopMicrophone();
          clearTimeout(this._disconnectTimeout);
          this._message('Connexion interrompue · micro coupé',true);
          this._disconnectTimeout=setTimeout(() => {
            if (this._pc === pc && pc.connectionState !== 'connected') this._fallback();
          },3000);
        }
      };
      this._connectTimeout=setTimeout(() => {
        if (this._pc === pc && !this._connected) this._fallback();
      },45000);
      this._render();
      await pc.setLocalDescription(await pc.createOffer());
      if (generation !== this._generation) return;
      await new Promise((resolve,reject) => {
        let timer;
        const done=() => { clearTimeout(timer); this._cancelIceWait=null; pc.removeEventListener('icegatheringstatechange',check); pc.removeEventListener('connectionstatechange',check); };
        const check=() => {
          if (pc.connectionState === 'closed') { done(); reject(new Error('Connexion annulée')); }
          else if (pc.iceGatheringState === 'complete') { done(); resolve(); }
        };
        this._cancelIceWait=() => { done(); resolve(); };
        timer=setTimeout(() => {done(); resolve();},8000);
        pc.addEventListener('icegatheringstatechange',check); pc.addEventListener('connectionstatechange',check); check();
      });
      if (generation !== this._generation) return;
      const request={entity,token:null,unsubscribe:null,stopSupported:settings.stop_supported===true};
      request.tokenReady=new Promise(resolve=>{request.resolveToken=resolve;});
      this._viewerRequest=request;
      request.ready=this._hass.connection.subscribeMessage(event => {
        if (event.type === 'session') {request.token=event.session_id;request.resolveToken();return;}
        if (generation !== this._generation) return;
        if (event.type === 'answer') pc.setRemoteDescription({type:'answer',sdp:event.answer}).catch(() => {
          if (generation === this._generation) { this._close(); this._message('Négociation vidéo impossible',true); }
        });
        else if (event.type === 'error') { this._close(); this._message(event.message || 'Connexion au visiophone impossible',true); }
      }, {type:'welcomeeye_local/player_offer',entity_id:entity,offer:pc.localDescription.sdp}).then(unsubscribe=>{
        request.unsubscribe=unsubscribe;if (request.abandoned) this._unsubscribeRequest(request);return unsubscribe;
      });
      await request.ready;
      if (generation !== this._generation) {await this._releaseRequest(request);return;}
    } catch (error) {
      if (generation === this._generation) { this._close(); this._message(error.message || 'Connexion impossible',true); }
    } finally { if (generation === this._generation) { this._opening=false; this._render(); } }
  }
  async _fallback() {
    if ((!this._pc && !this._opening) || this._fallbackPending) return;
    // Close this viewer and its microphone before using HA's existing stream.
    // No second WelcomeEye session and no replay of physical commands.
    const closing=this._close();
    const generation = this._generation;
    this._fallbackPending = true;
    this._message('WebRTC inaccessible · ouverture de la vidéo via Home Assistant…');
    let timer;
    try {
      if (!(await closing)) throw new Error('Fermeture du lecteur non confirmée');
      if (generation!==this._generation || !this.isConnected || document.hidden) return;
      await Promise.race([
        (async () => {
          if (!customElements.get('ha-hls-player') && window.loadCardHelpers) {
            const helpers = await window.loadCardHelpers();
            if (generation !== this._generation || !this.isConnected) return;
            helpers.createCardElement({type:'picture-entity',entity:this._entity(),camera_view:'live'});
          }
          await customElements.whenDefined('ha-hls-player');
        })(),
        new Promise((resolve, reject) => {
          this._cancelFallbackWait=resolve;
          timer=setTimeout(() => reject(new Error('Lecteur Home Assistant indisponible. Rechargez la page.')),8000);
        })
      ]);
      if (generation !== this._generation || !this.isConnected) return;
      const player = this._hls = document.createElement('ha-hls-player');
      Object.assign(player, {hass:this._hass,entityid:this._entity(),autoPlay:true,playsInline:true,muted:true,controls:false,fitMode:'contain'});
      player.addEventListener('load', () => {
        if (this._hls !== player) return;
        clearTimeout(this._fallbackTimeout);
        this._connected = true;
        this._message(()=>this._videoLabel()+' via Home Assistant · micro indisponible sur ce réseau');
      });
      player.addEventListener('streams', event => {
        if (this._hls === player && !event.detail.hasVideo) {
          this._connected = false;
          this._message('Flux vidéo indisponible via Home Assistant',true);
        }
      });
      this._video.hidden = true;
      this._fallbackPending = false;
      this._fallbackTimeout = setTimeout(() => {
        if (this._hls === player && !this._connected) {
          this._close(); this._message('La vidéo reste inaccessible via Home Assistant',true);
        }
      },45000);
      this.shadowRoot.querySelector('.screen').prepend(player);
      this._render();
    } catch (error) {
      if (generation === this._generation) {this._close(); this._message(error.message,true);}
    } finally {
      clearTimeout(timer);
      if (generation === this._generation) this._cancelFallbackWait=null;
    }
  }
  async _toggleMicrophone() {
    if (!this._supports('talkback')) return;
    if (this._mic || this._micPending) { this._stopMicrophone(); this._message('Micro coupé'); return; }
    if (!this._connected || !this._settings?.microphone_allowed || this._channel?.readyState !== 'open') return;
    if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia) {
      this._message('Pour le micro, ouvrez Home Assistant en HTTPS puis autorisez le microphone.',true); return;
    }
    const id=++this._micId, generation=this._generation;
    this._micPending=true; this._render();
    try {
      const stream=await navigator.mediaDevices.getUserMedia({audio:{echoCancellation:true,noiseSuppression:true,autoGainControl:true,channelCount:1},video:false});
      if (id !== this._micId || generation !== this._generation) { stream.getTracks().forEach(t=>t.stop()); return; }
      this._local=stream;
      const track=stream.getAudioTracks()[0]; track.enabled=false;
      track.onended=() => { if (this._local === stream) {this._stopMicrophone(); this._message('Microphone déconnecté',true);} };
      await this._replaceMicrophoneTrack(track,id);
      if (id !== this._micId || generation !== this._generation) return;
      this._channel.send(JSON.stringify({type:'microphone',enabled:true,id}));
      this._micTimeout=setTimeout(() => {
        if (id === this._micId && this._micPending) {this._stopMicrophone(); this._message('Le visiophone n’a pas confirmé le microphone',true);}
      },7000);
    } catch (error) {
      if (id === this._micId) {this._stopMicrophone(); this._message(error.name === 'NotAllowedError' ? 'Autorisez le microphone dans votre navigateur' : 'Microphone indisponible',true);}
    }
  }
  _replaceMicrophoneTrack(track, id) {
    const sender=this._audioSender;
    if (!sender) return Promise.resolve();
    const previous=this._senderUpdate?.sender === sender ? this._senderUpdate.promise : Promise.resolve();
    // Serialize attach/detach: a canceled attach must not finish after the next one.
    const promise=previous.catch(()=>{}).then(() => {
      if (track && (id !== this._micId || sender !== this._audioSender)) return;
      return sender.replaceTrack(track);
    });
    this._senderUpdate={sender,promise};
    return promise;
  }
  _stopMicrophone(notify=true) {
    const id=++this._micId;
    clearInterval(this._heartbeat); clearTimeout(this._micTimeout);
    this._mic=this._micPending=false;
    const local=this._local; this._local=null;
    local?.getTracks().forEach(t=>{t.onended=null;t.stop();});
    this._replaceMicrophoneTrack(null).catch(()=>{});
    try {
      if (notify && this._channel?.readyState === 'open') this._channel.send(JSON.stringify({type:'microphone',enabled:false,id}));
    } catch { /* Cleanup must continue even when the data channel closes mid-send. */ }
    this._render();
  }
  _actionError(error, action) {
    if (error?.code === 'unauthorized' || error?.code === 'unauthorized_entity') return action+' refusée : autorisation Home Assistant insuffisante';
    return action+' impossible : '+(error?.message || 'Home Assistant n’a pas confirmé la demande');
  }
  async _snapshot() {
    if (!this._supports('manual_snapshot')) return;
    if (!this._cameraAvailable() || this._snapshotBusy) return;
    if (this._hass?.states[this._entity()]?.attributes?.welcomeeye_snapshot_requires_live===true && !this._connected) {
      this._message('Ouvrez le direct de cette entrée pour prendre une photo.');return;
    }
    const entity_id=this._entity(), generation=this._actionGeneration;
    this._snapshotBusy=true; this._message('Capture d’une photo fraîche…');
    try {
      // The backend shares the current media session; do not touch this viewer.
      const result=await this._hass.callWS({type:'call_service',domain:'welcomeeye_local',service:'capture_snapshot',target:{entity_id},service_data:{save_to_media:true},return_response:true});
      if (generation !== this._actionGeneration || !this.isConnected || entity_id!==this._entity()) return;
      const capture=result?.response?.[entity_id];
      if (capture?.saved) this._message('Photo enregistrée');
      else if (capture?.save_error) this._message('Photo capturée · enregistrement dans Médias impossible ('+capture.save_error+')',true);
      else this._message('Sauvegarde de la photo non confirmée par Home Assistant',true);
    } catch (error) {
      if (generation === this._actionGeneration && this.isConnected && entity_id===this._entity()) this._message(this._actionError(error,'Capture'),true);
    } finally {this._snapshotBusy=false;this._render();}
  }
  async _output(target) {
    if (target==='strike' || target==='gate') target+='_1';
    const output=this._outputConfig(target);
    if (!this._cameraAvailable() || !this._connected || this._outputBusy || this._switching || !output
        || output.channel!==this._channelNumber() || !this.isConnected || document.hidden) return;
    const entity_id=output.entity_id, name=this._outputName(target);
    let generation=this._generation;
    this._outputBusy=target; this._message('Commande '+name+'…');
    try {
      // A delayed click from the previous camera cannot select another output.
      // The target still comes from HA's authorized channel/output mapping.
      // One explicit click, one HA service call. Never automatically replay.
      await this._hass.callService('button','press',{entity_id});
      if (generation === this._generation && this.isConnected && !document.hidden) this._message(name+' : commande confirmée par le visiophone'+(output.validation_status==='hardware_pending' ? ', vérifiez le résultat physique.' : '.'));
    } catch (error) {
      if (generation === this._generation) this._message(this._actionError(error,name),true);
    } finally {this._outputBusy=null;this._render();}
  }
  _releaseRequest(request) {
    if (!request) return Promise.resolve(true);
    if (request.release) return request.release;
    request.release=(async()=>{
      let timer;
      try {
        await Promise.race([(async()=>{
          await request.ready;
          if (request.abandoned) throw new Error('Session de lecteur déjà fermée');
          if (request.stopSupported) {
            await request.tokenReady;
            if (request.abandoned) throw new Error('Session de lecteur déjà fermée');
            if (!/^[a-f0-9]{32}$/.test(request.token || '')) throw new Error('Session de lecteur inconnue');
            const result=await this._hass.callWS({type:'welcomeeye_local/player_stop',entity_id:request.entity,session_id:request.token});
            if (result?.stopped!==true) throw new Error('Arrêt du lecteur non confirmé');
          }
        })(),new Promise((_,reject)=>{timer=setTimeout(()=>reject(new Error('Arrêt du lecteur non confirmé')),30000);})]);
        return true;
      } catch {return false;}
      finally {clearTimeout(timer);request.abandoned=true;await this._unsubscribeRequest(request);}
    })();
    return request.release;
  }
  async _unsubscribeRequest(request) {
    if (!request.unsubscribe || request.unsubscribed) return;
    request.unsubscribed=true;
    try {await request.unsubscribe();} catch { /* Transport may already be closed. */ }
  }
  _close() {
    ++this._generation;
    this._opening = false;
    if (this._cancelIceWait) this._cancelIceWait();
    if (this._cancelFallbackWait) {const cancel=this._cancelFallbackWait;this._cancelFallbackWait=null;cancel();}
    this._stopMicrophone();
    clearTimeout(this._connectTimeout);clearTimeout(this._disconnectTimeout);
    clearTimeout(this._fallbackTimeout);
    this._fallbackPending = false;
    const hls=this._hls;this._hls=null;hls?.remove();
    this._video.hidden=false;
    this._connected=false;
    const pc=this._pc, channel=this._channel;this._pc=null;this._channel=null;this._audioSender=null;this._senderUpdate=null;
    if (channel) channel.onopen=channel.onclose=channel.onmessage=null;
    if (pc) {pc.onconnectionstatechange=pc.ontrack=null;pc.close();}
    this._remote?.getTracks().forEach(t=>t.stop()); this._remote=null;
    this._video.srcObject=null;
    const request=this._viewerRequest;this._viewerRequest=null;
    const previous=this._closePromise;
    if (request || previous) this._closePromise=Promise.all([previous || true,this._releaseRequest(request)]).then(results=>results.every(Boolean));
    this._message(()=>this._videoLabel()+' fermée · micro coupé');
    return this._closePromise || Promise.resolve(true);
  }
}

class WelcomeEyeCardEditor extends HTMLElement {
  setConfig(config) {
    this._config={...config};
    if (!this.shadowRoot) this._draw();
    this._picker.value=this._config.entity;
    for (const key of NAME_FIELDS) {
      const field=this.shadowRoot.querySelector('.'+key.replaceAll('_','-')),value=this._config[key] || '';
      // HA echoes config-changed while the user types. Keep the same input and
      // avoid assigning an unchanged value so focus and the selection survive.
      if (field.value!==value) field.value=value;
    }
    this._updateFieldVisibility();
  }
  set hass(hass) {this._hass=hass;if (this._picker) this._picker.hass=hass;this._updateFieldVisibility();}
  _updateFieldVisibility() {
    if (!this.shadowRoot) return;
    const multiple=this._hass?.states[this._config?.entity]?.attributes?.welcomeeye_multichannel_available===true;
    for (const field of NAME_FIELDS.filter(name=>name.includes('_2_'))) this.shadowRoot.querySelector('.'+field.replaceAll('_','-')+'-row').hidden=!multiple;
  }
  _changed(field,value) {
    this._config={...this._config,[field]:value};
    this.dispatchEvent(new CustomEvent('config-changed',{detail:{config:{...this._config}},bubbles:true,composed:true}));
    this._updateFieldVisibility();
  }
  _draw() {
    if (!this.shadowRoot) this.attachShadow({mode:'open'});
    const labels={channel_1_name:'Nom entrée 1',channel_2_name:'Nom entrée 2',strike_1_name:'Nom portillon 1',strike_2_name:'Nom portillon 2',gate_1_name:'Nom portail 1',gate_2_name:'Nom portail 2'};
    // Native inputs remain editable when HA removes or lazily loads its own
    // private form components (ha-textfield was removed in 2026).
    this.shadowRoot.innerHTML=`<style>
      :host{display:grid;gap:16px;color:var(--primary-text-color);font-family:var(--primary-font-family,system-ui)}
      label{display:flex;flex-direction:column;gap:8px;font-size:14px}
      label span,p{color:var(--secondary-text-color)}
      input{box-sizing:border-box;width:100%;min-height:48px;padding:12px;font:inherit;color:var(--primary-text-color);background:var(--input-fill-color,var(--secondary-background-color,transparent));border:1px solid var(--divider-color,#888);border-radius:8px}
      input:focus-visible{outline:2px solid var(--primary-color,#03a9f4);outline-offset:2px}
      p{margin:0;font-size:13px;line-height:1.5}[hidden]{display:none!important}
    </style><ha-entity-picker></ha-entity-picker>`+NAME_FIELDS.map(field=>{
      const className=field.replaceAll('_','-');
      return '<label class="'+className+'-row"><span>'+labels[field]+'</span><input type="text" class="'+className+'" maxlength="64" autocomplete="off"></label>';
    }).join('')+'<p>Les noms modifient uniquement l’affichage. Les commandes disponibles dépendent des options de l’intégration.</p>';
    this._picker=this.shadowRoot.querySelector('ha-entity-picker');
    Object.assign(this._picker,{hass:this._hass,value:this._config.entity,includeDomains:['camera'],label:'Caméra WelcomeEye'});
    this._picker.addEventListener('value-changed',event => {
      if (event.detail.value) this._changed('entity',event.detail.value);
    });
    for (const key of NAME_FIELDS) {
      const field=this.shadowRoot.querySelector('.'+key.replaceAll('_','-'));
      field.maxLength=64;
      field.addEventListener('input',event=>{
        this._changed(key,String(event.target.value || '').slice(0,64));
      });
    }
    this._updateFieldVisibility();
  }
}
if (!customElements.get('welcomeeye-card')) customElements.define('welcomeeye-card',WelcomeEyeCard);
if (!customElements.get('welcomeeye-card-editor')) customElements.define('welcomeeye-card-editor',WelcomeEyeCardEditor);
window.customCards=window.customCards||[];
if (!window.customCards.some(c=>c.type==='welcomeeye-card')) window.customCards.push({type:'welcomeeye-card',name:'WelcomeEye — Interphone',description:'Vidéo, micro, commandes et captures dans un même lecteur',preview:true});
})();
