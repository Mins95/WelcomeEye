# Stabilisation beta.8 — journal des preuves et validation

Base auditée : `ab2870d`, origin/main, version 0.3.1-beta.7 ; arbre identique
au tag beta.7 (`45fb3de`). Beta.6 : `089592d`. Branche isolée :
`stabilization-beta8`. L'analyse précédente de sonnette reste dans son checkout.
Aucune commande matérielle, installation HA ou publication de release effectuée.

## Phase 1 — audit avant modification de production

| Classe | Observation | Preuve / limite |
| --- | --- | --- |
| FACTS — FAIT CONFIRMÉ | Connect 2 vidéo/audio/ouverture/sonnette fonctionnent sur matériel ; Wi-Fi entreprise bloque ICE, 5G fonctionne. | Données matérielles fournies par l'utilisateur ; aucun nouvel essai matériel. |
| FACTS — FAIT CONFIRMÉ | V1 reçoit Start AV, H264, envoie un 505 ; dernier essai sans 506 ni 5010, puis écran occupé. | Données utilisateur ; ne prouve pas que TCP local reste ouvert. |
| FACTS — FAIT CONFIRMÉ | Suite beta.7 intacte : 101 tests + 3 sous-tests passent sous Python 3.12.14. | Exécution locale du 14 septembre 2026. |
| FACTS — FAIT CONFIRMÉ | Le login/OWSP et les constructeurs protégés restent distincts des reprises réseau ; rediscovery bornée avant login uniquement. | `client.py`, `protected.py`, `control.py`, `v1_control.py`. |
| CONFIRMED BUGS — FAIT CONFIRMÉ par lecture, reproduction à suivre | Une erreur de flush AAC empêche `MediaPipeline.close` de fermer le conteneur. | `media.py`, absence de finally autour du flush. |
| CONFIRMED BUGS — FAIT CONFIRMÉ par lecture, reproduction à suivre | Une erreur de `hub.release` empêche `_serve` de fermer le writer et retirer son handler. | `hub.py`, cleanup séquentiel non protégé. |
| POSSIBLE BUGS — HYPOTHÈSE À VÉRIFIER | Fermeture d'un viewer pendant createAnswer/setLocalDescription : réponse tardive et ancien callback pouvant agir sur un ID réutilisé. | `rtc.py`, absence de vérification d'identité après ces await. |
| POSSIBLE BUGS — HYPOTHÈSE À VÉRIFIER | Réinitialisation du décodeur sans SPS/PPS et sans vérification d'IDR après erreur ; compteurs perdus à la fermeture du pipeline. | `media.py`, diagnostics ne recopient pas les compteurs. |
| FACTS — FAIT CONFIRMÉ | `_halt_media` fait SHUT_RD ; enfin le worker notifie media_closed, envoie 5009 puis ferme sans boucle de réception dédiée. | `hub.py`/`client.py`. SHUT_RD rend la lecture de 5010 impossible lors de ce chemin. |
| UNPROVEN HYPOTHESES — HYPOTHÈSE À VÉRIFIER | Attendre 5010 guérirait l'état occupé ; 505 suspendrait la vidéo ; 506 arriverait après un autre callback. | Nécessite analyse SDK et matériel. Ne pas transposer en production sans preuve. |
| UNPROVEN HYPOTHESES — HYPOTHÈSE À VÉRIFIER | Le preload DNS beta.7 reproduit encore CLASS32769.SRV. | Beta.7 précharge déjà cette classe, au-delà de load_all_types ; test réel aioice à faire. |

Périmètre lu : setup/unload, entités, configuration/reauth, discovery/login,
protection/TLV, réception V1, pipeline H264/G711/AAC/TS/JPEG, worker/leases/proxy,
contrôle des deux modèles, sonnette/standby, WebRTC/ICE et diagnostics. Aucun
parseur vidéo ni paquet de commande physique ne sera changé sans défaut démontré.

API HA vérifiée dans les sources officielles 2026.9.2 :
`homeassistant/components/web_rtc/__init__.py` et `camera/webrtc.py`.
L'API fournit bien des objets ICE servers avec urls/username/credential.
Leur conversion existante supporte TURN ; l'absence de TURN sur le réseau de
l'utilisateur n'est pas une panne H264. Aucun TURN externe n'est ajouté.

## Preuves natives de fermeture (phase 3)

APK extrait localement : `work/decompiled/sources/`, bibliothèque ARMv7
`work/native/libglnkio.so`, SHA256
`25063e0678644f907df3d7706d1968004c5e94114f31e9b8161d6e917d418992`.
Les chemins sont relatifs au workspace de recherche, hors dépôt distribuable.

