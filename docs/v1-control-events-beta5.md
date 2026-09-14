# Investigation V1 — contrôle et sonnette, après bêta 4

Base : `v0.3.1-beta.4`, `4e323aca7bf7adb5d71f49708fdd422d833c4626`.
La vidéo V1 est déclarée validée sur appareil par son propriétaire. Aucun
changement de codec, parsing vidéo, audio descendant ou diagnostic vidéo prévu.

## Faits établis avant modification

APK WelcomeEye 6.1.58.24, sources Java déjà décompilées, classes2.dex/classes4.dex.
`libglnkio.so` ARMv7 : SHA256
`25063e0678644f907df3d7706d1968004c5e94114f31e9b8161d6e917d418992`.

- `CLPreviewPresenter.unlock` (902), `startUnlock` (431), puis
  `CLPreviewModel.unlock` (185) → `QvLtPlayerCore.unlock` (1040).
  La présentation vérifie une prévisualisation et la permission d'ouverture.
- `QvLtPlayerCore` réutilise **liveChannel**, créé à 620–624 avec
  `channel + 15 / stream 1 / mode 2` : pour le canal logique 1, **16/1/2**.
  Aucun nouveau canal 0/3/0 n'est créé dans cette chaîne d'ouverture.
- Le choix chiffré dépend de `isNewDevice`, renseigné par JSON `type=3/devType`,
  via `SdkLtUtil.parseJsonData`. Cela n'est pas synonyme de modèle commercial
  Connect 2 : un V1 protégé emprunte également ce chemin.
- Chemin chiffré : `liveChannel.optLockReq(0, encodeWithHashAndMd5(code),
  output, 1, bytes(2))`. L'UI 1/2 devient **output 0/1**. Le code d'ouverture
  vient de la saisie/sauvegarde d'ouverture, pas intrinsèquement du mot de passe
  d'authentification. Ils peuvent coïncider ; cela reste à confirmer sur le V1.
- Natif `DataChannelIOCtrl::lockReq`, `0x901e0–0x904ba` : vérification du type
  de chiffrement, profil de nonces de login, corps LE de 44 octets précédé du
  temps appareil U64 ; AES-CFB, puis RC4 avec UID ; **TLV 505** de 100 octets.
  Champs : device U32=0, code encodé[32], délai U32=0, sortie U8, action U8=1,
  réservés[2]=0. Clé AES `a[8:14]+b[4:9]+zeros(5)`, IV `c[4:15]+zeros(5)`.
  Cela concorde avec le constructeur actuel ; aucune constante d'arming ni
  précommande constructeur supplémentaire démontrée dans cette chaîne.
- Chemin non chiffré distinct : mot de passe brut vérifié localement égal au
  mot de passe de session, `TCRequestBean.initUnlock`, TLV 425/426. Le code
  actuel exige une découverte protégée ; ne pas basculer arbitrairement à 425.
- L'application transforme le succès SDK en retour UI, sans capteur de position.
  **506 result=1/reason=0 n'est pas une preuve d'action physique**.

## Différence justifiant le correctif candidat

La bêta 4 utilise une session dédiée 0/3/0 pour V1. L'application utilise la
session média active 16/1/2. C'est une divergence certaine et une explication
plausible de l'acceptation sans effet, **pas une causalité déjà validée**.

Correctif implémenté : obtenir un bail sur le média existant (ou son démarrage normal
si absent), confier l'unique envoi 505 à son worker, lui faire observer 506 sans
second lecteur socket, puis libérer uniquement le bail de la commande. Le
constructeur chiffré reste inchangé. Aucun repli vers une autre session après
envoi ambigu. Aucun changement des commandes Connect 2. Pas de nouvelle session
vidéo permanente, de pause sonnette ou de délai artificiel.

## Sonnette : chemin officiel trouvé

`LtAlarmManager.startInitDevicePush` → `setAlarmStatus` (405–448) →
`LtAlarmConnectManager.sendData` : **requête HTTPS de souscription cloud**.
Le serveur est choisi via `LtConfig.ALARM_SET_URL/ALARM_SET_URL2`, endpoint
`pda_more_api.php` chez push2u. Le corps utilise `act_type=3`, la liste des
appareils, les types d'alarme, `switch_state`, un jeton **FCM**, un identifiant
client et des métadonnées d'application. Aucune requête de ce type n'a été
envoyée pendant l'investigation.

