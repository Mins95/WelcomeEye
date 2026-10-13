/* Supported HA custom panel; authenticated WS only, no filesystem paths. */
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
const PAGE_SIZE = 60;
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
  get t() { return TEXT[this._hass?.language?.startsWith('fr') ? 'fr' : 'en']; }
  connectedCallback() { this.render(); if (this._hass) this.loadDevices(); }
  disconnectedCallback() { this.generation++; this.busy = false; }
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
      this.photos = this.day ? (await this.call('list', {entry_id: device, day: this.day})).photos : [];
      if (generation !== this.generation || !this.isConnected) return;
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
  render() {
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
      const view = this.button(this.t.view, tile, () => { if (image.src) window.open(image.src, '_blank', 'noopener'); });
      const image = this.node('img', '', view, {alt:photo.name, loading:'lazy'});
      this.node('small', photo.name, tile);
      if (!this.busy) this._hass.callWS({type:'media_source/resolve_media', media_content_id:photo.media_content_id}).then(result => {
        if (generation === this.generation && image.isConnected && result?.url) image.src = this._hass.hassUrl(result.url);
      }).catch(() => { image.alt = this.t.error; });
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
