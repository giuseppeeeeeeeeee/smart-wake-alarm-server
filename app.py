import os
import re
import datetime as dt
from datetime import timedelta
import requests
from bs4 import BeautifulSoup
from flask import Flask, request, jsonify
 
app = Flask(__name__)
 
# =========================
# CONFIG
# =========================
 
PORTAL = "https://portal.gym-oppenheim.de"
LOGIN_URL = PORTAL + "/index.php"
KALENDER_URL = PORTAL + "/kalender.php"
VERTRETUNG_URL = PORTAL + "/vertretungsplan"
 
TOKEN = os.getenv("API_TOKEN", "1_smartwake2026")
USER = os.getenv("SCHULPORTAL_USER")
PASS = os.getenv("SCHULPORTAL_PASS")
 
# MSS-Stufe (anpassen wenn du in 12 bist)
MEINE_KLASSE = "11"
 
# =========================
# SESSION (cached 30 min)
# =========================
 
_session = None
_session_time = None
 
def get_session():
    global _session, _session_time
    now = dt.datetime.now()
    if _session and _session_time and (now - _session_time).seconds < 1800:
        return _session
    s = requests.Session()
    try:
        s.post(LOGIN_URL, data={"username": USER, "password": PASS}, timeout=15)
    except Exception as e:
        print(f"[WARN] Login fehlgeschlagen: {e}")
    _session = s
    _session_time = now
    return s
 
# =========================
# STUNDENPLAN (0=Mo..4=Fr)
# Stunde -> Kurs | None (fester Frei-Slot)
# =========================
 
STUNDENPLAN = {
    0: {1: "de3", 2: "ma5", 3: "ma5", 4: "skg1", 5: "skg1", 6: "EN4", 8: "BI4", 9: "BI4"},
    1: {1: "in1",  2: "in1",  3: "rk1",  4: "rk1",  5: "sp3", 6: "sp3", 8: "EK1", 9: "EK1"},
    2: {1: "mu1",  2: "mu1",  3: "de3",  4: "de3",  5: "ma5", 6: "ma5"},
    3: {1: None,   2: "EN4",  3: "rk1",  4: "rk1",  5: "mu1", 6: "mu1", 8: "in1", 9: "in1"},
    4: {1: "BI4",  2: "BI4",  3: "skg1", 4: "skg1", 5: "EK1", 6: "EK1"},
}
 
# =========================
# STUNDEN-STARTZEITEN
# =========================
 
START = {
    1: "07:55", 2: "08:45", 3: "09:45", 4: "10:30",
    5: "11:30", 6: "12:20", 7: "13:05", 8: "13:40",
    9: "14:25", 10: "15:15", 11: "16:00"
}
 
WECKZEITEN = {
    "normal":   "06:30",
    "free1":    "07:20",
    "free12":   "08:00",
    "free123":  "08:50",
    "free1234": "09:35",
}
 
# =========================
# FEIERTAGE RLP
# =========================
 
FESTE_FEIERTAGE = {
    (1, 1):   "Neujahr",
    (5, 1):   "Tag der Arbeit",
    (8, 15):  "Mariä Himmelfahrt",
    (10, 3):  "Tag der Deutschen Einheit",
    (11, 1):  "Allerheiligen",
    (12, 25): "1. Weihnachtstag",
    (12, 26): "2. Weihnachtstag",
}
 
def ostersonntag(jahr):
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
    return dt.date(jahr, monat, tag)
 
def bewegliche_feiertage(jahr):
    ostern = ostersonntag(jahr)
    return {
        ostern - timedelta(days=2): "Karfreitag",
        ostern + timedelta(days=1): "Ostermontag",
        ostern + timedelta(days=39): "Christi Himmelfahrt",
        ostern + timedelta(days=50): "Pfingstmontag",
        ostern + timedelta(days=60): "Fronleichnam",
    }
 
def ist_feiertag(tag):
    if (tag.month, tag.day) in FESTE_FEIERTAGE:
        return FESTE_FEIERTAGE[(tag.month, tag.day)]
    bew = bewegliche_feiertage(tag.year)
    if tag in bew:
        return bew[tag]
    return None
 
# =========================
# FERIEN (Kalender-Scraping)
# =========================
 
FERIEN_KEYWORDS = [
    "ferien", "ferienschluss", "ferientag", "ferienstart",
    "schulfrei", "bewegliche ferientage", "kein unterricht"
]
 
def ist_schulfrei(zieltag):
    try:
        s = get_session()
        html = s.get(KALENDER_URL, timeout=15).text
        soup = BeautifulSoup(html, "html.parser")
 
        entries_div = soup.find("div", {"id": "entries"})
        if not entries_div:
            return False
 
        for tr in entries_div.find_all("tr"):
            anchor = tr.find("a", {"name": re.compile(r"^\d{4}-\d{1,2}-\d{1,2}$")})
            if not anchor:
                continue
 
            name = anchor.get("name", "")
            parts = name.split("-")
            if len(parts) != 3:
                continue
 
            try:
                eintrag_datum = dt.date(int(parts[0]), int(parts[1]), int(parts[2]))
            except ValueError:
                continue
 
            text = tr.get_text(" ", strip=True).lower()
 
            # Ferien-Keyword?
            if not any(kw in text for kw in FERIEN_KEYWORDS):
                continue
 
            # Einzel-Tag passt?
            if eintrag_datum == zieltag:
                return True
 
            # Mehrtägiger Zeitraum? ("bis DD.MM.YYYY" oder "bis DD.MM.")
            bis_match = re.search(r"bis\s+(\d{1,2})\.(\d{1,2})\.(\d{4})?", text)
            if bis_match:
                bis_tag = int(bis_match.group(1))
                bis_mon = int(bis_match.group(2))
                bis_jahr_str = bis_match.group(3)
                bis_jahr = int(bis_jahr_str) if bis_jahr_str else eintrag_datum.year
                try:
                    bis_datum = dt.date(bis_jahr, bis_mon, bis_tag)
                    if eintrag_datum <= zieltag <= bis_datum:
                        return True
                except ValueError:
                    pass
 
        return False
 
    except Exception as e:
        print(f"[WARN] Kalender-Fetch fehlgeschlagen: {e}")
        return False
 
