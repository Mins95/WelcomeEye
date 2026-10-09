/* Recover transient module failures without polling or touching device media. */
const moduleUrl = new URL('./welcomeeye-card.js', import.meta.url);
moduleUrl.search = new URL(import.meta.url).search;
const state = window.__welcomeEyeCardLoading ||= {pending: null, attempt: 0};

export function loadWelcomeEyeCard() {
  const registered = customElements.get('welcomeeye-card');
  if (registered) return Promise.resolve(registered);
  if (state.pending) return state.pending;

  state.pending = (async () => {
    let lastError;
    for (let attempt = 0; attempt < 3; ++attempt) {
      if (customElements.get('welcomeeye-card')) return customElements.get('welcomeeye-card');
      const url = new URL(moduleUrl);
      // Failed imports are cached for the page lifetime. Each recovery must
      // therefore have a new URL, including a later explicit popup attempt.
      url.searchParams.set('load', `${Date.now()}-${++state.attempt}`);
      let timer;
      try {
        await Promise.race([
          import(url.href),
          new Promise((resolve, reject) => {
            timer = setTimeout(() => reject(new Error('Le chargement WelcomeEye dépasse 15 secondes.')), 15000);
          }),
        ]);
        const card = customElements.get('welcomeeye-card');
        if (!card) throw new Error("Le module n'a pas enregistré welcomeeye-card.");
        return card;
      } catch (error) {
        // A timed-out import is not canceled by the browser and may finish late.
        const card = customElements.get('welcomeeye-card');
        if (card) return card;
        lastError = error;
      } finally {
        clearTimeout(timer);
      }
      if (attempt < 2) await new Promise(resolve => setTimeout(resolve, 250 * (attempt + 1)));
    }
    throw new Error('Carte WelcomeEye non chargée après 3 tentatives. ' + (lastError.message || lastError));
  })().finally(() => { state.pending = null; });
  return state.pending;
}

window.loadWelcomeEyeCard = loadWelcomeEyeCard;
// Background failure is handled; an explicit popup gets the same error and can
// retry after the bounded startup attempts, without an automatic retry loop.
loadWelcomeEyeCard().catch(error => {
  console.warn('WelcomeEye card could not be loaded:', error.message || error);
});
