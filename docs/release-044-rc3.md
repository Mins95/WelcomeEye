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
- Le diagnostic autonome Connect 3 répond maintenant sans lecteur vidéo.
  **L'écoute n'est pas établie dans cette RC3** : la connexion de contrôle,
  son authentification et la souscription native A331 restent à démontrer.
  Le diagnostic retourne ce blocage avant toute connexion, sans faux délai
  d'écoute de 90 secondes. [Preuves et limites](connect3-ring-evidence.md#rc3-targeted-autonomous-listener-preflight).

Les vidéos, sons, microphones, photos manuelles et notifications existantes
sont conservés. La limitation des captures automatiques pendant un appel
fait l'objet d'un chantier distinct et n'est pas corrigée ici.

### Installation et essai

Dans HACS, afficher les préversions et télécharger **v0.4.4-rc.3**.
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

La réponse attendue dans cette RC3 est **`status: unavailable`** avec
**`last_error_reason: native_login_protocol_unestablished`**.
**Ne pas aller sonner après ce refus** : aucune écoute n'a commencé.
Relire la réponse avec `operation: status` et fournir les diagnostics si un
autre résultat apparaît. `operation: stop` reste disponible et n'ouvre aucun
flux. L'ancien `connect3_observe_doorbell`, lié à un direct existant, est conservé.

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
- The autonomous Connect 3 diagnostic no longer needs a video player to report
  its result. **Listening is unavailable in RC3**: the native control endpoint,
  login/protection and A331 subscription remain unestablished. Start refuses before
  any connection; no simulated 90-second listening task is created.

Existing media, manual photos and notifications are retained. The separate
automatic-photo ringing interruption is not addressed in this release.

### Install, test and rollback

Enable prereleases in HACS, download **v0.4.4-rc.3**, restart HA and fully reload
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

With HA and Philips videos closed, the YAML above targets the real Connect 3
status sensor. Expected response: `status: unavailable`,
`last_error_reason: native_login_protocol_unestablished`. **Do not press a bell
after this refusal; no listener is active.** `operation: status` reads the result;
`stop` is safe. The older live-session observation action remains available.

To roll back, disable secondary trials where available, redownload **v0.4.4-rc.2**,
restart HA and reload the frontend without deleting the entry. Recheck output
buttons before using them: RC2 uses its previous global choices and per-view
display, without RC3's four independent choices. Stable **v0.4.3** is unchanged.
