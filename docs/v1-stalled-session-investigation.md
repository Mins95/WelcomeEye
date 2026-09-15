# V1 : session bloquée après la première vidéo (0.4.0)

## Rapport matériel reçu

Connect V1 / DES9900VDP, HA 2026.9.2 / Python 3.14.6. Premier affichage en environ cinq secondes, puis quatre écrans noirs. Microphone sans son, commande gâche en échec. L'application officielle reprend après environ cinq secondes.

Le diagnostic contient une seule discovery, un seul login réussi (environ 1,57 s), un seul profil et un seul Start AV réussi. Treize paquets vidéo, deux IDR, aucune erreur de décodage comptée. Aucune fermeture média enregistrée. Le worker et la Session sont toujours présents, `connected=true`, alors que les consommateurs et spectateurs sont à zéro. WebRTC a enregistré `cleanup_error_type=RuntimeError`.

La commande d'ouverture a atteint `queued_on_v1_media` mais a expiré avec **zéro tentative 505**. Le microphone a envoyé zéro trame ; le dernier type d'erreur enregistré est `BrokenPipeError`.

## Ce que ces données ne prouvent pas

- Le snapshot ne localise pas le blocage initial du thread. Il ne contient ni sa pile ni son étape courante détaillée.
- `BrokenPipeError` peut être une erreur secondaire de stop-talk : le code 0.4.0 écrase l'erreur primaire dans ce cas.
- Le message de la carte « indisponible ou format non pris en charge » couvre toutes les erreurs de démarrage du micro. Il ne démontre aucune réponse 332 indiquant un codec incompatible.
- Le format G.711 A-law du son descendant n'établit pas le format négocié du microphone montant.
- Le retour de l'application officielle ne prouve pas que HA a exécuté correctement son teardown : le firmware peut avoir fermé sa connexion auparavant.

## Défauts reproduits dans le code publié

Reproductions hors matériel, à partir du code de `4bd7aa4` (fonctionnement 0.4.0). Injection contrôlée d'un worker qui ne se termine pas au join ; cela reproduit les conséquences d'un blocage, pas sa cause initiale.

1. `_halt_media()` ne passe à `_state(False)` qu'après un join réussi. Un join encore vivant lève RuntimeError et laisse `connected` et `ready` dans leur état précédent.
2. `acquire()` accepte ce worker vivant sans vérifier son stop-event. La session déjà vouée à l'arrêt est donc déclarée acquise immédiatement et peut recevoir une commande en file d'attente qui ne sera jamais traitée.
3. `_stop()` du microphone remplace l'erreur de démarrage par une éventuelle erreur de nettoyage. Une simulation `TimeoutError` au démarrage puis `BrokenPipeError` au stop reproduit exactement cette perte de cause primaire.
4. `_close_viewer()` peut sortir sur une erreur de stop-talk avant de libérer le lease, d'arrêter les pistes et de fermer le PeerConnection. Ce quatrième scénario n'est pas prouvé dans le rapport matériel, qui montre zéro consommateur.

Les quatre assertions ont échoué sur le code publié avant correction.

## Correction publiée dans 0.4.1-beta.1

- Révoquer la disponibilité dès le début de l'arrêt, avant le join.
- Refuser une acquisition sur un worker vivant dont l'arrêt est demandé. Ne créer aucun second worker et ne rejouer aucune commande. Une acquisition ultérieure peut démarrer normalement une fois le thread terminé.
- Rendre la libération d'un lease déjà absent sans effet : le finally de la commande V1 ne doit pas relancer le join après une acquisition refusée ni masquer cette première erreur.
- Poursuivre les étapes de fermeture WebRTC après une exception de nettoyage, en conservant le stage d'erreur.
- Conserver séparément l'erreur initiale du micro et son erreur de nettoyage. Distinguer refus explicite, format explicitement incompatible, timeout et transport dans le message de la carte.
- Exporter le stop-event, l'étape courante du worker, les phases/durée d'arrêt, l'étape de nettoyage WebRTC, la réception effective de 332 et le framing de la session courante. Aucun payload, adresse, identifiant ou texte d'exception brut ajouté.

Fichiers modifiés : `hub.py`, `rtc.py`, `talkback.py`, `diagnostics.py`. Aucun constructeur de paquets ni profil média modifié.

## Invariant de commande physique

`control.py`, `v1_control.py`, `client.py`, `protocol.py` et `protected.py` restent identiques à la base. Le nouvel échec d'acquisition précède la construction de PendingOutput. `send_pending()` conserve son passage irréversible queued → sent et efface le paquet avant l'unique tentative. Aucun retry, transfert de commande ou nouvelle session n'est ajouté. Le test de commande sur worker en arrêt confirme zéro tentative et aucun pending résiduel.

## Limite de la correction

Cette correction empêche la réutilisation d'une session arrêtée et améliore le diagnostic. Elle **ne garantit pas de débloquer un thread coincé dans une bibliothèque native**. La cause initiale, le microphone V1 et la gâche physique restent à vérifier avec le prochain retour matériel. Les journaux reçus ne contiennent que `ConnectionError`, sans localisation de la cause. Cette préversion ajoute les informations manquantes pour le prochain essai. Aucune commande physique ni installation sur le matériel du testeur n'a été effectuée pendant le développement.

## Test demandé

Installer **0.4.1-beta.1** parmi les préversions HACS, redémarrer HA puis recharger l'interface. Tester d'abord les ouvertures/fermetures de vidéo sans micro. Au premier échec, télécharger le diagnostic immédiatement, avant tout redémarrage, et noter l'heure de l'action. Si la vidéo fonctionne, tester séparément le micro en HTTPS et télécharger un nouveau diagnostic en cas d'échec. Aucun essai de sortie physique n'est nécessaire pour cette première collecte.

## Validation logicielle finale

**177 tests et 3 sous-tests passent en 126,50 secondes**, sur Python 3.12.14. La suite comprend les 50 acquisitions V1, les fermetures WebRTC, les connexions aiortc locales, les codecs, les confirmations retardées et les erreurs de transport après tentative 505. Les sept nouveaux tests couvrent les défauts ci-dessus, l'absence d'envoi après refus d'acquisition, les messages micro distincts et les champs numériques de la réponse 332 sans payload brut. Les tests sont conservés dans le workspace `work/v1-stalled-validation`, séparément du dépôt public.

Ce résultat valide les chemins logiciels simulés. Il ne valide ni la cause du blocage réel ni le fonctionnement physique du V1.
