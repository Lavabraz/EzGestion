# Bot Discord → Google Sheets

Bot Python qui écoute **un seul salon Discord** et enregistre les activités saisies avec la commande slash `/activite`. La tâche est choisie dans les suggestions provenant de l'onglet WBS, puis les heures sont cumulées sur la ligne exacte sélectionnée.

## Enregistrer une activité

Dans le salon Discord configuré, lancez `/activite` et renseignez :

- `duree` : par exemple `1h30`, `45min` ou `01:30`.
- `tache` : sélectionnez une tâche dans les suggestions; saisissez quelques lettres pour filtrer la liste.
- `date` : facultative. Formats acceptés : `JJ/MM/AAAA`, `JJ-MM-AAAA`, `JJ.MM.AAAA`, `AAAA-MM-JJ`, `JJ/MM` (année courante), `aujourd'hui` ou `hier`. Si elle est omise, la date du jour est utilisée.

Une date peut aussi être placée à la fin du champ `duree`, par exemple `1h30 Analyse 2026-10-21` ou `1h30 Analyse hier`. Le bot confirme dans le salon l'enregistrement et affiche la date au format français `JJ/MM/AAAA`. Il met à jour l'onglet individuel ainsi que le total de la ligne WBS sélectionnée. Les erreurs et les saisies invalides sont également signalées publiquement dans le salon.

## Enregistrement automatique des réunions

Chaque jour à 23 h 55 (heure de Paris), le bot ajoute les activités récurrentes pour chaque membre configuré :

- 35 min de « Réunion Hebdo » chaque lundi.
- 45 min de « Réunion Client » chaque lundi (la cadence hebdomadaire a commencé le 19/10/2026).
- 30 min de « Réunion Gp » un lundi sur deux, à partir du 02/11/2026.
- 5 min de « Réunion Daily » les jours ouvrés, sauf le lundi.

Les réunions du lundi sont enregistrées même si ce lundi est férié ou pendant des vacances. Les daily sont exclues les lundis, week-ends, jours fériés français et périodes définies par `GANTT_VACATION_RANGES`. Les dates manquées lorsque le bot est arrêté ne sont pas rattrapées. Chaque activité est écrite dans le bloc individuel du membre et ses heures sont ajoutées à la colonne K de la tâche WBS correspondante. Un message de confirmation est envoyé dans le salon pour chaque activité effectivement ajoutée. Les quatre tâches doivent donc déjà exister dans le WBS sous les noms « Réunion Hebdo », « Réunion Client », « Réunion Gp » et « Réunion Daily ».

## Générer un Gantt

Dans le salon configuré, utilisez `/gantt` avec le mode `Prévu` ou `Réel`. Les dates sont facultatives :

- `debut` : facultatif, au format `JJ/MM/AAAA`.
- `fin` : facultative, au format `JJ/MM/AAAA`.
- Vous pouvez préciser les deux dates, une seule, ou aucune. En mode prévu, une borne omise est déduite des dates minimales/maximales du WBS (« Commencer avant » / « Finir avant »). En mode réel, elle est déduite du premier/dernier enregistrement d'heures.
- Si aucune donnée ne permet de calculer une borne omise, le bot demande de la fournir. La période maximale est de 366 jours.

La commande `/frise` propose les modes `Prévu` et `Réel`, ainsi que les options facultatives `sprint` et `personne`. Le sprint est sélectionné depuis `SprintManagement` ; ses dates bornent la frise par défaut, ses tâches sont filtrées par ID WBS et ses jalons sont affichés sur la chronologie. Le filtre `personne` limite les tâches prévisionnelles aux tâches affectées à ce membre et, en mode réel, aux heures enregistrées dans son bloc de l'onglet `Heures`. Les antécédents restent chargés depuis l'ensemble du WBS pour ne pas casser le calcul des dépendances entre sprints. La frise prévisionnelle positionne les tâches WBS sur une flèche selon leur début prévu, avec l'effort estimé et le nombre de jours ouvrés. La frise réelle place les repères selon les jours où des heures ont été saisies; chaque tâche indique son total réel et sa période d'activité. Les paramètres facultatifs `debut` et `fin` sont déduits des dates du WBS en mode prévu, ou des enregistrements d'heures en mode réel.

Le mode prévu lit l'ID (A), les dépendances (B, un ou plusieurs ID séparés par des virgules), l'intitulé (D), l'estimation avec marge (G), les personnes affectées (H) et les dates limites de début/fin (I/J). Il génère un calendrier au plus tard, en jours ouvrés, et n'affiche que les tâches dont le calendrier chevauche la période demandée. Le chemin critique mis en évidence est le plus long chemin du graphe de dépendances, calculé à partir des durées estimées; les dates I/J placent les tâches, mais ne changent pas ce calcul.