**FAIT CONFIRMÉ** : `com/quvii/compathlt/QvLtPlayerCore.java:975` (`stop`)
appelle son helper `e(GlnkChannel)` ligne 426 : `stop()` puis `release()`.
`glnk/client/GlnkChannel.java:189` délègue à DataChannel, dont `stop()`
(`DataChannel.java:441`) appelle `native_stop`.
L'entrée JNI DataChannel est `0x86984` (table `0x16aa80`), appel `0x869b0`
vers `DataChannel::stop()` (`0x88cb4`). Ne pas confondre avec native_stop
du GlnkTest à `0x85d3c`.

Dans `DataChannel::stop`, garde démarré/en arrêt, attente des références de
callbacks avec usleep(10000), puis `0x88d40` charge **5005**. Appel
`packetOWSP(sequence=0, type=5005, data=NULL, length=0)`, puis
`sendingData` à `0x88d56`, puis `onStop` (`0x882b4`) à `0x88d6a`.
`onStop` ferme le transport (appel virtuel `0x88314`). `native_release`
(`0x8649c`, appel `0x864f2`) appelle également stop, dont la garde évite le
double envoi. L'attente des callbacks ne teste pas un résultat 5010.
Elle ne justifie pas de recopier une attente native non bornée en Python.

`packetOWSP` (`0x8ea54`) alloue length+12 ; écrit BE32(length+8), LE32(sequence),
LE16(type), LE16(length), puis le payload éventuel. Le paquet final est donc
exactement `00000008000000008d130000` (12 octets, aucune commande physique).

**FAIT CONFIRMÉ** : `native_stopStream` (`0x87648`, table `0x16ab7c`)
appelle `DataChannelIOCtrl::stopGetVideoStream` (`0x8f924`) à `0x87678`.
C'est cette autre fonction qui construit/envoie le **5009** protégé et retourne.
Le parseur accepte 5010 à `0x9163c`, indépendamment de l'appel d'envoi.
Aucune attente de 5010 n'est démontrée dans ces chaînes.

**DÉDUCTION FORTE** : beta7 omet le message de fin de session 5005 présent
dans le chemin natif de fermeture de preview. Cela peut contribuer au busy V1.
**HYPOTHÈSE À VÉRIFIER SUR MATÉRIEL** : l'ajout élimine effectivement le busy.
La candidate conserve le Stop AV 5009 existant et ajoute le 5005 natif avant
TCP close, uniquement pour une session média V1 authentifiée. Cette composition
est un choix conservateur d'intégration, pas une affirmation que l'APK envoie
toujours les deux messages dans cet ordre. Chaque écriture est bornée à 2 s.
Aucune boucle d'attente de 5010, aucune seconde lecture concurrente, aucun retry.
`v1_device_release_complete` reste **null**, même si tout a été envoyé.

505/506 : le chemin déjà documenté `QvLtPlayerCore.lockReq` utilise liveChannel
16/1/2, JNI puis `DataChannelIOCtrl::lockReq`. Constructeur 505, nonce,
horloge, mot de passe encodé, output 0/1 et action 1 restent inchangés.
L'observation 506 utilise le lecteur de cette même session. Aucune preuve nouvelle
d'un préalable, d'un arrêt de preview obligatoire ou d'un retry natif ne justifie
de changer cette séquence. Une pause média après 505 reste une hypothèse.

## Reproductions et corrections

| BUG confirmé par test rouge | ROOT CAUSE | FIX | REGRESSION TEST |
| --- | --- | --- | --- |
| Conteneur PyAV ouvert après erreur AAC | flush avant close sans finally | close en finally, idempotence | `test_pipeline_container_closes_even_when_audio_flush_fails` |
| Writer/handler HTTP conservés | release lève avant cleanup | finally imbriqués, wait_closed borné | `test_http_writer_and_handler_close_even_when_release_fails` |
| Réponse SDP après fermeture | await setLocalDescription sans garde d'identité | gardes après await et dans callbacks | `test_no_answer_after_viewer_closed_during_local_description` |
| Shutdown incomplet après listener défaillant | première exception interrompt serveur et worker | tenter chaque cleanup puis remonter les erreurs | `test_shutdown_continues_after_one_listener_fails` |

Le test du vrai `aioice.mdns.MDnsProtocol.datagram_received` confirme que
load_all_types seul provoque CLASS32769.SRV puis ANY.SRV ; le preload beta7
complet élimine ces imports. Son algorithme reste inchangé, chargement dynamique
global toujours autorisé. La matrice Python 3.14 est nécessaire pour conclure
sur la cible HA ; le résultat 3.12 seul ne suffit pas.

Deux reproductions supplémentaires ont guidé les corrections :

