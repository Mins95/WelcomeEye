# Sonnettes cloud — Connect 3 et Connect 2 R002

RC5 ajoute un abonnement propre à Home Assistant aux notifications de
**Philips Door Connect pour Connect 3**, et de **WelcomeEye pour R002**.
La vidéo, le son, le microphone et les ouvertures restent locaux.
Une notification ne lance ni vidéo ni capture photo.

## Configuration

1. Installer RC5 avec HACS et redémarrer Home Assistant. Conserver les entrées.
2. Ouvrir **Reconfigurer → Notifications de sonnette cloud → Configurer**.
3. Saisir le compte de l’application et autoriser son utilisation par HA.
   Il ne s’agit ni du mot de passe local du moniteur ni du code d’ouverture.
4. Choisir le visiophone parmi les appareils autorisés du compte et confirmer
   qu’il correspond à cette entrée HA.
5. Pour deux platines, conserver **Deuxième platine extérieure** activé.

L’ajout par QR local seul ne fournit pas un compte cloud. Sans compte contenant
l’appareil, cette méthode ne peut pas encore être utilisée. Aucun identifiant
n’est deviné et aucune association de l’application n’est remplacée.

## Essai après installation

Fermer les vidéos HA et Philips. Sonner une fois sur l’entrée 1, laisser finir
son cycle, puis essayer séparément l’entrée 2. Vérifier les capteurs de sonnette,
les moniteurs et les notifications du téléphone. Télécharger les diagnostics
HA après l’essai, en indiquant l’ordre des appuis.

Les événements utilisent `welcomeeye_local.ring`, avec `source: cloud_qv` et
`channel: 1` ou `channel: 2`. Les doublons, messages anciens et notifications
d’autres appareils sont ignorés. Aucun message de maintien de connexion local
n’est traité comme une sonnerie.

**Statut RC5 :** parcours APK implémenté et tests logiciels avec services simulés ;
réception réelle, attribution des platines et coexistence avec le téléphone à
confirmer sur chaque installation. Une inscription acceptée par le serveur ne
prouve pas encore la livraison d’une sonnerie.

Pour arrêter : **Reconfigurer → Notifications de sonnette cloud → Désactiver**.
HA désabonne uniquement sa propre inscription. Si le fournisseur est inaccessible,
le nettoyage reste signalé comme en attente ; recharger l’entrée pour le reprendre.
Les fonctions locales restent utilisables sans cloud. Après trois échecs de
connexion, le diagnostic demande de recharger l’entrée plutôt que de réessayer
indéfiniment.

## Confidentialité et transport

Compte, appareil sélectionné et inscription FCM sont conservés dans la configuration
privée HA. Ils ne figurent pas dans les attributs des entités ni dans les diagnostics.
L’abonnement est distinct de celui du téléphone. Seuls les appels de la platine
autorisée sont pris en compte ; les appels inter-moniteurs ne deviennent pas des
sonnettes extérieures.

Le point de découverte `sh2.qvcloud.net:443` utilise l’autorité privée publique
extraite de l’APK, avec vérification de chaîne et de validité. Son certificat
n’annonce pas de nom DNS : cette confiance authentifie l’autorité du fournisseur,
pas une correspondance DNS. Cette politique reste limitée à ce point précis.
Les services de compte et de notifications utilisent la vérification HTTPS
standard, nom DNS compris. Aucun mot de passe n’est envoyé à la découverte.

# English

RC5 adds an independent Home Assistant notification registration for **Door
Connect accounts on Connect 3** and **WelcomeEye accounts on R002**. Local media
and controls are unchanged. Incoming calls never start video or take snapshots.

Open **Reconfigure → Cloud doorbell notifications → Configure**, enter the app
account, approve cloud access, then select and confirm the matching account device.
Keep **Second outdoor panel** enabled when applicable. An app account containing
the device is required; a local QR/password alone is insufficient.

Close HA and Philips video players, press entrance 1 once, let its cycle finish,
then test entrance 2 separately. Check HA doorbell sensors, indoor monitors and
phone notifications. Download diagnostics and report the order of the presses.
Real delivery, channel attribution and phone coexistence still require this test.
Server subscription acceptance alone is not hardware validation.

Automation event: `welcomeeye_local.ring`, `source: cloud_qv`, `channel: 1` or `2`.
Disable through Reconfigure to remove only HA's subscription. Pending cleanup
after a provider failure is retried when the entry is reloaded. Rollback to RC4:
disable cloud first, redownload RC4 through HACS, restart HA and retain the entry.

The credential-free directory uses the APK's dedicated private vendor CA, with
chain/date checks and a fixed authority, without DNS-name authentication because
its certificate has no DNS name. Account and push APIs retain ordinary HTTPS
certificate and hostname verification. No trust policy is changed globally.