Le retour passe par le traitement des notifications et `LtAlarmManager.handlePush`
(339), avec déduplication et stockage. Les types UI sont aussi remappés :
`qvAlarmTypeToLtAlarmType`, notamment UI 19 → LT 14. `AlarmHelper` décrit des
types d'événements ; il n'est pas un abonnement réseau.

**Aucune commande locale fiable de souscription après login n'est établie.**
Cela ne prouve pas que le V1 est incapable d'alarme locale, mais ne justifie pas
de réactiver le listener 0/3/0 qui ne recevait que 40/57/70/502. Il reste en
standby. Les alarmes Connect 2 510 → OWSP → 14854/reportAlarm restent intactes.

## Repères reproductibles dans l'extraction locale

Les chemins suivants sont relatifs à `work/decompiled/sources`, à côté du
checkout de travail. Les sources propriétaires et les binaires ne sont pas
ajoutés au dépôt.

| Rôle | Source |
| --- | --- |
| UI et code d'ouverture | `com/quvii/qvfun/compat/lt/video/presenter/CLPreviewPresenter.java` |
| Passage au lecteur | `com/quvii/qvfun/compat/lt/video/model/CLPreviewModel.java` |
| Choix 425/505 et liveChannel | `com/quvii/compathlt/QvLtPlayerCore.java` |
| Corps de la commande legacy | `com/quvii/compathlt/device/TCRequestBean.java` |
| Type de chiffrement découvert | `com/quvii/compathlt/device/SdkLtUtil.java` |
| Passage JNI | `glnk/client/GlnkChannel.java`, `glnk/client/DataChannel.java` (`native_optLockReq`) |
| Souscription et réception push | `com/quvii/compathlt/alarm/LtAlarmManager.java` |
| Transport HTTPS du push | `com/quvii/compathlt/alarm/LtAlarmConnectManager.java` |
| Adresses du service push | `com/quvii/compathlt/LtConfig.java` |
| Libellés des alarmes | `com/quvii/qvfun/publico/sdk/AlarmHelper.java` |

Le natif analysé est `work/native/libglnkio.so`, fonction
`DataChannelIOCtrl::lockReq` à `0x901e0` (748 octets). L'inspection est limitée
au chemin de commande, au passage JNI et aux fonctions pertinentes.

Le code est encodé par SHA-256 hexadécimal, puis MD5, puis réduction des paires
d'octets modulo 62 dans l'alphabet `0-9A-Za-z`, donnant huit caractères.
Le champ de 32 octets est complété par des zéros. Le paquet 505 contient
`RC4(UID, a[16] + b[16] + AES-CFB(clear[52]) + c[16])` ; les nonces suivent
le profil négocié. Le temps est celui de l'appareil. Le délai zéro et l'action
un sont déjà présents dans le constructeur ; aucun sous-identifiant distinct
gâche/portail n'a été trouvé en dehors de l'index 0/1. Les contrôles de permission
et de prévisualisation de l'UI ne constituent pas une commande réseau d'arming.

## Implémentation et limites

- `v1_control.py` contient le contrôle V1 ; `control.py` le sélectionne uniquement
  pour le modèle V1. `hub.py` ajoute trois points de passage au worker existant :
  observation de réponse, envoi en attente, notification de fermeture.
- Le worker demeure l'unique lecteur/émetteur de sa socket. Il traite le lot reçu
  avant d'envoyer la commande, afin de ne pas accepter une confirmation déjà
  présente dans ce lot. Une commande est liée à l'identité de sa session et
  n'est jamais transférée à une session reconnectée.
- Une seule tentative 505 est autorisée, même si `sendall` échoue partiellement.
  Le cooldown de trois secondes commence à cette tentative. L'attente est bornée
  à dix secondes après préparation. Une annulation avant prise en charge par
  le worker produit zéro paquet ; un envoi déjà commencé ne peut être rappelé.
