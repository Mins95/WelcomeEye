# 0.4.4-beta.8 — Test du canal 2

1. Mettre à jour via HACS, puis redémarrer Home Assistant.
2. Fermer les vidéos HA et Philips. Dans **Paramètres → Appareils et services → WelcomeEye**, ouvrir la fiche de l'appareil dont la vidéo fonctionne déjà.
3. Cliquer sur la nouvelle caméra **Test caméra canal 2**, présente avec la vidéo habituelle, puis lancer le direct.
4. Indiquer si elle affiche la première platine, la seconde ou une erreur. Fermer le lecteur, vérifier la reprise dans Philips et envoyer les diagnostics de cette entrée.

Vidéo seule, fermeture automatique après 60 secondes maximum. Le canal 2 n'est pas encore identifié matériellement. Aucun changement d'adresse, de mot de passe ou de certificat n'est nécessaire. Le diagnostic est dans `connect3.channel2_trial`.

Retour arrière : réinstaller **v0.4.4-beta.7** depuis HACS et redémarrer HA ; conserver les entrées existantes.