| BUG | ROOT CAUSE | FIX | REGRESSION TEST |
| --- | --- | --- | --- |
| Après reset, un IDR sans SPS/PPS ne redonne aucune frame | Le nouveau décodeur a perdu les paramètres du flux | Cache par pipeline V1, un SPS et un PPS de 64 Kio maximum chacun ; préfixe uniquement l'entrée du décodeur en recovery, attend un vrai NAL5 | `test_v1_recovery_idr_without_repeated_parameter_sets`, vrai libavcodec |
| Timeout transmis à HA perd le marqueur incertain | asyncio reconstruit TimeoutError à la sortie de l'exécuteur | ControlFailure explicite créé côté exécuteur avant conversion ; exception synchrone originale conservée comme cause | `test_v1_uncertain_never_replayed[absent-None]` via unlock_for_ha |

## Architecture et portée du diff

Avant : un worker média, les leases pilotent sa vie ; un pending 505 V1 lié
au worker ; fermeture WebRTC/HTTP/PyAV essentiellement séquentielle.
Après : mêmes sessions, mêmes leases, même lecteur unique et mêmes 505/506 ;
cleanup protégé contre erreurs/annulations, gardes d'identité WebRTC, fermeture
native V1 complémentaire et diagnostics de cycle de vie. Aucun second flux
permanent, ni replay, ni déplacement d'une commande vers une nouvelle session.

Modifications de production : `__init__.py`, `button.py`, `camera.py`, `client.py`,
`control.py`, `diagnostics.py`, `hub.py`, `lifecycle.py` (nouveau), `manifest.json`,
`media.py`, `rtc.py`, `v1_control.py`. Infrastructure/documentation : workflow
Validate, README, CHANGELOG, index docs et cette note. Les nouveaux tests sont
`beta8_helpers.py` et `test_beta8_{reproductions,lifecycle,rtc,h264,diagnostics,setup}.py`.

`protected.py`, `protocol.py`, `v1_video.py`, `ring.py`, `standby_ring.py`,
config flow et les autres entités restent identiques à la baseline. Aucun
changement de paquet 505, de chiffrement, de nonce, de mot de passe encodé,
de numéro de sortie, de cooldown, ni de framing atypique 100/101.

## Matrice exécutée et limites de la simulation

Base intacte : **101 tests + 3 sous-tests**. Candidate : **135 tests + 3 sous-tests**,
soit 34 tests supplémentaires. Dernière exécution locale complète : Python
3.12.14, 135 réussis + 3 sous-tests, 21,33 s ; compilation réussie.
La CI Validate exécute pytest et compileall sous Python 3.12 et 3.14,
avec dnspython 2.8.0, aioice 0.10.2, aiortc 1.15.0 et PyAV 17.0.1.
Les résultats CI et le SHA final sont fournis avec la candidate, après le run.

- 50 cycles acquire/release **par modèle**, vrai worker/Session et socket en mémoire
  fragmentant les reads à 73 octets ; 50 connexions puis fermetures, aucun worker,
  socket simulé, consumer, buffer ni commande pendante restant après chaque cycle.
- 50 créations/destructions **par résolution** de pipelines réels H264/G711/AAC/TS/JPEG.
- 50 viewers successifs, réutilisation d'ID avec ancien callback, deux viewers
  simultanés, copie indépendante de frames réelles, queues bornées et pistes arrêtées.
- Annulation pendant close et pendant la négociation : tâches de nettoyage
  attendues, aucun viewer, lease, timeout ou offre restant à la fin du scénario.
- Vrai aiortc : génération de réponse SDP audio/vidéo et fermeture du PC,
  sans serveur STUN/TURN ni échange de candidats distants. Ce test ne prétend pas
  valider la traversée d'un pare-feu d'entreprise.
- Login : vrai envoi multi-TLV 40/501 et lecture fragmentée ; seul le décodage
  de la réponse de login est remplacé à cette frontière dans le simulateur worker.
  5007/5008, média, 505 et 506 chiffrés réels ; confirmations après 0/1/5/9 s.
- Absence 506, EOF, reset, retrait du viewer et arrêt HA pendant commande :
  au plus un 505 et aucune nouvelle session de commande. Le timeout absent est
  raccourci dans le test ; les délais 1/5/9 s sont des délais réels.
- 5009 suivi de 5005 puis TCP close ; présence/absence d'un 5010 mis en file
  lors du stop ne crée pas d'attente obligatoire. Un 5010 traité avant fermeture
  est décodé/compté. Une réponse arrivant après SHUT_RD n'est pas observable :
  le test n'affirme pas qu'elle aurait été acquittée ni que le device est libre.
- Les suites existantes conservent les cas OWSP partiel, limites 6/20 s,
  keepalive, zero frames, 506 avant/après deadline, refus, corruption,
  concurrence, mauvais UID/profil, découverte refusée et retry borné pré-login.
- Cinq cycles setup/unload, cinq échecs partiels de start et cinq échecs de
  platform setup, avec vraies fonctions de lifecycle et frontières HA simulées.
- Exceptions contenant de faux secrets : aucun texte sensible exporté.

