# v0.4.4-rc.3

## Français

### Changements

- **Reconfigurer → Commandes d'ouverture** : activer séparément Portillon 1,
  Portail 1, Portillon 2 et Portail 2, selon les capacités du modèle.
  Les choix secondaires sont accessibles avec **Deuxième platine extérieure**.
- La carte affiche toutes les commandes autorisées sous les deux vues, avec
  leurs noms personnalisés. Leur cible reste fixe : Portillon 1 `(1,1)`,
  Portail 1 `(1,2)`, Portillon 2 `(2,1)`, Portail 2 `(2,2)`.
- Une commande destinée à l'autre canal libère d'abord le lecteur incompatible.
  Elle refuse l'envoi si cette libération échoue. La source sélectionnée reste
  la même, sans réouverture automatique de la vidéo. Un clic reste une seule
  tentative, sans rejeu après timeout ou résultat incertain.
- **RC3 republiée** : le diagnostic autonome Connect 3 tente maintenant la
  vraie séquence native de connexion, authentification et abonnement aux alarmes,
  sans vidéo. Il s'arrête au bout de 90 secondes maximum ou sur demande.
  Les clés proviennent du CGI HTTPS vérifié et les identifiants natifs sont
  chiffrés. Aucun repli vers un mode moins protégé, aucune ouverture physique.
  La compatibilité A331 et le sens des notifications restent à valider sur
  matériel ; aucun événement de sonnette HA n'est encore émis.
  [Preuves et limites](connect3-native-alarm-evidence.md).

Les vidéos, sons, microphones, photos manuelles et notifications existantes
sont conservés. La limitation des captures automatiques pendant un appel
fait l'objet d'un chantier distinct et n'est pas corrigée ici.

### Installation et essai

Dans HACS, afficher les préversions et **retélécharger v0.4.4-rc.3**, même si
cette version est déjà installée : il s'agit d'une republication.
Redémarrer Home Assistant, puis recharger complètement le dashboard.
Conserver les entrées, identifiants, certificats et noms personnalisés.

Dans **Reconfigurer**, activer uniquement les commandes utiles. Les autorisations
de transport et d'essais secondaires restent dans **Options avancées**.
Le mot de passe et les certificats inchangés ne demandent pas une nouvelle
approbation. Désactiver la deuxième platine masque et bloque ses commandes,
conserve les choix enregistrés et révoque leur consentement : leur réactivation
nécessite une nouvelle approbation explicite.

Pour une installation à deux platines :

1. Vérifier les mêmes boutons sous les deux vidéos et leurs noms personnalisés.
   Désactiver un portillon et vérifier que son bouton disparaît sous les deux vues.
2. Vérifier vidéo, son, micro autorisé et photo de chaque entrée, puis fermer le
   direct et vérifier que Philips Door Connect reprend normalement.
3. Pour chaque sortie déjà autorisée, faire **un seul clic volontaire** et
   vérifier physiquement sa cible. Vérifier notamment Portail 1 depuis la vue 2
   et Portillon 2 depuis la vue 1. Ne pas recommencer si le résultat est ambigu.
   Le relais de la deuxième platine reste distinct de celui de la première,
   même si l'utilisateur leur donne le même nom.
4. Télécharger les diagnostics immédiatement après les essais, sans redémarrage
   ni rechargement d'intégration, et indiquer les sorties qui ont réellement réagi.

Pour le diagnostic autonome, fermer les lecteurs HA et Philips puis sélectionner
le vrai capteur Connect 3 dans **Outils de développement → Actions** :

```yaml
action: welcomeeye_local.connect3_listen_doorbell
target:
  entity_id: sensor.MON_CONNECT3
data:
  operation: start
  duration: 90
```

L'action rend immédiatement la main. Relancer avec `operation: status` :
si **`listening: true`**, sonner **une fois sur l'entrée 1**, laisser finir le
cycle et attendre la fin du diagnostic. Télécharger les diagnostics et vérifier
que le moniteur et Philips fonctionnent normalement. Refaire séparément le
même essai de 90 secondes pour l'entrée 2, en précisant l'entrée et le moment
approximatif de chaque appui. Deux observations séparées facilitent la comparaison.

