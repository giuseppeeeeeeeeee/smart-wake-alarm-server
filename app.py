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

PORTAL_BASE = "https://portal.gym-oppenheim.de"
LOGIN_URL = PORTAL_BASE + "/index.php"
KALENDER_URL = PORTAL_BASE + "/kalender.php"
VERTRETUNG_URL = PORTAL_BASE + "/vertretungsplan"

API_TOKEN = os.getenv("API_TOKEN", "1_smartwake2026")

PORTAL_USER = os.getenv("SCHULPORTAL_USER")
PORTAL_PASS = os.getenv("SCHULPORTAL_PASS")

# =========================
# SESSION CACHE
# =========================

_session_cache = {
    "session": None,
    "time": None
}

def get_session():
    now = dt.datetime.now()
    if _session_cache["session"] and _session_cache["time"]:
        if (now - _session_cache["time"]).seconds < 1800:
            return _session_cache["session"]

    s = requests.Session()

    try:
        s.post(LOGIN_URL, data={
            "username": PORTAL_USER,
            "password": PORTAL_PASS
        }, timeout=15)
    except:
        pass

    _session_cache["session"] = s
    _session_cache["time"] = now
    return s

# =========================
# STUNDENPLAN (DEIN DUMP)
# =========================

def get_first_hour(day, free_count):
    base = {
        0: 1,  # Mo
        1: 1,  # Di
        2: 1,  # Mi
        3: 2,  # Do (Stunde 1 frei)
        4: 1   # Fr
    }
    return base.get(day, 1) + free_count


STARTZEITEN = {
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
    11: "16:00"
}

# =========================
# FEIERTAGE RLP
# =========================

FIX_FEIERTAGE = {
    (1, 1): "Neujahr",
    (5, 1): "Tag der Arbeit",
    (10, 3): "Tag der Deutschen Einheit",
    (12, 25): "Weihnachten",
    (12, 26): "2. Weihnachtstag"
}

def ostern(jahr):
    a = jahr % 19
    b = jahr // 100
    c = jahr % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19*a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    l = (32 + 2*e + 2*i - h - k) % 7
    m = (a + 11*h + 22*l) // 451
    monat = (h + l - 7*m + 114) // 31
    tag = ((h + l - 7*m + 114) % 31) + 1
    return dt.date(jahr, monat, tag)

def bewegliche_feiertage(jahr):
    o = ostern(jahr)
    return {
        o - timedelta(days=2): "Karfreitag",
        o + timedelta(days=1): "Ostermontag",
        o + timedelta(days=39): "Christi Himmelfahrt",
        o + timedelta(days=50): "Pfingstmontag",
        o + timedelta(days=60): "Fronleichnam"
    }

def is_feiertag(date):
    if (date.month, date.day) in FIX_FEIERTAGE:
        return True
    if date in bewegliche_feiertage(date.year):
        return True
    return False

# =========================
# KALENDER (FERIEN)
# =========================

def is_ferien(tag):
    try:
        s = get_session()
        html = s.get(KALENDER_URL, timeout=15).text
        return "Ferien" in html
    except:
        return False

# =========================
# VERTRETUNGSPLAN
# =========================

def parse_ausfaelle(tag):
    try:
        s = get_session()
        html = s.get(VERTRETUNG_URL, timeout=15).text
        soup = BeautifulSoup(html, "html.parser")

        div = soup.find("div", {"vplan-id": tag.strftime("%Y-%m-%d")})
        if not div:
            return set()

        ausfaelle = set()

        for tr in div.find_all("tr"):
            tds = tr.find_all("td")
            if len(tds) < 6:
                continue

            klasse = tds[0].text.strip()
            stunde = tds[1].text.strip()
            art = tds[5].text.strip()

            if klasse != "12":
                continue

            if art not in ["Selbst", "Entfall", "Freisetzung"]:
                continue

            m = re.findall(r"\d+", stunde)
            for x in m:
                ausfaelle.add(int(x))

        return ausfaelle
    except:
        return set()

# =========================
# SZEENARIO LOGIK
# =========================

def get_free_count(day, ausfaelle):
    return len([x for x in ausfaelle if x <= 4])

def scenario(free):
    if free == 0:
        return "normal"
    if free == 1:
        return "free1"
    if free == 2:
        return "free12"
    if free == 3:
        return "free123"
    return "free1234"

# =========================
# WECKER
# =========================

def calc_alarm(day, free):
    first = get_first_hour(day, free)
    start = dt.datetime.strptime(STARTZEITEN[first], "%H:%M")
    alarm = start - timedelta(minutes=85)
    return alarm.strftime("%H:%M")

# =========================
# API
# =========================

@app.route("/api/weckzeit")
def api():
    token = request.args.get("token")
    if token != API_TOKEN:
        return jsonify({"error": "unauthorized"}), 403

    tag = dt.date.today() + timedelta(days=1)

    if tag.weekday() >= 5:
        return jsonify({"wecker": False, "szenario": "wochenende"})

    if is_feiertag(tag):
        return jsonify({"wecker": False, "szenario": "feiertag"})

    if is_ferien(tag):
        return jsonify({"wecker": False, "szenario": "ferien"})

    ausfaelle = parse_ausfaelle(tag)
    free = get_free_count(tag.weekday(), ausfaelle)

    szen = scenario(free)
    weck = calc_alarm(tag.weekday(), free)

    return jsonify({
        "wecker": True,
        "szenario": szen,
        "weckzeit": weck
    })

@app.route("/")
def home():
    return "Smart Wake Alarm API running"

# =========================
# RUN
# =========================

if __name__ == "__main__":
    app.run()
