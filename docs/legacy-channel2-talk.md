# Microphone de la seconde platine : Connect V1 et Connect 2 R001

L'essai est désactivé par défaut. Dans **Reconfigurer**, activer d'abord la
seconde platine, puis **Essai du microphone de la seconde platine**. Ouvrir le
direct de l'entrée 2 avant d'activer son micro. Vérifier physiquement quel
haut-parleur reçoit la voix : le routage logiciel suit l'APK mais sa destination
sur cette installation reste à confirmer. L'option ne change ni la sonnette
cloud V1, ni les autorisations de gâche/portail.

Le micro utilise le worker et la socket LT déjà ouverts. Il n'ouvre aucune
deuxième connexion et ne change pas de canal à lui seul. Arrêter ou changer
le direct ferme le microphone. Une session, des identifiants ou un profil
modifiés interdisent la poursuite de l'envoi.

## Preuves vérifiées dans WelcomeEye 6.1.58.24

Il s'agit de l'APK WelcomeEye historique, pas de l'APK Door Connect du
Connect 3. Son APK principal a l'empreinte SHA256
`ff205ff0d24527b912d5c227aa80f8a6500979393ec0cefa62e35082173d6b52`.
Les offsets natifs ci-dessous sont des adresses ELF ARM/Thumb de
`libglnkio.so`, relues dans l'extraction utilisée pour le micro principal.

- `CLPreviewModel.java:59` transmet le canal choisi à `QvLtPlayerCore`.
- `QvLtPlayerCore.java:623` crée le `liveChannel` avec `channel + 15`,
  stream 1 et mode 2 : l'entrée 2 utilise donc le canal vidéo natif 17.
- `QvLtPlayerCore.java:652` demande le micro sur ce même `liveChannel`.
  Le callback audio à la ligne 435 utilise `liveChannel.sendAudioData(0, data)`.
- `sendTalkCmd`, `0x8ed04`, produit TLV 331 avec huit octets et la commande
  à l'offset 4 ; le résultat TLV 332 doit autoriser un format pris en charge.
- `sendAudioData`, `0x8ed5c`, appelle `getChannelNO` à `0x8ed90`, ajoute un
  à `0x8ed94`, puis construit TLV 97 (`0x8edb8`) et TLV 98 (`0x8edcc`).
  Le micro de la session vidéo 17 envoie donc la métadonnée audio **18**.
- `getChannelNO`, `0x88ba2`, lit le canal conservé dans le `DataChannel`.

Ce chemin ne reprend pas le sélecteur QV 65535 du Connect 3. Les diagnostics
séparent `requested_channel: 2`, `wire_video_channel: 17` et
`wire_audio_channel: 18`, avec `physically_verified: false`.

## Vérification et limites

Les tests synthétiques couvrent les constructeurs LT réels, les permissions
HA, les deux modèles, l'annulation pendant la négociation, la révocation du
contexte, l'arrêt du direct et la réouverture du canal principal. Le parcours
HA utilise également aiortc et un microphone généré, sans contacter un appareil.

**R002 reste exclu de cet essai micro 2.** Son backend est QV : le SDK expose
un talk distinct avec le sélecteur par défaut 65535, pas les TLV LT ci-dessus.
Le rattachement de son firmware à un contexte de conversation de seconde
platine n'est pas matériellement établi. La présence de fonctions génériques
dans le SDK ne suffit pas à lui transposer le routage LT 17 → 18.
