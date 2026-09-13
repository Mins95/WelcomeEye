# Bêta 15 — revue avant publication

Statut : candidate locale `0.3.0-beta.15`, branche `beta15-v1-video-payload`. Aucun déploiement, push ou release. Tests matériels V1 encore nécessaires.

**Mise à jour vidéo après `138748d` :** la revue ci-dessous décrit l'étape initiale
à 38 tests. La correction suivante ajoute une réception OWSP bornée et un parcours
V1 pour images complètes, validés par **71 tests au total avec PyAV réel**.
Le détail actuel, les différences de `client.py`, les limites de compatibilité et
le protocole de test vidéo sont dans [beta15-v1-video-fix.md](beta15-v1-video-fix.md).
Les sections historiques disant « aucun nouveau parseur » ou « PyAV non testé »
s'appliquent uniquement à `138748d`. Les corrections de coordination restent conservées.

## 1. Base bêta 14

`main` vérifié : `c67d0d7c7aa2809a7c6e12b17320290b168b91a0`. Cette base était déjà celle de la branche ; aucune fusion ni écrasement nécessaire. La pause V1, le cache discovery, Start AV 5007/5008, Stop AV 5009/5010 et le traitement du padding de la bêta 13 sont présents.

## 2. Audit et corrections

- Le protocole pause/ack existait, mais sans verrou commun pour la publication de la session et la demande de pause. Désormais, une condition protège ces deux opérations ; la commande ne commence que lorsque le worker a terminé sa fermeture et retiré sa session. La pause reste active jusqu'au nettoyage de la commande.
- Un listener déjà arrêté ou dans son backoff libère immédiatement le droit d'utiliser le canal s'il ne possède aucune session ; plus d'attente inutile d'un acquittement d'un worker absent.
- Les exceptions des diagnostics ou du nettoyage ne peuvent plus sauter la reprise V1 ni laisser le verrou de commande bloqué. La reprise est demandée dans le nettoyage, y compris si la pause a expiré ou si la connexion de commande échoue avant l'envoi.
- `start()` est idempotent ; la reprise réveille le worker existant. Les notifications « connecté » périmées sont écartées pendant les transitions de pause/reprise.
- Si une fermeture de socket échoue réellement, la reprise reste bloquée : aucune nouvelle session concurrente n'est créée. L'état est visible dans les diagnostics. Une authentification refusée ou un UID incohérent ne sont pas automatiquement retentés.
- À l'arrêt du hub V1, la commande est annulée avant l'attente du worker sonnette ; une commande arrêtée pendant la pause n'est pas envoyée.
- Le choix V1 est utilisé de façon cohérente pendant la commande ; les intervalles de stabilisation de 0,2 s de la bêta 14 sont conservés, en complément de l'acquittement réel.

## 3. Fichiers

Coordination : `ring.py`, `control.py`, `hub.py`, `diagnostics.py`. Version et notes : `manifest.json`, `CHANGELOG.md`.

Travail vidéo conservé : `client.py`, `v1_video_diagnostics.py`, branchements de `hub.py` et exports `media.v1_video_structure` / `media.v1_video_previous_sessions`. `button.py` a été audité, sans modification : output 0 = gâche, output 1 = portail.

Tests : `tests/test_control_coordination.py` et `tests/test_v1_video_diagnostics.py`. Aucun nouveau parseur vidéo.

## 4. Tests

38 tests unitaires synthétiques réussis : outputs 0/1, fermeture de socket avant abandon effectif du worker, pause expirée, échec réel de fermeture, listener absent ou en backoff, connexion/envoi/confirmation/décodage en échec, diagnostics défaillants, reprise défaillante, double appui concurrent, cooldown, UID, arrêt pendant pause, unicité du worker, messages d'état périmés, padding, TLV 57 et alarmes 7/14/19/47.

Les tests structurels vidéo antérieurs sont conservés. Syntaxe des 18 modules vérifiée ; aucune erreur d'espaces dans le diff. Le décodage PyAV réel et le hardware ne sont pas simulés comme « validés ». Hassfest/HACS restent à exécuter à l'étape de publication, non demandée ici.

## 5. Connect 2 et fonctions conservées

`media.py`, `rtc.py`, `camera.py`, `button.py`, `protected.py` et `protocol.py` restent identiques à la bêta 14. Le fichier `client.py` garde exactement l'instrumentation vidéo précédente ; découverte, Start/Stop AV et gestion du padding sont donc inchangés.

