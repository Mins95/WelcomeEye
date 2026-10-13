# Connect 3 — preuve ciblée du chemin d’alarme natif

Cette analyse statique concerne le SDK ARM64 de Philips Door Connect 1.0.123.3.
Elle établit un chemin de diagnostic autonome reproductible. Elle ne prouve
pas que le firmware A331 accepte l’abonnement, ni qu’un message reçu correspond
à une sonnerie. Aucun matériel, compte cloud ou application en cours d’exécution
n’a été contacté pendant cette analyse.

## Source et portée

Source : `liblive_player.so`, SHA-256
`bb375c04df0f9b15407a8c158163ff121dc517be15ce55def0a4be2064019c68`.
Les adresses ci-dessous sont relatives à cette bibliothèque et ne valent pas
pour une autre version du SDK. Les extraits locaux sont conservés dans
`work/rc3-ring-local-dev/analysis`, hors du paquet de l’intégration.

## Endpoint et identifiants

Le chemin Java `QvPlayerCore.playFormLan` (`classes2.dex`, `0x28d71c`),
son callback `QvPlayerCore.e` (`0x28aba8`) et `startPlay` (`0x28e6a8`)
associent `adminapp2`, l’authCode encodé déjà fourni au CGI, l’IP configurée,
le port média et la clé retournée par HTTPS. Le port est le port média TLS
annoncé si cette option est active, sinon `34567`. `IpAddOpenDevice`
(`0x2897e4`) et `F` (`0x28bb18`) réutilisent ces mêmes champs pour le contrôle
IP local avec `QvDeviceCtrlCore.openDeviceIpAddQv` (`0x287168`, `ap=1`).

Le callback `QvPlayerCore$15.onNext` (`0x288428`) conserve la clé `key` du CGI ;
il n’utilise pas le champ `tdc` comme mot de passe du lecteur LAN. Aucun nouveau
mot de passe ne doit donc être inventé ou recherché. Ce pont statique permet
un essai sans démarrer la vidéo ; son acceptation sur le firmware reste à tester.

La sélection native est résolue : `CManager::Init` (`0x68bc54`) utilise
`WQ_LIB::plugin_info` (`0x99ac20`), `create_prob` (`0x7c54a0`) et le premier
slot de sa table, `try_connect_dhdvr` (`0x7c1330`). Le chemin décrit ci-dessous
n’est donc pas seulement une API générique sans implémentation sélectionnée.

## Négociation et protection du contrôle

`setup` (`0x7c1074`) envoie 32 octets nuls sauf `command=0xA9` à l’octet 0
et le sélecteur `100` à l’octet 9. Aucun identifiant n’est présent.
Seule la réponse moderne `0xA9`, résultat 0, chiffrement 2 et SHA 1 est utilisée
pour l’essai : les branches historiques sans protection sont refusées.

Le contrôle natif n’utilise pas l’enveloppe média QV. `CTcpSocket::EncryptData`
(`0x7f5498`) construit une enveloppe de 32 octets : temps Unix `u64` à 0,
longueur du corps chiffré `u32` à 8, longueur du paquet natif original `u32` à 12.
Le corps contient ce paquet, SHA-256 de l’en-tête externe clair et du paquet,
puis un remplissage nul jusqu’à un multiple de 16. L’en-tête et le corps sont
chiffrés séparément en AES-256-CBC, avec la clé ASCII de 32 octets du CGI et
un IV ASCII `0` répété 16 fois, réinitialisé pour chaque opération.

Ce SHA-256 n’est pas un MAC et ne prouve pas l’identité du pair ou une protection
contre le rejeu. Le CGI reste en HTTPS vérifié. Le mode TCP ne doit pas être
présenté comme possédant les garanties de TLS.

## Login natif exact

`build_login_packet` (`0x7c00dc`) produit un en-tête natif de 32 octets.
Sa longueur de paramètres est un `u32` little-endian à 4. Dans le profil IPv4
local, les champs sont : commande `0x0A`, type client à 1, indicateur 1 à 2,
`0x50` à 3, `0xFF` à 8, client OEM à partir de 9 par groupes de cinq octets,
4 à 24, 1 à 25, `0xA1` à 30 et `0xAA` à 31. Les paramètres sont
`username&&password`, sans zéro terminal pour un client OEM d’au plus 20 octets.

