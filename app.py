#!/usr/bin/env python3
"""
Smart Wake Alarm - Standalone Backend für Render.com Free Tier
Komplett eigenständig, keine externe Datenbank nötig.
Scrapet den Vertretungsplan von portal.gym-oppenheim.de und berechnet die Weckzeit.
"""

from flask import Flask, jsonify, request
from flask_cors import CORS
from bs4 import BeautifulSoup
import requests
from datetime import datetime, timedelta
import os
import json
import logging

# Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
CORS(app)

# ===== KONFIGURATION =====
# Credentials aus Umgebungsvariablen (sicher auf Render)
SCHOOL_URL = "https://portal.gym-oppenheim.de"
USERNAME = os.environ.get("SCHOOL_USERNAME", "GiuseppeFoggia")
PASSWORD = os.environ.get("SCHOOL_PASSWORD", "elehciM-1")
API_TOKEN = os.environ.get("API_TOKEN", "1_smartwake2026")

# Vorlaufzeit (Minuten vor Schulbeginn)
VORLAUFZEIT = int(os.environ.get("VORLAUFZEIT", "30"))

# ===== STUNDENZEITEN (Gymnasium Oppenheim) =====
STUNDEN_BEGINN = {
    1: "07:55",
    2: "08:45",
    3: "09:45",
    4: "10:30",
    5: "11:30",
    6: "12:20",
    7: "13:05",
    8: "13:40",
    9: "14:25",
    10: "15:15",
    11: "16:00",
}

# ===== STUNDENPLAN (echte Daten) =====
# Format: Tag -> Stundennummer -> Kurs-Kürzel (oder None)
STUNDENPLAN = {
    "Montag": {
        1: "ma5",
        2: "skg1",
        3: "BI4",
        4: "BI4",
    },
    "Dienstag": {
        1: "de3",
        2: "mu1",
        3: "EK1",
        4: "EK1",
        5: "BI4",
        6: "BI4",
        10: "sp3",
        11: "sp3",
    },
    "Mittwoch": {
        1: "in1",
        2: "skg1",
        3: "ma5",
        4: "ma5",
        5: "BI4",
        8: "EN4",
        9: "EN4",
    },
    "Donnerstag": {
        2: "EN4",
        3: "rk1",
        4: "rk1",
        5: "mu1",
        6: "mu1",
        8: "in1",
        9: "in1",
    },
    "Freitag": {
        1: "EN4",
        2: "EN4",
        3: "EK1",
        4: "EK1",
        5: "de3",
        6: "de3",
    },
}

# ===== WECKER-ZUORDNUNG =====
# Welcher Wecker für welche Weckzeit?
WECKER_ZUORDNUNG = {
    "06:30": "Smart Wecker Regulärer Unterricht",
    "07:20": "Smart Wecker 1ste Stunde Frei",
    "08:05": "Smart Wecker 2 Stunden frei",
}

# ===== SCHULFERIEN RLP 2025/2026 =====
FERIEN = [
    # Weihnachtsferien
    ("2025-12-22", "2026-01-02"),
    # Winterferien / Fastnacht
    ("2026-02-16", "2026-02-20"),
    # Osterferien
    ("2026-03-30", "2026-04-10"),
    # Pfingstferien
    ("2026-06-05", "2026-06-05"),
    # Fronleichnam (Feiertag)
    ("2026-06-04", "2026-06-04"),
    # Sommerferien
    ("2026-07-06", "2026-08-14"),
    # Herbstferien
    ("2026-10-12", "2026-10-23"),
    # Weihnachtsferien 2026/27
    ("2026-12-21", "2027-01-04"),
]

# Einzelne Feiertage (RLP)
FEIERTAGE = [
    "2026-01-01",  # Neujahr
    "2026-04-03",  # Karfreitag
    "2026-04-06",  # Ostermontag
    "2026-05-01",  # Tag der Arbeit
    "2026-05-14",  # Christi Himmelfahrt
    "2026-05-25",  # Pfingstmontag
    "2026-06-04",  # Fronleichnam
    "2026-10-03",  # Tag der Deutschen Einheit
    "2026-11-01",  # Allerheiligen
    "2026-12-25",  # 1. Weihnachtstag
    "2026-12-26",  # 2. Weihnachtstag
]


