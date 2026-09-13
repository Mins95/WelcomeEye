# Beta 15 — réception vidéo V1 (travail local)

## Preuves avant modification

Référence : `138748d`, 38 tests synthétiques. Bibliothèque examinée :
`libglnkio.so`, SHA-256
`25063e0678644f907df3d7706d1968004c5e94114f31e9b8161d6e917d418992`.

1. `Session._exact()` accumule localement des octets puis les perd si `recv()`
   lève `TimeoutError`. Le worker peut rappeler `read()` à la mauvaise frontière.
   Au contraire, `IParser::handleDataBytes`, `0x68a22–0x68a34`, conserve les
   octets non consommés ; `OWSPParser::handling`, `0x92d38`, attend la longueur
   extérieure complète avant `onParse` (`0x92d9e–0x92daa`). Correction prévue :
   poursuivre la même lecture V1 après un timeout intermédiaire, avec limites.
2. `onParse` : 97 (`0x91ff8`) copie huit octets de métadonnées audio ;
   99/102/109 (`0x911c4`) alimentent l'état vidéo de 16 octets. Les champs lus
   ensuite sont notamment le compteur à +4 et la taille à +12. Les types
   100 (`0x92730`) et 101 (`0x92746`) portent respectivement I/P et transmettent
   le reste du paquet OWSP. Le chemin Python générique peut promouvoir d'autres
   types en vidéo et découpe systématiquement selon la longueur TLV.
   Correction prévue : branche V1 explicite, métadonnées séparées et terminal
   vidéo selon la frontière OWSP seulement si sa taille est corroborée par
   les métadonnées du même paquet. Le parseur Connect 2 restera inchangé.
3. `JNIDataAdapter::onVideoData`, `0x94114`, copie les octets sans transformation.
   Java `JNIDataAdapter.onVideoData`, puis `QvLtPlayerCore` les transmettent au
   renderer. Aucun retrait d'en-tête ni conversion AVCC n'est justifié ici.

## Fragmentation différée

103/106 commencent respectivement une image I/P ; 107 ajoute et 108 termine.
`createDSFrame` (`0x92a12`) utilise la taille des métadonnées ;
`copyToDSFrame` (`0x92a9a`) borne les copies à la capacité. Le callback final
utilise la longueur cumulée, sans égalité finale démontrée avec la taille prévue.
Aucun index de fragment intermédiaire n'a été démontré. TCP ordonne les octets,
mais ne prouve pas l'identité de chaque fragment applicatif. Pour respecter la
validation stricte demandée, cette version compte et rejette ces types ; elle
ne transmet jamais un fragment isolé au décodeur.

Les divergences sont établies statiquement. Leur responsabilité dans la panne
du matériel V1, ainsi que son cadrage effectif, restent à vérifier sur appareil.

## Correction réalisée

- Activation seulement après authentification d'une session média V1 connue,
  ou après identification du format 352×288. Authentification, discovery,
  sessions sonnette/commande et parcours Connect 2 gardent leur lecteur existant.
- `_v1_exact()` conserve le tampon pendant les timeouts socket intermédiaires.
  Un timeout sans aucun octet du mot de longueur reste une attente vide normale.
  Dès qu'un en-tête est partiel ou qu'un payload est annoncé, la même lecture
  continue : **6 s maximum sans progression**, **20 s au total**, padding inclus,
  **1 048 576 octets maximum** de contenu OWSP, séquence comprise. La socket
  attend au plus 2 s à la fois et le keepalive est vérifié entre les lectures.
  Les délais socket précédents sont restaurés. Une fermeture/interruption
  interrompt la lecture ; un échec partiel interdit de réutiliser le lecteur,
  tout en conservant le côté écriture pour le Stop AV existant.
- `parse_video_tlvs()` ne reçoit que des paquets OWSP complets. Les TLV restent
  bornés et leur nombre est limité à 128 par paquet. Pour 100/101, une longueur
  TLV plus courte peut être remplacée par le reste du paquet **seulement** si
  les métadonnées vidéo de 16 octets du même paquet annoncent exactement cette
  taille. Une longueur trop grande, nulle ou non corroborée est rejetée.
- `V1VideoReceiver` sépare 99/102/109 des images 100/101. Il exige des métadonnées
  fraîches (moins de 20 s), non réutilisées et une taille exacte ; il ne conserve
  aucun payload entre les appels. Ces contrôles sont volontairement plus stricts
  que ceux démontrés pour les images complètes dans le natif.
