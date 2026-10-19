# Bot Discord → Google Sheets

Bot Python qui écoute **un seul salon Discord**, analyse les nouveaux messages au format `temps tâche date facultative`, puis ajoute l'activité à un Google Sheet existant.

## Format des messages accepté

Exemples :
- `1h30 Développement de la connexion`
- `45min Correction du bug de sauvegarde`
- `2h Rédaction du rapport 18/10/2026`
- `01:30 Revue de code 18-10-2026`

Règles :
- Le message commence par une durée positive, suivie d'un espace puis de la tâche : `1h30 Développement de la connexion`, `45min Correction du bug`, `2h Rédaction du rapport 18/10/2026`.
- Tout le texte après la durée constitue la tâche, sauf si le message se termine par une date `JJ/MM/AAAA` ou `JJ-MM-AAAA`, qui est alors interprétée comme la date de l'activité.
- Si aucune date n'est donnée, la date d'activité est celle du jour où le bot analyse le message.
- Les messages qui ne respectent pas le format sont ignorés et signalés dans les logs.
- Les messages des bots sont ignorés.
- Les messages envoyés avant le démarrage ne sont pas importés.

## Pré-requis

- Python 3.11+ (ou Docker).
- Un bot créé dans le [Discord Developer Portal](https://discord.com/developers/applications).
- Un compte de service Google avec l'API Google Sheets activée.
- Le Google Sheet existant partagé avec l'adresse e-mail du compte de service.

## 1. Configurer Discord

1. Créez une application puis un bot dans le Discord Developer Portal.
2. Dans **Bot**, activez **Message Content Intent**.
3. Invitez le bot sur votre serveur avec les permissions minimales : **View Channels** et **Read Message History**. Pour les nouveaux messages, il doit surtout pouvoir voir le salon et lire son contenu.
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

Copiez `.env.example` en `.env`, puis remplissez :
- `DISCORD_TOKEN`
- `DISCORD_CHANNEL_ID`
- `GOOGLE_SPREADSHEET_ID`
- `GOOGLE_WORKSHEET_NAME`
- les colonnes `SHEET_COL_*` pour correspondre à votre tableur.

Exemple : si la date est en colonne B, le membre en A, la durée en C et la tâche en F, utilisez `SHEET_COL_DATE=B`, `SHEET_COL_MEMBER=A`, `SHEET_COL_DURATION=C`, `SHEET_COL_TASK=F`.

`GOOGLE_DATA_START_ROW` doit être la première ligne où des activités peuvent être écrites, pas la ligne d'en-tête.

### Préserver le formatage existant

- Le bot **n'efface pas** la feuille et n'en recrée pas une nouvelle.
- Il écrit seulement dans les cellules des colonnes configurées.
- Laissez `GOOGLE_TEMPLATE_ROW=0` si les lignes de destination sont déjà formatées.
- Si vous avez une ligne vide qui possède exactement le format souhaité, indiquez son numéro dans `GOOGLE_TEMPLATE_ROW`. Le bot copiera uniquement son formatage vers chaque nouvelle ligne, sans copier ses valeurs ou ses formules.
- Faites un essai sur une copie du tableur avant la première utilisation. Vérifiez aussi que la zone d'écriture ne chevauche pas des formules ou d'autres tableaux.

## 4. Lancer en local pour tester

Windows PowerShell :

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
# Éditez .env et ajoutez service-account.json
python bot.py
```

Envoyez un message de test dans le salon surveillé, par exemple `30min Test d'intégration`. Vérifiez que la ligne apparaît dans le bon onglet et les bonnes colonnes.

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

## Notes techniques

- L'ID du message est disponible pour audit, mais l'implémentation n'enregistre pas les anciens messages.
- Le bot ne tente pas de réécrire un message corrigé ou supprimé après son enregistrement.
- La sélection de ligne cherche la première ligne vide à partir de `GOOGLE_DATA_START_ROW`. Réservez donc une zone dédiée aux activités et évitez les trous dans cette zone.
- Si les messages doivent être édités ultérieurement, ou si le réseau échoue après réception, une version avec stockage SQLite et reprise idempotente serait préférable.
- Le champ membre est le nom d'affichage au moment du traitement ; l'ID Discord peut être stocké dans une colonne technique facultative.


## Configuration adaptée au tableau « Heures individuelles »

Le bot est configuré par défaut pour l'onglet `Copie de HeuresIndividuelles` et les blocs visibles sur la capture :

- Thomas : date en B, heures en C, description en D.
- Lucas : date en F, heures en G, description en H.
- Nathan : date en J, heures en K, description en L.
- Première ligne de données : ligne 4.

Les heures sont écrites comme un nombre décimal pour fonctionner avec le récapitulatif : `1h30` devient `1.5`, `45min` devient `0.75`, `2h` devient `2`.

Dans `.env`, adaptez `MEMBER_THOMAS`, `MEMBER_LUCAS` et `MEMBER_NATHAN` aux noms d'affichage réels des auteurs Discord. La comparaison ignore les majuscules/minuscules, mais le nom doit sinon correspondre. Les messages d'un membre non configuré sont refusés et une erreur apparaît dans les logs.

Le bot écrit uniquement dans les trois cellules du bloc concerné. Il ne touche pas au récapitulatif, au graphique ni aux autres onglets. Faites un essai sur une copie du tableur avant la mise en production.
