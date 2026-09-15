# V1 : fermeture prématurée après les premières images

Base vérifiée : `e79cdd071231fde31f676a7f479aaa905b7ef1de`
(`v0.4.1-beta.1`). La branche distante correspond à cette base ; `main`
reste sur `4bd7aa4c31f3cf59b6adcc827c75e16e8fa1e754`.

## Ce qui est établi

Le testeur rapporte trois lectures réussies dans l'application officielle,
intégration désactivée, mais une image figée dans le lecteur WelcomeEye HA
ouvert comme dans la vignette. Recevoir quelques paquets H264 ne prouve donc
pas qu'un direct continu fonctionne.

Le lecteur V1 `Session._v1_exact()` impose des lectures de deux secondes.
Sur un en-tête entièrement vide, ce délai remonte comme `TimeoutError`, sans
empoisonner le lecteur. Avant la première image, le worker absorbe ces pauses
jusqu'à sa deadline d'acquisition. Après la première image, il appelait
`session.read()` sans traiter ce cas, malgré son `settimeout(10)` : la limite
interne de deux secondes prenait le dessus et provoquait Stop AV/fermeture.

Reproduction avant modification, avec le vrai Session, worker et PyAV et une
socket exclusivement en mémoire : après une première série de huit images,
une pause de trois secondes suivie de nouvelles images provoque la fermeture
à environ 2,01 secondes. Les deux tests de délai échouent sur la base.

## Correction

Le worker tolère les polls sans aucun octet d'en-tête sur le lecteur V1 déjà
actif, sur **la même session**, dans un budget de dix secondes depuis la
dernière lecture complète. Il revient dans la boucle normale entre les polls,
permettant arrêt et traitement d'une commande explicitement demandée.

Le parsing, les limites des paquets partiels et les keepalives sont inchangés.
Une exception dédiée `V1IdleTimeout` marque uniquement le timeout du `recv`
sur un en-tête vide : un timeout d'écriture de keepalive n'est jamais absorbé.
EOF, reset, erreur de protocole et silence dépassant dix secondes restent
fatals. Le chemin Connect 2 n'absorbe aucun nouveau timeout. Aucun nouveau
retry de connexion ni de commande n'est ajouté.

La comparaison statique du maintien de connexion n'autorise pas un nouveau
format : `work/native/libglnkio.so`, `DataChannel::sendAliveReq`,
`0x892a4–0x8930c`, construit bien un TLV 49 de quatre octets (octet de canal,
puis zéros), comme le code existant. Aucun changement de cette commande.

## Diagnostic conservé

Chaque lifecycle contient désormais les compteurs de paquets vidéo reçus,
d'images vidéo et de trames audio décodées, le nombre de polls live expirés,
le délai relatif du dernier paquet vidéo et la durée de session. À sa fermeture,
il conserve également son état de transport, ses compteurs codec et les
motifs d'acceptation/rejet du récepteur V1.

`media.last_media_lifecycle` conserve la dernière session ayant reçu des paquets
vidéo, même si quatre échecs de discovery effacent son entrée de l'historique
circulaire. Aucune adresse, UID, donnée audio/vidéo brute, clé, mot de passe,
SDP ou candidate ICE n'est ajoutée.

## Sécurité des commandes

`v1_control.py`, `control.py`, `protected.py`, `protocol.py`,
`media.py`, `rtc.py` et la carte ne sont pas modifiés. Le profil reste 16/1/2.
Le worker retrouve `send_pending()` après un poll vide, mais celui-ci ne peut
prendre qu'une requête à l'état `queued`, attachée à cette même session.
Sous verrou, il la marque `sent` et vide son paquet **avant** l'unique appel
d'envoi. Une itération suivante, un échec partiel ou une reconnexion ne peut
donc pas rejouer cette demande. Le test de commande pendant la pause compte
exactement un 505, sur une seule socket simulée.

## Limites

Validation locale finale : **186 tests et 3 sous-tests réussis en 148,87 secondes**.
Cela couvre la reprise vidéo après trois secondes avec
des images effectivement décodées et des PTS croissants, le silence permanent,
EOF/reset/paquet tronqué, la conservation du diagnostic après quatre échecs
de discovery, l'anonymisation, les cycles Connect 2, l'unicité du TLV 505 et
la non-absorption d'un timeout d'écriture de keepalive. PyAV et aiortc réels,
frontières Home Assistant simulées, Python 3.12 ; aucun matériel contacté.

Tests externes au dépôt :
`work/v1-stalled-validation/tests/test_v1_live_idle.py` ; résultat de la suite :
`work/v1-stalled-validation/live-idle-results.xml`.

Le défaut de délai est reproduit et corrigé ; son rôle dans le cas du testeur
reste non démontré. Ses journaux signalent aussi `ConnectionError`, alors que
la reproduction de ce défaut produit `TimeoutError`. La correction ne prétend
pas expliquer ces fermetures distantes, ni l'absence de réponse discovery
suivante, ni la minute hors ligne dans l'application officielle.

Le diagnostic enrichi conserve précisément la session initiale nécessaire
pour distinguer ces cas. Aucun test matériel V1, aucune ouverture réelle,
aucun déploiement ou publication ne font partie de cette validation locale.