Si l'état est `blocked`, `failed` ou `unavailable`, ne pas aller sonner :
transmettre le résultat et les diagnostics. `operation: stop` arrête et ferme
l'essai à tout moment. L'abonnement envoyé ne prouve pas qu'A331 le prend en
charge ; même une notification reçue ne prouve pas encore une sonnette.
Ce diagnostic utilise le mot de passe local saisi manuellement. Il refuse les
identifiants importés par QR qui nécessiteraient une vérification d'identité
supplémentaire, sans contourner cette protection ni lancer de découverte UDP.
L'ancien `connect3_observe_doorbell`, lié au direct, reste disponible.

### Retour arrière

Retélécharger **v0.4.4-rc.2** dans HACS, redémarrer HA et recharger complètement
le dashboard, sans supprimer l'entrée. Désactiver les essais secondaires avant
le retour arrière lorsque possible. RC2 applique ses anciens réglages d'ouverture
et son ancien affichage par entrée : revérifier les boutons avant de les utiliser,
car elle ne propose pas les quatre choix indépendants de RC3. La stable
**v0.4.3** reste inchangée.

## English

### Changes

- **Reconfigure → Opening commands** offers four independent choices, within
  each model's supported capabilities. Secondary choices require **Second outdoor panel**.
- All authorized commands appear under both camera views, retaining custom labels
  and fixed targets: strike 1 `(1,1)`, gate 1 `(1,2)`, strike 2 `(2,1)`, gate 2 `(2,2)`.
- A command targeting the other channel first releases incompatible media. Failed
  release prevents sending. Camera selection is retained without automatically
  reopening video. Each explicit click permits at most one physical attempt.
- **Republished RC3:** the autonomous Connect 3 diagnostic now attempts the
  SDK's real native control setup, protected login and alarm subscription, for
  at most 90 seconds. No video, microphone, photo or physical output. Verified
  CGI HTTPS supplies the key; weak control protection is refused without a
  fallback. A331 compatibility and notification meaning still require hardware
  validation; no Home Assistant ring event is emitted.

Existing media, manual photos and notifications are retained. The separate
automatic-photo ringing interruption is not addressed in this release.

### Install, test and rollback

Enable prereleases in HACS, **redownload v0.4.4-rc.3 even if already installed**,
restart HA and fully reload
the dashboard. Keep existing entries, credentials, certificate approvals and names.
Choose the required commands in **Reconfigure**; existing transport and secondary
trial approvals remain in **Advanced options**. Disabling the second panel retains
its choices but revokes secondary consent, which must be approved again when enabled.

Check the same authorized buttons under both views, their labels and disappearance
when disabled. Check video, sound, authorized microphone and manual photo on each
source, then Philips operation after closing HA. For each authorized physical
target, use **one deliberate click** and confirm the actual relay: include gate 1
while viewing source 2 and strike 2 while viewing source 1. Stop if uncertain.
Download fresh diagnostics immediately, without restarting or reloading the integration.

With HA and Philips videos closed, use the YAML above with the real Connect 3
sensor. Start returns immediately. Read `operation: status`; only after
`listening: true`, press panel 1 once and let its call and the diagnostic finish.
Download diagnostics and confirm normal monitor/Philips operation. Repeat in a
separate 90-second observation for panel 2, identifying the panel and approximate
press time. If blocked/failed/unavailable, send diagnostics without pressing a
bell. `operation: stop` closes the trial immediately. Subscription writes alone
do not prove firmware acceptance or a recognized doorbell event. QR-imported
credentials requiring a separate identity check are refused; manual credentials
need no UDP discovery. The existing live-session observation remains available.

To roll back, disable secondary trials where available, redownload **v0.4.4-rc.2**,
restart HA and reload the frontend without deleting the entry. Recheck output
buttons before using them: RC2 uses its previous global choices and per-view
display, without RC3's four independent choices. Stable **v0.4.3** is unchanged.
