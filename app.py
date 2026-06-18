import os
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

# =========================
# SESSION (cached login)
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
        s.post(LOGIN_URL, data={
            "username": USER,
            "password": PASS
        }, timeout=15)
    except:
        pass

    _session = s
    _session_time = now
    return s

# =========================
# STUNDENZEITEN (REAL)
# =========================

START = {
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

def parse_time(h):
    return dt.datetime.strptime(h, "%H:%M")

# =========================
# FREE LOGIC
# =========================

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

def calc_alarm(first_hour):
    start = parse_time(START[first_hour])
    return (start - timedelta(minutes=85)).strftime("%H:%M")

# =========================
# VERTRETUNGSPLAN PARSER
# =========================

def get_ausfaelle(date):
    try:
        s = get_session()
        html = s.get(VERTRETUNG_URL, timeout=15).text
        soup = BeautifulSoup(html, "html.parser")

        tag = soup.find("div", {"vplan-id": date.strftime("%Y-%m-%d")})
        if not tag:
            return set()

        out = set()

        for tr in tag.find_all("tr"):
            tds = tr.find_all("td")
            if len(tds) < 6:
                continue

            klasse = tds[0].text.strip()
            stunde = tds[1].text.strip()
            art = tds[5].text.strip()

            # FIX: echte Klasse erkennen
            if not klasse.startswith("12"):
                continue

            if art not in ["Selbst", "Entfall", "Freisetzung"]:
                continue

            import re
            nums = re.findall(r"\d+", stunde)

            for n in nums:
                out.add(int(n))

        return out

    except Exception as e:
        print("VERTRETUNG ERROR:", e)
        return set()

# =========================
# FREE COUNT LOGIC
# =========================

def get_free_count(ausfaelle):
    return len([x for x in ausfaelle if x <= 4])

# =========================
# MAIN API
# =========================

@app.route("/api/weckzeit")
def api():
    if request.args.get("token") != TOKEN:
        return jsonify({"error": "unauthorized"}), 403

    date = dt.date.today() + timedelta(days=1)

    # weekend
    if date.weekday() >= 5:
        return jsonify({"wecker": False, "szenario": "wochenende"})

    ausfaelle = get_ausfaelle(date)

    free = get_free_count(ausfaelle)
    szen = scenario(free)

    first_hour = free + 1
    if first_hour > 5:
        first_hour = 5

    weckzeit = calc_alarm(first_hour)

    return jsonify({
        "wecker": True,
        "szenario": szen,
        "ausfaelle": list(ausfaelle),
        "weckzeit": weckzeit
    })

# =========================
# DEBUG ROUTE
# =========================

@app.route("/")
def home():
    return "Smart Wake API OK"

# =========================
# RUN
# =========================

if __name__ == "__main__":
    app.run()