Le Gantt affiche la durée estimée à côté de chaque tâche, trie les lignes par début prévisionnel et utilise des flèches coudées pour rendre les dépendances plus lisibles. La frise `/frise` présente une flèche chronologique dédiée, avec les tâches principales réparties autour de celle-ci. Les intitulés très longs sont raccourcis dans l’image pour éviter qu’ils se chevauchent.

Le mode réel représente les heures datées de l'onglet individuel dans la période choisie et affiche les tâches ayant une activité réelle à cette période. Les activités sont associées à une tâche par son intitulé exact; si plusieurs tâches ont le même intitulé, les heures correspondantes sont signalées comme non attribuées. Dans ce mode, le chemin critique affiché est celui du planning prévisionnel, pas un chemin critique recalculé à partir des heures historiques. Si les données prévisionnelles sont invalides, les heures réelles restent affichées et le chemin critique est signalé comme indisponible.

Le calendrier exclut les week-ends, les jours fériés nationaux de France métropolitaine et, par défaut, les vacances du 26/10/2026 au 01/11/2026 inclus. Les paramètres facultatifs sont :

- `GANTT_HOURS_PER_PERSON_PER_DAY` : capacité journalière par personne (défaut : `7`).
- `GANTT_VACATION_RANGES` : périodes de congés inclusives au format `AAAA-MM-JJ:AAAA-MM-JJ`, séparées par des virgules.

La durée estimée est convertie en jours ouvrés avec `Estimation avec marge / (capacité par personne × nombre de personnes affectées)`, arrondie au jour supérieur. Une estimation vide ou nulle est affichée comme jalon. Le calcul ne nivelle pas les conflits lorsqu'une personne est affectée à plusieurs tâches en même temps; le diagramme le signale. En mode prévu, les références de dépendance invalides, les cycles et les erreurs de données entraînent un message d'erreur plutôt qu'un planning trompeur.

## Pré-requis

