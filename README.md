# Docker Kalender-Sync mit Web-UI (Multi-User)

Architektur:
- **Authentifizierung:** "Web Application" OAuth 2.0 Flow. Nutzer loggen sich auf Ihrer Domain ein.
- **Web-UI (Port 8000):** Für Login, Setup & Konfiguration (schreibt `/app/data/<user-id>.json`).
- **Backend (Cron):** Stündlicher Sync-Job im Container, der **alle** konfigurierten User-Dateien verarbeitet.
- **Persistence:** Das `/app/data` Verzeichnis (als Volume gemountet) enthält die verschlüsselten Konfigurationsdateien.

## Setup-Anleitung

### Schritt 1: Google Cloud Projekt (Webanwendung)

1.  Gehen Sie zur [Google Cloud Console](https://console.cloud.google.com/).
2.  Erstellen Sie ein neues Projekt und **aktivieren Sie die Google Calendar API**.
3.  Gehen Sie zu "APIs & Dienste" -> "Anmeldedaten".
4.  Klicken Sie auf "Anmeldedaten erstellen" -> "OAuth-Client-ID".
5.  Wählen Sie als Anwendungstyp **"Webanwendung"** (NICHT Desktop-App).
6.  Geben Sie einen Namen ein (z. B. "DHBW Cleaner").
7.  **WICHTIG (Autorisierte Weiterleitungs-URIs):**
    Fügen Sie die *exakte* Callback-URL Ihrer Anwendung hinzu:
    `https://dhbw-kalender-cleaner.ptb.ltm-labs.de/authorize`
8.  Klicken Sie auf "Erstellen". Sie erhalten eine **Client-ID** und einen **Client-Geheimschlüssel**. Kopieren Sie diese. **Speichern Sie keine `credentials.json`-Datei.**
9.  Gehen Sie zum "OAuth-Zustimmungsbildschirm". Setzen Sie den Status auf **"In Produktion"**. (Als "Test" müssten Sie jeden Nutzer manuell hinzufügen).

### Schritt 2: Docker-Volume vorbereiten

1.  Erstellen Sie ein Verzeichnis, das als Docker-Volume dienen wird:
    ```bash
    mkdir ./calendar-data
    ```

### Schritt 3: Docker bauen und starten

1.  Stellen Sie sicher, dass alle anderen Dateien (`Dockerfile`, `web_server.py`, `sync_all_users.py`, `sync_logic.py`, `requirements.txt`, `crontab`, `run.sh` und der `templates` Ordner) im Hauptverzeichnis liegen.
2.  Bauen Sie das Image:
    ```bash
    docker build -t calendar-sync-web .
    ```
3.  **Generieren Sie einen starken Secret Key:**
    z.B. mit `openssl rand -base64 32`
4.  **Starten Sie den Container:**
    Sie müssen dem Container jetzt alle Geheimnisse als Umgebungsvariablen (ENV) übergeben.

    ```bash
    docker run -d --name calendar-sync \
      -p 8000:8000 \
      -v $(pwd)/calendar-data:/app/data \
      -e TZ=Europe/Berlin \
      -e APP_BASE_URL="https.dhbw-kalender-cleaner.ptb.ltm-labs.de" \
      -e GOOGLE_CLIENT_ID="IHRE_CLIENT_ID_VON_GOOGLE" \
      -e GOOGLE_CLIENT_SECRET="IHR_CLIENT_SECRET_VON_GOOGLE" \
      -e SECRET_KEY="IHR_GENERIERTER_SECRET_KEY" \
      calendar-sync-web
    ```
    *(Windows PowerShell: `${PWD}` statt `$(pwd)`)*

### Schritt 4: Reverse Proxy (WICHTIG)

Der Container läuft auf `http://localhost:8000`. Sie *müssen* einen Reverse Proxy (z.B. Nginx, Caddy oder Traefik) auf Ihrem Server einrichten, der:
1.  Die Domain `dhbw-kalender-cleaner.ptb.ltm-labs.de` empfängt.
2.  **HTTPS (SSL/TLS)** bereitstellt (z.B. mit Let's Encrypt).
3.  Anfragen an `http://localhost:8000` weiterleitet.

Google **erzwingt** HTTPS für OAuth-Callbacks.

### Schritt 5: Nutzung

1.  Jeder Nutzer (einschließlich Ihnen) besucht `https://dhbw-kalender-cleaner.ptb.ltm-labs.de`.
2.  Klickt auf "Mit Google anmelden".
3.  Führt das Setup (Quelle, Ziel, Regex) im Dashboard durch.
4.  Das System synchronisiert diesen Nutzer ab sofort stündlich.
