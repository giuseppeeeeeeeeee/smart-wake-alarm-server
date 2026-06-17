import os
import re
import datetime
import requests
from bs4 import BeautifulSoup
from flask import Flask, request, jsonify

app = Flask(__name__)

API_TOKEN = "1_smartwake2026"

# ---------------------------------------------------------------------------
# SCHULPORTAL ZUGANG
# Niemals Klartext im Code. Werte kommen aus Render Environment Variables.
# In Render Dashboard -> Service -> Environment -> Add:
#   SCHULPORTAL_USER = giuseppefoggia
#   SCHULPORTAL_PASS = <dein neues Passwort, NICHT das alte aus dem Chat>
# ---------------------------------------------------------------------------
PORTAL_BASE = "https://portal.gym-oppenheim.de"
PORTAL_LOGIN_URL = f"{PORTAL_BASE}/index.php"
PORTAL_KALENDER_URL = f"{PORTAL_BASE}/kalender.php"

PORTAL_USER = os.environ.get("SCHULPORTAL_USER", "")
PORTAL_PASS = os.environ.get("SCHULPORTAL_PASS", "")

# ---------------------------------------------------------------------------
# STUNDENPLAN
# Key = Wochentag (0=Montag ... 4=Freitag), Value = Dict {Stunde: Kurscode|None}
# Donnerstag Stunde 1 = None (fester Frei-Slot, vorher fehlte er komplett -> Bug)
# ---------------------------------------------------------------------------
STUNDENPLAN = {
    0: {1: "ma5", 2: "skg1", 3: "BI4", 4: "BI4"},                                   # Montag
    1: {1: "de3", 2: "mu1", 3: "EK1", 4: "EK1", 5: "BI4", 6: "BI4",
        10: "sp3", 11: "sp3"},                                                       # Dienstag
    2: {1: "in1", 2: "skg1", 3: "ma5", 4: "ma5", 5: "BI4", 8: "EN4", 9: "EN4"},       # Mittwoch
    3: {1: None, 2: "EN4", 3: "rk1", 4: "rk1", 5: "mu1", 6: "mu1",
        8: "in1", 9: "in1"},                                                         # Donnerstag
    4: {1: "EN4", 2: "EN4", 3: "EK1", 4: "EK1", 5: "de3", 6: "de3"},                  # Freitag
}

WECKZEITEN = {
    "normal": "06:30",
    "free1": "07:20",
    "free12": "08:00",
}

# ---------------------------------------------------------------------------
# FESTE FEIERTAGE (jedes Jahr gleiches Datum, 100% schulfrei, Bundesland RLP)
# Format: (Monat, Tag): "Name"
# Bewegliche Feiertage (Ostern, Christi Himmelfahrt, Pfingsten, Fronleichnam)
# hängen vom Osterdatum ab -> eigene Berechnung weiter unten.
# ---------------------------------------------------------------------------
FESTE_FEIERTAGE_RLP = {
    (1, 1): "Neujahr",
    (5, 1): "Tag der Arbeit",
    (8, 15): "Mariä Himmelfahrt",      # RLP: nur in best. Gemeinden, hier als schulfrei angenommen
    (10, 3): "Tag der Deutschen Einheit",
    (11, 1): "Allerheiligen",
    (12, 25): "1. Weihnachtstag",
    (12, 26): "2. Weihnachtstag",
}


def berechne_ostersonntag(jahr: int) -> datetime.date:
    """Gauß'sche Osterformel."""
    a = jahr % 19
    b = jahr // 100
    c = jahr % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    monat = (h + l - 7 * m + 114) // 31
    tag = ((h + l - 7 * m + 114) % 31) + 1
    return datetime.date(jahr, monat, tag)


def bewegliche_feiertage(jahr: int) -> dict:
    """Karfreitag, Ostermontag, Christi Himmelfahrt, Pfingstmontag, Fronleichnam."""
    ostern = berechne_ostersonntag(jahr)
    return {
        ostern - datetime.timedelta(days=2): "Karfreitag",
        ostern + datetime.timedelta(days=1): "Ostermontag",
        ostern + datetime.timedelta(days=39): "Christi Himmelfahrt",
        ostern + datetime.timedelta(days=50): "Pfingstmontag",
        ostern + datetime.timedelta(days=60): "Fronleichnam",
    }


