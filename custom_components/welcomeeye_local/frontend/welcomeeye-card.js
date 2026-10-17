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

const CARD_TEXT = {
  "Prêt à ouvrir la vidéo": [
    "Ready to open video",
    "Listo para abrir el vídeo"
  ],
  "Lecture du son bloquée par le navigateur": [
    "Audio playback blocked by the browser",
    "El navegador ha bloqueado el sonido"
  ],
  "Choisissez une caméra WelcomeEye": [
    "Choose a WelcomeEye camera",
    "Seleccione una cámara WelcomeEye"
  ],
  "Nom d’entrée invalide (64 caractères maximum)": [
    "Invalid display name (64 characters maximum)",
    "Nombre no válido (64 caracteres como máximo)"
  ],
  "Entrée ": [
    "Entrance ",
    "Entrada "
  ],
  "Vidéo": [
    "Video",
    "Vídeo"
  ],
  "vidéo": [
    "video",
    "vídeo"
  ],
  "Portillon ": [
    "Wicket gate ",
    "Puerta peatonal "
  ],
  "Portail ": [
    "Vehicle gate ",
    "Portal "
  ],
  "Accès aux commandes non autorisé.": [
    "You are not authorized to use these controls.",
    "No tiene permiso para utilizar estas órdenes."
  ],
  "Réglages de la carte indisponibles. Rechargez le tableau de bord.": [
    "Card settings unavailable. Reload the dashboard.",
    "Ajustes de la tarjeta no disponibles. Recargue el panel."
  ],
  "Sélection de ": [
    "Selecting ",
    "Seleccionando "
  ],
  "Fermeture du lecteur non confirmée ; aucun changement effectué.": [
    "Player closure was not confirmed; no source was changed.",
    "No se ha confirmado el cierre del reproductor; no se ha cambiado la fuente."
  ],
  "Lecteur Home Assistant fermé. Fermez les autres lecteurs et attendez la libération du flux avant de sélectionner une autre entrée.": [
    "Home Assistant player closed. Close other players and wait for stream release before selecting another entrance.",
    "Reproductor de Home Assistant cerrado. Cierre los demás y espere a que se libere el flujo antes de seleccionar otra entrada."
  ],
  " sélectionnée": [
    " selected",
    " seleccionada"
  ],
  "Plein écran indisponible dans ce navigateur": [
    "Fullscreen is unavailable in this browser",
    "La pantalla completa no está disponible en este navegador"
  ],
  "Sélectionner ": [
    "Select ",
    "Seleccionar "
  ],
  "Ouvrir la ": [
    "Open ",
    "Abrir "
  ],
  "Fermer la ": [
    "Close ",
    "Cerrar "
  ],
  "VIA HOME ASSISTANT": [
    "VIA HOME ASSISTANT",
    "MEDIANTE HOME ASSISTANT"
  ],
  "EN DIRECT": [
    "LIVE",
    "EN DIRECTO"
  ],
  "CONNEXION": [
    "CONNECTING",
    "CONECTANDO"
  ],
  "INTERPHONE": [
    "INTERCOM",
    "VIDEOPORTERO"
  ],
  "Son coupé": [
    "Sound off",
    "Sonido desactivado"
  ],
  "Son actif": [
    "Sound on",
    "Sonido activado"
  ],
  "Micro indisponible": [
    "Microphone unavailable",
    "Micrófono no disponible"
  ],
  "Annuler micro": [
    "Cancel microphone",
    "Cancelar micrófono"
  ],
  "Micro actif": [
    "Microphone on",
    "Micrófono activado"
  ],
  "Micro coupé": [
    "Microphone off",
    "Micrófono desactivado"
  ],
  " · à vérifier": [
    " · verify on site",
    " · verificar"
  ],
  "Commander ": [
    "Operate ",
    "Accionar "
  ],
  " : cible physique à confirmer, vérifiez le résultat sur place": [
    ": physical target needs confirmation; check the result on site",
    ": destino físico pendiente de confirmación; compruebe el resultado físicamente"
  ],
  "Ouvrez le direct pour prendre une photo": [
    "Open live video to take a photo",
    "Abra el vídeo para hacer una foto"
  ],
  "Enregistrer une photo fraîche dans Médias": [
    "Save a new photo to Media",
    "Guardar una foto nueva en Multimedia"
  ],
  "Capture…": [
    "Capturing…",
    "Capturando…"
  ],
  "Photo": [
    "Photo",
    "Foto"
  ],
  "Connexion ": [
    "Connecting ",
    "Conectando "
  ],
  "Fermeture précédente non confirmée. Rechargez la carte.": [
    "Previous closure was not confirmed. Reload the card.",
    "No se ha confirmado el cierre anterior. Recargue la tarjeta."
  ],
  "Micro coupé : canal de contrôle fermé": [
    "Microphone off: control channel closed",
    "Micrófono desactivado: canal de control cerrado"
  ],
  "Micro actif — vous pouvez parler": [
    "Microphone on — you can speak",
    "Micrófono activado — puede hablar"
  ],
  " en direct · micro coupé": [
    " live · microphone off",
    " en directo · micrófono desactivado"
  ],
  "Connexion interrompue. Rouvrez la vidéo pour réessayer.": [
    "Connection interrupted. Reopen video to try again.",
    "Conexión interrumpida. Vuelva a abrir el vídeo para intentarlo de nuevo."
  ],
  "Connexion interrompue · micro coupé": [
    "Connection interrupted · microphone off",
    "Conexión interrumpida · micrófono desactivado"
  ],
  "Négociation vidéo impossible": [
    "Video negotiation failed",
    "Ha fallado la negociación del vídeo"
  ],
  "Connexion au visiophone impossible": [
    "Unable to connect to the intercom",
    "No se puede conectar al videoportero"
  ],
  "Connexion impossible": [
    "Unable to connect",
    "No se puede conectar"
  ],
  "WebRTC inaccessible · ouverture de la vidéo via Home Assistant…": [
    "WebRTC unavailable · opening video through Home Assistant…",
    "WebRTC no disponible · abriendo el vídeo mediante Home Assistant…"
  ],
  "Fermeture du lecteur non confirmée": [
    "Player closure was not confirmed",
    "No se ha confirmado el cierre del reproductor"
  ],
  " via Home Assistant · micro indisponible sur ce réseau": [
    " through Home Assistant · microphone unavailable on this network",
    " mediante Home Assistant · micrófono no disponible en esta red"
  ],
  "Flux vidéo indisponible via Home Assistant": [
    "Video stream unavailable through Home Assistant",
    "Flujo de vídeo no disponible mediante Home Assistant"
  ],
  "La vidéo reste inaccessible via Home Assistant": [
    "Video is still unavailable through Home Assistant",
    "El vídeo sigue sin estar disponible mediante Home Assistant"
  ],
  "Pour le micro, ouvrez Home Assistant en HTTPS puis autorisez le microphone.": [
    "For the microphone, open Home Assistant over HTTPS and allow microphone access.",
    "Para usar el micrófono, abra Home Assistant mediante HTTPS y autorice el acceso al micrófono."
  ],
  "Microphone déconnecté": [
    "Microphone disconnected",
    "Micrófono desconectado"
  ],
  "Le visiophone n’a pas confirmé le microphone": [
    "The intercom did not confirm the microphone",
    "El videoportero no ha confirmado el micrófono"
  ],
  "Autorisez le microphone dans votre navigateur": [
    "Allow microphone access in your browser",
    "Autorice el acceso al micrófono en su navegador"
  ],
  "Microphone indisponible": [
    "Microphone unavailable",
    "Micrófono no disponible"
  ],
  " refusée : autorisation Home Assistant insuffisante": [
    " denied: insufficient Home Assistant permission",
    " rechazada: permiso de Home Assistant insuficiente"
  ],
  " impossible : ": [
    " failed: ",
    " no disponible: "
  ],
  "Home Assistant n’a pas confirmé la demande": [
    "Home Assistant did not confirm the request",
    "Home Assistant no ha confirmado la solicitud"
  ],
  "Ouvrez le direct de cette entrée pour prendre une photo.": [
    "Open this entrance's live video to take a photo.",
    "Abra el vídeo de esta entrada para hacer una foto."
  ],
  "Capture d’une photo fraîche…": [
    "Capturing a new photo…",
    "Capturando una foto nueva…"
  ],
  "Photo enregistrée": [
    "Photo saved",
    "Foto guardada"
  ],
  "Photo capturée · enregistrement dans Médias impossible (": [
    "Photo captured · unable to save to Media (",
    "Foto capturada · no se puede guardar en Multimedia ("
  ],
  "Sauvegarde de la photo non confirmée par Home Assistant": [
    "Photo saving was not confirmed by Home Assistant",
    "Home Assistant no ha confirmado que se haya guardado la foto"
  ],
  "Capture": [
    "Capture",
    "Captura"
  ],
  "Commande ": [
    "Command ",
    "Orden "
  ],
  "Fermeture du lecteur non confirmée ; aucune commande envoyée.": [
    "Player closure was not confirmed; no command was sent.",
    "No se ha confirmado el cierre del reproductor; no se ha enviado ninguna orden."
  ],
  "Libération du flux non confirmée. Fermez les autres lecteurs avant de commander une autre entrée.": [
    "Stream release was not confirmed. Close other players before operating another entrance.",
    "No se ha confirmado la liberación del flujo. Cierre los demás reproductores antes de accionar otra entrada."
  ],
  "Autorisation de la commande indisponible ; aucune commande envoyée.": [
    "Command authorization unavailable; no command was sent.",
    "Autorización de la orden no disponible; no se ha enviado ninguna orden."
  ],
  "Autorisation de la commande non confirmée ; aucune commande envoyée.": [
    "Command authorization was not confirmed; no command was sent.",
    "No se ha confirmado la autorización; no se ha enviado ninguna orden."
  ],
  "Fermeture précédente non confirmée ; aucune commande envoyée.": [
    "Previous closure was not confirmed; no command was sent.",
    "No se ha confirmado el cierre anterior; no se ha enviado ninguna orden."
  ],
  "Cette commande a été désactivée ou modifiée ; aucune commande envoyée.": [
    "This command was disabled or changed; no command was sent.",
    "Esta orden se ha desactivado o modificado; no se ha enviado ninguna orden."
  ],
  " : commande confirmée par le visiophone": [
    ": command confirmed by the intercom",
    ": orden confirmada por el videoportero"
  ],
  ", vérifiez le résultat physique.": [
    ", check the physical result.",
    ", compruebe el resultado físico."
  ],
  "Session de lecteur déjà fermée": [
    "Player session already closed",
    "Sesión del reproductor ya cerrada"
  ],
  "Session de lecteur inconnue": [
    "Unknown player session",
    "Sesión del reproductor desconocida"
  ],
  "Arrêt du lecteur non confirmé": [
    "Player stop was not confirmed",
    "No se ha confirmado la parada del reproductor"
  ],
  " fermée · micro coupé": [
    " closed · microphone off",
    " cerrado · micrófono desactivado"
  ],
  "Caméra WelcomeEye": [
    "WelcomeEye camera",
    "Cámara WelcomeEye"
  ],
  "Nom entrée 1": [
    "Entrance 1 name",
    "Nombre de la entrada 1"
  ],
  "Nom entrée 2": [
    "Entrance 2 name",
    "Nombre de la entrada 2"
  ],
  "Nom portillon 1": [
    "Wicket gate 1 name",
    "Nombre de la puerta peatonal 1"
  ],
  "Nom portillon 2": [
    "Wicket gate 2 name",
    "Nombre de la puerta peatonal 2"
  ],
  "Nom portail 1": [
    "Vehicle gate 1 name",
    "Nombre del portal 1"
  ],
  "Nom portail 2": [
    "Vehicle gate 2 name",
    "Nombre del portal 2"
  ],
  "Les noms modifient uniquement l’affichage. Les commandes disponibles dépendent des options de l’intégration.": [
    "Names only change the display. Available commands depend on integration options.",
    "Los nombres solo cambian la presentación. Las órdenes disponibles dependen de las opciones de la integración."
  ],
  "Plein écran": [
    "Fullscreen",
    "Pantalla completa"
  ],
  "Fermer et libérer la vidéo": [
    "Close and release video",
    "Cerrar y liberar el vídeo"
  ],
  "Caméra inaccessible": [
    "Camera unavailable",
    "Cámara no disponible"
  ],
  "Format microphone annoncé par le visiophone non pris en charge": [
    "The intercom's microphone format is unsupported",
    "El formato de micrófono anunciado por el videoportero no es compatible"
  ],
  "Microphone refusé par le visiophone": [
    "Microphone rejected by the intercom",
    "El videoportero ha rechazado el micrófono"
  ],
  "Le visiophone n’a pas confirmé le microphone à temps": [
    "The intercom did not confirm the microphone in time",
    "El videoportero no ha confirmado el micrófono a tiempo"
  ],
  "Microphone indisponible ; consulter le diagnostic de l’intégration": [
    "Microphone unavailable; see integration diagnostics",
    "Micrófono no disponible; consulte los diagnósticos de la integración"
  ],
  "Nombre maximal de lecteurs atteint ou caméra arrêtée": [
    "Maximum number of viewers reached or camera stopped",
    "Se ha alcanzado el número máximo de reproductores o la cámara está detenida"
  ],
  "Micro coupé : connexion audio interrompue": [
    "Microphone off: audio connection interrupted",
    "Micrófono desactivado: conexión de audio interrumpida"
  ],
  "Fermez les autres lecteurs et attendez la libération du flux avant de changer d’entrée.": [
    "Close other players and wait for stream release before changing entrance.",
    "Cierre los demás reproductores y espere a que se libere el flujo antes de cambiar de entrada."
  ],
  "Impossible de démarrer la vidéo WelcomeEye": [
    "Unable to start WelcomeEye video",
    "No se puede iniciar el vídeo WelcomeEye"
  ]
};
const EDITOR_LABELS = {channel_1_name:'Nom entrée 1',channel_2_name:'Nom entrée 2',strike_1_name:'Nom portillon 1',strike_2_name:'Nom portillon 2',gate_1_name:'Nom portail 1',gate_2_name:'Nom portail 2'};
const presentationLanguage = (hass,entity,settings) => {
  const configured=hass?.states?.[entity]?.attributes?.welcomeeye_language || settings?.language;
  const selected=['fr','en','es'].includes(configured) ? configured : (hass?.language || 'fr').split('-')[0];
  return ['fr','en','es'].includes(selected) ? selected : 'en';
};
const CARD_SOURCE = new Map(Object.entries(CARD_TEXT).flatMap(([key,values])=>values.map(value=>[value,key])));
const translated = (text,language) => {
  const key=CARD_TEXT[text] ? text : CARD_SOURCE.get(text);
  if (language==='fr') return key || text;
  return CARD_TEXT[key]?.[language==='es' ? 1 : 0] || text;
};

class WelcomeEyeCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({mode: 'open'});
    this._generation = 0;
    this._actionGeneration = 0;
    this._micId = 0;
    this._settingsRevision = 0;
    this._status = this._t("Prêt à ouvrir la vidéo");
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
          <button class="action strike-2" hidden>${svg('strike')}<span>Portillon 2 · à vérifier</span></button>
          <button class="action gate-2" hidden>${svg('gate')}<span>Portail 2 · à vérifier</span></button>
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
        if (generation === this._generation) this._message(this._t("Lecture du son bloquée par le navigateur"), true);
      });
      this._render();
    };
    this.shadowRoot.querySelector('.mic').onclick = () => this._toggleMicrophone();
    this.shadowRoot.querySelector('.snapshot').onclick = () => this._snapshot();
    for (const channel of [1,2]) this.shadowRoot.querySelector('.channel-'+channel).onclick = () => this._selectChannel(channel);
    for (const [target,descriptor] of Object.entries(OUTPUT_TARGETS)) this.shadowRoot.querySelector(descriptor.selector).onclick = () => this._output(target);
  }
  setConfig(config) {
    if (!config || typeof config.entity !== 'string' || !/^camera\.[a-z0-9_]+$/.test(config.entity)) throw new Error(this._t("Choisissez une caméra WelcomeEye"));
    for (const field of NAME_FIELDS) {
      if (config[field] !== undefined && (typeof config[field] !== 'string' || config[field].length > 64)) throw new Error(this._t("Nom d’entrée invalide (64 caractères maximum)"));
    }
    if (this._config?.entity !== config.entity) {
      ++this._actionGeneration;
      this._close();
      this._settings = null;
      this._outputSettings = null;
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
  _t(text) {return translated(text,presentationLanguage(this._hass,this._entity(),this._settings));}
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
  _channelName(number) { return this._config?.['channel_'+number+'_name'] || this._t('Entrée ')+number; }
  _videoLabel(capitalized=true) { return (capitalized ? this._t("Vidéo") : this._t("vidéo"))+(this._channels?.length===2 ? ' '+this._channelName(this._channelNumber()) : ''); }
  _outputName(target) { return this._config?.[target+'_name'] || (target.startsWith('strike') ? this._t("Portillon ") : this._t("Portail "))+OUTPUT_TARGETS[target].channel; }
  _outputConfig(target) {
    const expected=OUTPUT_TARGETS[target];if (!expected) return null;
    const settings=this._settings || this._outputSettings;
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
        state.attributes.welcomeeye_multichannel_available,state.attributes.welcomeeye_output_target,state.attributes.welcomeeye_language]).sort((a,b)=>a[0].localeCompare(b[0]));
    return JSON.stringify([entity,!!states[entity],this._hass?.connection?.connected,states[entity]?.attributes?.welcomeeye_language,related]);
  }
  _applySettings(settings, entity, key=this._metadataKey(entity)) {
    clearTimeout(this._settingsRetryTimer);this._settingsRetryTimer=null;this._settingsRetryCount=0;
    this._settings=settings; this._outputSettings=settings; this._settingsEntity=entity;
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
        this._message(forbidden ? this._t("Accès aux commandes non autorisé.") : this._t("Réglages de la carte indisponibles. Rechargez le tableau de bord."),true);
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
    this._message(this._t("Sélection de ")+this._channelName(number)+'…');
    try {
      if (!(await closing)) throw new Error(this._t("Fermeture du lecteur non confirmée ; aucun changement effectué."));
      if (generation!==this._generation || !this.isConnected || document.hidden) return;
      // HA owns its HLS lease: removing the browser player does not acknowledge
      // upstream media release. A later explicit attempt is still checked by HA.
      if (wasHls) throw new Error(this._t("Lecteur Home Assistant fermé. Fermez les autres lecteurs et attendez la libération du flux avant de sélectionner une autre entrée."));
      this._selectedEntity=target.entity_id;this._settings=null;this._settingsEntity=null;this._invalidateSettings();
      this._message(this._channelName(number)+this._t(" sélectionnée"));
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
    } catch { this._message(this._t("Plein écran indisponible dans ce navigateur"), true); }
  }
  _message(text, error=false) {
    this._statusMessage=typeof text==='function' ? text : null;
    this._status=this._statusMessage ? this._statusMessage() : this._t(text);
    this._error=error; this._render();
  }
  _render() {
    const q = s => this.shadowRoot.querySelector(s);
    q('.full').title=this._t('Plein écran');q('.full').setAttribute('aria-label',this._t('Plein écran'));
    q('.close').title=this._t('Fermer et libérer la vidéo');
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
      q('.channel-'+channel).setAttribute('aria-label',this._t("Sélectionner ")+this._channelName(channel));
      q('.channel-'+channel).setAttribute('aria-pressed',String(channel===this._channelNumber()));
      q('.channel-'+channel).disabled=!!(this._switching || this._outputBusy);
    }
    q('.open').setAttribute('aria-label',this._t("Ouvrir la ")+this._videoLabel(false));
    q('.close').setAttribute('aria-label',this._t("Fermer la ")+this._videoLabel(false));
    q('.open').hidden = active;
    q('.open').disabled = !available || !!this._switching || !!this._outputBusy;
    q('.close').hidden = !active;
    q('.badge').textContent = this._connected ? (this._hls ? this._t("VIA HOME ASSISTANT") : this._t("EN DIRECT")) : active ? this._t("CONNEXION") : this._t("INTERPHONE");
    q('.badge').classList.toggle('live', !!this._connected);
    q('.sound').disabled = !this._connected;
    q('.sound').setAttribute('aria-pressed', String(!muted));
    q('.sound span').textContent = muted ? this._t("Son coupé") : this._t("Son actif");
    q('.mic').disabled = !this._connected || !this._settings?.microphone_allowed || this._channel?.readyState !== 'open';
    q('.mic').setAttribute('aria-pressed', String(!!this._mic));
    q('.mic span').textContent = this._hls ? this._t("Micro indisponible") : this._micPending ? this._t("Annuler micro") : this._mic ? this._t("Micro actif") : this._t("Micro coupé");
    for (const [target,descriptor] of Object.entries(OUTPUT_TARGETS)) {
      const output=this._outputConfig(target), button=q(descriptor.selector), name=this._outputName(target);
      const trial=output?.validation_status==='hardware_pending';
      button.hidden=(this._settings || this._outputSettings) ? !output : descriptor.channel===2 || !this._supports(target.split('_')[0]);
      button.disabled=button.hidden || !available || !!this._outputBusy || !!this._switching || !output || this._settingsEntity!==this._entity();
      button.classList.toggle('working',this._outputBusy===target);
      q(descriptor.selector+' span').textContent=name+(trial ? this._t(" · à vérifier") : '');
      button.setAttribute('aria-label',this._t("Commander ")+name+' — '+this._channelName(descriptor.channel)+(trial ? this._t(" · à vérifier") : ''));
      button.title=trial ? name+this._t(" : cible physique à confirmer, vérifiez le résultat sur place") : name;
      const context={entity:this._entity(),generation:this._generation,actionGeneration:this._actionGeneration};
      button.onclick=()=>this._output(target,context);
    }
    const snapshotNeedsLive=this._hass?.states[this._entity()]?.attributes?.welcomeeye_snapshot_requires_live===true;
    q('.snapshot').disabled = !available || !!this._snapshotBusy || (snapshotNeedsLive && !this._connected);
    q('.snapshot').title=snapshotNeedsLive && !this._connected ? this._t("Ouvrez le direct pour prendre une photo") : this._t("Enregistrer une photo fraîche dans Médias");
    q('.snapshot').classList.toggle('working', !!this._snapshotBusy);
    q('.snapshot span').textContent = this._snapshotBusy ? this._t("Capture…") : this._t("Photo");
    if (this._statusMessage) this._status=this._statusMessage();
    q('.status').textContent = this._t(this._status);
    q('.status').classList.toggle('error', !!this._error);
  }
  async _open() {
    if (this._opening || this._pc || this._hls || this._fallbackPending || this._outputBusy || !this._cameraAvailable() || !this.isConnected || document.hidden) return;
    const generation = ++this._generation;
    this._opening = true;
    this._message(()=> this._t("Connexion ")+this._videoLabel(false)+'…');
    try {
      if (this._closePromise && !(await this._closePromise)) throw new Error(this._t("Fermeture précédente non confirmée. Rechargez la carte."));
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
        this._message(this._t("Micro coupé : canal de contrôle fermé"),true);
      };
      dc.onmessage = event => {
        let message;
        try { message=JSON.parse(event.data); } catch { return; }
        if (generation !== this._generation || !message || message.type !== 'microphone' || message.id !== this._micId) return;
        if (!message.enabled || message.error) {
          this._stopMicrophone(false);
          this._message(message.error || this._t("Micro coupé"), !!message.error);
          return;
        }
        this._micPending=false; this._mic=true;
        clearInterval(this._heartbeat); clearTimeout(this._micTimeout);
        for (const track of this._local?.getTracks() || []) track.enabled=true;
        this._heartbeat=setInterval(() => {
          try {
            if (dc.readyState === 'open') dc.send(JSON.stringify({type:'heartbeat'}));
            else this._stopMicrophone(false);
          } catch { this._stopMicrophone(false); this._message(this._t("Micro coupé : canal de contrôle fermé"),true); }
        },1000);
        this._message(this._t("Micro actif — vous pouvez parler"));
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
          this._connected=true; this._message(()=>this._videoLabel()+this._t(" en direct · micro coupé"));
        } else if (pc.connectionState === 'failed') {
          this._fallback();
        } else if (pc.connectionState === 'closed') {
          this._close(); this._message(this._t("Connexion interrompue. Rouvrez la vidéo pour réessayer."),true);
        } else if (pc.connectionState === 'disconnected') {
          this._connected=false;
          this._stopMicrophone();
          clearTimeout(this._disconnectTimeout);
          this._message(this._t("Connexion interrompue · micro coupé"),true);
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
          if (generation === this._generation) { this._close(); this._message(this._t("Négociation vidéo impossible"),true); }
        });
        else if (event.type === 'error') { this._close(); this._message(event.message || this._t("Connexion au visiophone impossible"),true); }
      }, {type:'welcomeeye_local/player_offer',entity_id:entity,offer:pc.localDescription.sdp}).then(unsubscribe=>{
        request.unsubscribe=unsubscribe;if (request.abandoned) this._unsubscribeRequest(request);return unsubscribe;
      });
      await request.ready;
      if (generation !== this._generation) {await this._releaseRequest(request);return;}
    } catch (error) {
      if (generation === this._generation) { this._close(); this._message(error.message || this._t("Connexion impossible"),true); }
    } finally { if (generation === this._generation) { this._opening=false; this._render(); } }
  }
  async _fallback() {
    if ((!this._pc && !this._opening) || this._fallbackPending) return;
    // Close this viewer and its microphone before using HA's existing stream.
    // No second WelcomeEye session and no replay of physical commands.
    const closing=this._close();
    const generation = this._generation;
    this._fallbackPending = true;
    this._message(this._t("WebRTC inaccessible · ouverture de la vidéo via Home Assistant…"));
    let timer;
    try {
      if (!(await closing)) throw new Error(this._t("Fermeture du lecteur non confirmée"));
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
        this._message(()=>this._videoLabel()+this._t(" via Home Assistant · micro indisponible sur ce réseau"));
      });
      player.addEventListener('streams', event => {
        if (this._hls === player && !event.detail.hasVideo) {
          this._connected = false;
          this._message(this._t("Flux vidéo indisponible via Home Assistant"),true);
        }
      });
      this._video.hidden = true;
      this._fallbackPending = false;
      this._fallbackTimeout = setTimeout(() => {
        if (this._hls === player && !this._connected) {
          this._close(); this._message(this._t("La vidéo reste inaccessible via Home Assistant"),true);
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
    if (this._mic || this._micPending) { this._stopMicrophone(); this._message(this._t("Micro coupé")); return; }
    if (!this._connected || !this._settings?.microphone_allowed || this._channel?.readyState !== 'open') return;
    if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia) {
      this._message(this._t("Pour le micro, ouvrez Home Assistant en HTTPS puis autorisez le microphone."),true); return;
    }
    const id=++this._micId, generation=this._generation;
    this._micPending=true; this._render();
    try {
      const stream=await navigator.mediaDevices.getUserMedia({audio:{echoCancellation:true,noiseSuppression:true,autoGainControl:true,channelCount:1},video:false});
      if (id !== this._micId || generation !== this._generation) { stream.getTracks().forEach(t=>t.stop()); return; }
      this._local=stream;
      const track=stream.getAudioTracks()[0]; track.enabled=false;
      track.onended=() => { if (this._local === stream) {this._stopMicrophone(); this._message(this._t("Microphone déconnecté"),true);} };
      await this._replaceMicrophoneTrack(track,id);
      if (id !== this._micId || generation !== this._generation) return;
      this._channel.send(JSON.stringify({type:'microphone',enabled:true,id}));
      this._micTimeout=setTimeout(() => {
        if (id === this._micId && this._micPending) {this._stopMicrophone(); this._message(this._t("Le visiophone n’a pas confirmé le microphone"),true);}
      },7000);
    } catch (error) {
      if (id === this._micId) {this._stopMicrophone(); this._message(error.name === 'NotAllowedError' ? this._t("Autorisez le microphone dans votre navigateur") : this._t("Microphone indisponible"),true);}
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
    if (error?.code === 'unauthorized' || error?.code === 'unauthorized_entity') return action+this._t(" refusée : autorisation Home Assistant insuffisante");
    return action+this._t(" impossible : ")+(error?.message || this._t("Home Assistant n’a pas confirmé la demande"));
  }
  async _snapshot() {
    if (!this._supports('manual_snapshot')) return;
    if (!this._cameraAvailable() || this._snapshotBusy) return;
    if (this._hass?.states[this._entity()]?.attributes?.welcomeeye_snapshot_requires_live===true && !this._connected) {
      this._message(this._t("Ouvrez le direct de cette entrée pour prendre une photo."));return;
    }
    const entity_id=this._entity(), generation=this._actionGeneration;
    this._snapshotBusy=true; this._message(this._t("Capture d’une photo fraîche…"));
    try {
      // The backend shares the current media session; do not touch this viewer.
      const result=await this._hass.callWS({type:'call_service',domain:'welcomeeye_local',service:'capture_snapshot',target:{entity_id},service_data:{save_to_media:true},return_response:true});
      if (generation !== this._actionGeneration || !this.isConnected || entity_id!==this._entity()) return;
      const capture=result?.response?.[entity_id];
      if (capture?.saved) this._message(this._t("Photo enregistrée"));
      else if (capture?.save_error) this._message(this._t("Photo capturée · enregistrement dans Médias impossible (")+capture.save_error+')',true);
      else this._message(this._t("Sauvegarde de la photo non confirmée par Home Assistant"),true);
    } catch (error) {
      if (generation === this._actionGeneration && this.isConnected && entity_id===this._entity()) this._message(this._actionError(error,this._t("Capture")),true);
    } finally {this._snapshotBusy=false;this._render();}
  }
  async _output(target,clickContext=null) {
    if (target==='strike' || target==='gate') target+='_1';
    const output=this._outputConfig(target);
    if (!this._cameraAvailable() || this._outputBusy || this._switching || !output || !this.isConnected || document.hidden
        || this._settingsEntity!==this._entity()) return;
    if (clickContext && (clickContext.entity!==this._entity() || clickContext.generation!==this._generation
        || clickContext.actionGeneration!==this._actionGeneration)) return;
    const entity_id=output.entity_id, name=this._outputName(target);
    const camera=this._entity(), actionGeneration=this._actionGeneration;
    let generation=this._generation;
    this._outputBusy=target; this._message(this._t("Commande ")+name+'…');
    try {
      const crossChannel=output.channel!==this._channelNumber();
      const viewer=!!(this._pc || this._opening || this._hls || this._fallbackPending || this._viewerRequest);
      if (crossChannel && viewer) {
        const acknowledgedStop=!this._hls && !this._fallbackPending && this._viewerRequest?.stopSupported===true;
        const closing=this._close();generation=this._generation;
        if (!(await closing)) throw new Error(this._t("Fermeture du lecteur non confirmée ; aucune commande envoyée."));
        if (!acknowledgedStop) throw new Error(this._t("Libération du flux non confirmée. Fermez les autres lecteurs avant de commander une autre entrée."));
        if (generation!==this._generation || camera!==this._entity() || actionGeneration!==this._actionGeneration || !this.isConnected || document.hidden) return;
        // Refresh the authorized targets after release: a reconfiguration made
        // while stopping the viewer must not leave a disabled relay clickable.
        this._invalidateSettings();this._refreshSettings();
        const request=this._settingsRequest;
        if (!request) throw new Error(this._t("Autorisation de la commande indisponible ; aucune commande envoyée."));
        await request.promise;
        if (this._settingsEntity!==camera || this._settingsKey!==this._metadataKey(camera)) throw new Error(this._t("Autorisation de la commande non confirmée ; aucune commande envoyée."));
      } else if (this._closePromise && !(await this._closePromise)) {
        throw new Error(this._t("Fermeture précédente non confirmée ; aucune commande envoyée."));
      }
      const current=this._outputConfig(target);
      if (generation!==this._generation || camera!==this._entity() || actionGeneration!==this._actionGeneration
          || !this.isConnected || document.hidden || !this._cameraAvailable()) return;
      if (!current || current.entity_id!==entity_id || current.channel!==output.channel || current.output!==output.output) {
        throw new Error(this._t("Cette commande a été désactivée ou modifiée ; aucune commande envoyée."));
      }
      // The source selected in the card never determines the physical target.
      // One explicit click, one HA service call. Never automatically replay.
      await this._hass.callService('button','press',{entity_id});
      if (generation === this._generation && this.isConnected && !document.hidden) this._message(name+this._t(" : commande confirmée par le visiophone")+(output.validation_status==='hardware_pending' ? this._t(", vérifiez le résultat physique.") : '.'));
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
          if (request.abandoned) throw new Error(this._t("Session de lecteur déjà fermée"));
          if (request.stopSupported) {
            await request.tokenReady;
            if (request.abandoned) throw new Error(this._t("Session de lecteur déjà fermée"));
            if (!/^[a-f0-9]{32}$/.test(request.token || '')) throw new Error(this._t("Session de lecteur inconnue"));
            const result=await this._hass.callWS({type:'welcomeeye_local/player_stop',entity_id:request.entity,session_id:request.token});
            if (result?.stopped!==true) throw new Error(this._t("Arrêt du lecteur non confirmé"));
          }
        })(),new Promise((_,reject)=>{timer=setTimeout(()=>reject(new Error(this._t("Arrêt du lecteur non confirmé"))),30000);})]);
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
    this._message(()=>this._videoLabel()+this._t(" fermée · micro coupé"));
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
  _t(text) {return translated(text,presentationLanguage(this._hass,this._config?.entity));}
  _updateFieldVisibility() {
    if (!this.shadowRoot) return;
    for (const [field,label] of Object.entries(EDITOR_LABELS)) {
      const span=this.shadowRoot.querySelector('.'+field.replaceAll('_','-')+'-row span');
      if(span)span.textContent=this._t(label);
    }
    if (this._picker) this._picker.label=this._t('Caméra WelcomeEye');
    const hint=this.shadowRoot.querySelector('p');
    if(hint)hint.textContent=this._t('Les noms modifient uniquement l’affichage. Les commandes disponibles dépendent des options de l’intégration.');
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
    const labels={channel_1_name:this._t("Nom entrée 1"),channel_2_name:this._t("Nom entrée 2"),strike_1_name:this._t("Nom portillon 1"),strike_2_name:this._t("Nom portillon 2"),gate_1_name:this._t("Nom portail 1"),gate_2_name:this._t("Nom portail 2")};
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
    Object.assign(this._picker,{hass:this._hass,value:this._config.entity,includeDomains:['camera'],label:this._t("Caméra WelcomeEye")});
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