Pour Connect 2, les trois parcours de commande sont vérifiés : session média temporaire au repos, contrôle direct pendant une vidéo, repli de connexion après échec du démarrage vidéo. Ils ne demandent pas de pause sonnette et gardent les mêmes profils. Les protections communes du cycle de vie du listener ne modifient ni son protocole ni ses messages. Ces vérifications de code et tests ne remplacent pas une non-régression physique.

## 6. Aucune ouverture automatiquement retentée

Un seul appel d'envoi du TLV 505 par demande admise. Aucun retry après écriture partielle, timeout ou confirmation invalide. Le cooldown de trois secondes commence avant l'envoi, même si l'écriture échoue. Le verrou rejette une commande concurrente. Le contrôle UID et le mapping des sorties sont conservés.

`request_send_attempt_count` compte les appels d'envoi, y compris ceux en erreur ; `request_sent_count` compte seulement les envois terminés. `ring_resume_success` signifie « reprise autorisée », **pas** « reconnexion confirmée » : cette dernière est comptée par `doorbell.resume_reconnected_count`.

## Preuve du rôle du TLV 57

Bibliothèque étudiée : `libglnkio.so`, SHA-256 `25063e0678644f907df3d7706d1968004c5e94114f31e9b8161d6e917d418992`.

- Table de dispatch de `DataChannelIOCtrl::onParse` : TLV 57 → `0x92008`. Chargement du premier entier LE16 du corps, puis appel au slot virtuel `+0x38` (`0x92024–0x9202a`).
- La vtable de `JNIDataAdapter` à `0x166778` possède la relocation du thunk `onKeepliveResp(int)` à `+0xf8`, soit `+0x38` depuis le point d'adresse secondaire `+0xc0` utilisé par le listener.
- `JNIDataAdapter::onKeepliveResp` (`0x9434a`, thunk `0x94368`) poste l'événement JNI numéro 8. `JNIDataAdapter.java:82–83` le transmet à `datasource.onKeepliveResp(message.arg1)`.
- La requête correspondante `DataChannel::sendAliveReq` (`0x892a4`) construit le TLV 49 (`0x31`), corps de quatre octets, puis l'envoie.

Conclusion solide pour ce SDK : **57 est une réponse de maintien de connexion**. Ce n'est pas une preuve de sonnerie. Sa gestion reste inchangée ; aucune interprétation nouvelle du code numérique contenu dans sa réponse.

## 7. Protocole exact à transmettre au propriétaire du V1

À exécuter seulement après revue et installation explicite de cette candidate. Les actions ci-dessous sont manuelles ; aucun script d'ouverture automatique.

1. Fermer la vidéo HA et l'application Philips. Attendre 20 secondes. Télécharger un diagnostic **A — repos** depuis la fiche de l'intégration. Vérifier `device.model = WelcomeEye Connect V1`, `doorbell.connected = true` et `currently_paused = false`.
2. Appuyer une seule fois sur la sonnette physique. Confirmer l'événement/capteur HA. Cela fixe le fonctionnement avant toute ouverture.
3. Depuis HA, cliquer **une seule fois sur « Ouvrir la gâche »**. Observer physiquement la gâche, puis attendre 10 secondes. Télécharger **B — gâche**. Ne pas cliquer à nouveau si la confirmation manque ; signaler le résultat physique et l'erreur.
4. Vérifier dans B, par rapport à A : un envoi au maximum, une confirmation si succès, pause demandée/réussie, `command_started_after_ring_release = true`, reprise demandée, `resume_reconnected_count` augmenté, `currently_paused = false`, session de commande inactive. Sonner une fois pour confirmer le retour de la détection.
5. Cliquer **une seule fois sur « Ouvrir le portail »**. Observer physiquement le portail, attendre 10 secondes et télécharger **C — portail**. Vérifier les mêmes variations de compteurs ; sonner une fois pour confirmer à nouveau la détection après reprise.
6. Une fois cette coordination vérifiée, ouvrir la caméra V1 environ 15 secondes, puis fermer. Télécharger **D — vidéo**, comprenant `media.v1_video_structure`, `media.v1_video_previous_sessions`, les compteurs Start/Stop AV et `control`/`doorbell`.
7. Renvoyer A/B/C/D et uniquement les résultats physiques : gâche oui/non, portail oui/non, sonnerie détectée avant/après chaque commande, image vidéo oui/non. Aucun mot de passe, IP, UID ni capture de contenu vidéo.

Une pause volontaire peut faire manquer un appui pendant la commande ; vérifier la sonnerie **après** reconnexion. Si `release_failed=true` ou une session reste bloquée, arrêter la séquence et renvoyer le diagnostic, sans répéter l'ouverture. Les doubles clics et défaillances sont couverts synthétiquement ; ils n'ont pas besoin d'être provoqués sur le portail réel.
