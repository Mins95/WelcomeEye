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

class WelcomeEyeCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({mode: 'open'});
    this._generation = 0;
    this._actionGeneration = 0;
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
        ha-hls-player{position:absolute;inset:0;width:100%;height:100%}.screen-tools{z-index:2}
        .toolbar{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:6px;padding:16px 10px 10px}
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
        <div class="screen"><video playsinline autoplay muted></video><button class="open" aria-label="Ouvrir la vidéo">${svg('play')}</button>
          <div class="screen-tools"><button class="full" title="Plein écran" aria-label="Plein écran">${svg('full')}</button><button class="close" hidden title="Fermer et libérer la vidéo" aria-label="Fermer la vidéo">${svg('close')}</button></div>
        </div>
        <div class="toolbar">
          <button class="action sound" aria-pressed="false">${svg('sound')}<span>Son coupé</span></button>
          <button class="action mic" aria-pressed="false">${svg('mic')}<span>Micro coupé</span></button>
          <button class="action strike">${svg('strike')}<span>Gâche</span></button>
          <button class="action gate">${svg('gate')}<span>Portail</span></button>
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
    for (const name of ['strike', 'gate']) this.shadowRoot.querySelector('.'+name).onclick = () => this._output(name);
  }
  setConfig(config) {
    if (!config || typeof config.entity !== 'string' || !/^camera\.[a-z0-9_]+$/.test(config.entity)) throw new Error('Choisissez une caméra WelcomeEye');
    if (this._config?.entity !== config.entity) {
      ++this._actionGeneration;
      this._close();
      this._settings = null;
    }
    this._config = {...config};
    this.shadowRoot.querySelector('h2').textContent = config.name || 'WelcomeEye';
    this._render();
  }
  set hass(hass) { this._hass = hass; if (this._hls) this._hls.hass = hass; this._render(); }
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
    ++this._actionGeneration;
    this._close();
  }
  _cameraAvailable() {
    const camera = this._hass?.states[this._config?.entity];
    return !!camera && this._supports('camera') && camera.state !== 'unavailable' && camera.state !== 'unknown';
  }
  _supports(capability) {
    const capabilities = this._hass?.states[this._config?.entity]?.attributes?.welcomeeye_capabilities;
    // Older 0.4.2 backends have no matrix attribute; preserve their existing card.
    return capabilities === undefined ? true : capabilities[capability] === true;
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
  _message(text, error=false) { this._status=text; this._error=error; this._render(); }
  _render() {
    const q = s => this.shadowRoot.querySelector(s);
    const available = this._cameraAvailable();
    const active = !!(this._opening || this._pc || this._hls || this._fallbackPending);
    const muted = (this._hls || this._video).muted;
    for (const [control, capability] of Object.entries({sound:'downstream_audio',mic:'talkback',strike:'strike',gate:'gate',snapshot:'manual_snapshot'})) {
      q('.'+control).hidden = !this._supports(capability);
    }
    q('.open').hidden = active;
    q('.open').disabled = !available;
    q('.close').hidden = !active;
    q('.badge').textContent = this._connected ? (this._hls ? 'VIA HOME ASSISTANT' : 'EN DIRECT') : active ? 'CONNEXION' : 'INTERPHONE';
    q('.badge').classList.toggle('live', !!this._connected);
    q('.sound').disabled = !this._connected;
    q('.sound').setAttribute('aria-pressed', String(!muted));
    q('.sound span').textContent = muted ? 'Son coupé' : 'Son actif';
    q('.mic').disabled = !this._connected || !this._settings?.microphone_allowed || this._channel?.readyState !== 'open';
    q('.mic').setAttribute('aria-pressed', String(!!this._mic));
    q('.mic span').textContent = this._hls ? 'Micro indisponible' : this._micPending ? 'Annuler micro' : this._mic ? 'Micro actif' : 'Micro coupé';
    for (const name of ['strike','gate']) {
      q('.'+name).disabled = !available || !this._connected || !!this._outputBusy || !this._settings?.buttons?.[name];
      q('.'+name).classList.toggle('working', this._outputBusy === name);
    }
    q('.snapshot').disabled = !available || !!this._snapshotBusy;
    q('.snapshot').classList.toggle('working', !!this._snapshotBusy);
    q('.snapshot span').textContent = this._snapshotBusy ? 'Capture…' : 'Photo';
    q('.status').textContent = this._status;
    q('.status').classList.toggle('error', !!this._error);
  }
  async _open() {
    if (this._opening || this._pc || this._hls || this._fallbackPending || !this._cameraAvailable() || !this.isConnected || document.hidden) return;
    const generation = ++this._generation;
    this._opening = true;
    this._message('Connexion au visiophone…');
    try {
      const settings = await this._hass.callWS({type:'welcomeeye_local/player_config',entity_id:this._config.entity});
      if (generation !== this._generation || !this.isConnected) return;
      this._settings = settings;
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
          this._connected=true; this._message('Vidéo en direct · micro coupé');
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
      const unsubscribe = await this._hass.connection.subscribeMessage(event => {
        if (generation !== this._generation) return;
        if (event.type === 'answer') pc.setRemoteDescription({type:'answer',sdp:event.answer}).catch(() => {
          if (generation === this._generation) { this._close(); this._message('Négociation vidéo impossible',true); }
        });
        else if (event.type === 'error') { this._close(); this._message(event.message || 'Connexion au visiophone impossible',true); }
      }, {type:'welcomeeye_local/player_offer',entity_id:this._config.entity,offer:pc.localDescription.sdp});
      if (generation !== this._generation) { Promise.resolve().then(unsubscribe).catch(()=>{}); return; }
      this._unsubscribe=unsubscribe;
    } catch (error) {
      if (generation === this._generation) { this._close(); this._message(error.message || 'Connexion impossible',true); }
    } finally { if (generation === this._generation) { this._opening=false; this._render(); } }
  }
  async _fallback() {
    if ((!this._pc && !this._opening) || this._fallbackPending) return;
    // Close this viewer and its microphone before using HA's existing stream.
    // No second WelcomeEye session and no replay of physical commands.
    this._close();
    const generation = this._generation;
    this._fallbackPending = true;
    this._message('WebRTC inaccessible · ouverture de la vidéo via Home Assistant…');
    let timer;
    try {
      await Promise.race([
        (async () => {
          if (!customElements.get('ha-hls-player') && window.loadCardHelpers) {
            const helpers = await window.loadCardHelpers();
            if (generation !== this._generation || !this.isConnected) return;
            helpers.createCardElement({type:'picture-entity',entity:this._config.entity,camera_view:'live'});
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
      Object.assign(player, {hass:this._hass,entityid:this._config.entity,autoPlay:true,playsInline:true,muted:true,controls:false,fitMode:'contain'});
      player.addEventListener('load', () => {
        if (this._hls !== player) return;
        clearTimeout(this._fallbackTimeout);
        this._connected = true;
        this._message('Vidéo via Home Assistant · micro indisponible sur ce réseau');
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
    const entity_id=this._config.entity, generation=this._actionGeneration;
    this._snapshotBusy=true; this._message('Capture d’une photo fraîche…');
    try {
      // The backend shares the current media session; do not touch this viewer.
      const result=await this._hass.callWS({type:'call_service',domain:'welcomeeye_local',service:'capture_snapshot',target:{entity_id},service_data:{save_to_media:true},return_response:true});
      if (generation !== this._actionGeneration || !this.isConnected) return;
      const capture=result?.response?.[entity_id];
      if (capture?.saved) this._message('Photo enregistrée');
      else if (capture?.save_error) this._message('Photo capturée · enregistrement dans Médias impossible ('+capture.save_error+')',true);
      else this._message('Sauvegarde de la photo non confirmée par Home Assistant',true);
    } catch (error) {
      if (generation === this._actionGeneration && this.isConnected) this._message(this._actionError(error,'Capture'),true);
    } finally {this._snapshotBusy=false;this._render();}
  }
  async _output(name) {
    if (!['strike','gate'].includes(name) || !this._supports(name)) return;
    const entity_id=this._settings?.buttons?.[name];
    if (!this._cameraAvailable() || !this._connected || this._outputBusy || !entity_id) return;
    const generation=this._generation;
    this._outputBusy=name; this._message('Envoi de la commande…');
    try {
      // One explicit click, one HA service call. Never automatically replay.
      await this._hass.callService('button','press',{entity_id});
      if (generation === this._generation) this._message('Commande confirmée par le visiophone');
    } catch (error) {
      if (generation === this._generation) this._message(error.message || 'Confirmation inconnue. Vérifiez sur place avant de réessayer.',true);
    } finally {this._outputBusy=null;this._render();}
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
    if (this._unsubscribe) {const unsubscribe=this._unsubscribe;this._unsubscribe=null;Promise.resolve().then(unsubscribe).catch(()=>{});}
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
if (!window.customCards.some(c=>c.type==='welcomeeye-card')) window.customCards.push({type:'welcomeeye-card',name:'WelcomeEye — Interphone',description:'Vidéo, micro, commandes et captures dans un même lecteur',preview:true});
})();