def ist_schulfrei(datum_str):
    """Prüft ob ein Datum in den Ferien oder an einem Feiertag liegt."""
    datum = datetime.strptime(datum_str, "%Y-%m-%d").date()
    
    # Feiertage prüfen
    if datum_str in FEIERTAGE:
        return True
    
    # Ferien prüfen
    for start, ende in FERIEN:
        start_d = datetime.strptime(start, "%Y-%m-%d").date()
        ende_d = datetime.strptime(ende, "%Y-%m-%d").date()
        if start_d <= datum <= ende_d:
            return True
    
    return False


def normalize_kurs(kurs_code):
    """Normalisiert Kurs-Codes: FR_GK_1 -> FR1, BI_LK_4 -> BI4"""
    if not kurs_code or kurs_code.startswith("---"):
        return None
    
    import re
    # Pattern: FACH_TYP_NUMMER (z.B. BI_LK_4, MA_GK_5)
    match = re.match(r'([A-Za-z]+)_[A-Za-z]+_(\d+)', kurs_code)
    if match:
        return match.group(1).upper() + match.group(2)
    
    return kurs_code.strip()


def kurs_match(kurs_a, kurs_b):
    """Prüft ob zwei Kurs-Codes zum selben Kurs gehören."""
    if not kurs_a or not kurs_b:
        return False
    
    a = normalize_kurs(kurs_a)
    b = normalize_kurs(kurs_b)
    
    if not a or not b:
        return False
    
    return a.lower() == b.lower()


def subtrahiere_minuten(zeit_str, minuten):
    """Subtrahiert Minuten von einer Uhrzeit (HH:MM)."""
    h, m = map(int, zeit_str.split(":"))
    total = h * 60 + m - minuten
    if total < 0:
        total = 0
    return f"{total // 60:02d}:{total % 60:02d}"


def anmelden():
    """Meldet sich am Schulportal an und gibt die Session zurück."""
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    })
    
    try:
        # Login-Request
        response = session.post(
            SCHOOL_URL + "/",
            data={
                "username": USERNAME,
                "password": PASSWORD,
                "bttSenden": "anmelden",
            },
            timeout=15,
            allow_redirects=True,
        )
        
        if response.status_code == 200:
            logger.info("✅ Anmeldung erfolgreich")
            return session
        else:
            logger.warning(f"⚠️ Login Status: {response.status_code}")
            return None
    except Exception as e:
        logger.error(f"❌ Anmeldungsfehler: {e}")
        return None


def vertretungsplan_scrapen(session):
    """Scraped den Vertretungsplan und gibt relevante Einträge zurück."""
    try:
        response = session.get(
            SCHOOL_URL + "/vertretungsplan",
            timeout=15,
        )
        
        if response.status_code != 200:
            logger.warning(f"Vertretungsplan Status: {response.status_code}")
            return []
        
        soup = BeautifulSoup(response.text, "html.parser")
        table = soup.find("table")
        
        if not table:
            logger.warning("Keine Vertretungsplan-Tabelle gefunden")
            return []
        
        eintraege = []
        for row in table.find_all("tr")[1:]:  # Skip header
            cells = row.find_all("td")
            if len(cells) < 7:
                continue
            
            klasse = cells[0].get_text(strip=True)
            stunde = cells[1].get_text(strip=True)
            vertretung = cells[2].get_text(strip=True)
            raum = cells[3].get_text(strip=True)
            fach = cells[4].get_text(strip=True)
            art = cells[5].get_text(strip=True)
            info = cells[6].get_text(strip=True)
            
            if not fach or not stunde:
                continue
            
            normalized = normalize_kurs(fach)
            if not normalized:
                continue
            
            # Parse Stunden (z.B. "1-2" -> [1, 2])
            stunden = []
            if "-" in stunde:
                parts = stunde.split("-")
                try:
                    start = int(parts[0].strip())
                    end = int(parts[1].strip())
                    stunden = list(range(start, end + 1))
                except ValueError:
                    continue
            else:
                try:
                    stunden = [int(stunde.strip())]
                except ValueError:
                    continue
            
            eintraege.append({
                "kurs": normalized,
                "stunden": stunden,
                "art": art.lower(),
                "raum": raum,
                "info": info,
            })
        
        logger.info(f"📋 Vertretungsplan: {len(eintraege)} Einträge gescraped")
        return eintraege
    
    except Exception as e:
        logger.error(f"❌ Vertretungsplan-Fehler: {e}")
        return []