# =========================
# VERTRETUNGSPLAN PARSER
# =========================
 
def get_ausfaelle(datum):
    """
    Gibt Set von Stunden zurück die für MEINE_KLASSE ausfallen.
    Frei-Arten: Selbst, Entfall, Freisetzung
    """
    try:
        s = get_session()
        html = s.get(VERTRETUNG_URL, timeout=15).text
        soup = BeautifulSoup(html, "html.parser")
 
        vplan_id = datum.strftime("%Y-%m-%d")
        tag_div = soup.find("div", {"vplan-id": vplan_id})
 
        if not tag_div:
            print(f"[INFO] Kein Vplan-Div für {vplan_id}")
            return set()
 
        # "Keine relevanten Einträge" → leer
        kein_td = tag_div.find("td", {"colspan": "7"})
        if kein_td:
            return set()
 
        FREI_ARTEN = {"Selbst", "Entfall", "Freisetzung", "selbständiges Arbeiten"}
 
        ausfaelle = set()
 
        for tr in tag_div.find_all("tr"):
            tds = tr.find_all("td")
            if len(tds) < 6:
                continue
 
            klasse = tds[0].text.strip()
            stunde_raw = tds[1].text.strip()
            art = tds[5].text.strip()
 
            # Nur meine Klasse
            if klasse != MEINE_KLASSE:
                continue
 
            # Nur Frei-Arten
            if art not in FREI_ARTEN:
                continue
 
            # Stunden parsen: "1", "3 - 4", "8 - 9"
            nums = re.findall(r"\d+", stunde_raw)
            for n in nums:
                ausfaelle.add(int(n))
 
        return ausfaelle
 
    except Exception as e:
        print(f"[ERROR] Vertretungsplan: {e}")
        return set()
 
# =========================
# FREE COUNT + SZENARIO
# =========================
 
def get_free_count(wochentag, ausfaelle):
    """
    Zählt konsekutive freie Stunden vom Anfang des Tages.
    Stoppt bei erster echter Stunde (Kurs != None, nicht in ausfaelle).
    """
    plan = STUNDENPLAN.get(wochentag, {})
 
    if not plan:
        return 0
 
    alle_stunden = sorted(plan.keys())
    free_count = 0
 
    for stunde in alle_stunden:
        kurs = plan[stunde]
 
        ist_fester_frei = (kurs is None)
        ist_ausfall = (kurs is not None) and (stunde in ausfaelle)
 
        if ist_fester_frei or ist_ausfall:
            free_count += 1
        else:
            break  # erste echte Stunde → Stopp
 
    return free_count
 
def szenario_str(free_count):
    mapping = {0: "normal", 1: "free1", 2: "free12", 3: "free123", 4: "free1234"}
    return mapping.get(free_count, "free1234")
 
# =========================
# HAUPTENDPOINT
# =========================
 
@app.route("/api/weckzeit")
def api_weckzeit():
    if request.args.get("token") != TOKEN:
        return jsonify({"error": "unauthorized"}), 403
 
    tag_param = request.args.get("tag", "morgen")
    if tag_param == "morgen":
        datum = dt.date.today() + timedelta(days=1)
    elif tag_param == "heute":
        datum = dt.date.today()
    else:
        try:
            datum = dt.date.fromisoformat(tag_param)
        except ValueError:
            return jsonify({"error": "ungültiges Datum"}), 400
 
    # 1. Wochenende
    if datum.weekday() >= 5:
        return jsonify({"wecker": False, "szenario": "wochenende", "datum": str(datum)})
 
    # 2. Feiertag
    feiertag = ist_feiertag(datum)
    if feiertag:
        return jsonify({"wecker": False, "szenario": "feiertag", "name": feiertag, "datum": str(datum)})
 
    # 3. Schulferien
    if ist_schulfrei(datum):
        return jsonify({"wecker": False, "szenario": "ferien", "datum": str(datum)})
 
    # 4. Vertretungsplan-Ausfälle
    ausfaelle = get_ausfaelle(datum)
 
    # 5. Freistunden berechnen
    wochentag = datum.weekday()  # 0=Mo..4=Fr
    free_count = get_free_count(wochentag, ausfaelle)
    szen = szenario_str(free_count)
    weckzeit = WECKZEITEN[szen]
 
    return jsonify({
        "wecker": True,
        "szenario": szen,
        "weckzeit": weckzeit,
        "ausfaelle": sorted(list(ausfaelle)),
        "free_count": free_count,
        "datum": str(datum)
    })
 
# =========================
# HEALTH CHECK
# =========================
 
@app.route("/")
def home():
    return "Smart Wake API OK"
 
if __name__ == "__main__":
    app.run()
