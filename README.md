# Smart Wake Alarm - Backend

Kostenloser Backend-Server für den Smart Wake Alarm iOS Shortcut.
Deployed auf Render.com Free Tier.

## Was macht der Server?

1. Scraped den Vertretungsplan von portal.gym-oppenheim.de
2. Vergleicht mit dem Stundenplan
3. Berechnet die optimale Weckzeit
4. Gibt das Ergebnis als JSON zurück

## API Endpoint

```
GET /api/weckzeit?token=DEIN_TOKEN&tag=morgen
```

### Antwort-Beispiel:
```json
{
  "status": "success",
  "tag": "Dienstag",
  "datum": "2026-06-17",
  "szenario": "normal",
  "label": "Normaler Schultag",
  "weckzeit": "07:25",
  "schulbeginn": "07:55",
  "ersteStunde": 1,
  "wecker": true,
  "zusammenfassung": "Normaler Schultag – Wecker um 07:25, Unterricht ab 07:55",
  "nachrichtAnMutter": "Unterricht ab 07:55 (1. Stunde)."
}
```

## Szenarien

| Szenario | Bedeutung | Wecker |
|----------|-----------|--------|
| `normal` | Normaler Schultag | 06:30 (07:55 - 30min ≈ 07:25, gerundet auf Wecker) |
| `free1` | 1. Stunde frei | 07:20 |
| `free12` | 1.+2. Stunde frei | 08:05 |
| `wochenende` | Wochenende/Ferien/Frei | Kein Wecker |

## Umgebungsvariablen

| Variable | Beschreibung | Standard |
|----------|-------------|----------|
| `SCHOOL_USERNAME` | Portal-Benutzername | GiuseppeFoggia |
| `SCHOOL_PASSWORD` | Portal-Passwort | - |
| `API_TOKEN` | Token für Authentifizierung | 1_smartwake2026 |
| `VORLAUFZEIT` | Minuten vor Schulbeginn | 30 |
| `PORT` | Server-Port | 5000 |

## Deployment auf Render.com

1. Push dieses Repo auf GitHub
2. Gehe zu render.com → New Web Service
3. Verbinde dein GitHub-Repo
4. Render erkennt automatisch die Python-App
5. Setze die Umgebungsvariablen
6. Deploy!
