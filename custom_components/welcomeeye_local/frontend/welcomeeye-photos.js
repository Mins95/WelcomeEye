/* Supported HA custom panel; authenticated WS only, no filesystem paths. */
(() => {
const TEXT = {
  fr: {title: 'Photos WelcomeEye', device: 'Appareil', day: 'Journée', refresh: 'Actualiser',
    all: 'Tout sélectionner', none: 'Désélectionner', selected: 'photo(s) sélectionnée(s)',
    delete: 'Supprimer la sélection', deleteDay: 'Supprimer cette journée',
    deleteDevice: 'Supprimer toutes les photos de cet appareil', empty: 'Aucune photo enregistrée.',
    noDevice: 'Aucun appareil WelcomeEye chargé.', confirm: 'Supprimer définitivement',
    photos: 'photo(s)', from: 'de', cancel: 'Annuler', loading: 'Chargement…',
    error: 'Opération impossible. Actualise la liste et vérifie tes droits administrateur.',
    expired: 'La confirmation a expiré. Actualise puis recommence.',
    limit: 'Bibliothèque trop volumineuse pour cette opération. Choisis une journée.',
    deleted: 'supprimée(s)', missing: 'déjà absente(s)', failed: 'échec(s)',
    folders: 'dossier(s) vide(s)', clean: 'Nettoyer les dossiers vides',
    previous: 'Précédent', next: 'Suivant', page: 'Page', view: 'Afficher la photo',
    media: 'Consulter dans Média', admin: 'Accès administrateur requis.', close: 'Fermer',
    retention: 'Conservation', unlimited: 'illimitée', days: 'jours'},
  en: {title: 'WelcomeEye photos', device: 'Device', day: 'Day', refresh: 'Refresh',
    all: 'Select all', none: 'Clear selection', selected: 'photo(s) selected',
    delete: 'Delete selection', deleteDay: 'Delete this day',
    deleteDevice: 'Delete all photos of this device', empty: 'No saved photos.',
    noDevice: 'No loaded WelcomeEye device.', confirm: 'Permanently delete',
    photos: 'photo(s)', from: 'from', cancel: 'Cancel', loading: 'Loading…',
    error: 'Operation unavailable. Refresh the list and check administrator access.',
    expired: 'Confirmation expired. Refresh and try again.',
    limit: 'Library too large for this operation. Choose a day.',
    deleted: 'deleted', missing: 'already missing', failed: 'failed',
    folders: 'empty folder(s)', clean: 'Clean up empty folders',
    previous: 'Previous', next: 'Next', page: 'Page', view: 'View photo',
    media: 'Browse in Media', admin: 'Administrator access required.', close: 'Close',
    retention: 'Retention', unlimited: 'unlimited', days: 'days'}
};
TEXT.es = {
  title:'Fotos WelcomeEye', device:'Dispositivo', day:'Día', refresh:'Actualizar',
  all:'Seleccionar todo', none:'Quitar selección', selected:'foto(s) seleccionada(s)',
  delete:'Eliminar la selección', deleteDay:'Eliminar este día',
  deleteDevice:'Eliminar todas las fotos de este dispositivo', empty:'No hay fotos guardadas.',
  noDevice:'No hay ningún dispositivo WelcomeEye cargado.', confirm:'Eliminar definitivamente',
  photos:'foto(s)', from:'de', cancel:'Cancelar', loading:'Cargando…',
  error:'Operación no disponible. Actualice la lista y compruebe sus permisos de administrador.',
  expired:'La confirmación ha caducado. Actualice la lista y vuelva a intentarlo.',
  limit:'Biblioteca demasiado grande para esta operación. Seleccione un día.',
  deleted:'eliminada(s)', missing:'ya ausente(s)', failed:'error(es)',
  folders:'carpeta(s) vacía(s)', clean:'Limpiar las carpetas vacías',
  previous:'Anterior', next:'Siguiente', page:'Página', view:'Ver foto',
  media:'Consultar en Multimedia', admin:'Se requiere acceso de administrador.', close:'Cerrar',
  retention:'Conservación', unlimited:'sin límite', days:'días'
};
const PAGE_SIZE = 60;
Object.assign(TEXT.fr,{zoomIn:'Zoom avant',zoomOut:'Zoom arrière',zoomReset:'Réinitialiser le zoom',photoPrevious:'Photo précédente',photoNext:'Photo suivante'});
Object.assign(TEXT.en,{zoomIn:'Zoom in',zoomOut:'Zoom out',zoomReset:'Reset zoom',photoPrevious:'Previous photo',photoNext:'Next photo'});
Object.assign(TEXT.es,{zoomIn:'Ampliar',zoomOut:'Reducir',zoomReset:'Restablecer zoom',photoPrevious:'Foto anterior',photoNext:'Foto siguiente'});
// Shared photo viewer: keep this block identical in both standalone resources.
if (!customElements.get('welcomeeye-photo-viewer')) customElements.define('welcomeeye-photo-viewer',class extends HTMLElement {
  constructor() {
    super();this.attachShadow({mode:'open'});this._revision=0;this._pointers=new Map();
    this.shadowRoot.innerHTML=`<style>
      dialog{box-sizing:border-box;width:min(960px,calc(100vw - 24px));max-height:calc(100dvh - 24px);padding:12px;border:1px solid #65747c;border-radius:12px;background:#152129;color:#fff;font:14px system-ui;overflow:auto}
      dialog::backdrop{background:#000b}.tools{display:flex;gap:8px;align-items:center;flex-wrap:wrap}button{min-width:44px;min-height:44px;border:1px solid #73818a;border-radius:7px;background:#283c48;color:inherit;font:inherit;padding:8px}button:disabled{opacity:.45}button:focus-visible{outline:3px solid #7edbd3;outline-offset:2px}.close{margin-left:auto}
      .surface{height:min(64dvh,680px);position:relative;overflow:hidden;touch-action:none;user-select:none;background:#080e12;margin:10px 0;cursor:grab}.surface:active{cursor:grabbing}img{position:absolute;width:100%;height:100%;object-fit:contain;pointer-events:none;transform-origin:center}.caption{margin:10px 0;overflow-wrap:anywhere}.status{min-height:20px} [hidden]{display:none!important}
    </style><dialog aria-modal="true" aria-labelledby="photo-caption"><div class="tools"><button class="previous" type="button">←</button><button class="next" type="button">→</button><span class="position"></span><button class="close" type="button"></button></div><p class="caption" id="photo-caption"></p><div class="surface"><img draggable="false" hidden></div><div class="tools"><button class="out" type="button">−</button><button class="reset" type="button">100%</button><button class="in" type="button">+</button></div><p class="status" role="status" aria-live="polite"></p></dialog>`;
    const q=s=>this.shadowRoot.querySelector(s);this._dialog=q('dialog');this._image=q('img');this._surface=q('.surface');
    q('.close').onclick=()=>this.close();q('.previous').onclick=()=>this.navigate(-1);q('.next').onclick=()=>this.navigate(1);
    q('.in').onclick=()=>this.zoom(this._scale+.5);q('.out').onclick=()=>this.zoom(this._scale-.5);q('.reset').onclick=()=>this.zoom(1);
    this._dialog.oncancel=event=>{event.preventDefault();this.close();};this._dialog.onclose=()=>this.close();
    this._dialog.onkeydown=event=>{
      if(event.altKey||event.ctrlKey||event.metaKey)return;
      const actions={ArrowLeft:()=>this.navigate(-1),ArrowRight:()=>this.navigate(1),'+':()=>this.zoom(this._scale+.5),'=':()=>this.zoom(this._scale+.5),'-':()=>this.zoom(this._scale-.5),'0':()=>this.zoom(1)};
      if(actions[event.key]){event.preventDefault();actions[event.key]();}
    };
    this._surface.addEventListener('wheel',event=>{if(event.ctrlKey){event.preventDefault();this.zoom(this._scale*(event.deltaY<0?1.2:1/1.2));}},{passive:false});
    this._surface.ondblclick=()=>this.zoom(this._scale===1?2:1);
    this._surface.onpointerdown=event=>{
      if(event.button!==0)return;this._pointers.set(event.pointerId,{x:event.clientX,y:event.clientY});
      this._surface.setPointerCapture(event.pointerId);this._rebaseGesture();
    };
    this._surface.onpointermove=event=>{
      if(!this._pointers.has(event.pointerId))return;
      this._pointers.set(event.pointerId,{x:event.clientX,y:event.clientY});const points=[...this._pointers.values()],g=this._gesture;
      if(points.length===2&&g.distance>0)this.zoom(g.scale*Math.hypot(points[0].x-points[1].x,points[0].y-points[1].y)/g.distance);
      else if(points.length===1){this._x=g.x+points[0].x-g.point.x;this._y=g.y+points[0].y-g.point.y;this._transform();}
    };
    const end=event=>{this._pointers.delete(event.pointerId);this._rebaseGesture();};
    this._surface.onpointerup=this._surface.onpointercancel=this._surface.onlostpointercapture=end;
  }
  show(photos,index,{resolve,label,labels,opener}) {
    this._photos=photos.slice();this._resolve=resolve;this._label=label;this._labels=labels;this._opener=opener;this._closed=false;
    for(const key of ['previous','next','close','in','out','reset']){
      const button=this.shadowRoot.querySelector('.'+key);button.setAttribute('aria-label',labels[key]);button.title=labels[key];
      if(key==='close')button.textContent=labels[key];
    }
    this._index=index;this._dialog.showModal();this._load();this.shadowRoot.querySelector('.close').focus();
  }
  navigate(delta) {
    const index=this._index+delta;if(this._closed||index<0||index>=this._photos.length)return;
    this._index=index;this._load();
  }
  async _load() {
    const revision=++this._revision,photo=this._photos[this._index],q=s=>this.shadowRoot.querySelector(s);
    this._pointers.clear();this._x=0;this._y=0;
    this._image.onload=this._image.onerror=null;this._image.removeAttribute('src');
    this._image=document.createElement('img');this._image.draggable=false;this._image.hidden=true;this._surface.replaceChildren(this._image);this.zoom(1);
    this._image.alt=this._label(photo);q('.caption').textContent=this._label(photo);q('.position').textContent=(this._index+1)+' / '+this._photos.length;
    const focused=this.shadowRoot.activeElement;
    q('.previous').disabled=this._index===0;q('.next').disabled=this._index===this._photos.length-1;q('.status').textContent=this._labels.loading;
    if(focused===q('.previous')&&focused.disabled)(q('.next').disabled?q('.close'):q('.next')).focus();
    if(focused===q('.next')&&focused.disabled)(q('.previous').disabled?q('.close'):q('.previous')).focus();
    const current=()=>!this._closed&&revision===this._revision&&this.isConnected;
    this._image.onload=()=>{if(current()){this._image.hidden=false;q('.status').textContent='';}};
    this._image.onerror=()=>{if(current()){this._image.hidden=true;q('.status').textContent=this._labels.error;}};
    try{const url=await this._resolve(photo);if(current())this._image.src=url;}
    catch{if(current())this._image.onerror();}
  }
  _rebaseGesture() {
    const points=[...this._pointers.values()];this._gesture={scale:this._scale,x:this._x,y:this._y,point:points[0],distance:points.length===2?Math.hypot(points[0].x-points[1].x,points[0].y-points[1].y):0};
  }
  zoom(value) {this._scale=Math.min(4,Math.max(1,value));this._transform();}
  _transform() {
    const box=this._surface.getBoundingClientRect(),x=box.width*(this._scale-1)/2,y=box.height*(this._scale-1)/2;
    this._x=Math.min(x,Math.max(-x,this._x||0));this._y=Math.min(y,Math.max(-y,this._y||0));
    this._image.style.transform=`translate(${this._x}px,${this._y}px) scale(${this._scale})`;
    this.shadowRoot.querySelector('.reset').textContent=Math.round(this._scale*100)+'%';
    const focused=this.shadowRoot.activeElement,out=this.shadowRoot.querySelector('.out'),inside=this.shadowRoot.querySelector('.in');
    out.disabled=this._scale<=1;inside.disabled=this._scale>=4;
    if((focused===out||focused===inside)&&focused.disabled)this.shadowRoot.querySelector('.reset').focus();
  }
  close(restore=true) {
    if(this._closed)return;this._closed=true;++this._revision;this._pointers.clear();this._photos=[];this._resolve=null;
    this._image.onload=this._image.onerror=null;this._image.removeAttribute('src');if(this._dialog.open)this._dialog.close();this.remove();
    if(restore&&this._opener?.isConnected)this._opener.focus();this._opener=null;
  }
  disconnectedCallback(){this.close(false);}
});
// End shared photo viewer.

class WelcomeEyePhotos extends HTMLElement {
  constructor() {
    super(); this.attachShadow({mode: 'open'});
    this.devices = []; this.days = []; this.photos = []; this.selected = new Set();
    this.device = ''; this.day = ''; this.page = 0; this.generation = 0;
    this.busy = false; this.message = ''; this.retention = 0; this.emptyDirectories = 0;
  }
  set hass(value) {
    const changed = this._hass?.connection !== value?.connection;
    this._hass = value;
    if (changed && this.isConnected) this.loadDevices();
  }
  get t() {
    const configured=this.devices.find(item=>item.entry_id===this.device)?.language;
    const lang=['fr','en','es'].includes(configured) ? configured : (this._hass?.language || 'en').split('-')[0];
    return TEXT[lang] || TEXT.en;
  }
  connectedCallback() { this.render(); if (this._hass) this.loadDevices(); }
  disconnectedCallback() { this.generation++; this.busy = false; this._photoViewer?.close(false); }
  call(operation, data = {}) {
    return this._hass.callWS({type: 'welcomeeye_local/photos', operation, ...data});
  }
  error(error) {
    return error?.code === 'photo_library_limit' ? this.t.limit :
      error?.code === 'confirmation_invalid_or_expired' ? this.t.expired : this.t.error;
  }
  async loadDevices() {
    const generation = ++this.generation;
    if (!this._hass?.user?.is_admin) { this.message = this.t.admin; this.render(); return; }
    this.busy = true; this.render();
    try {
      const devices = await this.call('devices');
      if (generation !== this.generation || !this.isConnected) return;
      this.devices = devices;
      if (!devices.some(item => item.entry_id === this.device)) this.device = devices[0]?.entry_id || '';
      this.busy = false;
      await this.refresh();
    } catch (error) {
      if (generation === this.generation) { this.busy = false; this.message = this.error(error); this.render(); }
    }
  }
  async refresh() {
    if (!this.device) { this.busy = false; this.render(); return; }
    const generation = ++this.generation, device = this.device;
    this.busy = true; this.selected.clear(); this.photos = []; this.page = 0; this.render();
    try {
      let result;
      try { result = await this.call('list', {entry_id: device}); }
      catch (error) {
        if (error?.code !== 'photo_library_limit' || !this.day) throw error;
        result = await this.call('list', {entry_id: device, day:this.day});
      }
      if (generation !== this.generation || !this.isConnected) return;
      this.days = result.days; this.retention = result.retention_days;
      this.emptyDirectories = result.empty_directories || 0;
      if (!this.days.some(item => item.day === this.day)) this.day = this.days[0]?.day || '';
      const photos = this.day ? (await this.call('list', {entry_id: device, day: this.day})).photos : [];
      if (generation !== this.generation || !this.isConnected) return;
      this.photos = photos;
      this.busy = false; this.render();
    } catch (error) {
      if (generation === this.generation) { this.busy = false; this.message = this.error(error); this.render(); }
    }
  }
  async changeDay(day) {
    if (this.busy) return;
    this.day = day;
    const generation = ++this.generation;
    this.busy = true; this.selected.clear(); this.photos = []; this.page = 0; this.render();
    try {
      const result = await this.call('list', {entry_id:this.device, day});
      if (generation !== this.generation || !this.isConnected) return;
      this.photos = result.photos; this.retention = result.retention_days;
      if (!this.days.some(item=>item.day===day)) this.days.push({day,count:result.count});
      this.busy = false; this.render();
    } catch (error) {
      if (generation===this.generation) {this.busy=false;this.message=this.error(error);this.render();}
    }
  }
  async deletePhotos(scope) {
    if (this.busy || !this.device || !this._hass?.user?.is_admin) return;
    if (scope === 'selection' && !this.selected.size) return;
    const generation = this.generation, device = this.device;
    this.busy = true; this.render();
    try {
      const plan = await this.call('prepare', {entry_id: device, scope,
        ...(scope === 'day' || scope === 'selection' ? {day: this.day} : {}),
        ...(scope === 'selection' ? {ids: [...this.selected]} : {})});
      if (generation !== this.generation || !this.isConnected) return;
      const name = this.devices.find(item => item.entry_id === device)?.name || this.t.device;
      const empty = plan.empty_directories || 0;
      const description = scope === 'empty' ? `${empty} ${this.t.folders}` :
        `${plan.count} ${this.t.photos}` + (empty ? ` · ${empty} ${this.t.folders}` : '');
      const accepted = (plan.count > 0 || empty > 0) && window.confirm(
        `${this.t.confirm} ${description} ${this.t.from} « ${name} »` +
        (scope === 'day' ? ` (${this.day})` : '') + ' ?');
      if (!accepted) {
        await this.call('cancel', {token: plan.token});
        this.busy = false; this.render(); return;
      }
      if (generation !== this.generation || !this.isConnected) return;
      const result = await this.call('delete', {token: plan.token});
      if (generation !== this.generation || !this.isConnected) return;
      this.message = `${result.deleted} ${this.t.deleted} · ${result.missing} ${this.t.missing} · ${result.failed} ${this.t.failed}`;
      if (result.directories_removed) this.message += ` · ${result.directories_removed} ${this.t.folders}`;
      if (result.directory_errors) this.message += ` · ${result.directory_errors} ${this.t.failed}`;
      this.busy = false; await this.refresh();
    } catch (error) {
      if (generation === this.generation) { this.busy = false; this.message = this.error(error); this.render(); }
    }
  }
  node(tag, text, parent, props = {}) {
    const node = document.createElement(tag); node.textContent = text || '';
    Object.assign(node, props); parent.append(node); return node;
  }
  button(text, parent, action, disabled = false) {
    const button = this.node('button', text, parent, {disabled: this.busy || disabled});
    button.addEventListener('click', action); return button;
  }
  selectionChanged() {
    this._selectionButton.disabled = this.busy || !this.selected.size;
    this._countNode.textContent = `${this.selected.size} ${this.t.selected}`;
    this._allButton.textContent = this.selected.size === this.photos.length ? this.t.none : this.t.all;
  }
  async photoUrl(photo) {
    this._photoUrls ||= new WeakMap();
    if(this._photoUrls.has(photo))return this._photoUrls.get(photo);
    const pending=this._hass.callWS({type:'media_source/resolve_media',media_content_id:photo.media_content_id}).then(result=>{
      if(!result?.url)throw new Error('photo_unavailable');return this._hass.hassUrl(result.url);
    });
    this._photoUrls.set(photo,pending);
    try{return await pending;}catch(error){this._photoUrls.delete(photo);throw error;}
  }
  viewPhoto(photo,opener) {
    if(this.busy||!this.isConnected||!this.photos.includes(photo))return;this._photoViewer?.close(false);
    const viewer=document.createElement('welcomeeye-photo-viewer');this._photoViewer=viewer;this.shadowRoot.append(viewer);
    viewer.show(this.photos,this.photos.indexOf(photo),{resolve:p=>this.photoUrl(p),label:p=>p.name,opener,
      labels:{previous:this.t.photoPrevious,next:this.t.photoNext,close:this.t.close,in:this.t.zoomIn,out:this.t.zoomOut,reset:this.t.zoomReset,loading:this.t.loading,error:this.t.error}});
  }
  render() {
    this._photoViewer?.close(false);this._photoViewer=null;
    const root = this.shadowRoot; root.replaceChildren();
    const style = this.node('style', '', root);
    style.textContent = `:host{display:block;height:100%;overflow:auto;padding:16px;box-sizing:border-box;color:var(--primary-text-color);font-family:var(--paper-font-body1_-_font-family,system-ui)}
      header,.toolbar,.paging{display:flex;gap:12px;align-items:center;flex-wrap:wrap}h1{font-size:22px;margin:12px 0}
      select,button{font:inherit;padding:10px;border:1px solid var(--divider-color,#aaa);border-radius:8px;background:var(--card-background-color,#fff);color:inherit;min-height:44px}
      button{cursor:pointer}button:disabled{opacity:.5;cursor:default}.danger{color:var(--error-color,#b3261e)}
      label{display:flex;gap:8px;align-items:center}input[type=checkbox]{width:22px;height:22px}
      .grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:12px;margin:16px 0}
      article{padding:8px;border:1px solid var(--divider-color,#aaa);border-radius:10px;overflow:hidden}
      img{width:100%;height:150px;object-fit:contain;background:var(--secondary-background-color,#eee)}
      article button{width:100%;padding:4px;border:0}article small{overflow-wrap:anywhere}
      .status{margin:12px 0;min-height:24px}a{color:var(--primary-color)}@media(max-width:450px){.grid{grid-template-columns:repeat(2,minmax(0,1fr))}:host{padding:10px}article img{height:100px}}`;
    const header = this.node('header', '', root);
    this.button('☰', header, () => this.dispatchEvent(new CustomEvent('hass-toggle-menu', {bubbles:true, composed:true})));
    this.node('h1', this.t.title, header);
    this.node('a', this.t.media, header, {href:'/media-browser'});
    if (!this._hass?.user?.is_admin) { this.node('p', this.t.admin, root); return; }
    const toolbar = this.node('div', '', root, {className:'toolbar'});
    const deviceLabel = this.node('label', this.t.device, toolbar);
    const device = this.node('select', '', deviceLabel, {disabled:this.busy});
    for (const item of this.devices) this.node('option', item.name, device, {value:item.entry_id, selected:item.entry_id===this.device});
    device.addEventListener('change', () => { if (!this.busy) { this.device=device.value; this.day=''; this.message=''; this.refresh(); } });
    const dayLabel = this.node('label', this.t.day, toolbar);
    const day = this.node('select', '', dayLabel, {disabled:this.busy || !this.days.length});
    for (const item of this.days) this.node('option', `${item.day} (${item.count})`, day, {value:item.day, selected:item.day===this.day});
    day.addEventListener('change', () => this.changeDay(day.value));
    const calendar = this.node('input', '', toolbar, {type:'date', value:this.day, disabled:this.busy});
    calendar.setAttribute('aria-label', this.t.day);
    calendar.addEventListener('change', () => {if(calendar.value) this.changeDay(calendar.value);});
    this.button(this.t.refresh, toolbar, () => this.loadDevices());
    this.node('p', `${this.t.retention} : ${this.retention ? this.retention+' '+this.t.days : this.t.unlimited}`, root);
    const controls = this.node('div', '', root, {className:'toolbar'});
    this._allButton = this.button(this.selected.size === this.photos.length && this.photos.length ? this.t.none : this.t.all, controls, () => {
      this.selected = this.selected.size === this.photos.length ? new Set() : new Set(this.photos.map(item => item.id)); this.render();
    }, !this.photos.length);
    this._selectionButton = this.button(this.t.delete, controls, () => this.deletePhotos('selection'), !this.selected.size);
    this._selectionButton.className='danger';
    this.button(this.t.deleteDay, controls, () => this.deletePhotos('day'), !this.photos.length).className='danger';
    this.button(this.t.deleteDevice, controls, () => this.deletePhotos('device'), !this.days.length).className='danger';
    this.button(this.t.clean, controls, () => this.deletePhotos('empty'), !this.emptyDirectories);
    this._countNode = this.node('p', `${this.selected.size} ${this.t.selected}`, root);
    this.node('div', this.busy ? this.t.loading : this.message, root, {className:'status', role:'status'});
    const grid = this.node('div', '', root, {className:'grid'});
    if (!this.photos.length) this.node('p', this.devices.length ? this.t.empty : this.t.noDevice, grid);
    const generation = this.generation;
    for (const photo of this.photos.slice(this.page*PAGE_SIZE, (this.page+1)*PAGE_SIZE)) {
      const tile = this.node('article', '', grid);
      const label = this.node('label', `${photo.day} · ${photo.channel}`, tile);
      const checkbox = this.node('input', '', label, {type:'checkbox', checked:this.selected.has(photo.id), disabled:this.busy});
      checkbox.setAttribute('aria-label', photo.name);
      checkbox.addEventListener('change', () => { if (this.busy) return; checkbox.checked ? this.selected.add(photo.id) : this.selected.delete(photo.id); this.selectionChanged(); });
      const view = this.button(this.t.view, tile, () => this.viewPhoto(photo,view));
      view.setAttribute('aria-label',this.t.view+' · '+photo.name);
      const image = this.node('img', '', view, {alt:photo.name, loading:'lazy'});
      this.node('small', photo.name, tile);
      if (!this.busy) this.photoUrl(photo).then(url => {
        if (generation === this.generation && image.isConnected) image.src = url;
      }).catch(() => { if(generation===this.generation&&image.isConnected)image.alt = this.t.error; });
    }
    const pages = Math.ceil(this.photos.length/PAGE_SIZE);
    if (pages > 1) {
      const paging = this.node('div', '', root, {className:'paging'});
      this.button(this.t.previous, paging, () => { this.page--; this.render(); this.scrollTop=0; }, this.page===0);
      this.node('span', `${this.t.page} ${this.page+1}/${pages}`, paging);
      this.button(this.t.next, paging, () => { this.page++; this.render(); this.scrollTop=0; }, this.page+1>=pages);
    }
  }
}
if (!customElements.get('welcomeeye-photos')) customElements.define('welcomeeye-photos', WelcomeEyePhotos);
})();