Le type client n’est pas la version négociée du protocole : il vient de
`CManager::SetClientType` (`0x67c150`). L’asset `CBBConfig.xml` de l’APK fixe
`ClientType=3`. `SdkManager.init` (`classes4.dex`, `0x1d4668`),
`QvOpenSDK.setKey` (`0x2b1248`) et `QvCore.initParams` (`0x282b5c`) transmettent
le client OEM à `QvJniApi.init`. `QVNETSDKIOControl(0)` (`0x489164`) appelle
`IQUIIManager::SetCustomID` (`0x535430`), puis `SDK_SetCustomer` (`0x687870`).
Le profil normal de l’APK est `A0000G0000C0000` après retrait des virgules.
Un choix utilisateur de service dans l’application peut le remplacer ; aucun
profil alternatif ne doit être essayé automatiquement.

`try_connect_dhdvr`, `0x7c2dc4–0x7c2df4`, effectue deux écritures protégées
consécutives du paquet complet : d’abord `0x0A`, puis le même paquet avec
seulement la commande remplacée par `0xA0`. L’attente de réponse vient ensuite.
`0xA0` appartient ici au login de contrôle ; aucune requête média PLAY n’est
construite. `DealSpecialPacket` (`0x7f3430`) dirige la réponse `0xB0` vers le
login. `parse_login_respond` (`0x7c0594`) considère l’octet 8 nul comme une
acceptation, sinon lit le motif numérique à 9. Le nombre de canaux est à 10.

## Abonnement sans vidéo

`IQUIIDevice::OnAlarmListenStart` (`0x4b0ae4`) appelle `SDK_StartListenEx`
(`0x67bad0`) sur le handle principal. `CAlarmDeal::StartListenEx` (`0x772608`)
ouvre la catégorie d’alarme 6 en mode 1. `CDvrDevice::open_channel` (`0x7ce664`)
réutilise le socket de contrôle : la création d’un autre socket ne concerne
que les catégories 3 et 11, pas la catégorie 6.

`sendAlarmQuery_dvr2` (`0x7e5b58`) envoie 35 en-têtes natifs de 32 octets :
commande `0x68`, action 2 à 8, sous-type à 12, mot `get_info(14)` à 28.
Les sous-types exacts sont `1..13`, `157`, `159`, `160`, `161`, `174..190`, `192`.
Le constructeur `CDvrDevice` (`0x7ca13c`) initialise le membre `0x2e48` à zéro.
`get_info(14)` lit ce membre ; seul `set_info(14)` (`0x7d438c`) le remplace.
Le login principal et le démarrage de cette écoute ne font pas ce remplacement.
Le mot est donc 0 dans cette nouvelle session ; aucune requête réseau
« query 14 » n’est nécessaire.

`CDvrAlarmChannel::OnRespond` (`0x7db2ac`) attend `0x69` en mode 1 et lit le
sous-type à 12. Le sous-type 5 avec octet 13 égal à 1 est normalisé en 254.
Les branches reconnues sont `1..8`, `10..13`, `157..190`, `192`, `251..255`.
Le callback reçoit le sous-type +100 et les paramètres après l’en-tête.
Ces seuls champs ne démontrent pas encore un numéro de platine ou un appui.
L’essai ne doit exporter aucun contenu arbitraire des paramètres.

## Maintien et fermeture

`SetKeepLifePacket` (`0x7d5e74`) installe un en-tête natif de 32 octets nuls
sauf `0xA1` à 0, avec intervalle 10 secondes. `TPTCPClient::Heartbeat`
(`0x7fde94`) convertit cet intervalle en millisecondes. L’essai peut maintenir
ce contrôle pendant son délai borné sans vidéo, micro ou commande physique.

`SDK_StopListen` (`0x67bb90`) ferme le canal d’alarme. La branche catégorie 6
de `removechannel` libère l’objet local, sans paquet de désabonnement identifié.
La fermeture du socket dédié à la fin, à l’arrêt volontaire, au déchargement
ou après erreur est donc indispensable. Aucun logout spéculatif n’est requis.

## Ce que le matériel doit encore confirmer

- Acceptation de ce login et des souscriptions par A331, sans flux vidéo.
- Nature des notifications reçues pendant chaque appui et éventuel canal cible.
- Absence d’interruption de l’appel des moniteurs et de conflit avec Philips.
- Retour normal de Philips après fermeture du socket de diagnostic.

Un test synthétique vérifie la construction, le chiffrement et le cycle de vie.
Il ne constitue pas une validation matérielle de sonnette. Aucun événement HA
de sonnerie ne doit être émis avant corrélation d’un message avec un appui réel.