def berechne_weckzeit(tag_name, datum_str):
    """
    Hauptlogik: Berechnet die Weckzeit für einen bestimmten Tag.
    Berücksichtigt Stundenplan, Vertretungsplan und Ferien.
    """
    
    # 1. Wochenende?
    datum = datetime.strptime(datum_str, "%Y-%m-%d")
    wochentag = datum.weekday()  # 0=Mo, 6=So
    
    if wochentag >= 5:  # Samstag oder Sonntag
        return {
            "status": "success",
            "tag": tag_name,
            "datum": datum_str,
            "szenario": "wochenende",
            "label": "Kein Schultag (Wochenende)",
            "weckzeit": "—",
            "schulbeginn": "—",
            "ersteStunde": 0,
            "wecker": False,
            "zusammenfassung": "Wochenende – ausschlafen!",
            "nachrichtAnMutter": "Wochenende, kein Unterricht.",
        }
    
    # 2. Ferien/Feiertag?
    if ist_schulfrei(datum_str):
        return {
            "status": "success",
            "tag": tag_name,
            "datum": datum_str,
            "szenario": "wochenende",
            "label": "Schulfrei (Ferien/Feiertag)",
            "weckzeit": "—",
            "schulbeginn": "—",
            "ersteStunde": 0,
            "wecker": False,
            "zusammenfassung": "Schulfrei – ausschlafen!",
            "nachrichtAnMutter": "Heute schulfrei.",
        }
    
    # 3. Stundenplan für diesen Tag
    stunden_heute = STUNDENPLAN.get(tag_name, {})
    
    if not stunden_heute:
        return {
            "status": "success",
            "tag": tag_name,
            "datum": datum_str,
            "szenario": "wochenende",
            "label": "Kein Unterricht",
            "weckzeit": "—",
            "schulbeginn": "—",
            "ersteStunde": 0,
            "wecker": False,
            "zusammenfassung": "Kein Unterricht heute.",
            "nachrichtAnMutter": "Heute kein Unterricht.",
        }
    
    # 4. Vertretungsplan scrapen
    vertretungen = []
    try:
        session = anmelden()
        if session:
            vertretungen = vertretungsplan_scrapen(session)
    except Exception as e:
        logger.warning(f"Scraping fehlgeschlagen: {e}")
    
    # 5. Finde ausgefallene Stunden (nur meine Kurse)
    ausgefallen = set()
    for vp in vertretungen:
        # Prüfe ob der Kurs in meinem Stundenplan ist
        for stunde_nr, mein_kurs in stunden_heute.items():
            if kurs_match(vp["kurs"], mein_kurs):
                # Prüfe ob es ein Entfall ist
                art = vp["art"].lower()
                if "entfall" in art or "freisetz" in art or "selbst" in art:
                    for s in vp["stunden"]:
                        if s == stunde_nr:
                            ausgefallen.add(s)
    
    # 6. Finde die erste Stunde die NICHT ausfällt
    sortierte_stunden = sorted(stunden_heute.keys())
    erste_stunde = sortierte_stunden[0]
    freigestellt_bis = 0
    
    for s in sortierte_stunden:
        if s in ausgefallen:
            freigestellt_bis = s
            # Nächste Stunde wird die neue erste
            idx = sortierte_stunden.index(s)
            if idx + 1 < len(sortierte_stunden):
                erste_stunde = sortierte_stunden[idx + 1]
            else:
                # Alle Stunden fallen aus!
                return {
                    "status": "success",
                    "tag": tag_name,
                    "datum": datum_str,
                    "szenario": "wochenende",
                    "label": "Alle Stunden entfallen",
                    "weckzeit": "—",
                    "schulbeginn": "—",
                    "ersteStunde": 0,
                    "wecker": False,
                    "zusammenfassung": "Alle Stunden entfallen – frei!",
                    "nachrichtAnMutter": "Alle Stunden entfallen, habe frei.",
                }
        else:
            erste_stunde = s
            break
    
    # 7. Berechne Szenario
    if freigestellt_bis >= 3:
        szenario = "free123"
    elif freigestellt_bis == 2:
        szenario = "free12"
    elif freigestellt_bis == 1:
        szenario = "free1"
    else:
        szenario = "normal"
    
    # 8. Berechne Weckzeit
    schulbeginn = STUNDEN_BEGINN.get(erste_stunde, "07:55")
    weckzeit = subtrahiere_minuten(schulbeginn, VORLAUFZEIT)
    
    # 9. Labels
    LABELS = {
        "normal": "Normaler Schultag",
        "free1": "1. Stunde frei",
        "free12": "1.–2. Stunde frei",
        "free123": "1.–3. Stunde frei",
    }
    
    NACHRICHTEN = {
        "normal": f"Unterricht ab {schulbeginn} ({erste_stunde}. Stunde).",
        "free1": f"1. Stunde frei, komme erst zur {erste_stunde}. Stunde ({schulbeginn}).",
        "free12": f"1.–2. Stunde frei, komme erst zur {erste_stunde}. Stunde ({schulbeginn}).",
        "free123": f"1.–3. Stunde frei, komme erst zur {erste_stunde}. Stunde ({schulbeginn}).",
    }
    
    label = LABELS.get(szenario, "Schultag")
    nachricht = NACHRICHTEN.get(szenario, f"Unterricht ab {schulbeginn}.")
    
    return {
        "status": "success",
        "tag": tag_name,
        "datum": datum_str,
        "szenario": szenario,
        "label": label,
        "weckzeit": weckzeit,
        "schulbeginn": schulbeginn,
        "ersteStunde": erste_stunde,
        "freigestelltBis": freigestellt_bis,
        "wecker": True,
        "zusammenfassung": f"{label} – Wecker um {weckzeit}, Unterricht ab {schulbeginn}",
        "nachrichtAnMutter": nachricht,
        "vertretungen": [v for v in vertretungen if any(
            kurs_match(v["kurs"], stunden_heute.get(s, ""))
            for s in v["stunden"]
        )],
    }