- Les octets Annex-B sont transmis tels quels. Un préfixe inconnu, de l'AVCC,
  un NAL interdit ou l'absence de NAL de slice sont rejetés. Cette validation est
  structurelle, pas une preuve de décodabilité : le vrai décodeur doit encore
  produire une image. Aucun octet n'est retiré, aucun start-code n'est inventé.
- Le flag I/P est exclusivement 100/101 : une I-picture n'est pas forcément IDR.
  Comme à `0x924b0–0x924c6`, la continuité du compteur est contrôlée lorsque le
  précédent est non nul. Après rejet/perte, les P attendent une nouvelle I ;
  aucune commande `needIFrame` n'est envoyée. Une I autorise la resynchronisation.
- 97, 57 et les types inconnus ne deviennent jamais de la vidéo. Les fragments
  103/106/107/108 sont comptés et rejetés, sans tampon de réassemblage.

### Diagnostics

`media.v1_video_structure` et `media.v1_video_previous_sessions` sont conservés.
Dans la structure courante :

| Champ | Signification |
|---|---|
| `valid_length_words` / `last_announced_size` | Paquets annoncés, pas forcément complets |
| `completed_owsp_payloads` / `last_complete_size` | Paquets entièrement reçus |
| `socket_receive_timeouts` | Timeouts socket intermédiaires, y compris récupérés |
| `partial_payload_timeouts` | Timeouts durant un payload annoncé |
| `bytes_progress_before_timeout` | Octets déjà reçus dans la dernière lecture ayant expiré |
| `read_failures` / `recent_read_errors` | Attentes vides ou échecs remontés à l'appelant ; consulter la phase |
| `video_receive.tlv99_count`, `tlv100_count`, `tlv101_count` | Types observés par le récepteur, sans limite d'échantillonnage |
| `video_receive.complete_video_count` | Images structurellement admises, pas nécessairement décodées |
| `video_receive.*` autres compteurs | Motifs de rejet : taille, metadata absente/périmée, framing, séquence, fragments, etc. |

Les structures détaillées restent limitées à 128 paquets échantillonnés, huit
résumés récents et 64 Kio scannés par candidat. Les compteurs ne contiennent ni
octets vidéo, ni image, ni identifiant, IP, secret ou horodatage brut. Aucun
compteur « réassemblé » ne prétend qu'une fonctionnalité absente a réussi.

## Validation locale

**71 tests réussis**, dont les **38 tests d'origine inchangés** et 33 nouveaux.
PyAV **17.0.1**, Python 3.12 ; aucune connexion appareil/HA. Les assertions de
décodage exigent un vrai `av.CodecContext` et échouent avec un module factice.

- Pauses TCP dépassant les 2 s précédentes, mot de longueur scindé, deux OWSP
  concaténés, keepalives pendant la progression, EOF, absence de payload,
  expiration d'inactivité, progression continue dépassant le budget total,
  padding continu, annulation, plafond de taille et lecteur invalidé.
- Metadata → I/P, limites de longueur, image >65 535 octets avec frontière
  corroborée, métadonnées manquantes/périmées/réutilisées, séquences discontinues,
  wrap et compteur nul natif, fragments dans différents ordres jamais décodés.
- H.264 synthétique créé par libx264 à partir de plans YUV générés, sans capture.
  Une I et sept P passent par `Session.read()` → récepteur V1 → **vrai**
  `MediaPipeline.feed_video()`. Décodage de frames 352×288 et production JPEG
  effectivement vérifiés, ainsi que la sortie MPEG-TS.
- Le **vrai `_worker()` du hub** est exécuté avec ses seules frontières HA et
  réseau simulées : V1 connu, V1 reconnu via 203, puis Connect 2. Les octets
  vidéo, flags I/P et octets audio A-law arrivant aux vrais pipelines sont
  comparés exactement aux fixtures. Le décodage audio est aussi vérifié.
- `media.py`, `rtc.py`, `camera.py`, `button.py`, `protected.py`, `protocol.py`,
  `control.py` et `ring.py` sont inchangés par rapport à `138748d`. Aucun retry,
  profil, mapping, cooldown, commande physique ou logique WebRTC n'a été modifié.