- Après confirmation absente, tardive, illisible ou envoi incertain, une nouvelle
  commande sur cette même session est refusée. Il faut fermer les vues actives
  puis rouvrir une session ; cela ne rejoue aucune action précédente.
- La libération du bail de commande conserve les vues déjà ouvertes. Sans autre
  consommateur, elle utilise le Stop AV et la fermeture existants. Le chemin
  vidéo V1, les codecs et le flux Connect 2 ne sont pas modifiés.
- Les diagnostics ajoutent le chemin de contrôle, les tentatives d'envoi et
  l'absence de vérification physique. Les compteurs de souscription restent zéro
  et son résultat reste indéterminé, puisqu'aucun abonnement n'est implémenté.
  Ni code, UID, adresse, paquet brut ni contenu d'alarme n'est ajouté.

Les modèles commerciaux ne suffisent pas à prouver que tous les firmwares V1
emploient ce même chemin. Le correctif vise le V1 protégé identifié ici. Le code
d'ouverture de l'application officielle peut être distinct du mot de passe
configuré dans l'intégration : ce point doit être vérifié sans publier ce code.

## Validation logicielle et validation matérielle restante

La suite locale complète passe : **86 tests, 3 sous-tests**, avec PyAV 17.0.1.
La validation GitHub de l'implémentation `d06f5aa` est également entièrement
verte : [pytest, HACS et Hassfest](https://github.com/Mins95/WelcomeEye/actions/runs/34806086551).
Le numéro candidat `0.3.1-beta.5` a été préparé seulement après ces trois succès.
Il ne constitue ni une release publiée ni une validation physique. La branche
`v1-control-events-beta5` reste séparée de `main`.

`tests/test_beta5_v1_control.py` couvre les deux sorties, les champs chiffrés,
l'unicité d'envoi, le timeout, l'UID incorrect, les erreurs avant envoi, les
concurrences, l'annulation, la reconnexion et la libération. Elle utilise aussi
le véritable worker média, son cycle acquire/release et le décodage PyAV de
la forme vidéo V1 observée sur matériel, sur socket simulée.

Les tests historiques des sessions dédiées sont conservés pour Connect 2 dans
`test_control_coordination.py`, `test_beta2_v1_control.py` et
`test_beta3_v1_doorbell_standby.py`. Le standby V1 reste testé. Les tests vidéo,
audio et protocolaires existants font partie de la suite complète.

**Aucune commande n'a été envoyée au matériel pendant ce développement.**
L'action physique V1 reste non validée. Les vérifications manuelles restantes,
après installation explicite de la version candidate, sont :

1. Vérifier que le code d'ouverture choisi dans l'application officielle est
   celui configuré, puis ouvrir la caméra V1 et vérifier vidéo/audio descendant.
2. Déclencher une seule action volontaire sur la gâche (output 0), observer
   physiquement le résultat et relever uniquement les compteurs de diagnostics.
   Ne pas relancer automatiquement si l'accusé arrive sans effet.
3. Si le portail est câblé et son test souhaité, effectuer une action distincte
   sur output 1, après le cooldown, et noter séparément le résultat physique.
4. Vérifier que la vidéo reste ouverte après la commande, puis fermer les vues
   et vérifier que l'application officielle peut reprendre sans état busy.
5. Vérifier séparément une commande volontaire sans vue HA déjà ouverte : le
   média temporaire doit être libéré ensuite. Vérifier les fonctions Connect 2
   sur son appareil lors de la validation finale.

La sonnette V1 demeure indisponible en local tant qu'une séquence fiable n'est
pas établie. Si une capture devient nécessaire, limiter l'essai à environ
30 secondes : application officielle au premier plan, début avant ouverture
du direct, un seul appui sur sonnette, puis fermeture du direct. Filtre local :
`host <IP_V1> and host <IP_TELEPHONE>`. Fournir un PCAP privé et les horaires
des trois actions ; aucune commande gâche/portail pendant cet essai. Une absence
de paquet local ne déchiffre pas le push HTTPS et ne prouve pas l'absence d'une
autre capacité locale. Aucun essai réseau n'est demandé avant la validation
du correctif de contrôle.
