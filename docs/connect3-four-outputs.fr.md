# Connect 3 — noms, quatre sorties et sonnettes (0.4.4)

[English](connect3-four-outputs.md) · [Installation Connect 3](connect3-auto-tls.md)

## La carte

Conservez votre entrée et votre carte WelcomeEye. Dans **Modifier la carte**,
renommez indépendamment les entrées, les portillons et les portails. Les noms
sont limités à 64 caractères et conservés dans la configuration du dashboard.
Ils ne changent ni les identifiants HA ni la destination des commandes.

Les réglages de la seconde entrée sont masqués sans l’option **Seconde platine
extérieure**. La carte actualise désormais ses canaux après un changement
d’option, sans devoir ouvrir une vidéo. Après une mise à jour du JavaScript,
rechargez néanmoins complètement la page ou l’application Companion.

La configuration YAML reste compatible (remplacer l’entité par la vôtre) :

```yaml
type: custom:welcomeeye-card
entity: camera.votre_welcomeeye
name: WelcomeEye
channel_1_name: Entrée principale
channel_2_name: Entrée secondaire
strike_1_name: Portillon maison
gate_1_name: Grand portail
strike_2_name: Portillon bureau
gate_2_name: Portail garage
```

## Quatre cibles séparées

| Commande | Canal | Relais | État |
| --- | --- | --- | --- |
| Portillon 1 | 1 | 1 | Chemin déjà confirmé sur A331 |
| Portail 1 | 1 | 2 | Chemin déjà confirmé sur A331 |
| Portillon 2 | 2 | 1 | Autorisation séparée ; cible à vérifier |
| Portail 2 | 2 | 2 | Autorisation séparée ; cible à vérifier |

Les deux portails ne sont pas fusionnés. Chaque commande sélectionnée conserve
son canal et son relais sous les deux vues caméra. L’intégration utilise le
lecteur de la platine concernée.
Le comportement physique des deux relais du canal 2 reste à confirmer.
Les deux commandes utilisent le **code d’ouverture Philips déjà configuré**
pour cet équipement, distinct du mot de passe local de connexion.

Dans **Reconfigurer → Commandes d’ouverture**, sélectionnez les commandes
souhaitées puis confirmez chaque cible secondaire. Les autorisations existantes
restent dans **Options avancées**. Ces réglages sont proposés lorsque la seconde
platine est activée ; vidéo, commandes d’ouverture et code doivent être
configurés. En TCP, l’accord existant pour ses contrôles reste nécessaire.
Enregistrer rend le bouton disponible sans envoyer de commande. Les sorties
secondaires conservent une indication **à vérifier** ; un accusé de réception
natif ne valide pas l’ouverture réelle.

Chaque clic fait au maximum une tentative. Il n’y a aucun rejeu après échec ou
timeout. Une session au résultat incertain refuse les commandes suivantes.
Lors d’une commande visant l’autre entrée, la carte ferme d’abord son lecteur
et son micro. Elle ne ferme pas de force les lecteurs d’autres utilisateurs :
si l’appareil est encore occupé, l’action échoue sans commander un autre relais.
Les boutons ne représentent pas la position ouverte/fermée du portail.

## Essais à effectuer séparément

1. Vérifiez les six noms, la bascule 1 → 2 → 1 et le son de chaque platine.
2. Activez **Portillon 2**, confirmez **canal 2, relais 1**, puis cliquez
   **une fois**. Vérifiez sur place que seul ce portillon réagit, sans aucun
   autre relais, puis téléchargez les diagnostics.
3. Faites séparément la même chose avec **Portail 2**, **canal 2,
   relais 2**. Téléchargez de nouveaux diagnostics. Ne répétez pas un résultat
   ambigu ; signalez-le avec le nom de la cible.
4. Pour les commandes habituelles du canal 1, vérifiez aussi qu’aucune sortie
   voisine ne réagit. Pas besoin de multiplier les essais du canal 2 : les deux
   essais précédents servent déjà à vérifier leur isolation.
5. Fermez le lecteur HA et vérifiez la reprise dans Philips Door Connect.

## Sonnettes et observation locale facultative

Les [notifications cloud](qv-cloud-doorbell.md) se configurent séparément avec
le compte Philips Door Connect. La réception réelle et l’attribution des platines
restent à confirmer sur place. Elles ne lancent ni vidéo ni photo.

Le diagnostic ci-dessous observe les notifications de **la vidéo déjà ouverte**,
sur le canal choisi. La détection locale autonome reste non confirmée.
Le canal indiqué dans le diagnostic est celui du lecteur observé, pas une
identification confirmée de la platine ayant sonné.

Ouvrez l’entrée 1, puis lancez une fois cette action dans **Outils de développement
→ Actions** avec votre capteur Connect 3 :

```yaml
action: welcomeeye_local.connect3_observe_doorbell
target:
  entity_id: sensor.votre_capteur_connect3
data:
  operation: start
  channel: 1
  duration: 120
```

Sonnez une fois sur la platine 1. Aussitôt après, relancez cette action avec
`operation: mark`, puis `operation: status` pour lire le résultat. Un marqueur
signale votre appui déclaré ; ce n’est pas une détection automatique.
Téléchargez les diagnostics avant un nouvel essai. L’observation s’arrête à la
fermeture de la vidéo, après 120 secondes, ou avec `operation: stop`.

Refaites séparément l’essai avec la vidéo de l’entrée 2 et `channel: 2` dans
les actions. Indiquez aussi si la sonnerie du moniteur s’est interrompue lors de
l’ouverture du direct et si la notification Philips a été reçue. Aucun texte
de notification ni octet brut n’est exporté. Aucun événement de sonnette HA
ou photo automatique n’est créé à partir de ces candidats.

Le micro du canal 2 possède une option d’essai séparée, désactivée par défaut ;
sa destination physique reste à confirmer. L’écoute d’alarme native a reçu des
messages de maintien de connexion sans événement de sonnette prouvé.
L’[analyse SDK historique](connect3-rc1-sdk-evidence.md) conserve les observations RC1.

Le bouton **Photo** utilise le direct déjà ouvert. Les administrateurs peuvent
consulter et supprimer les captures dans **Photos WelcomeEye** ; voir
[photos et durée de conservation](captures.md).

## Mise à jour et retour arrière

Dans HACS, installer la stable **0.4.4**, redémarrer HA puis
recharger la page. Conserver les entrées, mots de passe et approbations TLS.
Pour revenir à une version antérieure, désactiver d’abord les notifications
cloud et suivre les [instructions de retour arrière](release-044.md#upgrade-and-rollback).
Ne supprimer aucune entrée ; une ancienne version peut ne pas prendre en charge
la connexion TCP sélectionnée.