def ist_feiertag(tag: datetime.date) -> str | None:
    """Gibt Feiertagsnamen zurück, wenn tag ein Feiertag ist, sonst None."""
    key = (tag.month, tag.day)
    if key in FESTE_FEIERTAGE_RLP:
        return FESTE_FEIERTAGE_RLP[key]
    bewegliche = bewegliche_feiertage(tag.year)
    if tag in bewegliche:
        return bewegliche[tag]
    return None


# ---------------------------------------------------------------------------
# SCHULPORTAL KALENDER-FETCH
# ---------------------------------------------------------------------------
_session_cache = {"session": None, "eingeloggt_am": None}


def get_portal_session() -> requests.Session:
    """
    Loggt sich beim Schulportal ein und gibt eine eingeloggte Session zurück.
    Session wird 30 Minuten im Speicher gecacht, um nicht bei jedem Call neu
    einzuloggen (spart Zeit -> wichtig wegen iOS 60-Sek-Timeout).

    WICHTIG: Feldnamen "username"/"password" sind ein Standardraten für
    schulportal-typische Logins (IServ/eigene PHP-Systeme verwenden oft genau
    diese Namen). Falls Login fehlschlägt: echtes Formular im Browser öffnen,
    Rechtsklick -> Seitenquelltext -> <input name="..."> Werte hier eintragen.
    """
    now = datetime.datetime.now()
    cached = _session_cache["session"]
    eingeloggt_am = _session_cache["eingeloggt_am"]

    if cached and eingeloggt_am and (now - eingeloggt_am).total_seconds() < 1800:
        return cached

    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0 (SmartWakeAlarm-Bot)"})

    login_payload = {
        "username": PORTAL_USER,   # TODO: Feldname anpassen falls Portal anderen Namen nutzt
        "password": PORTAL_PASS,   # TODO: Feldname anpassen falls Portal anderen Namen nutzt
    }

    resp = session.post(PORTAL_LOGIN_URL, data=login_payload, timeout=15)
    resp.raise_for_status()

    _session_cache["session"] = session
    _session_cache["eingeloggt_am"] = now
    return session


def fetch_kalender_html(monat_jahr: str) -> str:
    """
    Holt die Kalenderseite für einen Monat (Format 'MM.YYYY' o.ä., je nach
    Portal-URL-Schema). Hier: Portal zeigt anscheinend pro Monatslink eigene
    Ansicht (Aug 2025, Sep 2025, ...). Wir nutzen Query-Parameter ?monat=.
    TODO: echten URL-Parameter aus Browser-Adresszeile prüfen, falls Format
    abweicht (z.B. ?m=8&y=2026 statt ?monat=08.2026).
    """
    session = get_portal_session()
    resp = session.get(PORTAL_KALENDER_URL, params={"monat": monat_jahr}, timeout=15)
    resp.raise_for_status()
    return resp.text


def parse_termine_aus_html(html: str) -> list[dict]:
    """
    Parst die Terminliste-Tabelle aus dem Kalender-HTML.
    Erwartete Struktur laut Portal-Export (siehe Beispiel):
      Fr 26.6.   Letzter Schultag vor den Sommerferien. ...
      Sa 27.6.   bis 07.08.  Beginn der Sommerferien (bis 07.08.2026)
      So 28.6.
      Mo 29.6.
    -> Tabellenzeilen mit Datum + optionalem Text. Leere Zeilen = kein Termin.
    """
    soup = BeautifulSoup(html, "html.parser")  # statt "lxml"
    termine = []

    zeilen = soup.find_all("tr")
    datum_pattern = re.compile(
        r"(Mo|Di|Mi|Do|Fr|Sa|So)\s+(\d{1,2})\.(\d{1,2})\.?"
    )
    bis_pattern = re.compile(r"bis\s+(\d{1,2})\.(\d{1,2})\.(\d{4})")

    for zeile in zeilen:
        text = zeile.get_text(" ", strip=True)
        match = datum_pattern.search(text)
        if not match:
            continue

        tag_zahl = int(match.group(2))
        monat_zahl = int(match.group(3))

        beschreibung = text[match.end():].strip()
        if not beschreibung:
            continue  # leere Zeile, kein echter Termin

        bis_match = bis_pattern.search(beschreibung)
        bis_datum = None
        if bis_match:
            bis_datum = datetime.date(
                int(bis_match.group(3)), int(bis_match.group(2)), int(bis_match.group(1))
            )

        termine.append({
            "tag": tag_zahl,
            "monat": monat_zahl,
            "beschreibung": beschreibung,
            "bis_datum": bis_datum,
            "ist_ferien_oder_frei": any(
                wort in beschreibung.lower()
                for wort in ["ferien", "schulfrei", "frei", "beweglicher ferientag"]
            ),
        })

    return termine