Les tests vérifient les propriétaires de ressources et tailles bornées ; ils
ne constituent pas une mesure exhaustive de la mémoire native FFmpeg sur HAOS.
La suite n'installe ni ne redémarre Home Assistant et n'exerce aucune sortie réelle.

## Revue du diff

Relecture des changements par sous-système : aucune écriture 505 supplémentaire,
aucun nouveau retry, pas de modification du parser V1 ni des chemins Connect 2
de chiffrement/sonnette. Les erreurs de cleanup sont comptées/journalisées ou
remontées après les autres fermetures, jamais remplacées par un broad `except: pass`.
Les raisons des trois dernières sessions sont conservées pour qu'une reconnexion
ne fasse pas disparaître l'origine de l'incident. TURN_USED reste inconnu,
plutôt que déduit à tort de la présence d'un candidat relay.

## Rapport de livraison (27 points)

1. **Base/version** : arbre beta7, comparé à beta6 et aux corrections beta5.
2. **SHA initial** : `ab2870dc35dd504ba8aca27e91239e5b573c600c`.
3. **Bugs confirmés** : les six reproductions listées ci-dessus ; perte des
   compteurs de codec à la fermeture également corrigée et testée.
4. **Causes racines** : ordre de cleanup non protégé, identité de viewer non
   revérifiée après await, paramètres H264 perdus au reset, conversion d'exception
   asyncio, absence de conservation des statistiques.
5. **Hypothèses réfutées/non étayées** : load_all_types seul suffit (faux),
   beta7 reproduit nécessairement le SRV après preload complet (non reproduit),
   5010 obligatoirement attendu par stop natif (non démontré), absence de TURN
   équivaut à une panne H264 (faux).
6. **Ouvertes** : cause matérielle exacte du busy, effet réel de 5005, motif
   d'absence 506, pause média post-505, particularités d'autres firmwares.
7. **Fichiers** : liste précise dans la section portée du diff.
8. **Architecture** : même mono-session ; cleanup et observation renforcés.
9. **Tests ajoutés** : six fichiers de tests, 34 cas nouveaux détaillés ci-dessus.
10. **Nombre** : 135 tests et 3 sous-tests.
11. **Python 3.12** : local vert ; CI jointe à la candidate.
12. **Python 3.14** : résultat du run CI final joint à la candidate.
13. **HACS** : résultat du run CI final joint à la candidate.
14. **Hassfest** : résultat du run CI final joint à la candidate.
15. **Stress** : 100 cycles média, 100 pipelines, 50 viewers, 15 cycles de
    setup/unload/échec, plus scénarios concurrence et interruption.
16. **Fuites** : défauts HTTP/PyAV confirmés corrigés ; aucun propriétaire
    résiduel dans les scénarios de stress, sans prétendre couvrir toute mémoire native.
17. **Races** : réponse après fermeture corrigée ; ancien callback/replacement
    et annulation pendant cleanup couverts par tests.
18. **Connect 2** : baseline matériel validée auparavant ; non-régression logicielle
    verte, aucun nouveau test matériel de la candidate.
19. **V1 vidéo** : framing/profil/résolution conservés ; recovery V1 étendu/testé.
20. **V1 output** : un 505 au plus, mêmes octets/routage ; action physique et
    506 sur l'installation réelle toujours non validés.
21. **V1 Stop AV** : 5009 existant puis 5005 natif ajouté ; 5010 optionnellement
    observé, aucune garantie de libération appareil déduite du close local.
22. **WebRTC** : cleanup/gardes/queues validés ; ICE/TURN réels sur Wi-Fi
    d'entreprise exigent un test réseau ultérieur et la configuration HA adéquate.
23. **DNS** : vrai paquet mDNS SRV dans aioice après preload, aucun import_module
    dans la callback du test ; chargement dynamique global conservé.
24. **H264** : invalidité ponctuelle isolée, P-frames ignorées après reset,
    SPS/PPS conservés seulement pour V1 ; compteurs persistants.
25. **Risques résiduels** : 5005 et recovery sur les firmwares réels ; une seule
    paire SPS/PPS conservée, changements complexes de jeux de paramètres à vérifier ;
    PC/codec dépendants de bibliothèques natives ; sélection ICE non attestée.
26. **Matériel requis** : gâche puis portail séparément, réception réelle 506,
    vidéo maintenue, 5009/5010/5005, disparition d'occupé, reprise par l'app officielle,
    réouverture après commande/timeout et répétitions ; recontrôle Connect 2 complet.
27. **SHA final** : fourni dans la livraison et vérifiable par `git rev-parse HEAD`
    sur `stabilization-beta8` ; aucune release ni merge main effectué.

**Résultat physique de cette phase : non testé.** Candidate logicielle pour revue,
pas une validation des relais, du message occupé ou du talkback V1.
