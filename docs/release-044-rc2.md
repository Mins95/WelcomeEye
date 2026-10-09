# v0.4.4-rc.2

- Chargement de la carte fiabilisé ; commandes de l'entrée 1 sous l'entrée 1, celles de l'entrée 2 sous l'entrée 2.
- Essai du micro secondaire activable dans Reconfigurer, sur Connect 3 et V1/Connect 2 R001. La destination physique reste à vérifier ; R002 exclu de cet essai.
- Photos manuelles Connect 3 et seconde entrée V1/R001 sur le direct déjà ouvert, avec une image et un dossier par source.
- Diagnostics des sonneries enrichis. L'écoute autonome et les photos automatiques sur sonnerie du Connect 3 restent non établies.

HACS → WelcomeEye → Afficher les préversions → **v0.4.4-rc.2**, puis redémarrer Home Assistant et recharger le dashboard. Conserver les entrées et identifiants.

Sur Connect 3, activer **Essai du microphone de la seconde platine** dans **Reconfigurer → Options avancées**. Tester vidéo, son, micro et photo de chaque entrée, puis la reprise dans Philips après fermeture. Pour les sorties secondaires déjà autorisées : un appui volontaire par cible, aucun nouvel essai si le résultat est ambigu.

Pour la sonnette Connect 3 : [observation croisée des deux platines](https://github.com/Mins95/WelcomeEye/blob/v0.4.4-rc.2/docs/connect3-ring-evidence.md#voluntary-cross-panel-check). Fournir les diagnostics après chaque essai, avant de relancer une observation. Les simulations logicielles ne remplacent pas cette validation physique.

Retour arrière : désactiver les essais micro/sorties secondaires, retélécharger **v0.4.4-rc.1** via HACS, redémarrer HA et recharger le dashboard. Ne pas supprimer l'entrée. La stable **v0.4.3** est conservée.