Reproduction depuis le checkout dans un environnement Python disposant de
`av==17.0.1`, Pillow et cryptography :

```text
python -m unittest discover -s tests -q
```

L'exécution locale a nécessité l'accès hors sandbox aux bibliothèques PyAV à
cause de leurs permissions Windows. Cela n'a impliqué aucun accès au matériel.
Hassfest/HACS et HA réel n'ont pas été exécutés ; il n'y a ni push ni déploiement.

## Limites et cause probable

La perte de données partielles sur timeout est une **divergence certaine du
code** et une **cause plausible majeure** de la panne. Une taille annoncée de
6396 octets ne prouve toujours pas que le contenu est arrivé : il faut les
compteurs de complétion pour l'établir. Le mauvais découpage des terminaux et la
promotion des métadonnées sont deux autres différences désormais traitées.

Si le V1 réel émet des images Annex-B complètes 100/101 avec les métadonnées
observées dans le SDK, celles-ci peuvent désormais atteindre le décodeur malgré
des pauses TCP. Le flux synthétique le démontre, **pas le matériel**.

Restent à confirmer : présence et fréquence des métadonnées, validité du champ
taille pour chaque image complète, SPS/PPS disponibles, cadrage Annex-B effectif,
cadence des I, latence réseau acceptable, synchronisation audio/vidéo et libération
du visiophone réel. AVCC, métadonnées réutilisées et flux uniquement fragmenté
restent volontairement non pris en charge. Le premier OWSP avant identification
V1 conserve le lecteur historique. Aucune image V1 réelle n'a été vue pendant
ce travail. La non-régression WebRTC réelle reste également à effectuer.

## Protocole exact de test physique — à exécuter ultérieurement

Ce protocole n'a pas été exécuté. L'installation de la candidate nécessite une
instruction distincte ; aucune commande physique ne fait partie du test vidéo.

1. Fermer tous les lecteurs HA et l'application Philips. Attendre 20 s. Après
   installation explicitement demandée, vérifier la version `0.3.0-beta.15` et
   la provenance du commit. Télécharger un diagnostic **A — repos** ; vérifier
   le modèle V1 et la connexion du listener sonnette.
2. Ouvrir **un seul** lecteur caméra HA. Attendre au maximum **35 s**. Noter si
   une vraie image apparaît et bouge, le délai approximatif, la fluidité et le
   son. Ne pas ouvrir l'application Philips en parallèle.
3. Avant de fermer, télécharger **B — vidéo**. Vérifier le profil 16/1/2,
   Start AV 5008 `result=1`, H264 352×288/20 fps, les paquets OWSP complets,
   compteurs 99/100/101, `complete_video_count` et `media.has_snapshot`.
   Un compteur d'images admises sans snapshot ne suffit pas à conclure au succès.
4. Fermer le lecteur HA, attendre 20 s, télécharger **C — arrêt**. Vérifier
   le Stop AV et la reprise du listener. Ouvrir ensuite l'application Philips
   pour vérifier que le visiophone est libre, puis la refermer. Sonner une fois
   et confirmer l'événement HA après reprise. Ne pas tester les sorties ici.
5. Si une image mobile a été obtenue et que la libération est correcte, refaire
   une fois ouverture/lecture 20 s/fermeture. Cela vérifie une nouvelle session,
   sans accumulation d'état ni blocage du visiophone.
6. Si aucune image ou si le visiophone reste occupé, arrêter les essais et
   conserver A/B/C, incluant les deux sessions précédentes. Relever les motifs
   `missing_video_metadata`, `video_size_mismatch`, `unsupported_video_framing`,
   `waiting_for_keyframe`, `video_sequence_gap`, `unsupported_fragment_tlv_count`
   et les erreurs de réception. Ne pas multiplier les reconnexions manuelles.
7. Sur Connect 2, effectuer séparément une lecture 20 s avec audio, fermer,
   vérifier la reconnexion via l'application, puis la sonnerie. Aucun changement
   aux sorties n'étant présent, leur protocole reste celui de la revue précédente.

Si seuls 103/106/107/108 apparaissent, il faudra une trace structurelle ordonnée
de leurs tailles et métadonnées pour établir la stratégie de réassemblage. Si
les OWSP restent incomplets malgré la progression conservée, il faudra les
compteurs des phases/timeouts avant toute autre modification de framing.
