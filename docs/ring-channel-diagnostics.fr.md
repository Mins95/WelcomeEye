# Relever le canal des sonneries R001 et V1

Les événements `welcomeeye_local.ring` conservent déjà leur canal. Le diagnostic
`doorbell.observed_protocol_channels` permet maintenant de comparer les
sonneries reçues depuis le chargement de l'intégration. Il ne modifie ni
l'écoute, ni les événements, ni les photos et ne crée aucune nouvelle entité.

- `legacy_local` : canal brut du message local `reportAlarm` du Connect 2 R001.
  Le canal publié dans l'événement conserve exactement cette valeur.
- `cloud_lt` : canal brut de la notification cloud LT du V1. L'événement HA
  ajoute 1, comme l'application : brut 0 → événement 1, brut 1 → événement 2.
  Cette conversion n'est pas appliquée aux alarmes locales du R001.

`raw_channel_counts` contient les compteurs par source et valeur brute.
`last_source`, `last_raw_channel` et `last_event_channel` décrivent la dernière
sonnerie acceptée. `physical_mapping_verified` reste `false` : une valeur du
protocole ne prouve pas à elle seule quelle platine physique a sonné. Les
numéros vidéo 16/17 ne suffisent pas à établir cette correspondance.

Pour relever les valeurs sur votre installation :

1. Téléchargez les diagnostics avant l'essai, sans ouvrir de vidéo.
2. Appuyez une fois sur la première platine physique, puis téléchargez les
   diagnostics et notez manuellement « platine 1 ». Relevez la source, les deux
   champs `last_*_channel` et le compteur qui a augmenté.
3. Laissez le capteur Sonnette revenir au repos, puis répétez sur la seconde
   platine et notez « platine 2 ». Vous pouvez aussi écouter
   `welcomeeye_local.ring` dans les outils de développement HA.

Les valeurs identiques ne permettent pas de distinguer les platines avec ce
champ. Une absence de sonnerie acceptée laisse les compteurs inchangés ; le
diagnostic ne déduit rien de l'image, d'une connexion ou d'un appui déclaré.
Le V1 conserve son inscription cloud existante et facultative ; ce relevé
n'active pas de cloud sur le R001.

Les compteurs repartent à zéro au rechargement de l'intégration. Ils sont
bornés à 256 canaux par source et à 2 147 483 647 par compteur. Aucun identifiant,
horodatage du fabricant, contenu de notification, image ou son n'est conservé
dans ce relevé. Les captures suivent leur fonctionnement et leur réglage
existants, indépendamment de ces métadonnées.
