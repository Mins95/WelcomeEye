/* WelcomeEye intercom card. No credentials, media or SDP are persisted. */
const icons = {
  play: '<path d="m9 5 11 7-11 7z"/>',
  close: '<path d="m6 6 12 12M6 18 18 6"/>',
  mic: '<rect x="9" y="2" width="6" height="13" rx="3"/><path d="M5 10v2a7 7 0 0 0 14 0v-2M12 19v3M8 22h8"/>',
  sound: '<path d="M3 9h4l5-4v14l-5-4H3zM16 8a6 6 0 0 1 0 8M19 5a10 10 0 0 1 0 14"/>',
  strike: '<path d="M4 21h16M7 21V3h10v18M13 12h1"/>',
  gate: '<path d="M3 21V4m18 17V4M3 8h18M3 17h18M8 8v9m8-9v9M12 8v13"/>',
  full: '<path d="M9 3H3v6m12-6h6v6M3 15v6h6m6 0h6v-6"/>'
};
const svg = (name) => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${icons[name]}</svg>`;

class WelcomeEyeCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({mode: 'open'});
    this._generation = 0;
    this._micId = 0;
    this._status = 'Prêt à ouvrir la vidéo';
    this._visibility = () => { if (document.hidden) this._close(); };
    this._pagehide = () => this._close();
    this.shadowRoot.innerHTML = `
      <style>
        :host{display:block;--accent:#75e5c2;font-family:var(--primary-font-family,system-ui)}
        ha-card{display:block;overflow:hidden;border-radius:22px;background:#111d25;color:#f4f8fa;box-shadow:0 8px 32px #0002}
        header{display:flex;align-items:center;justify-content:space-between;padding:18px 20px;gap:12px}
        h2{margin:0;font-size:17px;letter-spacing:.2px;font-weight:600}.badge{font-size:10px;letter-spacing:1.6px;color:#a3b4bf;display:flex;align-items:center;gap:7px}
        .badge:before{content:'';width:6px;height:6px;border-radius:50%;background:#657780}.badge.live{color:var(--accent)}.badge.live:before{background:var(--accent)}
        .screen{position:relative;background:#081116;aspect-ratio:4/3;display:grid;place-items:center}
        video{width:100%;height:100%;position:absolute;object-fit:contain}.open{z-index:1;border:1px solid #ffffff35;background:#21333dd9;border-radius:50%;width:72px;height:72px;display:grid;place-items:center;color:var(--accent)}
        .open svg{width:30px;height:30px;fill:currentColor;stroke:none;margin-left:4px}.screen-tools{position:absolute;top:12px;right:12px;display:flex;gap:8px}.screen-tools button{padding:9px;background:#081116bf;border:1px solid #ffffff22;border-radius:12px;color:white}
        .toolbar{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;padding:16px 14px 10px}
        button{font:inherit;cursor:pointer;touch-action:manipulation}button:focus-visible{outline:2px solid var(--accent);outline-offset:3px}button:disabled{opacity:.35;cursor:default}
        .action{min-height:68px;border:1px solid #ffffff13;border-radius:14px;background:#1c2c36;color:#d1dce2;display:flex;align-items:center;justify-content:center;flex-direction:column;gap:7px;font-size:12px}
        svg{width:23px;height:23px}.action[aria-pressed=true]{background:#75e5c21c;color:var(--accent);border-color:#75e5c27a}.action.working{opacity:.65}
        .status{margin:0;padding:4px 18px 17px;min-height:32px;color:#a7bac6;font-size:12px;line-height:1.5}.status.error{color:#ffc0af}.hint{color:#718793;font-size:10px;letter-spacing:.5px;text-align:right;padding:0 18px 12px}
        [hidden]{display:none!important}:host(:fullscreen){background:#081116;display:grid;place-items:center} :host(:fullscreen) ha-card{width:min(100vw,1100px)}
        @media(max-width:360px){header{padding:15px}.toolbar{gap:5px;padding:12px 8px}.action{font-size:11px}}
      </style>
      <ha-card>
        <header><h2>WelcomeEye</h2><span class="badge">INTERPHONE</span></header>
        <div class="screen"><video playsinline autoplay muted></video><button class="open" aria-label="Ouvrir la vidéo">${svg('play')}</button>
          <div class="screen-tools"><button class="full" title="Plein écran" aria-label="Plein écran">${svg('full')}</button><button class="close" hidden title="Fermer et libérer la vidéo" aria-label="Fermer la vidéo">${svg('close')}</button></div>
        </div>
        <div class="toolbar">
          <button class="action sound" aria-pressed="false">${svg('sound')}<span>Son coupé</span></button>
          <button class="action mic" aria-pressed="false">${svg('mic')}<span>Micro coupé</span></button>
          <button class="action strike">${svg('strike')}<span>Gâche</span></button>
          <button class="action gate">${svg('gate')}<span>Portail</span></button>
        </div><p class="status" role="status" aria-live="polite"></p><div class="hint">PHILIPS WELCOMEEYE · LOCAL</div>
      </ha-card>`;
    this._video = this.shadowRoot.querySelector('video');
    this.shadowRoot.querySelector('.open').onclick = () => this._open();
    this.shadowRoot.querySelector('.close').onclick = () => this._close();
    this.shadowRoot.querySelector('.full').onclick = () => {
      if (document.fullscreenElement) document.exitFullscreen?.();
      else this.requestFullscreen?.().catch(() => this._message('Plein écran indisponible', true));
    };
    this.shadowRoot.querySelector('.sound').onclick = () => {
      this._video.muted = !this._video.muted;
      this._video.play().catch(() => this._message('Lecture du son bloquée par le navigateur', true));
      this._render();
    };
    this.shadowRoot.querySelector('.mic').onclick = () => this._toggleMicrophone();
    for (const name of ['strike', 'gate']) this.shadowRoot.querySelector('.'+name).onclick = () => this._output(name);
  }
  setConfig(config) {
    if (!config.entity || !config.entity.startsWith('camera.')) throw new Error('Choisissez une caméra WelcomeEye');
    if (this._config?.entity !== config.entity) this._close();
    this._config = {...config};
    this.shadowRoot.querySelector('h2').textContent = config.name || 'WelcomeEye';
    this._render();
  }
  set hass(hass) { this._hass = hass; this._render(); }
  getCardSize() { return 6; }
  static getStubConfig(hass) {
    return {entity: Object.keys(hass.states).find(id => id.startsWith('camera.') && hass.states[id].attributes.welcomeeye_player), name: 'WelcomeEye'};
  }
  static getConfigElement() { return document.createElement('welcomeeye-card-editor'); }
  connectedCallback() {
    document.addEventListener('visibilitychange', this._visibility);
    window.addEventListener('pagehide', this._pagehide);
    this._render();
  }
  disconnectedCallback() {
    document.removeEventListener('visibilitychange', this._visibility);
    window.removeEventListener('pagehide', this._pagehide);
    this._close();
  }
  _message(text, error=false) { this._status=text; this._error=error; this._render(); }
  _render() {
    const q = s => this.shadowRoot.querySelector(s);
    const available = this._hass?.states[this._config?.entity]?.state !== 'unavailable';
    q('.open').hidden = !!this._pc;
    q('.close').hidden = !this._pc;
    q('.badge').textContent = this._connected ? 'EN DIRECT' : this._pc ? 'CONNEXION' : 'INTERPHONE';
    q('.badge').classList.toggle('live', !!this._connected);
    q('.sound').disabled = !this._connected;
    q('.sound').setAttribute('aria-pressed', String(!this._video.muted));
    q('.sound span').textContent = this._video.muted ? 'Son coupé' : 'Son actif';
    q('.mic').disabled = !this._connected || !this._settings?.microphone_allowed || this._channel?.readyState !== 'open';
    q('.mic').setAttribute('aria-pressed', String(!!this._mic));
    q('.mic span').textContent = this._micPending ? 'Annuler micro' : this._mic ? 'Micro actif' : 'Micro coupé';
    for (const name of ['strike','gate']) {
      q('.'+name).disabled = !available || !this._connected || !!this._outputBusy || !this._settings?.buttons?.[name];
      q('.'+name).classList.toggle('working', this._outputBusy === name);
    }
    q('.status').textContent = this._status;
    q('.status').classList.toggle('error', !!this._error);
  }
  async _open() {
    if (this._pc || !this._hass || !this._config) return;
    const generation = ++this._generation;
    this._message('Connexion au visiophone…');
    try {
      const settings = await this._hass.callWS({type:'welcomeeye_local/player_config',entity_id:this._config.entity});
      if (generation !== this._generation || !this.isConnected) return;
      this._settings = settings;
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
        if (generation !== this._generation || message.type !== 'microphone' || message.id !== this._micId) return;
        if (!message.enabled || message.error) {
          this._stopMicrophone(false);
          this._message(message.error || 'Micro coupé', !!message.error);
          return;
        }
        this._micPending=false; this._mic=true;
        clearInterval(this._heartbeat); clearTimeout(this._micTimeout);
        for (const track of this._local?.getTracks() || []) track.enabled=true;
        this._heartbeat=setInterval(() => {
          if (dc.readyState === 'open') dc.send(JSON.stringify({type:'heartbeat'}));
        },1000);
        this._message('Micro actif — vous pouvez parler');
      };
      pc.ontrack = event => {
        if (generation !== this._generation) return;
        this._remote.addTrack(event.track);
        this._video.play().catch(() => {});
      };
      pc.onconnectionstatechange = () => {
        if (generation !== this._generation) return;
        if (pc.connectionState === 'connected') {
          clearTimeout(this._connectTimeout); clearTimeout(this._disconnectTimeout);
          this._connected=true; this._message('Vidéo en direct · micro coupé');
        } else if (['failed','closed'].includes(pc.connectionState)) {
          this._close(); this._message('Connexion interrompue. Rouvrez la vidéo pour réessayer.',true);
        } else if (pc.connectionState === 'disconnected') {
          this._stopMicrophone();
          this._disconnectTimeout=setTimeout(() => {
            if (this._pc === pc && pc.connectionState !== 'connected') { this._close(); this._message('Connexion perdue',true); }
          },3000);
        }
      };
      this._connectTimeout=setTimeout(() => {
        if (this._pc === pc && !this._connected) { this._close(); this._message('Le visiophone ne répond pas ou WebRTC est inaccessible. Consultez le diagnostic de connexion.',true); }
      },45000);
      this._render();
      await pc.setLocalDescription(await pc.createOffer());
      await new Promise((resolve,reject) => {
        let timer;
        const done=() => { clearTimeout(timer); pc.removeEventListener('icegatheringstatechange',check); pc.removeEventListener('connectionstatechange',check); };
        const check=() => {
          if (pc.connectionState === 'closed') { done(); reject(new Error('Connexion annulée')); }
          else if (pc.iceGatheringState === 'complete') { done(); resolve(); }
        };
        timer=setTimeout(() => {done(); resolve();},8000);
        pc.addEventListener('icegatheringstatechange',check); pc.addEventListener('connectionstatechange',check); check();
      });
      if (generation !== this._generation) return;
      const unsubscribe = await this._hass.connection.subscribeMessage(event => {
        if (generation !== this._generation) return;
        if (event.type === 'answer') pc.setRemoteDescription({type:'answer',sdp:event.answer}).catch(() => {
          if (generation === this._generation) { this._close(); this._message('Négociation vidéo impossible',true); }
        });
        else if (event.type === 'error') { this._close(); this._message(event.message || 'Connexion au visiophone impossible',true); }
      }, {type:'welcomeeye_local/player_offer',entity_id:this._config.entity,offer:pc.localDescription.sdp});
      if (generation !== this._generation) { unsubscribe(); return; }
      this._unsubscribe=unsubscribe;
    } catch (error) {
      if (generation === this._generation) { this._close(); this._message(error.message || 'Connexion impossible',true); }
    }
  }
  async _toggleMicrophone() {
    if (this._mic || this._micPending) { this._stopMicrophone(); this._message('Micro coupé'); return; }
    if (!this._connected || this._channel?.readyState !== 'open') return;
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
      await this._audioSender.replaceTrack(track);
      if (id !== this._micId || generation !== this._generation) return;
      this._channel.send(JSON.stringify({type:'microphone',enabled:true,id}));
      this._micTimeout=setTimeout(() => {
        if (id === this._micId && this._micPending) {this._stopMicrophone(); this._message('Le visiophone n’a pas confirmé le microphone',true);}
      },7000);
    } catch (error) {
      if (id === this._micId) {this._stopMicrophone(); this._message(error.name === 'NotAllowedError' ? 'Autorisez le microphone dans votre navigateur' : 'Microphone indisponible',true);}
    }
  }
  _stopMicrophone(notify=true) {
    const id=++this._micId;
    clearInterval(this._heartbeat); clearTimeout(this._micTimeout);
    this._mic=this._micPending=false;
    const local=this._local; this._local=null;
    local?.getTracks().forEach(t=>t.stop());
    this._audioSender?.replaceTrack(null).catch(()=>{});
    if (notify && this._channel?.readyState === 'open') this._channel.send(JSON.stringify({type:'microphone',enabled:false,id}));
    this._render();
  }
  async _output(name) {
    const entity_id=this._settings?.buttons?.[name];
    if (!this._connected || this._outputBusy || !entity_id) return;
    this._outputBusy=name; this._message('Envoi de la commande…');
    try {
      // One explicit click, one HA service call. Never automatically replay.
      await this._hass.callService('button','press',{entity_id});
      this._message('Commande confirmée par le visiophone');
    } catch (error) {
      this._message(error.message || 'Confirmation inconnue. Vérifiez sur place avant de réessayer.',true);
    } finally {this._outputBusy=null;this._render();}
  }
  _close() {
    ++this._generation;
    this._stopMicrophone();
    clearTimeout(this._connectTimeout);clearTimeout(this._disconnectTimeout);
    this._connected=false;
    const pc=this._pc;this._pc=null;this._channel=null;this._audioSender=null;
    if (pc) {pc.onconnectionstatechange=null;pc.close();}
    this._remote?.getTracks().forEach(t=>t.stop()); this._remote=null;
    this._video.srcObject=null;
    if (this._unsubscribe) {Promise.resolve(this._unsubscribe()).catch(()=>{});this._unsubscribe=null;}
    this._message('Vidéo fermée · micro coupé');
  }
}

class WelcomeEyeCardEditor extends HTMLElement {
  setConfig(config) {this._config=config;this._draw();}
  set hass(hass) {this._hass=hass;if (this._picker) this._picker.hass=hass;}
  _draw() {
    if (!this.shadowRoot) this.attachShadow({mode:'open'});
    this.shadowRoot.innerHTML='<ha-entity-picker></ha-entity-picker><p>Vidéo WebRTC, microphone et commandes dans un seul lecteur. Le micro nécessite HTTPS.</p>';
    this._picker=this.shadowRoot.querySelector('ha-entity-picker');
    Object.assign(this._picker,{hass:this._hass,value:this._config.entity,includeDomains:['camera'],label:'Caméra WelcomeEye'});
    this._picker.addEventListener('value-changed',event => {
      if (event.detail.value) this.dispatchEvent(new CustomEvent('config-changed',{detail:{config:{...this._config,entity:event.detail.value}},bubbles:true,composed:true}));
    });
  }
}
if (!customElements.get('welcomeeye-card')) customElements.define('welcomeeye-card',WelcomeEyeCard);
if (!customElements.get('welcomeeye-card-editor')) customElements.define('welcomeeye-card-editor',WelcomeEyeCardEditor);
window.customCards=window.customCards||[];
if (!window.customCards.some(c=>c.type==='welcomeeye-card')) window.customCards.push({type:'welcomeeye-card',name:'WelcomeEye — Interphone',description:'Vidéo, micro, gâche et portail dans un même lecteur',preview:true});