def ist_schulfrei_laut_portal(zieltag: datetime.date) -> bool:
    """
    Holt den passenden Monat vom Portal und prüft, ob zieltag innerhalb
    eines als "Ferien"/"schulfrei" markierten Zeitraums liegt, oder ob direkt
    ein Termin mit Ferien-Schlagwort auf diesen Tag fällt.
    """
    try:
        monat_str = f"{zieltag.month:02d}.{zieltag.year}"
        html = fetch_kalender_html(monat_str)
        termine = parse_termine_aus_html(html)
    except Exception as e:
        # Portal nicht erreichbar / Login fehlgeschlagen -> nicht blockieren,
        # einfach als "kein Ferien-Hinweis verfügbar" werten.
        print(f"[WARN] Kalender-Fetch fehlgeschlagen: {e}")
        return False

    for termin in termine:
        if not termin["ist_ferien_oder_frei"]:
            continue

        # Direkter Tagestreffer
        if termin["tag"] == zieltag.day and termin["monat"] == zieltag.month:
            return True

        # Mehrtägiger Zeitraum ("bis DD.MM.YYYY")
        if termin["bis_datum"]:
            start = datetime.date(zieltag.year, termin["monat"], termin["tag"])
            if start <= zieltag <= termin["bis_datum"]:
                return True

    return False


# ---------------------------------------------------------------------------
# SZENARIO-LOGIK (Stundenplan-Lücken)
# ---------------------------------------------------------------------------
def get_free_count(wochentag: int, ausfaelle: set) -> int:
    plan = STUNDENPLAN.get(wochentag, {})
    if not plan:
        return 0

    max_stunde = max(plan.keys())
    free_count = 0

    for stunde in range(1, max_stunde + 1):
        kurs = plan.get(stunde)

        ist_fester_frei_slot = (stunde in plan) and (kurs is None)
        ist_ausfall = (kurs is not None) and (stunde in ausfaelle)

        if ist_fester_frei_slot or ist_ausfall:
            free_count += 1
        else:
            break

    return free_count


def berechne_szenario(wochentag: int, ausfaelle: set) -> str:
    free_count = get_free_count(wochentag, ausfaelle)
    if free_count >= 2:
        return "free12"
    if free_count == 1:
        return "free1"
    return "normal"


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------
@app.route("/api/weckzeit", methods=["GET"])
def weckzeit():
    token = request.args.get("token")
    if token != API_TOKEN:
        return jsonify({"error": "invalid token"}), 401

    tag_param = request.args.get("tag", "heute")
    heute = datetime.date.today()
    zieltag = heute + datetime.timedelta(days=1) if tag_param == "morgen" else heute

    wochentag = zieltag.weekday()  # 0=Montag ... 6=Sonntag

    # 1) Wochenende
    if wochentag >= 5:
        return jsonify({"wecker": False, "szenario": "wochenende"})

    # 2) Feste, datumsgebundene Feiertage (kein Portal-Fetch nötig, instant)
    feiertag_name = ist_feiertag(zieltag)
    if feiertag_name:
        return jsonify({"wecker": False, "szenario": "feiertag", "name": feiertag_name})

    # 3) Schulferien laut Portal-Kalender
    if ist_schulfrei_laut_portal(zieltag):
        return jsonify({"wecker": False, "szenario": "ferien"})

    # 4) TODO: echten Vertretungsplan-Parser einhängen
    # ausfaelle = parse_vertretungsplan(zieltag)
    ausfaelle = set()  # Platzhalter

    # 5) Normale Stundenplan-Lücken-Logik
    szenario = berechne_szenario(wochentag, ausfaelle)
    return jsonify({
        "wecker": True,
        "szenario": szenario,
        "weckzeit": WECKZEITEN[szenario],
    })


@app.route("/", methods=["GET"])
def health():
    return jsonify({"status": "ok"})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
