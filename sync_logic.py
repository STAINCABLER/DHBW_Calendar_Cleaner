import re
import requests
import arrow
from datetime import datetime, timedelta, timezone
from googleapiclient.errors import HttpError
from ics import Calendar

class CalendarSyncer:
    def __init__(self, service, log_callback=print, user_log_file=None):
        self.service = service
        self.system_log = log_callback  # Dies ist print() -> geht an system.log/docker logs
        self.user_log_file = user_log_file # Pfad zur <user_id>.log

    def log(self, message):
        # 1. Immer in den System-Log (für den Admin)
        self.system_log(message) 
        
        # 2. Zusätzlich in die User-Log-Datei (für das UI)
        if self.user_log_file:
            try:
                # 'a' für append (anhängen)
                with open(self.user_log_file, 'a') as f:
                    # Fügt die Nachricht mit einem Zeilenumbruch an
                    f.write(message + '\n')
            except Exception as e:
                # Wichtig: Der Sync darf nicht fehlschlagen, nur weil das Loggen fehlschlägt.
                self.system_log(f"!!! KRITISCHER LOG-FEHLER: Konnte nicht in User-Log schreiben {self.user_log_file}: {e}")

    def standardize_event(self, event_data, source_type):
        if source_type == 'google':
            return {
                'summary': event_data.get('summary', 'Kein Titel'),
                'description': event_data.get('description', ''),
                'location': event_data.get('location', ''),
                'start': event_data.get('start'),
                'end': event_data.get('end'),
            }
        elif source_type == 'ics':
            # Diese Funktion erhält jetzt zeitzonenbewusste 'arrow'-Objekte
            start_arrow = event_data.begin
            end_arrow = event_data.end

            start, end = {}, {}
            if event_data.all_day:
                start['date'] = start_arrow.format('YYYY-MM-DD')
                end['date'] = end_arrow.shift(days=1).format('YYYY-MM-DD')
            else:
                # isoformat() enthält jetzt den korrekten Offset (z.B. +01:00)
                start['dateTime'] = start_arrow.isoformat()
                end['dateTime'] = end_arrow.isoformat()
            return {
                'summary': event_data.name or 'Kein Titel',
                'description': event_data.description or '',
                'location': event_data.location or '',
                'start': start,
                'end': end,
            }

    def fetch_google_events(self, calendar_id, time_min=None, time_max=None):
        self.log(f"Rufe Google Kalender-Ereignisse ab für: {calendar_id}")
        try:
            params = {
                'calendarId': calendar_id,
                'singleEvents': True,
                'orderBy': 'startTime'
            }
            # Optional time window nur hinzufügen, wenn gesetzt
            if time_min:
                params['timeMin'] = time_min
            if time_max:
                params['timeMax'] = time_max

            events_result = self.service.events().list(**params).execute()
            return [self.standardize_event(e, 'google') for e in events_result.get('items', [])]
        except HttpError as error:
            self.log(f'Fehler beim Abrufen von Google-Ereignissen: {error}')
            return []

    def fetch_ics_events(self, url, time_min_dt=None, time_max_dt=None, source_timezone='Europe/Berlin'): # Zeitzone hinzugefügt
        """Ruft Ereignisse aus einer ICS-URL ab und filtert sie nach Zeit."""
        self.log(f"Rufe ICS-Ereignisse ab von: {url}")
        self.log(f"Verwende Quell-Zeitzone: {source_timezone} für 'naive' Zeitangaben.")
        try:
            response = requests.get(url)
            response.raise_for_status()
            calendar = Calendar(response.text)
            
            events = []
            for event in calendar.events:
                if not event.end or not event.begin:
                    continue

                # --- *** NEUE ZEITZONEN-KORREKTUR START *** ---
                start_arrow = event.begin
                end_arrow = event.end

                # Wir ignorieren die (potenziell falsche) Zeitzone der ICS-Datei
                # und stempeln die "naive" Zeit (z.B. 09:00) mit der vom Benutzer gewählten Zeitzone.
                # Das löst das 1-Stunden-Offset-Problem.
                start_arrow = arrow.get(start_arrow.naive, tzinfo=source_timezone)
                end_arrow = arrow.get(end_arrow.naive, tzinfo=source_timezone)
                
                event.begin = start_arrow
                event.end = end_arrow
                # --- *** NEUE ZEITZONEN-KORREKTUR ENDE *** ---

                # Falls kein Zeitfenster gesetzt ist, alle Ereignisse übernehmen
                if time_min_dt is None or time_max_dt is None:
                    events.append(self.standardize_event(event, 'ics'))
                else:
                    if event.end > time_min_dt and event.begin < time_max_dt:
                        events.append(self.standardize_event(event, 'ics'))
            return events
        except Exception as e:
            self.log(f'Fehler beim Abrufen oder Parsen der ICS-URL: {e}')
            return []

    def filter_events(self, events, regex_patterns_raw):
        if not regex_patterns_raw:
            return events, 0
        
        regex_patterns = [re.compile(p, re.IGNORECASE) for p in regex_patterns_raw if p]
        filtered_events = []
        excluded_count = 0
        
        for event in events:
            summary = event['summary']
            is_excluded = any(pattern.search(summary) for pattern in regex_patterns)
            if not is_excluded:
                filtered_events.append(event)
            else:
                excluded_count += 1
                self.log(f"  -> Ausgeschlossen: '{summary}'")
                
        self.log(f"{excluded_count} Ereignisse ausgeschlossen, {len(filtered_events)} Ereignisse verbleiben.")
        return filtered_events, excluded_count

    def sync_to_target(self, target_id, events_to_sync, time_min=None, time_max=None):
        self.log(f"Lösche vorhandene Ereignisse im Zielkalender ({target_id})...")
        deleted_count = 0
        try:
            page_token = None
            while True:
                params = {
                    'calendarId': target_id,
                    'singleEvents': True,
                    'pageToken': page_token
                }
                # Füge timeMin/timeMax nur hinzu, wenn gesetzt (sonst werden alle Events abgefragt)
                if time_min:
                    params['timeMin'] = time_min
                if time_max:
                    params['timeMax'] = time_max

                existing_events = self.service.events().list(**params).execute()
                
                items = existing_events.get('items', [])
                if not items: break
                
                for event in items:
                    try:
                        self.service.events().delete(calendarId=target_id, eventId=event['id']).execute()
                        deleted_count += 1
                    except HttpError as e:
                        self.log(f"  -> Fehler beim Löschen von Event {event['id']}: {e}")
                
                page_token = existing_events.get('nextPageToken')
                if not page_token: break
            self.log(f"{deleted_count} Ereignisse im Zielkalender gelöscht.")
        except HttpError as e:
            self.log(f"Fehler beim Abrufen von Zielereignissen zum Löschen: {e}")
            return 0, 0

        self.log(f"Erstelle {len(events_to_sync)} neue Ereignisse...")
        created_count = 0
        for event_body in events_to_sync:
            try:
                self.service.events().insert(calendarId=target_id, body=event_body).execute()
                created_count += 1
            except HttpError as e:
                self.log(f"  -> Fehler beim Erstellen von Event '{event_body['summary']}': {e}")
        self.log(f"{created_count} Ereignisse erfolgreich erstellt.")
        return created_count, deleted_count

    def run_sync(self, config):
        """Führt den gesamten Sync-Prozess für eine gegebene Konfiguration aus."""
        self.log(f"Starte Sync für Quelle '{config.get('source_id')}'...")
        SOURCE_CALENDAR_ID = config.get('source_id')
        TARGET_CALENDAR_ID = config.get('target_id')
        REGEX_PATTERNS = config.get('regex_patterns', [])
        # Zeitzone des Benutzers holen, Standard ist Berlin
        SOURCE_TIMEZONE = config.get('source_timezone', 'Europe/Berlin') 

        if not SOURCE_CALENDAR_ID or not TARGET_CALENDAR_ID:
            self.log("Fehler: source_id oder target_id nicht konfiguriert.")
            return

        # Standard: synchronisiere ALLE Ereignisse (kein Zeitfenster)
        time_min_iso = None
        time_max_iso = None
        self.log("Zeitfenster: vollständig (keine Einschränkung). Achtung: dies kann viele Events betreffen.")

        source_events = []
        is_ics = SOURCE_CALENDAR_ID.startswith('http://') or SOURCE_CALENDAR_ID.startswith('https://')

        if is_ics:
            # Zeitzone wird übergeben; ohne Zeitfenster alle ICS-Ereignisse übernehmen
            source_events = self.fetch_ics_events(SOURCE_CALENDAR_ID, time_min_iso, time_max_iso, SOURCE_TIMEZONE)
        else:
            source_events = self.fetch_google_events(SOURCE_CALENDAR_ID, time_min_iso, time_max_iso)

        self.log(f"{len(source_events)} Ereignisse aus der Quelle abgerufen.")
        eligible_events, excluded = self.filter_events(source_events, REGEX_PATTERNS)
        created, deleted = self.sync_to_target(TARGET_CALENDAR_ID, eligible_events, time_min_iso, time_max_iso)

        self.log(f"Sync abgeschlossen: {created} erstellt, {deleted} gelöscht, {excluded} ausgeschlossen.")