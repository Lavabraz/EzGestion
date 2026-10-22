# Bot Discord → Google Sheets

Bot Python qui écoute **un seul salon Discord** et enregistre les activités saisies avec la commande slash `/activite`. La tâche est choisie dans les suggestions provenant de l'onglet WBS, puis les heures sont cumulées sur la ligne exacte sélectionnée.

## Participation dans le WBS

La colonne dont l'en-tête est `Participation` dans la feuille `WBS` est alimentée automatiquement à partir des activités de la feuille `Heures`. À chaque utilisation de `/activite`, le nom du membre est ajouté à la tâche WBS sans doublon. Au démarrage du bot, la colonne est reconstruite à partir de l'historique déjà présent dans les blocs individuels de `Heures`.

Pour l'historique, les noms de tâches doivent être uniques dans le WBS : si plusieurs lignes portent le même nom, le bot ignore cette tâche lors de la reconstruction et le signale dans les logs, car la feuille `Heures` ne contient pas l'identifiant de ligne WBS.

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

Dans le salon surveillé, utilisez `/activite`, choisissez une durée et sélectionnez une tâche dans la liste proposée. Vérifiez que la ligne apparaît dans le bon bloc membre et que le total de la même tâche augmente dans WBS. Au démarrage, les commandes slash sont synchronisées dans le serveur du salon configuré.

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


## Générer une frise chronologique avec Discord

La commande `/frise` génère une frise prévisionnelle ou réelle à partir des données du WBS et de la feuille `Heures`. Elle peut être filtrée par sprint et par personne.

Paramètres disponibles :
- `mode` : `Prévu` ou `Réel`.
- `sprint` : sprint provenant de l'onglet `SprintManagement` (facultatif).
- `personne` : membre à afficher (facultatif).
- `debut` et `fin` : dates facultatives au format `JJ/MM/AAAA`.

Sans dates, la période est déduite des données ou du sprint sélectionné. La commande doit être utilisée dans le salon Discord configuré. La commande `/gantt` n'est pas conservée.
