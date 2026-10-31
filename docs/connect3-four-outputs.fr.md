# Connect 3 — noms, quatre sorties et sonnettes (0.4.5)

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
Chaque relais secondaire possède sa propre autorisation explicite.
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
   voisine ne réagit. Pas besoin de multiplier les diagnostics du canal 2 : les deux
   diagnostics précédents servent déjà à vérifier leur isolation.
5. Fermez le lecteur HA et vérifiez la reprise dans Philips Door Connect.

## Sonnettes, microphone et photos

Utilisez les [notifications cloud](qv-cloud-doorbell.md) pour les sonneries Connect 3. Leur réception est confirmée. Chaque entrée configurée dispose de son capteur et de son canal d’événement. L’écoute locale reste un diagnostic séparé, pas un détecteur de sonnerie en veille.

Le microphone secondaire possède sa propre activation ; vérifiez la voix sur la platine choisie. **Photo** utilise le direct déjà ouvert. La capture automatique est facultative et peut interrompre la sonnerie du moniteur. [Photos et conservation](captures.md).

Installez **0.4.5 stable** dans HACS, redémarrez HA et rechargez l’interface. Conservez les entrées et leurs identifiants. [Version et retour arrière](release-045.md).