- Python 3.11+ (ou Docker).
- Un bot créé dans le [Discord Developer Portal](https://discord.com/developers/applications).
- Un compte de service Google avec l'API Google Sheets activée.
- Le Google Sheet existant partagé avec l'adresse e-mail du compte de service.

## 1. Configurer Discord

1. Créez une application puis un bot dans le Discord Developer Portal.
2. Aucune activation de **Message Content Intent** n'est nécessaire : les activités sont saisies avec une commande slash.
3. Invitez le bot sur votre serveur avec les scopes **bot** et **applications.commands**, et les permissions nécessaires pour voir le salon et utiliser les commandes.
4. Copiez le token du bot. Ne le partagez jamais.
5. Activez le mode développeur Discord, faites clic droit sur le salon cible et copiez son identifiant.

## 2. Configurer Google Sheets

1. Dans Google Cloud Console, créez un projet ou choisissez-en un.
2. Activez **Google Sheets API**.
3. Créez un **compte de service**, puis générez une clé JSON.
4. Téléchargez le JSON et placez-le dans le dossier du projet sous le nom `service-account.json`.
5. Ouvrez ce JSON et copiez son adresse `client_email`.
6. Partagez le Google Sheet existant avec cette adresse, avec le rôle **Éditeur**.
7. Récupérez l'identifiant du tableur dans son URL : `https://docs.google.com/spreadsheets/d/IDENTIFIANT/edit`.
8. Notez le nom exact de l'onglet (la feuille en bas de Google Sheets).

## 3. Configurer le projet

Créez un fichier `.env`, puis renseignez :
- `DISCORD_TOKEN`
- `DISCORD_CHANNEL_ID`
- `GOOGLE_SPREADSHEET_ID`
- `GOOGLE_WORKSHEET_NAME`
- `GOOGLE_DATA_START_ROW` (première ligne disponible pour les activités, défaut : `4`)
- `GOOGLE_SERVICE_ACCOUNT_FILE` si le fichier de credentials n'est pas `service-account.json`
- `MEMBER_THOMAS`, `MEMBER_LUCAS` et `MEMBER_NATHAN` si les noms d'affichage Discord diffèrent des valeurs par défaut.

Les colonnes de saisie individuelle sont configurées dans le code : date/heures/tâche en B-D, F-H et J-L pour Thomas, Lucas et Nathan.

### Préserver le formatage existant

- Le bot **n'efface pas** la feuille et n'en recrée pas une nouvelle.
- Il écrit uniquement dans le bloc du membre concerné, puis dans la cellule K de la tâche WBS sélectionnée.
- Faites un essai sur une copie du tableur avant la première utilisation. Vérifiez aussi que la zone d'écriture ne chevauche pas des formules ou d'autres tableaux.

## 4. Lancer en local pour tester

Windows PowerShell :

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
# Créez .env et placez service-account.json dans ce dossier
python bot.py
```

Dans le salon surveillé, utilisez `/activite`, choisissez une durée et sélectionnez une tâche dans la liste proposée. Vérifiez que la ligne apparaît dans le bon bloc membre et que le total de la même tâche augmente dans WBS. Testez aussi `/gantt` en modes prévu et réel. Au démarrage, les commandes slash sont synchronisées dans le serveur du salon configuré.

## 5. Déployer en continu avec Docker

Sur un serveur disposant de Docker, transférez le projet, créez `.env` et placez `service-account.json` à côté. Puis :

```bash
docker build -t discord-activity-bot .
docker run -d \
  --name discord-activity-bot \
  --restart unless-stopped \
  --env-file .env \
  -v "$PWD/service-account.json:/app/service-account.json:ro" \
  discord-activity-bot
```

Pour consulter les logs :

```bash
docker logs -f discord-activity-bot
```

Ne publiez jamais `.env`, le token Discord ou la clé JSON dans Git, un dépôt public, ou un message Discord.

### Mettre à jour le bot sur le serveur

Dans le dossier du dépôt sur le serveur, donnez les droits d'exécution au script puis lancez-le :

```bash
chmod +x update-bot.sh
./update-bot.sh
```

Le script ne fait aucune opération Git : il construit une image Docker depuis les fichiers déjà présents sur le serveur, remplace le conteneur en conservant les fichiers `.env` et `service-account.json` du dossier, puis vérifie que le bot reste démarré. Si le nouveau conteneur échoue à démarrer, il restaure l'ancien. L'ancien conteneur est conservé sous `discord-activity-bot-previous` jusqu'à la prochaine mise à jour réussie. Transférez ou mettez à jour le code sur le serveur avant de lancer le script.

La connexion à Google Sheets est initialisée en arrière-plan après le démarrage Discord. Si les logs indiquent une erreur réseau vers Google, les commandes Discord restent disponibles, mais les opérations qui lisent ou écrivent dans le tableur échouent tant que l'accès réseau n'est pas rétabli.

## Notes techniques

- Les tâches de la colonne D de WBS sont rechargées au démarrage puis toutes les cinq minutes pour alimenter l'autocomplétion. Le bot vérifie que la ligne sélectionnée n'a pas changé avant d'enregistrer.
- La sélection de ligne cherche la première ligne vide à partir de `GOOGLE_DATA_START_ROW`. Réservez donc une zone dédiée aux activités et évitez les trous dans cette zone.


## Configuration adaptée au tableau « Heures individuelles »

Le bot est configuré par défaut pour l'onglet `Heures` et les blocs individuels ci-dessous :

- Thomas : date en B, heures en C, description en D.
- Lucas : date en F, heures en G, description en H.
- Nathan : date en J, heures en K, description en L.
- Première ligne de données : ligne 4.

Les heures sont écrites comme un nombre décimal pour fonctionner avec le récapitulatif : `1h30` devient `1.5`, `45min` devient `0.75`, `2h` devient `2`.

Dans `.env`, adaptez `MEMBER_THOMAS`, `MEMBER_LUCAS` et `MEMBER_NATHAN` aux noms d'affichage réels des auteurs Discord. La comparaison ignore les majuscules/minuscules, mais le nom doit sinon correspondre. Les messages d'un membre non configuré sont refusés et une erreur apparaît dans les logs.

Dans l'onglet individuel, le bot écrit uniquement dans les trois cellules du bloc du membre concerné. Il ajoute aussi les heures dans WBS. Il ne touche pas au récapitulatif ni au graphique. Faites un essai sur une copie du tableur avant la mise en production.

### Ajout des heures dans WBS

La commande propose les tâches de la colonne D de l'onglet `WBS` à partir de la ligne 2. Après sélection, la durée est ajoutée au total de la colonne K sur cette même ligne. La valeur déjà présente en K doit être numérique ou vide.

Les paramètres facultatifs sont `GOOGLE_WBS_WORKSHEET_NAME` (défaut : `WBS`) et `GOOGLE_WBS_DATA_START_ROW` (défaut : `2`). Vérifiez la structure et faites un essai sur une copie du tableur avant la mise en production.