# ===== API ENDPOINTS =====

@app.route("/", methods=["GET"])
def index():
    """Health Check / Landing Page."""
    return jsonify({
        "status": "ok",
        "service": "Smart Wake Alarm",
        "version": "3.0",
        "endpoints": ["/api/weckzeit?token=TOKEN&tag=morgen"],
    })


@app.route("/api/weckzeit", methods=["GET"])
def api_weckzeit():
    """
    Haupt-API-Endpoint für den iOS Shortcut.
    Parameter:
      - token: Authentifizierungs-Token
      - tag: "heute" oder "morgen" (Standard: "morgen")
    """
    # Token prüfen
    token = request.args.get("token", "")
    if not token or token != API_TOKEN:
        return jsonify({"error": "missing or invalid token"}), 401
    
    # Tag bestimmen
    tag_param = request.args.get("tag", "morgen")
    
    # Deutsch-Wochentage
    WOCHENTAGE = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]
    
    now = datetime.now()
    
    if tag_param == "heute":
        ziel_datum = now
    elif tag_param == "morgen":
        ziel_datum = now + timedelta(days=1)
    else:
        try:
            ziel_datum = datetime.strptime(tag_param, "%Y-%m-%d")
        except ValueError:
            return jsonify({"error": "Ungültiges Datum. Nutze 'heute', 'morgen' oder YYYY-MM-DD"}), 400
    
    datum_str = ziel_datum.strftime("%Y-%m-%d")
    tag_name = WOCHENTAGE[ziel_datum.weekday()]
    
    logger.info(f"📅 Weckzeit-Anfrage für {tag_name}, {datum_str}")
    
    # Weckzeit berechnen
    result = berechne_weckzeit(tag_name, datum_str)
    
    logger.info(f"✅ Ergebnis: {result.get('szenario')} → Weckzeit: {result.get('weckzeit')}")
    
    return jsonify(result)


@app.route("/api/stundenplan", methods=["GET"])
def api_stundenplan():
    """Zeigt den aktuellen Stundenplan an."""
    token = request.args.get("token", "")
    if not token or token != API_TOKEN:
        return jsonify({"error": "missing or invalid token"}), 401
    
    return jsonify({
        "stundenplan": STUNDENPLAN,
        "stunden_beginn": {str(k): v for k, v in STUNDEN_BEGINN.items()},
        "vorlaufzeit_minuten": VORLAUFZEIT,
    })


@app.route("/api/debug", methods=["GET"])
def api_debug():
    """Debug-Informationen."""
    token = request.args.get("token", "")
    if not token or token != API_TOKEN:
        return jsonify({"error": "missing or invalid token"}), 401
    
    now = datetime.now()
    return jsonify({
        "server_time": now.strftime("%Y-%m-%d %H:%M:%S"),
        "timezone": "UTC",
        "school_url": SCHOOL_URL,
        "username_set": bool(USERNAME),
        "password_set": bool(PASSWORD),
        "vorlaufzeit": VORLAUFZEIT,
        "token_configured": bool(API_TOKEN),
    })


# ===== START =====
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    logger.info(f"🚀 Smart Wake Alarm startet auf Port {port}...")
    app.run(host="0.0.0.0", port=port, debug=False)
