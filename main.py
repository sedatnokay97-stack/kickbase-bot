import os
import sys
import re
import json
import time
import base64
import requests
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, date, timedelta
from bs4 import BeautifulSoup

KB_EMAIL = os.environ.get("KB_EMAIL")
KB_PASSWORD = os.environ.get("KB_PASSWORD")
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN")
GITHUB_REPOSITORY = os.environ.get("GITHUB_REPOSITORY")
ODDS_API_KEY = os.environ.get("ODDS_API_KEY")

CONFIG = {
    "DRY_RUN": False,
    "TELEGRAM_MAX_LEN": 4000,
    "REQUEST_TIMEOUT": 15,
    "DAYS_UNTIL_MATCHDAY": 11,
    "OVERPAY_BASE_FACTOR": 0.60,
    "HISTORY_FILE": "history.json",
    # 8 Tage reichen fuer die Trendberechnung, aber Spieler rotieren etwa alle
    # 2-3 Wochen durch den Markt. Mit 25 Tagen bleibt der Verlauf ueber einen
    # kompletten Zyklus erhalten - wichtig fuer Vergleiche "wie war sein Wert,
    # als er das letzte Mal auf dem Markt war" und als Basis fuer spaeteres ML.
    "MV_HISTORY_DAYS": 25,
    # Rotierender Crawler: so viele zusaetzliche Spieler pro Lauf auffrischen.
    # Bei 3 Laeufen/Tag sind ~500 Spieler in rund 3-4 Tagen einmal komplett
    # durch, danach laufende Auffrischung. Auf 0 setzen zum Deaktivieren.
    "CRAWL_BATCH_SIZE": 50,
    "CRAWL_SLEEP": 0.2,
    "MAX_PER_TEAM": 2,
    "OPPONENT_FETCH_SLEEP": 0.3,
    "SEASON_YEAR": 2026,
    "FIXTURE_LOOKAHEAD": 3,
    "TOP_DETAIL_COUNT": 10,
    "MIN_PROFIT_FOR_TOP": 300_000,
    "MIN_TEAMWERT_STATUS": 2,
    "MAX_PER_TEAM_EARLY": 3,
    "MAX_PER_TEAM_FROM_MATCHDAY": 3,
    "NEWS_MAX_PER_PLAYER": 2,
    "FEED_DEBUG_DUMP": False,
    "RV_CONFIDENCE_FULL_AT_MATCHDAY": 4,
    "RV_CONFIDENCE_BASE": 0.5,
    "AVG_POINTS_PLAUSIBILITY_MAX": 320,
    # Hoechstens so viel Prozent des Marktwerts pro Tag - alles darueber ist
    # eine Datenluecke, kein Trend.
    "MAX_DAILY_TREND_PCT": 0.05,
    # So viele Tage muss der Bot einen Spieler selbst beobachtet haben,
    # bevor er dessen Trend fuer eine Prognose nutzt.
    "MIN_OBSERVED_DAYS": 3,
    # So viele Tage muss Kickbase selbst einen Spieler kennen, damit sein
    # Trend als belastbar gilt. Darunter: echter Neuzugang.
    "MIN_KB_HISTORY_DAYS": 30,
    # Einstiegswert, den Kickbase neuen Spielern gibt. Eine Jahresspanne, die
    # dort beginnt, sagt nichts ueber "teuer oder guenstig" aus.
    "KB_ENTRY_VALUE": 500_000,
    # Ab dieser Spreizung (Hoch/Tief) ist die Spanne ein Aufstiegsverlauf,
    # kein Preisniveau.
    "MAX_MEANINGFUL_RANGE_RATIO": 6.0,
    # Unterhalb dieses Marktwerts gilt ein Spieler als "Aufsteiger vom Boden":
    # dort beschleunigt der Anstieg, statt abzuflachen.
    "LOW_MV_THRESHOLD": 1_500_000,
    "LOW_MV_GROWTH": 1.15,      # taegliche Beschleunigung statt Daempfung
    "LOW_MV_MAX_ACCEL": 3.0,    # hoechstens das 3-fache des Ausgangstrends
    "MAX_TOTAL_GROWTH": 1.5,    # hoechstens +150% Gesamtanstieg in der Prognose
    # Wird zur Laufzeit automatisch auf True gesetzt, sobald die Feldzuordnung
    # fuer die Einsatzzahl gegengeprueft und bestaetigt ist.
    "USE_APPEARANCE_WEIGHT": False,
    # Sucht einmalig den Endpunkt fuer den historischen Marktwert-Verlauf.
    # Auf False setzen, sobald er gefunden und angebunden ist.
    "DISCOVER_MV_ENDPOINT": False,
    # Einmalige Strukturanalyse des /performance-Endpunkts, um die echten
    # Feldnamen fuer Einsaetze und Saisonpunkte zu finden.
    "DIAGNOSE_PERFORMANCE": False,
    # Einmalige Suche nach der vollstaendigen Transfer-Historie pro Manager.
    # Der Liga-Feed ist zu kurz - dadurch ist die Cash-Schaetzung unvollstaendig.
    "DISCOVER_MANAGER_TRANSFERS": False,
    # Sucht einen Endpunkt fuer den Kontostand der Mitspieler. Geprueft wird
    # gegen den eigenen, exakt bekannten Kontostand.
    # Suche abgeschlossen: Kickbase liefert den Kontostand der Mitspieler
    # ueber keinen erreichbaren Endpunkt. Bleibt aus.
    "DISCOVER_BUDGET": False,
    # Anteil des Teamwerts, um den man ins Minus gehen darf (Kickbase-Regel).
    "OVERDRAFT_PCT": 0.33,
    # Sucht einmalig den Endpunkt fuer den historischen Marktwert-Verlauf
    # (die App zeigt bis zu 1 Jahr). Nach dem Fund abschaltbar.

    "PREDICTION_HIT_TOLERANCE": 0.5,
    "URGENCY_THRESHOLD_MINUTES": 180,
    "ODDS_SPORT": "soccer_germany_bundesliga",
    "ODDS_REGION": "eu",
    "ODDS_MARKET": "h2h",
    "MATCHUP_FACTOR_STRONG": 1.30,
    "MATCHUP_FACTOR_NEUTRAL": 1.00,
    "MATCHUP_FACTOR_WEAK": 0.75,
    "MOMENTUM_BOOST": 1.25,
    "MOMENTUM_BRAKE": 0.80,
}

NEWS_FEEDS = [
    "https://newsfeed.kicker.de/news/bundesliga",
    "https://www.sportschau.de/fussball/bundesliga/index~rss2.xml",
    "https://feeds.t-online.de/rss/transfermarkt",
]

LIGAINSIDER_INJURY_URLS = [
    "https://www.ligainsider.de/bundesliga/verletzte-und-gesperrte-spieler/",
    "https://www.ligainsider.de/verletzungen-sperren/",
]

BROWSER_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"),
    "Accept-Language": "de-DE,de;q=0.9",
}

NEGATIVE_NEWS_PATTERN = (r"(verletz|muskul|ausfall|gesperrt|rote karte|kreuzband|"
                          r"bruch|reha|op\b|operation|trainingsr(ü|ue)ckstand|"
                          r"f(ä|ae)llt aus|bank)")

KICKBASE_TID_TO_TEAM = {
    "2": "bayern", "3": "dortmund", "4": "frankfurt", "5": "freiburg",
    "6": "hamburg", "7": "leverkusen", "8": "schalke", "9": "stuttgart",
    "10": "werder", "13": "augsburg", "14": "hoffenheim", "15": "gladbach",
    "18": "mainz", "28": "koeln", "29": "paderborn", "40": "union berlin",
    "43": "leipzig", "77": "elversberg",
}

TEAM_DISPLAY_NAMES = {
    "bayern": "Bayern", "dortmund": "Dortmund", "leipzig": "Leipzig",
    "stuttgart": "Stuttgart", "hoffenheim": "Hoffenheim", "leverkusen": "Leverkusen",
    "freiburg": "Freiburg", "frankfurt": "Frankfurt", "augsburg": "Augsburg",
    "mainz": "Mainz", "union berlin": "Union", "gladbach": "Gladbach",
    "hamburg": "HSV", "koeln": "Köln", "werder": "Werder",
    "paderborn": "Paderborn", "schalke": "Schalke", "elversberg": "Elversberg",
}

BUNDESLIGA_TEAM_KEYS = list(TEAM_DISPLAY_NAMES.keys())
TEAM_STRENGTH_LAST_SEASON = {
    "bayern": 89, "dortmund": 73, "leipzig": 65, "stuttgart": 62,
    "hoffenheim": 61, "leverkusen": 59, "freiburg": 47, "frankfurt": 44,
    "augsburg": 43, "mainz": 40, "union berlin": 39, "gladbach": 38,
    "hamburg": 38, "koeln": 32, "werder": 32, "paderborn": 22,
    "schalke": 20, "elversberg": 20,
}

TEAM_STRENGTH_DEFAULT = 45
POSITION_NAMES = {1: "TW", 2: "ABW", 3: "MF", 4: "ANG"}
POSITION_MIN_NEEDED = {1: 1, 2: 3, 3: 3, 4: 1}

TEAMWERT_MIN_AVG_POINTS = {1: 25, 2: 30, 3: 35, 4: 40}
TEAMWERT_MIN_AVG_POINTS_DEFAULT = 30

def fmt_money(value):
    try:
        value = int(value)
    except (TypeError, ValueError):
        return "?"
    sign = "-" if value < 0 else ""
    v = abs(value)
    if v >= 1_000_000:
        return f"{sign}{v/1_000_000:.1f}".replace(".", ",") + " Mio"
    if v >= 1_000:
        return f"{sign}{v/1_000:.0f}k"
    return f"{sign}{v}"

def fmt_profit(value):
    if value is None:
        return "?"
    return ("+" + fmt_money(value)) if value > 0 else fmt_money(value)

def esc(value):
    return str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

def fmt_minutes(seconds):
    if seconds <= 0:
        return "abgelaufen"
    m = seconds // 60
    if m < 60:
        return f"{m} min"
    return f"{m//60}h {m%60}m"

def kickbase_login():
    print("🚀 Starte Kickbase Login mit Android-Header...")
    headers = {"User-Agent": "Kickbase/8.10.0 (Android)", "Content-Type": "application/json"}
    body = {"em": KB_EMAIL, "pass": KB_PASSWORD, "loy": False, "rep": {}}
    resp = requests.post("https://api.kickbase.com/v4/user/login", json=body, headers=headers)
    if resp.status_code != 200:
        raise ValueError(f"❌ Kickbase Login-Fehler (HTTP {resp.status_code}): {resp.text}")
    data = resp.json()
    token = data.get("tkn")
    league_id = data.get("lins", [{}])[0].get("i") if data.get("lins") else None
    if not token or not league_id:
        raise ValueError("❌ Kein Token oder keine Liga-ID erhalten.")
    user_obj = data.get("u", {}) or {}
    my_user_id = user_obj.get("id") or user_obj.get("i")
    my_manager_id = user_obj.get("mid") or my_user_id
    return token, str(league_id), my_manager_id

def _kb(token):
    return {"Authorization": f"Bearer {token}", "User-Agent": "Kickbase/8.10.0 (Android)",
            "Accept": "application/json"}

def get_market_players(token, league_id):
    url = f"https://api.kickbase.com/v4/leagues/{league_id}/market"
    resp = requests.get(url, headers=_kb(token), timeout=CONFIG["REQUEST_TIMEOUT"])
    resp.raise_for_status()
    data = resp.json()
    if "players" in data and data["players"]:
        return data["players"]
    for key, value in data.items():
        if isinstance(value, list) and value and isinstance(value[0], dict):
            if any(k in value[0] for k in ["id", "i", "n", "lastName", "mv"]):
                print(f"🔍 Markt im Ordner '{key}' gefunden.")
                return value
    print(f"⚠️ DEBUG RAW MARKTDATEN: {json.dumps(data)[:800]}")
    return []

SELLER_FIELD_CANDIDATES = ["u", "unm", "unn", "usm", "uid", "seller", "ownerId", "oid", "own"]

def get_seller_name(player):
    u = player.get("u")
    if isinstance(u, dict):
        return u.get("n") or u.get("name")
    if isinstance(u, str) and u.strip():
        return u.strip()
    for k in SELLER_FIELD_CANDIDATES[1:]:
        v = player.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return None

def is_free_market_player(player):
    return get_seller_name(player) is None

def get_lineup_prob(player):
    v = player.get("prob")
    try:
        f = float(v)
        return f if 0.0 <= f <= 1.0 else None
    except (TypeError, ValueError):
        return None

def diagnose_market_fields(players):
    pass

def get_my_budget(token, league_id):
    for path in ["me/budget", "me"]:
        try:
            resp = requests.get(f"https://api.kickbase.com/v4/leagues/{league_id}/{path}",
                                headers=_kb(token), timeout=CONFIG["REQUEST_TIMEOUT"])
            resp.raise_for_status()
            data = resp.json()
            for key in ["budget", "b", "amount"]:
                if key in data and data[key] is not None:
                    return int(data[key])
        except Exception as e:
            print(f"⚠️ Budget /{path}: {e}")
    return None

def get_ranking(token, league_id):
    try:
        resp = requests.get(f"https://api.kickbase.com/v4/leagues/{league_id}/ranking",
                            headers=_kb(token), timeout=CONFIG["REQUEST_TIMEOUT"])
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        print(f"⚠️ Rangliste nicht ladbar: {e}")
        return {}

# Felder, auf denen die Bewertung tatsaechlich beruht - mit Mindestquote.
# Faellt eines davon aus, rechnet der Bot still mit Standardwerten weiter.
# Genau das ist mehrfach passiert (prob lieferte "unbekannt", weil die Skala
# 1-5 statt 0-4 geht). Ein Waechter auf FEHLENDE Felder trifft die reale
# Ausfallart - anders als eine Meldung ueber neu hinzugekommene Felder.
CRITICAL_FIELDS = {
    "mv":   ("Marktwert",          0.90),
    "prob": ("Startelf-Status",    0.50),
    "pos":  ("Position",           0.80),
    "tid":  ("Vereins-ID",         0.80),
}

def check_field_coverage(players):
    """
    Prueft, bei wie viel Prozent der Spieler die tragenden Felder vorhanden
    sind. Rueckgabe: Liste von Warntexten (leer = alles in Ordnung).
    """
    if not players:
        return []
    warnungen = []
    gesamt = len(players)
    for feld, (bezeichnung, mindest) in CRITICAL_FIELDS.items():
        vorhanden = sum(1 for p in players
                        if isinstance(p, dict) and p.get(feld) not in (None, "", 0))
        quote = vorhanden / gesamt
        if quote < mindest:
            warnungen.append(
                f"{bezeichnung} nur bei {quote*100:.0f}% der Spieler "
                f"(erwartet ≥{mindest*100:.0f}%) — Feld '{feld}' prüfen")
    return warnungen


def extract_team_values(ranking_res):
    """
    Liest den Teamwert je Manager aus der Rangliste.

    Das Feld "tv" steht dort fuer jeden Teilnehmer bereit (verifiziert:
    eigener Eintrag zeigte tv = 153.524.571). Kein Zusatzabruf noetig -
    die Rangliste wird ohnehin bei jedem Lauf geholt.
    """
    werte = {}
    if not isinstance(ranking_res, dict):
        return werte
    for value in ranking_res.values():
        if not (isinstance(value, list) and value and isinstance(value[0], dict)):
            continue
        for item in value:
            uid = item.get("mid") or item.get("id") or item.get("i")
            tv = item.get("tv")
            if uid and isinstance(tv, (int, float)) and tv > 0:
                werte[str(uid)] = int(tv)
        if werte:
            break
    return werte


def effective_buying_power(budget, team_value):
    """
    Tatsaechliche Kaufkraft = Kontostand + erlaubter Ueberziehungsrahmen.

    Kickbase erlaubt es, bis zu 33% des aktuellen Teamwerts ins Minus zu
    gehen. Ohne diese Regel meldet der Bot bei negativem Kontostand alles
    als "ueber Budget" - im letzten Report waren das 13 Spieler, obwohl
    real noch 26 Mio Spielraum bestanden.
    """
    if budget is None:
        return None, None
    if not team_value:
        return budget, None          # ohne Teamwert bleibt es beim Kontostand
    rahmen = int(team_value * CONFIG["OVERDRAFT_PCT"])
    return budget + rahmen, rahmen


def extract_league_users(ranking_res):
    if not isinstance(ranking_res, dict):
        return []
    id_keys = ["mid", "id", "i", "u", "uid"]
    name_keys = ["name", "n", "unm", "userName"]
    for key, value in ranking_res.items():
        if not (isinstance(value, list) and value and isinstance(value[0], dict)):
            continue
        users = []
        for item in value:
            uid = next((item[k] for k in id_keys if item.get(k)), None)
            uname = next((item[k] for k in name_keys
                          if isinstance(item.get(k), str) and item[k].strip()), None)
            if uid:
                users.append((str(uid), uname or "?"))
        if users:
            print(f"🔍 Liga-Teilnehmer im Ordner '{key}': {len(users)}")
            return users
    print("⚠️ Keine Teilnehmerliste in Rangliste. Rohstruktur:")
    print(json.dumps(ranking_res, ensure_ascii=False)[:600])
    return []

def get_squad(token, league_id, manager_id):
    try:
        resp = requests.get(
            f"https://api.kickbase.com/v4/leagues/{league_id}/managers/{manager_id}/squad",
            headers=_kb(token), timeout=CONFIG["REQUEST_TIMEOUT"])
        resp.raise_for_status()
        return resp.json().get("it", [])
    except Exception:
        return []

MV_EPOCH = date(1970, 1, 1)

def fetch_mv_history(token, league_id, player_id, days=365):
    """
    Holt den historischen Marktwert-Verlauf.

    Endpunkt aus Live-Daten bestaetigt:
      leagues/{liga}/players/{id}/marketvalue/365
      -> {"it": [{"dt": 20320, "mv": 1191709.0}, ...]}
    "dt" sind Tage seit dem 01.01.1970 (verifiziert: dt=20320 -> 2025-08-20,
    die Liste endet tagesgenau gestern).

    Rueckgabe: Liste von (datum, marktwert), aelteste zuerst. Leer bei Fehler.
    """
    try:
        r = requests.get(
            f"https://api.kickbase.com/v4/leagues/{league_id}/players/{player_id}/marketvalue/{days}",
            headers=_kb(token), timeout=CONFIG["REQUEST_TIMEOUT"])
        if r.status_code != 200:
            return []
        items = r.json().get("it")
        if not isinstance(items, list):
            return []
    except Exception:
        return []

    reihe = []
    for e in items:
        if not isinstance(e, dict):
            continue
        dt, mv = e.get("dt"), e.get("mv")
        if not isinstance(dt, (int, float)) or not isinstance(mv, (int, float)) or mv <= 0:
            continue
        reihe.append((MV_EPOCH + timedelta(days=int(dt)), int(mv)))
    reihe.sort(key=lambda x: x[0])
    return reihe


def range_position(reihe, current_mv):
    """
    Wo liegt der aktuelle Marktwert innerhalb der historischen Spanne?
      0.0 = am Tiefstwert, 1.0 = am Hoechstwert

    Beantwortet die Frage, die der Tagestrend nicht beantwortet: "steigt" ist
    etwas anderes als "steht schon nahe am Jahreshoch". Genau der Unterschied,
    der bei einem teuren Spieler ueber Gewinn oder Verlust entscheidet.

    Rueckgabe: (position, tief, hoch) oder (None, None, None)
    """
    if not reihe or len(reihe) < 30 or not current_mv:
        return None, None, None
    werte = [mv for _, mv in reihe]
    tief, hoch = min(werte), max(werte)
    if hoch <= tief:
        return None, tief, hoch
    pos = (current_mv - tief) / (hoch - tief)
    return max(0.0, min(1.0, pos)), tief, hoch


def is_meaningful_range(tief, hoch):
    """
    Ist die Jahresspanne ueberhaupt aussagekraeftig?

    Kickbase setzt neue Spieler auf einen Einstiegswert von 500k. Wer seitdem
    nur gestiegen ist, hat zwangsläufig Tief = 500k und steht automatisch bei
    100% der Spanne. Die Meldung "nahe Jahreshoch" bedeutet dann NICHT
    "teuer", sondern nur "war noch nie hoeher, weil er gerade erst anfing" -
    also das Gegenteil der beabsichtigten Warnung.

    Im letzten Report traf das auf 6 von 9 Kandidaten zu (Dzeko, Schwolow,
    Guether ... alle mit Tief = 500k). Eine Warnung, die fast immer feuert,
    unterscheidet nichts mehr.
    """
    if not tief or not hoch or hoch <= tief:
        return False
    if tief <= CONFIG["KB_ENTRY_VALUE"] * 1.05:
        return False        # Spanne beginnt am Einstiegswert -> Artefakt
    if hoch / tief > CONFIG["MAX_MEANINGFUL_RANGE_RATIO"]:
        return False        # extreme Spreizung: Aufsteiger, kein Preisniveau
    return True


# Einstiegswert, den Kickbase neuen Spielern gibt. Eine Jahresspanne, die
# genau dort beginnt, ist kein echter Kursverlauf, sondern der Weg eines
# Spielers von seinem Startwert nach oben.
KB_FLOOR_MV = 500_000


def range_is_meaningful(tief, hoch):
    """
    Ist die Jahresspanne ueberhaupt aussagekraeftig?

    Nein, wenn sie am Kickbase-Einstiegswert (500k) beginnt: Solche Spieler
    sind seit ihrem Eintritt nur gestiegen, ihr aktueller Wert liegt also
    zwangslaeufig beim Jahreshoch. Die Meldung "nahe Jahreshoch" hiesse dann
    nicht "teuer", sondern "war noch nie hoeher, weil er gerade erst
    angefangen hat" - das Gegenteil der beabsichtigten Warnung.

    Beobachtet bei Dzeko (500k-9,7 Mio), Schwolow (500k-5,7 Mio) und
    Guether (500k-2,1 Mio): alle bei 100%, alle ohne Aussagewert.
    """
    if not tief or not hoch:
        return False
    return tief > KB_FLOOR_MV * 1.05


def range_position_factor(pos):
    """
    Uebersetzt die Lage in der Jahresspanne in einen Overpay-Faktor.

    Bewusst nur EINE Regel, keine Feinabstufung: Wer nahe am Jahreshoch
    kauft, hat wenig Luft nach oben und viel Fallhoehe.
      > 0.75 -> 0.85 (teuer, Abschlag)
      < 0.35 -> 1.10 (guenstig im historischen Vergleich)
      sonst  -> 1.00
    """
    if pos is None:
        return 1.0
    if pos > 0.75:
        return 0.85
    if pos < 0.35:
        return 1.10
    return 1.0


def get_cached_mv_range(token, league_id, player_id, current_mv, history, today_str):
    """
    Liefert (position, tief, hoch) und holt den Verlauf hoechstens einmal
    pro Spieler und Tag - 365 Eintraege bei jedem Lauf neu zu ziehen waere
    unnoetige Last, da sich der Verlauf nur einmal taeglich aendert.
    """
    cache = history.setdefault("_mvrange", {})
    pid = str(player_id)
    eintrag = cache.get(pid)
    if eintrag and eintrag.get("d") == today_str:
        tief, hoch = eintrag.get("lo"), eintrag.get("hi")
    else:
        reihe = fetch_mv_history(token, league_id, player_id)
        if len(reihe) < 30:
            # Sehr kurze Historie = echter Neuzugang. Laenge trotzdem melden.
            return None, None, None, len(reihe)
        werte = [mv for _, mv in reihe]
        tief, hoch = min(werte), max(werte)
        cache[pid] = {"d": today_str, "lo": tief, "hi": hoch, "n": len(reihe)}
        time.sleep(0.15)

    # Laenge der Kickbase-Historie mitgeben: Sie sagt, wie lange KICKBASE den
    # Spieler kennt - im Gegensatz zu unserer eigenen Historie, die nur zeigt,
    # wie lange WIR ihn beobachten. Ein HSV-Torwart mit 33 Einsaetzen hat
    # 365 Eintraege, ein echter Neuzugang deutlich weniger.
    hist_len = (cache.get(pid) or {}).get("n")
    if not is_meaningful_range(tief, hoch):
        # Spanne unbrauchbar (beginnt am 500k-Einstiegswert oder ist extrem
        # gespreizt) - lieber keine Aussage als eine irrefuehrende.
        return None, tief, hoch, hist_len
    pos = max(0.0, min(1.0, (current_mv - tief) / (hoch - tief)))
    return pos, tief, hoch, hist_len


def fetch_season_stats(token, league_id, player_id, history=None, today_str=None):
    """
    Holt Einsaetze und Punkte der letzten abgeschlossenen Saison aus
    /performance - dem einzigen Ort, wo diese Werte belastbar stehen.

    Struktur (aus Live-Daten verifiziert):
      {"it": [{"sid": "11", "ti": "2018/2019", "ph": [
          {"day": 1, "p": 41, "mp": "90'", "ap": 57, "tp": 746}, ...]}]}
      p  = Punkte an diesem Spieltag (fehlt, wenn nicht gespielt)
      ap = Saison-Durchschnitt, tp = Saison-Gesamtpunkte

    Einsaetze werden EXAKT gezaehlt (Eintraege mit "p"), nicht geschaetzt.
    Die letzte Saison in der Liste ist die laufende und meist leer - genommen
    wird die letzte mit tatsaechlichen Punkten.

    Rueckgabe: {"appearances", "total_points", "avg_points", "season"} oder None
    """
    cache = history.setdefault("_seasonstats", {}) if history is not None else {}
    pid = str(player_id)
    zwischenspeicher = cache.get(pid)
    if zwischenspeicher and zwischenspeicher.get("d") == today_str:
        return zwischenspeicher.get("v")

    try:
        r = requests.get(
            f"https://api.kickbase.com/v4/leagues/{league_id}/players/{player_id}/performance",
            headers=_kb(token), timeout=CONFIG["REQUEST_TIMEOUT"])
        if r.status_code != 200:
            return None
        saisons = r.json().get("it")
        if not isinstance(saisons, list):
            return None
    except Exception:
        return None

    ergebnis = None
    for saison in reversed(saisons):        # neueste zuerst
        if not isinstance(saison, dict):
            continue
        spieltage = saison.get("ph")
        if not isinstance(spieltage, list):
            continue
        punkte = [e.get("p") for e in spieltage
                  if isinstance(e, dict) and isinstance(e.get("p"), (int, float))]
        if not punkte:
            continue                        # laufende Saison ohne Spiele
        einsaetze = len(punkte)
        gesamt = int(sum(punkte))
        ergebnis = {
            "appearances": einsaetze,
            "total_points": gesamt,
            "avg_points": round(gesamt / einsaetze, 1),
            "season": saison.get("ti"),
        }
        break

    if history is not None and today_str:
        cache[pid] = {"d": today_str, "v": ergebnis}
    time.sleep(0.15)
    return ergebnis


def discover_manager_budget(token, league_id, my_manager_id, my_budget, users):
    """
    Sucht einen Endpunkt, der den Kontostand ANDERER Manager direkt liefert.

    WARUM: Die Cash-Formel (Startgeld + Boni - Kaeufe + Verkaeufe) ist
    richtig, aber der Liga-Feed zeigt nur die letzten ~13 Transfers der
    ganzen Liga. Bei schalkerboy77 fehlten dadurch 8 von 12 Kaeufen.
    Ablesen waere besser als rechnen.

    PRUEFSTEIN: Der eigene Kontostand ist ueber /me/budget exakt bekannt.
    Liefert ein Kandidat fuer die eigene ID genau diesen Wert, ist er
    bestaetigt - und wir koennen ihm auch fuer die Gegner trauen.
    """
    if my_budget is None:
        print("🔎 Budget-Suche uebersprungen: eigener Kontostand unbekannt")
        return None

    print(f"🔎 Suche Kontostand-Endpunkt (Pruefwert: eigener Stand = {fmt_money(my_budget)})")

    # 1) Steht der Wert vielleicht schon in der Rangliste?
    for uid, name in users:
        if str(uid) == str(my_manager_id):
            print(f"   Eigener Eintrag in der Rangliste ist '{name}' (ID {uid})")
            break

    # 2) Kandidaten-Adressen durchprobieren, geprueft gegen den eigenen Stand
    geld_keys = ["budget", "b", "amount", "cash", "bgt", "money"]
    kandidaten = [
        f"leagues/{league_id}/managers/{my_manager_id}/budget",
        f"leagues/{league_id}/managers/{my_manager_id}",
        f"leagues/{league_id}/users/{my_manager_id}/budget",
        f"leagues/{league_id}/managers/{my_manager_id}/dashboard",
        f"leagues/{league_id}/managers/{my_manager_id}/profile",
        f"leagues/{league_id}/managers/{my_manager_id}/stats",
    ]
    treffer = []
    for pfad in kandidaten:
        try:
            r = requests.get(f"https://api.kickbase.com/v4/{pfad}",
                             headers=_kb(token), timeout=CONFIG["REQUEST_TIMEOUT"])
            if r.status_code != 200:
                continue
            data = r.json()
            if not isinstance(data, dict):
                continue
            # Flach und eine Ebene tief nach Geldwerten suchen
            flach = dict(data)
            for k, v in list(data.items()):
                if isinstance(v, dict):
                    flach.update({f"{k}.{k2}": v2 for k2, v2 in v.items()})
            funde = {k: v for k, v in flach.items()
                     if any(g == k.split(".")[-1] for g in geld_keys)
                     and isinstance(v, (int, float))}
            if funde:
                passend = [k for k, v in funde.items() if abs(int(v) - my_budget) < 1000]
                status = "✅ STIMMT" if passend else "⚠️ Werte weichen ab"
                print(f"   {status} {pfad}: {funde}")
                if passend:
                    treffer.append((pfad, passend[0]))
            else:
                print(f"   ℹ️ {pfad}: HTTP 200, aber kein Geldfeld "
                      f"(Felder: {sorted(flach.keys())[:12]})")
        except Exception:
            continue
        time.sleep(0.2)

    if not treffer:
        print("   ❌ Kein Endpunkt liefert den bekannten Kontostand - "
              "Cash bleibt eine Schaetzung.")
    return treffer


def dump_ranking_structure(ranking_res, my_manager_id, my_budget):
    """
    Zeigt einen vollstaendigen Ranglisten-Eintrag. Falls Kickbase den
    Kontostand dort schon mitliefert, ersparen wir uns jeden Zusatzabruf.
    Der eigene Eintrag wird bevorzugt gezeigt, weil sein Kontostand bekannt
    ist und sich direkt vergleichen laesst.
    """
    if not isinstance(ranking_res, dict):
        return
    for key, value in ranking_res.items():
        if not (isinstance(value, list) and value and isinstance(value[0], dict)):
            continue
        eigener = next((x for x in value
                        if str(x.get("mid") or x.get("id") or x.get("i")) == str(my_manager_id)),
                       value[0])
        print(f"🔬 Ranglisten-Eintrag (Ordner '{key}', eigener Stand = {fmt_money(my_budget)}):")
        print(f"   {json.dumps(eigener, ensure_ascii=False)[:600]}")
        break


def discover_manager_transfers(token, league_id, manager_id, manager_name="?"):
    """
    Sucht den Endpunkt fuer die vollstaendige Transfer-Historie eines Managers.

    WARUM: Der Liga-Feed ist ein rollierendes Fenster (~13 Eintraege fuer die
    ganze Liga). Bei schalkerboy77 zaehlte der Bot dadurch 4 von 12 Kaeufen
    und 2 von 11 Verkaeufen - die Cash-Schaetzung lag 45,7 Mio daneben.
    Die App zeigt die vollstaendige Liste, es muss also einen Endpunkt geben.
    """
    kandidaten = [
        f"leagues/{league_id}/managers/{manager_id}/transfers",
        f"leagues/{league_id}/managers/{manager_id}/transferhistory",
        f"leagues/{league_id}/managers/{manager_id}/activities",
        f"leagues/{league_id}/managers/{manager_id}/dashboard",
        f"leagues/{league_id}/users/{manager_id}/transfers",
        f"leagues/{league_id}/users/{manager_id}/activities",
        f"leagues/{league_id}/managers/{manager_id}/performance",
    ]
    print(f"🔎 Suche Transfer-Historie fuer '{manager_name}' (ID {manager_id}) ...")
    treffer = []
    for pfad in kandidaten:
        try:
            r = requests.get(f"https://api.kickbase.com/v4/{pfad}",
                             headers=_kb(token), timeout=CONFIG["REQUEST_TIMEOUT"])
            if r.status_code != 200:
                continue
            data = r.json()
            groesste, schluessel = 0, None
            if isinstance(data, list):
                groesste, schluessel = len(data), "(Wurzel)"
            elif isinstance(data, dict):
                for k, v in data.items():
                    if isinstance(v, list) and len(v) > groesste:
                        groesste, schluessel = len(v), k
            print(f"   ✅ {pfad} -> HTTP 200, Liste '{schluessel}' mit {groesste} Eintraegen")
            if groesste >= 5:
                liste = data[schluessel] if schluessel != "(Wurzel)" else data
                print(f"      Beispiel: {json.dumps(liste[0], ensure_ascii=False)[:350]}")
                treffer.append((pfad, schluessel, groesste))
        except Exception:
            continue
        time.sleep(0.2)

    if not treffer:
        print("   ❌ Kein Endpunkt gefunden - Cash-Schaetzung bleibt unvollstaendig.")
    return treffer


def diagnose_performance_endpoint(token, league_id, player_id, player_name="?"):
    """
    Zeigt die Struktur des /performance-Endpunkts.

    Hintergrund: Die Kandidaten "smdc"/"stud" aus dem Detail-Endpunkt sind
    NICHT Einsaetze und Gesamtpunkte - die Gegenprobe ergab 355 statt 105.
    Der /performance-Endpunkt lieferte in der Suche 8 Eintraege (vermutlich
    Saisons) und ist damit der naechstliegende Ort fuer diese Werte.
    """
    try:
        r = requests.get(
            f"https://api.kickbase.com/v4/leagues/{league_id}/players/{player_id}/performance",
            headers=_kb(token), timeout=CONFIG["REQUEST_TIMEOUT"])
        if r.status_code != 200:
            print(f"🔬 /performance: HTTP {r.status_code}")
            return
        data = r.json()
    except Exception as e:
        print(f"🔬 /performance nicht abrufbar: {e}")
        return

    print(f"🔬 /performance fuer '{player_name}' - Struktur:")
    if isinstance(data, dict):
        print(f"   Wurzel-Felder: {sorted(data.keys())}")
    items = data.get("it") if isinstance(data, dict) else None
    if isinstance(items, list) and items:
        print(f"   'it' enthaelt {len(items)} Eintraege")
        print(f"   Erster Eintrag:  {json.dumps(items[0], ensure_ascii=False)[:400]}")
        if len(items) > 1:
            print(f"   Letzter Eintrag: {json.dumps(items[-1], ensure_ascii=False)[:400]}")


def get_player_detail(token, league_id, player_id):
    for path in [
        f"leagues/{league_id}/players/{player_id}",
        f"players/{player_id}",
    ]:
        try:
            resp = requests.get(f"https://api.kickbase.com/v4/{path}",
                                headers=_kb(token), timeout=CONFIG["REQUEST_TIMEOUT"])
            if resp.status_code == 200:
                return resp.json()
        except Exception:
            pass
    return None

def extract_player_stats(detail):
    if not detail:
        return {"daily_trend_api": None, "status_code": None, "mdsum": None,
                "mvt_flag": None, "avg_points": None,
                "appearances": None, "total_points": None}
    daily_trend_api = None
    v = detail.get("tfhmvt")
    if isinstance(v, (int, float)) and v != 0:
        daily_trend_api = int(v)
    status_code = None
    v = detail.get("prob")
    if isinstance(v, int) and 1 <= v <= 5:
        status_code = v
    mdsum = detail.get("mdsum")
    if not isinstance(mdsum, list):
        mdsum = None
    mvt_flag = detail.get("mvt")
    avg_points = None
    for apkey in ["ap", "stpkt", "avgPoints", "avp", "pts"]:
        v = detail.get(apkey)
        if isinstance(v, (int, float)) and v > 0:
            avg_points = float(v)
            break
    if avg_points is not None and avg_points > CONFIG["AVG_POINTS_PLAUSIBILITY_MAX"]:
        avg_points = None

    # Einsaetze und Gesamtpunkte der Vorsaison.
    # Ein Oe-Punkte-Wert aus 29 Spielen ist eine Aussage, einer aus 2 Spielen
    # Zufall - der Bot behandelte beide bisher gleich.
    # UNBESTAETIGT: "smdc"/"stud" sind Kandidaten aus dem Karius-Rohdump. Die
    # Zuordnung wird zur Laufzeit gegengeprueft (verify_appearance_mapping):
    # total_points / appearances muss ungefaehr den Oe-Punkten entsprechen.
    appearances = None
    for k in ("smdc", "mdc", "apps", "games", "ec"):
        v = detail.get(k)
        if isinstance(v, int) and 0 < v <= 40:
            appearances = v
            break
    total_points = None
    for k in ("stud", "tp", "totalPoints", "sp"):
        v = detail.get(k)
        if isinstance(v, (int, float)) and v > 0:
            total_points = int(v)
            break

    return {
        "daily_trend_api": daily_trend_api,
        "status_code": status_code,
        "mdsum": mdsum,
        "mvt_flag": mvt_flag,
        "avg_points": avg_points,
        "appearances": appearances,
        "total_points": total_points,
    }


def verify_appearance_mapping(samples, tolerance=0.15):
    """
    Gegenprobe fuer die Feldzuordnung: Stimmt "total_points / appearances"
    ungefaehr mit dem bekannten Oe-Punkte-Wert ueberein?

    samples: Liste von (avg_points, total_points, appearances)
    Rueckgabe: (bestaetigt, geprueft, treffer)

    Ohne diese Pruefung waere die Einsatz-Gewichtung reines Raten - die
    Feldnamen stammen aus einem einzigen Rohdaten-Dump.
    """
    checked = hits = 0
    for avg, total, apps in samples:
        if not (avg and total and apps):
            continue
        checked += 1
        computed = total / apps
        if avg > 0 and abs(computed - avg) / avg <= tolerance:
            hits += 1
    confirmed = checked >= 3 and hits / checked >= 0.7
    return confirmed, checked, hits


def appearance_confidence(appearances):
    """
    Wie belastbar ist ein Oe-Punkte-Wert, der auf N Einsaetzen beruht?
      >= 20 Einsaetze -> 1.00   10-19 -> 0.85   5-9 -> 0.65
      2-4  -> 0.40              0-1   -> 0.20
    Unbekannt -> 0.75 (vorsichtige Mitte: weder Bonus noch Ausschluss)
    """
    if appearances is None:
        return 0.75
    if appearances >= 20:
        return 1.0
    if appearances >= 10:
        return 0.85
    if appearances >= 5:
        return 0.65
    if appearances >= 2:
        return 0.40
    return 0.20

def count_by_team(items):
    c = {}
    for x in items:
        t = x.get("tid")
        if t:
            c[t] = c.get(t, 0) + 1
    return c

def count_by_pos(items):
    c = {}
    for x in items:
        try:
            p = int(x.get("pos", x.get("p", 0)))
        except (TypeError, ValueError):
            continue
        if p:
            c[p] = c.get(p, 0) + 1
    return c

def pos_gaps(pos_counts):
    return [f"{POSITION_NAMES.get(p, p)} ({pos_counts.get(p,0)}/{n})"
            for p, n in POSITION_MIN_NEEDED.items() if pos_counts.get(p, 0) < n]

def missing_position_codes(pos_counts):
    return [p for p, n in POSITION_MIN_NEEDED.items() if pos_counts.get(p, 0) < n]

def analyze_opponents(token, league_id, ranking_res, my_manager_id,
                      league_start_budget=None, history=None, today_str=None):
    """
    Rueckgabe: (blocked_teams, profiles, squads)
    Erkennt beim Kader-Abruf gleich Verkaeufe ueber die Differenz zum letzten
    gespeicherten Kaderstand - dafuer werden history und today_str gebraucht.
    """
    blocked, profiles, squads = {}, [], []
    total_sales = total_buys = 0
    for uid, name in extract_league_users(ranking_res):
        if str(uid) == str(my_manager_id):
            continue
        squad = get_squad(token, league_id, uid)
        if not squad:
            continue
        if not squads:          # nur beim ersten Kader ausgeben
            diagnose_squad_fields(squad, "Gegner-Kader")
        squads.append(squad)

        if history is not None and today_str:
            sales, buys = detect_sales(name, squad, history, today_str)
            if sales:
                total_sales += len(sales)
                summe = sum(s["value"] for s in sales)
                print(f"💸 {name}: {len(sales)} Verkauf/Verkäufe erkannt "
                      f"(~{fmt_money(summe)} geschätzt)")
            if buys:
                total_buys += len(buys)
                summe_b = sum(b["value"] for b in buys)
                print(f"🛒 {name}: {len(buys)} Kauf/Käufe erkannt "
                      f"(~{fmt_money(summe_b)} geschätzt)")

        for tid, cnt in count_by_team(squad).items():
            if cnt >= CONFIG["MAX_PER_TEAM"]:
                blocked.setdefault(tid, []).append(name)
        pc = count_by_pos(squad)
        profiles.append({
            "name": name, "squad_size": len(squad),
            "gaps": pos_gaps(pc),
            "missing_positions": missing_position_codes(pc),
        })
        time.sleep(CONFIG["OPPONENT_FETCH_SLEEP"])
    zusatz = []
    if total_sales:
        zusatz.append(f"{total_sales} Verkäufe")
    if total_buys:
        zusatz.append(f"{total_buys} Käufe")
    print(f"🕵️ Konkurrenten analysiert: {len(profiles)}"
          + (f" ({', '.join(zusatz)} in diesem Lauf erkannt)" if zusatz else ""))
    return blocked, profiles, squads

def get_my_team_counts(token, league_id, my_manager_id):
    if not my_manager_id:
        return {}
    return count_by_team(get_squad(token, league_id, my_manager_id))

def assess_competition(pos_code, opponent_profiles, cash_by_name, price_threshold):
    if pos_code is None:
        return []
    competitors = []
    for prof in opponent_profiles:
        if pos_code not in prof.get("missing_positions", []):
            continue
        cash_info = cash_by_name.get(prof["name"])
        min_cash = cash_info["min_cash"] if cash_info else None
        competitors.append((prof["name"], min_cash))
    competitors.sort(key=lambda x: (x[1] is None, -(x[1] or 0)))
    return competitors

def build_deep_analysis(p, my_gap_codes, competitors):
    """
    Bewusst zurueckhaltend: Vor Spieltag 1 haben praktisch alle Konkurrenten
    genug Cash, um jedes Gebot zu ueberbieten. Eine Warnung, die bei JEDEM
    Spieler steht, ist kein Signal mehr, sondern Rauschen - und lenkt vom
    Wesentlichen ab.

    Ausgegeben wird deshalb nur, was tatsaechlich unterscheidet:
      - eigene Kaderluecke (immer relevant)
      - unkontestierte Position (echte gute Nachricht)
      - Rivale, dessen Cash-Untergrenze bereits unter deinem Limit liegt
        (wird erst im Saisonverlauf relevant, wenn Konten leerer werden)
    Der Normalfall "Rivalen vorhanden, alle koennen zahlen" bleibt still.
    """
    parts = []
    pos_code = p.get("pos_code")
    pos_name = POSITION_NAMES.get(pos_code, pos_code)

    if pos_code is not None and pos_code in (my_gap_codes or []):
        parts.append(f"füllt deine eigene Lücke auf {pos_name}")

    max_bid = p.get("max_bid")
    if pos_code is not None:
        if not competitors:
            parts.append(f"✅ kein Rivale mit Bedarf auf {pos_name} — wenig Gegenwehr erwartet")
        elif max_bid is not None:
            # Nur melden, wenn ALLE Bedarfstraeger unter deinem Limit liegen -
            # dann ist es ein echter Vorteil und keine Standardaussage.
            tight = [(n, c) for n, c in competitors if c is not None and c < max_bid]
            if tight and len(tight) == len(competitors):
                names = ", ".join(n for n, _ in tight[:2])
                parts.append(
                    f"💡 alle Rivalen mit Bedarf auf {pos_name} liegen unter deinem Limit "
                    f"({names}) — Cash-Vorteil möglich"
                )
            # Standardfall: bewusst keine Ausgabe

    return " · ".join(parts) if parts else None

def get_league_feed(token, league_id):
    for path in ["activitiesFeed", "feed", "activity"]:
        try:
            resp = requests.get(f"https://api.kickbase.com/v4/leagues/{league_id}/{path}",
                                headers=_kb(token), timeout=CONFIG["REQUEST_TIMEOUT"])
            if resp.status_code != 200:
                continue
            data = resp.json()
            items = data if isinstance(data, list) else None
            if not items and isinstance(data, dict):
                for key, value in data.items():
                    if isinstance(value, list) and value and isinstance(value[0], dict):
                        items = value
                        print(f"🔍 Liga-Feed im Ordner '{key}'.")
                        break
            if items:
                if CONFIG["FEED_DEBUG_DUMP"]:
                    print("🔬 FEED-ROHSTRUKTUR:", json.dumps(items[0], ensure_ascii=False, indent=2)[:800])
                return items
        except Exception as e:
            print(f"⚠️ Feed /{path}: {e}")
    return []

def parse_feed(feed_items):
    """
    Liest Kaeufe UND Verkaeufe aus dem Feed.

    Struktur aus Live-Daten verifiziert (Typ 15 deckt beides ab):
      Kauf:    data.byr = Kaeufer,    data.pn = Spieler, data.trp = Preis
      Verkauf: data.slr = Verkaeufer, data.pn = Spieler, data.trp = Preis
    Ein Manager-zu-Manager-Transfer kann beide Felder tragen und wird dann
    als Kauf UND Verkauf gezaehlt - genau richtig, denn beide Konten bewegen sich.
    """
    transfers = []
    buyer_keys = ["byr", "buyerName", "bn", "userName"]
    seller_keys = ["slr", "sellerName", "sn"]
    player_keys = ["pn", "playerName", "pfn"]
    price_keys = ["trp", "price", "value", "mv"]
    for item in feed_items:
        if not isinstance(item, dict):
            continue
        flat = dict(item)
        for nk in ["data", "d", "meta"]:
            if isinstance(item.get(nk), dict):
                flat.update(item[nk])
        buyer = next((flat[k].strip() for k in buyer_keys
                      if isinstance(flat.get(k), str) and flat[k].strip()), None)
        seller = next((flat[k].strip() for k in seller_keys
                       if isinstance(flat.get(k), str) and flat[k].strip()), None)
        player = next((flat[k].strip() for k in player_keys
                       if isinstance(flat.get(k), str) and flat[k].strip()), None)
        price = next((int(flat[k]) for k in price_keys
                      if isinstance(flat.get(k), (int, float)) and flat[k] > 0), None)
        if not (player and price):
            continue
        base_id = str(item.get("i") or "")
        if buyer:
            transfers.append({
                "id": f"{base_id}|buy" if base_id else "",
                "kind": "buy", "manager": buyer,
                "player": player, "price": price, "date": item.get("dt"),
            })
        if seller:
            transfers.append({
                "id": f"{base_id}|sell" if base_id else "",
                "kind": "sell", "manager": seller,
                "player": player, "price": price, "date": item.get("dt"),
            })
    return transfers


# ==========================================
# MANUELL ERFASSTE TRANSFERS (aus der App)
# ==========================================
# Der Liga-Feed zeigt nur ~13 Eintraege fuer die gesamte Liga; alles Aeltere
# ist fuer den Bot unsichtbar. Bei El Meiers 95 fehlten dadurch 3 von 8
# Kaeufen und 11 von 12 Verkaeufen - die Cash-Schaetzung lag 32,3 Mio daneben
# (-24,6 statt +7,7 Mio).
#
# Diese Liste schliesst die Luecke fuer die Zeit VOR dem ersten Bot-Lauf.
# Format: (Manager, "buy"/"sell", Spieler, Betrag)
# Die Eintraege werden ueber einen stabilen Schluessel dedupliziert, koennen
# also gefahrlos mehrfach eingelesen werden. Ergaenzen ist jederzeit moeglich.
MANUAL_TRANSFERS = [
    # --- El Meiers 95 (Stand: 21.08.2026, vollstaendig aus der App) ---
    ("El Meiers 95", "buy",  "Matanović",   19_111_111),
    ("El Meiers 95", "buy",  "Kabak",       22_000_006),
    ("El Meiers 95", "buy",  "Ginter",      26_999_999),
    ("El Meiers 95", "buy",  "Machino",      3_333_330),
    ("El Meiers 95", "buy",  "Mwene",        8_000_020),
    ("El Meiers 95", "buy",  "Aouchiche",   15_111_111),
    ("El Meiers 95", "buy",  "Knauff",       4_600_001),
    ("El Meiers 95", "buy",  "Campbell",     5_300_090),
    ("El Meiers 95", "sell", "Thielmann",    7_231_793),
    ("El Meiers 95", "sell", "Poreba",      10_244_112),
    ("El Meiers 95", "sell", "Gendrey",      4_213_224),
    ("El Meiers 95", "sell", "Banks",        6_447_416),
    ("El Meiers 95", "sell", "Skarke",       5_304_068),
    ("El Meiers 95", "sell", "Uno",          3_171_707),
    ("El Meiers 95", "sell", "Al Dakhil",    3_776_624),
    ("El Meiers 95", "sell", "Rosenfelder",  3_945_406),
    ("El Meiers 95", "sell", "Njinmah",      5_837_031),
    ("El Meiers 95", "sell", "Vogt",         2_227_889),
    ("El Meiers 95", "sell", "Gomis",        4_632_742),
    ("El Meiers 95", "sell", "Reggiani",     4_538_029),

    # --- MR_2606 (Stand: 21.08.2026, vollstaendig aus der App) ---
    # Gegengeprueft ueber den Kader: 16 Spieler = 11 gekaufte + 5 aus dem
    # Startkader. Keine Luecke.
    ("MR_2606", "buy",  "Stange",       5_500_001),
    ("MR_2606", "buy",  "Schwolow",     5_900_001),
    ("MR_2606", "buy",  "Reis",         7_000_001),
    ("MR_2606", "buy",  "Lochoshvili", 11_000_001),
    ("MR_2606", "buy",  "Katic",       15_300_001),
    ("MR_2606", "buy",  "Moreira",     26_500_001),
    ("MR_2606", "buy",  "Hack",        11_200_001),
    ("MR_2606", "buy",  "Futkeu",      11_000_001),
    ("MR_2606", "buy",  "Remberg",     12_500_001),
    ("MR_2606", "buy",  "Gruda",       24_000_001),
    ("MR_2606", "buy",  "Oermann",      6_500_001),
    ("MR_2606", "buy",  "Köbbing",        500_001),
    ("MR_2606", "buy",  "Mickelson",    3_100_001),
    ("MR_2606", "buy",  "Deman",       11_000_001),
    ("MR_2606", "buy",  "Stiller",     37_000_001),
    ("MR_2606", "buy",  "Baumgartner", 20_000_001),
    ("MR_2606", "sell", "Schwolow",     5_817_322),
    ("MR_2606", "sell", "Oermann",      6_277_246),
    ("MR_2606", "sell", "Stiller",     37_027_842),
    ("MR_2606", "sell", "Sakar",        6_453_438),
    ("MR_2606", "sell", "Jovanovic",    6_137_990),
    ("MR_2606", "sell", "Millgramm",    6_748_792),
    ("MR_2606", "sell", "Deman",       10_459_311),
    ("MR_2606", "sell", "Weiser",       5_137_648),
    ("MR_2606", "sell", "Baumgartner", 19_871_019),
    ("MR_2606", "sell", "Siebeking",    2_749_854),
    ("MR_2606", "sell", "Widmer",       3_229_455),
    ("MR_2606", "sell", "Andrich",      8_999_397),
    ("MR_2606", "sell", "Bouanani",     1_708_594),
    ("MR_2606", "sell", "Wójcik",       6_821_948),
    ("MR_2606", "sell", "Nebel",        8_746_478),
]


def import_manual_transfers(history):
    """
    Traegt die manuell erfassten Transfers in die History ein.

    Schluessel: "manual|<manager>|<kind>|<spieler>" - stabil und unabhaengig
    von Feed-IDs. Ein Vorgang, den der Feed spaeter ebenfalls liefert, bekommt
    dort eine andere ID und wuerde doppelt zaehlen. Deshalb wird zusaetzlich
    geprueft, ob derselbe Manager/Spieler/Betrag schon vorliegt.

    Rueckgabe: Anzahl neu eingetragener Vorgaenge.
    """
    if not MANUAL_TRANSFERS:
        return 0
    store = history.setdefault("_transfers", {})

    # Bereits vorhandene Vorgaenge als Fingerabdruck sammeln
    vorhanden = set()
    for e in store.values():
        if not isinstance(e, dict):
            continue
        mgr = e.get("manager") or e.get("buyer")
        if mgr and e.get("player") and e.get("price"):
            vorhanden.add((mgr, e.get("kind", "buy"), e["player"], int(e["price"])))

    neu = 0
    for manager, kind, player, price in MANUAL_TRANSFERS:
        if (manager, kind, player, price) in vorhanden:
            continue
        key = f"manual|{manager}|{kind}|{player}"
        if key in store:
            continue
        store[key] = {"kind": kind, "manager": manager, "player": player,
                      "price": price, "date": None, "source": "manual"}
        vorhanden.add((manager, kind, player, price))
        neu += 1
    return neu


def merge_transfers_into_history(transfers, history):
    """
    Speichert Transfers dauerhaft, dedupliziert ueber die Feed-ID.
    Der Kickbase-Feed ist ein rollierendes Fenster - ohne Persistenz wuerden
    aeltere Kaeufe/Verkaeufe "vergessen" und die Cash-Schaetzung driftet.
    """
    store = history.setdefault("_transfers", {})
    added = 0
    for t in transfers:
        key = t.get("id") or f"{t['kind']}|{t['manager']}|{t['player']}|{t['price']}"
        if key in store:
            continue
        store[key] = {
            "kind": t.get("kind", "buy"),
            "manager": t["manager"], "player": t["player"],
            "price": t["price"], "date": t.get("date"),
        }
        added += 1
    return added


def summarize_transfers_from_history(history):
    """
    Kaeufe je Manager aus ALLEN gespeicherten Transfers.
    Rueckwaertskompatibel: alte Eintraege ohne "kind" gelten als Kauf und
    nutzen noch den alten Schluessel "buyer".
    """
    summary = {}
    for entry in history.get("_transfers", {}).values():
        if entry.get("kind", "buy") != "buy":
            continue
        mgr = entry.get("manager") or entry.get("buyer")
        price = entry.get("price")
        if not mgr or not isinstance(price, (int, float)):
            continue
        e = summary.setdefault(mgr, {"count": 0, "total": 0, "players": []})
        e["count"] += 1
        e["total"] += int(price)
        if len(e["players"]) < 3 and entry.get("player"):
            e["players"].append(entry["player"])
    return summary


def sales_from_feed_history(history):
    """
    Verkaufserloese je Manager aus den gespeicherten Feed-Transfers.
    Das sind ECHTE Preise - im Gegensatz zur Kader-Differenz-Schaetzung.
    """
    summary = {}
    for entry in history.get("_transfers", {}).values():
        if entry.get("kind") != "sell":
            continue
        mgr = entry.get("manager")
        price = entry.get("price")
        if not mgr or not isinstance(price, (int, float)):
            continue
        e = summary.setdefault(mgr, {"count": 0, "total": 0, "players": []})
        e["count"] += 1
        e["total"] += int(price)
        if len(e["players"]) < 3 and entry.get("player"):
            e["players"].append(entry["player"])
    return summary


def combine_sales(feed_sales, squad_sales):
    """
    Fuehrt beide Verkaufsquellen zusammen.
    Der Feed liefert echte Preise, ist aber ein rollierendes Fenster und kann
    Verkaeufe verpasst haben, die vor dem ersten Lauf lagen. Die Kader-Differenz
    faengt alles ab, schaetzt aber nur. Pro Manager wird der HOEHERE Wert
    genommen - beide Quellen zaehlen dieselben Verkaeufe, addieren waere
    doppelt gezaehlt.
    """
    combined = {}
    for name in set(feed_sales) | set(squad_sales):
        f = feed_sales.get(name, {"count": 0, "total": 0})
        s = squad_sales.get(name, {"count": 0, "total": 0})
        if f["total"] >= s["total"]:
            combined[name] = {"count": f["count"], "total": f["total"], "source": "Feed"}
        else:
            combined[name] = {"count": s["count"], "total": s["total"], "source": "Kader"}
    return combined


def summarize_transfers(transfers):
    summary = {}
    for t in transfers:
        e = summary.setdefault(t["buyer"], {"count": 0, "total": 0, "players": []})
        e["count"] += 1
        e["total"] += t["price"]
        if len(e["players"]) < 3:
            e["players"].append(t["player"])
    return summary


# ==========================================
# VERKAUFSERKENNUNG UEBER KADER-DIFFERENZ
# ==========================================
def migrate_transfer_store(history):
    """
    Bereinigt doppelte Transfer-Eintraege.

    HINTERGRUND: Als das Verkaufs-Parsing dazukam, aenderte sich das
    Schluesselschema von "<feedid>" auf "<feedid>|buy" bzw. "<feedid>|sell".
    Alte Eintraege wurden dadurch nicht mehr als Duplikat erkannt und
    derselbe Kauf zaehlte doppelt - das verfaelscht die Cash-Schaetzung
    massiv (bei MR_2606 waren es 69,4 statt 34,7 Mio).

    Diese Funktion vereinheitlicht alle Schluessel auf das neue Schema und
    entfernt Doppelungen. Laeuft bei jedem Start, ist aber nach der ersten
    Bereinigung wirkungslos.
    Rueckgabe: Anzahl entfernter Duplikate.
    """
    store = history.get("_transfers")
    if not isinstance(store, dict):
        return 0

    normalized = {}
    removed = 0
    for key, entry in store.items():
        if not isinstance(entry, dict):
            continue
        kind = entry.get("kind", "buy")
        manager = entry.get("manager") or entry.get("buyer")
        if not manager:
            continue
        # Manuell erfasste Eintraege haben bereits einen eindeutigen
        # Schluessel ("manual|<manager>|<kind>|<spieler>") und duerfen NICHT
        # auf "<basis>|<kind>" verkuerzt werden - sonst kollidieren alle
        # Kaeufe eines Managers auf "manual|buy" und ueberschreiben sich.
        if key.startswith("manual|"):
            canonical = key
        else:
            base = key.split("|")[0]
            canonical = f"{base}|{kind}"
        clean = {
            "kind": kind,
            "manager": manager,
            "player": entry.get("player"),
            "price": entry.get("price"),
            "date": entry.get("date"),
        }
        if entry.get("source"):
            clean["source"] = entry["source"]
        if canonical in normalized:
            removed += 1
            continue
        normalized[canonical] = clean

    history["_transfers"] = normalized
    return removed


def diagnose_squad_fields(squad, label="Kader"):
    """
    Zeigt einmalig die Feldnamen eines Kader-Eintrags.
    Noetig, weil snapshot_squad() bisher leere Schnappschuesse erzeugt hat -
    die erwarteten Felder (id/i + mv/m) passen offenbar nicht.
    """
    if not squad:
        print(f"🔬 {label}: leer")
        return
    first = squad[0]
    if isinstance(first, dict):
        print(f"🔬 {label}-Felder: {sorted(first.keys())}")
        probe = {k: first.get(k) for k in ("i", "id", "mv", "m", "n", "pos") if k in first}
        print(f"🔬 {label}-Beispielwerte: {probe}")


def snapshot_squad(squad):
    """
    Reduziert einen Kader auf {spieler_id: marktwert}.
    Breitere Feldnamen-Suche als zuvor - die enge Variante lieferte in der
    Praxis nur leere Schnappschuesse.
    """
    snap = {}
    id_keys = ("i", "id", "pi", "playerId")
    mv_keys = ("mv", "m", "cv", "marketValue")
    for p in squad:
        if not isinstance(p, dict):
            continue
        pid = next((p[k] for k in id_keys if p.get(k)), None)
        mv = next((p[k] for k in mv_keys
                   if isinstance(p.get(k), (int, float)) and p[k] > 0), None)
        if pid and mv:
            snap[str(pid)] = int(mv)
    return snap


def detect_sales(manager_name, current_squad, history, today_str):
    """
    Erkennt Verkaeufe, indem der aktuelle Kader mit dem letzten gespeicherten
    verglichen wird. Ein Spieler kann einen Kader nur durch Verkauf verlassen,
    daher gilt: verschwunden = verkauft.

    Der Erloes wird mit dem zuletzt bekannten Marktwert geschaetzt. Das ist
    eine Naeherung:
      - Verkauf an Kickbase  -> exakt der Marktwert
      - Verkauf an Mitmanager -> meist Marktwert + Aufschlag, also eher zu niedrig
    Die Schaetzung ist damit konservativ (unterschaetzt eher), was zur
    Logik der Cash-UNTERGRENZE passt.

    Rueckgabe: Liste erkannter Verkaeufe fuer diesen Lauf.
    """
    squads_store = history.setdefault("_squads", {})
    sales_store = history.setdefault("_sales", {})

    current_snap = snapshot_squad(current_squad)
    previous = squads_store.get(manager_name, {}).get("players", {})

    buys_store = history.setdefault("_squadbuys", {})

    new_sales, new_buys = [], []
    if previous:   # beim allerersten Lauf gibt es nichts zu vergleichen
        for pid, last_mv in previous.items():
            if pid not in current_snap:
                new_sales.append({"player_id": pid, "value": last_mv, "date": today_str})
        # NEU: Zugaenge im Kader = Kaeufe.
        #
        # Der Liga-Feed zeigt nur ~13 Eintraege fuer die ganze Liga; bei
        # schalkerboy77 fehlten dadurch 8 von 12 Kaeufen. Die Kader holen wir
        # ohnehin bei jedem Lauf - wer neu darin auftaucht, wurde gekauft.
        # Bewertet mit dem aktuellen Marktwert (Untergrenze: bei Kaeufen vom
        # Markt wird meist darueber geboten).
        for pid, mv_jetzt in current_snap.items():
            if pid not in previous:
                new_buys.append({"player_id": pid, "value": mv_jetzt, "date": today_str})

    if new_sales:
        entry = sales_store.setdefault(manager_name, {"count": 0, "total": 0})
        for s in new_sales:
            entry["count"] += 1
            entry["total"] += s["value"]

    if new_buys:
        entry = buys_store.setdefault(manager_name, {"count": 0, "total": 0})
        for b in new_buys:
            entry["count"] += 1
            entry["total"] += b["value"]

    # Aktuellen Stand fuer den naechsten Vergleich sichern
    squads_store[manager_name] = {"players": current_snap, "date": today_str}
    return new_sales, new_buys


def get_squadbuys_summary(history):
    """Kaeufe je Manager, erkannt ueber Kader-Zugaenge."""
    return history.get("_squadbuys", {})


def combine_buys(feed_buys, squad_buys):
    """
    Fuehrt Feed-Kaeufe und ueber den Kader erkannte Kaeufe zusammen.
    Beide erfassen dieselben Vorgaenge - der hoehere Wert gewinnt, addieren
    waere Doppelzaehlung. Der Feed hat echte Preise, die Kaderbeobachtung
    ist lueckenlos ab Beobachtungsbeginn.
    """
    combined = {}
    for name in set(feed_buys) | set(squad_buys):
        f = feed_buys.get(name, {"count": 0, "total": 0, "players": []})
        s = squad_buys.get(name, {"count": 0, "total": 0})
        if f["total"] >= s["total"]:
            combined[name] = {"count": f["count"], "total": f["total"],
                              "players": f.get("players", []), "source": "Feed"}
        else:
            combined[name] = {"count": s["count"], "total": s["total"],
                              "players": f.get("players", []), "source": "Kader"}
    return combined


def get_sales_summary(history):
    """Kumulierte Verkaufserloese je Manager aus dem Verlauf."""
    return history.get("_sales", {})


def diagnose_feed_types(feed_items, max_samples=4):
    """
    Schluesselt die vorkommenden Feed-Typen auf und zeigt je einen Beispiel-
    Eintrag. Damit laesst sich spaeter der exakte Verkaufs-Eintrag
    identifizieren und die Schaetzung durch echte Zahlen ersetzen.
    """
    by_type = {}
    for item in feed_items:
        if not isinstance(item, dict):
            continue
        t = item.get("t")
        if t not in by_type:
            by_type[t] = item
    print(f"🔬 FEED-TYPEN: {sorted(str(k) for k in by_type)}")
    for t, sample in list(by_type.items())[:max_samples]:
        data_keys = sorted((sample.get("data") or {}).keys()) if isinstance(sample.get("data"), dict) else []
        print(f"🔬   Typ {t}: data-Felder = {data_keys}")


# ==========================================
# ROTIERENDER CRAWLER
# ==========================================
def collect_known_player_ids(all_market, squads, history=None):
    """
    Sammelt alle bekannten Spieler-IDs aus:
      - aktuellem Transfermarkt
      - eigenem Kader + Kadern aller Gegner
      - ALLEN bereits im Verlauf gespeicherten Spielern

    Der letzte Punkt ist entscheidend: Spieler rotieren etwa alle 2-3 Wochen
    durch den Markt. Wer nur die aktuellen Quellen abfragt, verliert einen
    Spieler wieder aus dem Pool, sobald er den Markt verlaesst - sein Verlauf
    wuerde einfrieren, bis er Wochen spaeter zufaellig wieder auftaucht.
    Einmal gesehen = dauerhaft im Pool.
    """
    ids = set()
    for p in all_market:
        pid = p.get("id") or p.get("i")
        if pid:
            ids.add(str(pid))
    for squad in squads:
        for p in squad:
            pid = p.get("id") or p.get("i")
            if pid:
                ids.add(str(pid))
    # Bereits bekannte Spieler aus dem Verlauf uebernehmen (ohne Sonderschluessel
    # wie "_transfers", die keine Spieler sind)
    if history:
        for key in history:
            if not key.startswith("_"):
                ids.add(str(key))
    return ids


def pick_crawl_batch(known_ids, history, batch_size, today_str):
    """
    Waehlt die naechste Portion Spieler zum Auffrischen.

    Prioritaet: wer am laengsten nicht aktualisiert wurde, kommt zuerst.
    Spieler, die heute schon einen Messpunkt haben (z.B. weil sie am Markt
    stehen), werden uebersprungen - die sind ohnehin aktuell.
    """
    candidates = []
    for pid in known_ids:
        entry = history.get(pid)
        if not entry or not entry.get("days"):
            candidates.append((pid, ""))          # noch nie gesehen -> hoechste Prioritaet
            continue
        last_day = entry["days"][-1].get("d", "")
        if last_day == today_str:
            continue                               # heute bereits erfasst
        candidates.append((pid, last_day))
    # aelteste zuerst ("" sortiert vor jedem Datum)
    candidates.sort(key=lambda x: x[1])
    return [pid for pid, _ in candidates[:batch_size]]


def crawl_players(token, league_id, player_ids, history, today_str, sleep_s=0.2):
    """
    Holt fuer eine Portion Spieler die Detaildaten und schreibt den
    Marktwert in die History. Reine Datensammlung - diese Spieler
    erscheinen NICHT im Report, sie bauen nur den Verlauf auf.
    Rueckgabe: (erfolgreich, fehlgeschlagen)
    """
    ok, failed = 0, 0
    for pid in player_ids:
        try:
            detail = get_player_detail(token, league_id, pid)
            if not detail:
                failed += 1
                continue
            mv = detail.get("mv") or detail.get("cv")
            if isinstance(mv, (int, float)) and mv > 0:
                update_history(pid, int(mv), history, today_str)
                ok += 1
            else:
                failed += 1
        except Exception:
            failed += 1
        time.sleep(sleep_s)
    return ok, failed

# Saisonstart der Liga - Bezugspunkt fuer die kumulierte Auflaufpraemie.
LEAGUE_START_DATE = date(2026, 8, 14)
OPPONENT_START_CASH = 50_000_000

def cumulative_login_bonus(today=None):
    """
    Kumulierte Auflaufpraemie seit Saisonstart - PRO MANAGER, nicht pro Spieler.

    Kickbase zahlt taeglich eine Praemie, die mit jedem Login in Folge um
    10.000 EUR steigt und ab Tag 10 bei 100.000 EUR gedeckelt ist:
      Tag 1: 10k, Tag 2: 20k, ... Tag 9: 90k, ab Tag 10: 100k taeglich.

    Wir rechnen mit dem MAXIMUM (taeglicher Login ohne Unterbrechung). Wer
    Tage auslaesst, faellt auf der Staffel zurueck - das koennen wir nicht
    sehen. Der Wert ist damit eine OBERGRENZE.

    Vorher wurde dieser Betrag faelschlich mit der Kadergroesse multipliziert,
    was die Praemie um das Zehn- bis Fuenfzehnfache ueberschaetzte (bei
    MR_2606: 5,4 Mio statt 360k).
    """
    today = today or date.today()
    days = max(1, (today - LEAGUE_START_DATE).days + 1)

    if days <= 9:
        # Summe 10k + 20k + ... + days*10k
        return 10_000 * days * (days + 1) // 2

    # Tage 1-9 ergeben 450k, danach 100k pro Tag
    return 450_000 + (days - 9) * 100_000

def build_opponent_cash_tracker(opponent_profiles, transfer_summary, today=None,
                                 sales_summary=None):
    """
    Cash-Schaetzung: Startkapital + Login-Boni - Kaeufe + geschaetzte Verkaufserloese.

    Verkaufserloese stammen aus der Kader-Differenz (detect_sales) und sind mit
    dem letzten bekannten Marktwert bewertet. Bei Verkaeufen an Mitmanager liegt
    der echte Erloes meist hoeher, die Schaetzung bleibt also konservativ.
    """
    login_bonus_gesamt = cumulative_login_bonus(today)
    sales_summary = sales_summary or {}
    rows = []

    for profile in opponent_profiles:
        name = profile["name"]
        squad_size = profile["squad_size"]
        buys = (transfer_summary or {}).get(name, {"count": 0, "total": 0})
        sells = sales_summary.get(name, {"count": 0, "total": 0})
        # Praemie ist pro Manager - NICHT mit der Kadergroesse multiplizieren.
        login_bonus = login_bonus_gesamt
        min_cash = (OPPONENT_START_CASH + login_bonus
                    - buys["total"] + sells["total"])

        rows.append({
            "name": name,
            "squad_size": squad_size,
            "known_buy_count": buys["count"],
            "known_buy_total": buys["total"],
            "known_sell_count": sells["count"],
            "known_sell_total": sells["total"],
            "login_bonus": login_bonus,
            "min_cash": min_cash,
        })

    return rows

def print_cash_debug(cash_rows):
    print("\n💰 CASH DEBUG — Start 50 Mio + Auflaufprämie (max.) - Käufe + Verkäufe")

    for row in sorted(cash_rows, key=lambda item: item["min_cash"]):
        sell_part = ""
        if row.get("known_sell_count"):
            sell_part = (f" | Verkäufe +{fmt_money(row['known_sell_total'])} "
                         f"({row['known_sell_count']}×)")
        print(
            f"• {row['name']}: Cash ≈ {fmt_money(row['min_cash'])} "
            f"| Start 50,0 Mio "
            f"| Prämie +{fmt_money(row['login_bonus'])} "
            f"| Käufe -{fmt_money(row['known_buy_total'])} "
            f"({row['known_buy_count']}×)"
            f"{sell_part}"
        )

def get_bundesliga_odds():
    if not ODDS_API_KEY:
        print("ℹ️ Kein ODDS_API_KEY - Wettquoten-Feature inaktiv.")
        return {}
    try:
        url = "https://api.the-odds-api.com/v4/sports/{sport}/odds/".format(
            sport=CONFIG["ODDS_SPORT"])
        params = {
            "apiKey": ODDS_API_KEY,
            "regions": CONFIG["ODDS_REGION"],
            "markets": CONFIG["ODDS_MARKET"],
            "oddsFormat": "decimal",
        }
        resp = requests.get(url, params=params, timeout=CONFIG["REQUEST_TIMEOUT"])
        resp.raise_for_status()
        events = resp.json()
        win_prob = {}
        for event in events:
            for book in event.get("bookmakers", [])[:1]:
                for market in book.get("markets", []):
                    if market.get("key") != "h2h":
                        continue
                    for outcome in market.get("outcomes", []):
                        tname = outcome.get("name", "").lower()
                        odds = outcome.get("price", 0)
                        if odds > 1:
                            prob = 1.0 / odds
                            key = get_team_key(tname)
                            if key and key not in win_prob:
                                win_prob[key] = prob
        remaining = resp.headers.get("x-requests-remaining", "?")
        print(f"✅ Wettquoten geladen: {len(win_prob)} Teams ({remaining} API-Calls uebrig)")
        return win_prob
    except Exception as e:
        print(f"⚠️ Wettquoten nicht ladbar: {e}")
        return {}

def matchup_factor(win_prob):
    if win_prob is None:
        return 1.0
    if win_prob >= 0.75:
        return CONFIG["MATCHUP_FACTOR_STRONG"]
    if win_prob >= 0.40:
        return CONFIG["MATCHUP_FACTOR_NEUTRAL"]
    return CONFIG["MATCHUP_FACTOR_WEAK"]

def get_rss_headlines():
    headlines = []
    for url in NEWS_FEEDS:
        try:
            resp = requests.get(url, headers=BROWSER_HEADERS, timeout=CONFIG["REQUEST_TIMEOUT"])
            resp.raise_for_status()
            resp.encoding = "utf-8"
            root = ET.fromstring(resp.text)
            count = 0
            for item in root.findall(".//item"):
                title_el = item.find("title")
                if title_el is not None and title_el.text:
                    headlines.append(title_el.text.strip())
                    count += 1
            print(f"ℹ️ RSS: {count} Schlagzeilen von {url.split('/')[2]}")
        except Exception as e:
            print(f"⚠️ RSS {url}: {e}")
    seen, unique = set(), []
    for h in headlines:
        if h not in seen:
            seen.add(h)
            unique.append(h)
    return unique

def _try_urls(urls, extractor, label, diagnose=False):
    last_soup = None
    for url in urls:
        try:
            resp = requests.get(url, headers=BROWSER_HEADERS, timeout=CONFIG["REQUEST_TIMEOUT"])
            if resp.status_code != 200:
                print(f"⚠️ {label}: HTTP {resp.status_code} ({url})")
                continue
            resp.encoding = "utf-8"
            soup = BeautifulSoup(resp.text, "html.parser")
            last_soup = soup
            result = extractor(soup)
            if result:
                print(f"✅ {label}: {len(result)} Eintraege")
                return result
            print(f"⚠️ {label}: 0 Eintraege bei {url}")
        except Exception as e:
            print(f"⚠️ {label}: {e}")
    print(f"❌ {label}: keine URL lieferte Daten.")
    if diagnose and last_soup:
        hrefs = [a["href"] for a in last_soup.find_all("a", href=True)]
        interesting = [h for h in hrefs if any(w in h.lower()
                       for w in ["spieler", "aufstell", "lineup", "team"])]
        print(f"🔬 {label} Links: {interesting[:10]}")
    return set()

def get_ligainsider_injuries():
    def ex(soup):
        names = set()
        for link in soup.find_all("a", href=True):
            if "/spieler/" in link["href"]:
                n = link.get_text(strip=True)
                if n and len(n) > 2:
                    names.add(n.lower())
        if names:
            return names
        for tag in soup.find_all(["b", "strong"]):
            t = tag.get_text(strip=True)
            if t and 2 <= len(t.split()) <= 3 and not any(c.isdigit() for c in t):
                names.add(t.lower())
        return names
    return _try_urls(LIGAINSIDER_INJURY_URLS, ex, "LigaInsider Verletzte")

def name_in_set(lastname, collection):
    if not lastname or len(lastname) < 3 or not collection:
        return False
    pattern = re.compile(r"\b" + re.escape(lastname.lower()) + r"\b")
    return any(pattern.search(entry) for entry in collection)

def match_news(name, headlines):
    if not name or len(name) < 3:
        return []
    pattern = r"\b" + re.escape(name) + r"\b"
    return [h for h in headlines if re.search(pattern, h, re.IGNORECASE)]

def has_neg_news(matched):
    return any(re.search(NEGATIVE_NEWS_PATTERN, h, re.IGNORECASE) for h in matched)

def get_season_fixtures():
    url = f"https://api.openligadb.de/getmatchdata/bl1/{CONFIG['SEASON_YEAR']}"
    try:
        resp = requests.get(url, timeout=CONFIG["REQUEST_TIMEOUT"])
        resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, list) else []
    except Exception as e:
        print(f"⚠️ OpenLigaDB: {e}")
        return []

def get_team_key(name):
    if not name:
        return None
    n = name.lower()
    if "köln" in n or "koeln" in n:
        return "koeln"
    for key in BUNDESLIGA_TEAM_KEYS:
        if key in n:
            return key
    return None

def parse_mdsum(mdsum):
    if not mdsum:
        return None
    opponents = []
    for match in mdsum[:CONFIG["FIXTURE_LOOKAHEAD"]]:
        if not isinstance(match, dict):
            continue
        t1 = str(match.get("t1", ""))
        t2 = str(match.get("t2", ""))
        opponents.append((t1, t2))
    if not opponents:
        return None
    labels = []
    names = []
    for t1, t2 in opponents:
        k1 = KICKBASE_TID_TO_TEAM.get(t1)
        k2 = KICKBASE_TID_TO_TEAM.get(t2)
        s1 = TEAM_STRENGTH_LAST_SEASON.get(k1, TEAM_STRENGTH_DEFAULT) if k1 else TEAM_STRENGTH_DEFAULT
        s2 = TEAM_STRENGTH_LAST_SEASON.get(k2, TEAM_STRENGTH_DEFAULT) if k2 else TEAM_STRENGTH_DEFAULT
        opp_key = k1 if s1 >= s2 else k2
        opp_name = TEAM_DISPLAY_NAMES.get(opp_key, "?") if opp_key else "?"
        names.append(opp_name)
        labels.append(max(s1, s2))
    avg = sum(labels) / len(labels)
    label = "leicht" if avg <= 35 else ("mittel" if avg <= 55 else "schwer")
    return {"label": label, "opponents": names}

def days_until_next_matchday(fixtures, fallback):
    today = datetime.now(timezone.utc).date()
    upcoming = []
    for m in fixtures:
        if m.get("matchIsFinished"):
            continue
        raw = m.get("matchDateTimeUTC")
        if not raw:
            continue
        try:
            d = datetime.fromisoformat(raw.replace("Z", "+00:00")).date()
            if d >= today:
                upcoming.append(d)
        except ValueError:
            continue
    return max(0, (min(upcoming) - today).days) if upcoming else fallback

def fixture_difficulty(fixtures, team_key):
    if not team_key or not fixtures:
        return None
    upcoming = []
    for m in fixtures:
        if m.get("matchIsFinished"):
            continue
        k1 = get_team_key((m.get("team1") or {}).get("teamName", ""))
        k2 = get_team_key((m.get("team2") or {}).get("teamName", ""))
        if k1 == team_key and k2:
            upcoming.append((m.get("matchDateTimeUTC", ""), k2))
        elif k2 == team_key and k1:
            upcoming.append((m.get("matchDateTimeUTC", ""), k1))
    upcoming.sort(key=lambda x: x[0])
    opponents = upcoming[:CONFIG["FIXTURE_LOOKAHEAD"]]
    if not opponents:
        return None
    strengths = [TEAM_STRENGTH_LAST_SEASON.get(k, TEAM_STRENGTH_DEFAULT) for _, k in opponents]
    avg = sum(strengths) / len(strengths)
    names = [TEAM_DISPLAY_NAMES.get(k, k.title()) for _, k in opponents]
    label = "leicht" if avg <= 35 else ("mittel" if avg <= 55 else "schwer")
    return {"label": label, "opponents": names}

def count_matchdays_played(fixtures):
    if not fixtures:
        return 0
    finished = sum(1 for m in fixtures if m.get("matchIsFinished"))
    return finished // 9

def load_history():
    if GITHUB_TOKEN and GITHUB_REPOSITORY:
        url = f"https://api.github.com/repos/{GITHUB_REPOSITORY}/contents/{CONFIG['HISTORY_FILE']}"
        hdr = {"Authorization": f"token {GITHUB_TOKEN}", "Accept": "application/vnd.github+json"}
        try:
            resp = requests.get(url, headers=hdr, timeout=CONFIG["REQUEST_TIMEOUT"])
            if resp.status_code == 404:
                return {}, None
            resp.raise_for_status()
            data = resp.json()
            h = json.loads(base64.b64decode(data["content"]).decode("utf-8"))
            h.pop("players", None)
            # "_transfers" ist der Transfer-Speicher, kein Spieler - nicht mitzaehlen
            player_count = sum(1 for k in h if not k.startswith("_"))
            tx_count = len(h.get("_transfers", {}))
            print(f"✅ Verlauf geladen ({player_count} Spieler, {tx_count} Transfers).")
            return h, data.get("sha")
        except Exception as e:
            print(f"⚠️ Verlauf nicht ladbar: {e}")
            return {}, None
    if os.path.exists(CONFIG["HISTORY_FILE"]):
        try:
            with open(CONFIG["HISTORY_FILE"], "r", encoding="utf-8") as f:
                h = json.load(f)
            h.pop("players", None)
            return h, None
        except Exception:
            return {}, None
    return {}, None

def save_history(history, sha=None):
    content = json.dumps(history, indent=2, ensure_ascii=False)
    if GITHUB_TOKEN and GITHUB_REPOSITORY:
        url = f"https://api.github.com/repos/{GITHUB_REPOSITORY}/contents/{CONFIG['HISTORY_FILE']}"
        hdr = {"Authorization": f"token {GITHUB_TOKEN}", "Accept": "application/vnd.github+json"}
        payload = {"message": f"Update {int(time.time())}",
                   "content": base64.b64encode(content.encode()).decode()}
        if sha:
            payload["sha"] = sha
        try:
            requests.put(url, headers=hdr, json=payload,
                         timeout=CONFIG["REQUEST_TIMEOUT"]).raise_for_status()
            print("✅ Verlauf gespeichert.")
            return
        except Exception as e:
            print(f"⚠️ Verlauf nicht speicherbar: {e}")
            return
    try:
        with open(CONFIG["HISTORY_FILE"], "w", encoding="utf-8") as f:
            f.write(content)
    except Exception as e:
        print(f"⚠️ Lokale Speicherung fehlgeschlagen: {e}")

def update_history(player_id, current_mv, history, today_str):
    pid = str(player_id)
    today_str = today_str or datetime.now(timezone.utc).date().isoformat()
    entry = history.get(pid, {})
    days = entry.get("days")
    if days is None:
        days = []
        lm = entry.get("mv_history")
        if isinstance(lm, list) and lm:
            days = [{"d": "legacy", "mv": lm[-1]}]
        elif entry.get("mv"):
            days = [{"d": "legacy", "mv": entry["mv"]}]
    days = [d for d in days if isinstance(d, dict) and d.get("mv")]

    # Prognose-Pruefung an die tatsaechliche Marktwert-AENDERUNG koppeln,
    # nicht an den Kalendertagwechsel.
    #
    # Grund: Kickbase aktualisiert gegen 22 Uhr deutscher Zeit = 20:00 UTC,
    # also mitten im UTC-Tag. Beim Tageswechsel (00:00 UTC) hat sich seit dem
    # letzten Abend-Lauf nichts bewegt -> Vergleich immer null. Die echte
    # Aenderung passiert dagegen INNERHALB desselben UTC-Tages.
    #
    # "predbase" haelt fest, VON WELCHEM Wert aus prognostiziert wurde. Ohne
    # das wuerde nach einem Tageswechsel gegen den falschen Ausgangswert
    # gerechnet.
    prediction_check = None
    if days and days[-1].get("pred") is not None:
        prev_entry = days[-1]
        base_mv = prev_entry.get("predbase", prev_entry["mv"])
        if current_mv != base_mv:          # es gab wirklich ein Update
            predicted_mv = prev_entry["pred"]
            prediction_check = {
                "predicted_mv": predicted_mv,
                "actual_mv": current_mv,
                "predicted_move": predicted_mv - base_mv,
                "actual_move": current_mv - base_mv,
            }
            # Verbrauchte Prognose entfernen, damit dieselbe Aenderung nicht
            # bei mehreren Laeufen mehrfach gewertet wird.
            prev_entry.pop("pred", None)
            prev_entry.pop("predbase", None)

    if days and days[-1].get("d") == today_str:
        days[-1]["mv"] = current_mv
    else:
        # Noch nicht eingeloeste Prognose samt Bezugswert auf den neuen Tag
        # uebernehmen - sonst bliebe sie am alten Eintrag haengen und wuerde
        # nie gegen das abendliche Update gemessen.
        carried_pred = days[-1].get("pred") if days else None
        carried_base = days[-1].get("predbase", days[-1].get("mv")) if days else None
        new_entry = {"d": today_str, "mv": current_mv}
        if carried_pred is not None:
            new_entry["pred"] = carried_pred
            new_entry["predbase"] = carried_base
        days.append(new_entry)
    days = days[-CONFIG["MV_HISTORY_DAYS"]:]
    history[pid] = {"days": days}
    # Deltas PRO TAG normieren, nicht pro Eintrag.
    #
    # Grund: Spieler verschwinden zeitweise vom Markt und werden dann tagelang
    # nicht erfasst. Die Differenz zwischen zwei Eintraegen deckt dann mehrere
    # Tage ab. Wer sie ungeteilt als "Tagestrend" nimmt, ueberschaetzt massiv -
    # bei Bornauw wurden aus +9,3 Mio ueber 8 Tage ein prognostizierter
    # Tagesanstieg von +9,3 Mio (real: -400k).
    deltas = []
    for i in range(1, len(days)):
        vorher, jetzt = days[i-1], days[i]
        wert_delta = jetzt["mv"] - vorher["mv"]
        try:
            t = (date.fromisoformat(jetzt["d"]) - date.fromisoformat(vorher["d"])).days
        except (ValueError, TypeError, KeyError):
            t = 1                      # "legacy"-Eintraege ohne echtes Datum
        if t < 1:
            t = 1
        if t > 14:
            continue                   # zu grosse Luecke -> nicht aussagekraeftig
        deltas.append(int(round(wert_delta / t)))

    # Anzahl echter Beobachtungstage mitgeben: Bei neu in die Liga
    # gekommenen Spielern meldet Kickbase am ersten Tag einen absurden
    # tfhmvt-Wert (z.B. +9,3 Mio), weil gegen einen nicht existierenden
    # Vorwert gerechnet wird. Solche Spieler brauchen erst ein paar Tage
    # eigener Beobachtung, bevor eine Prognose sinnvoll ist.
    echte_tage = len([d for d in days if d.get("d") != "legacy"])
    if deltas:
        return deltas, True, prediction_check, echte_tage
    return [], False, prediction_check, echte_tage

def store_prediction(pid, history, predicted_next_mv):
    """
    Merkt sich die Prognose fuer den naechsten Marktwert.
    "predbase" haelt fest, von welchem Wert aus prognostiziert wurde - noetig,
    damit der spaetere Vergleich auch dann korrekt rechnet, wenn zwischendurch
    ein Kalendertag gewechselt hat.
    """
    pid = str(pid)
    if pid in history and history[pid].get("days"):
        last = history[pid]["days"][-1]
        last["pred"] = int(predicted_next_mv)
        last["predbase"] = last["mv"]

def compute_trend(deltas):
    if not deltas:
        return 0, False
    s = sorted(deltas)
    return s[len(s) // 2], True

def compute_momentum(deltas):
    if len(deltas) < 3:
        return "neutral"
    median = sorted(deltas)[len(deltas) // 2]
    if median == 0:
        return "neutral"
    recent_avg = sum(deltas[-2:]) / 2
    ratio = recent_avg / median
    if ratio >= 1.25:
        return "beschleunigt"
    if ratio <= 0.75:
        return "verlangsamt"
    return "neutral"

def predict(current_mv, daily_trend, days_left, overpay_factor):
    """
    Marktwert-Prognose.

    Zwei Wachstumsmuster, weil Kickbase sich unterschiedlich verhaelt:

    1) Etablierte Spieler: Anstiege flachen ab -> Daempfung 0,96.
    2) Aufsteiger vom Boden (unter LOW_MV_THRESHOLD, typisch ab 500k):
       Der Anstieg BESCHLEUNIGT sich, oft ueber Tage. Beobachtet bei Silas
       (prognostiziert +14k, real +250k) und bei Bogdanov. Die uebliche
       Daempfung rechnet hier genau falsch herum.

    Die Beschleunigung wird bewusst gedeckelt: exponentielles Wachstum ohne
    Grenze wuerde nach wenigen Tagen absurde Werte liefern.
    """
    if daily_trend <= 0 or days_left <= 0:
        return current_mv, None, 0

    aufsteiger = current_mv < CONFIG["LOW_MV_THRESHOLD"]
    faktor = CONFIG["LOW_MV_GROWTH"] if aufsteiger else 0.96

    growth, cd = 0.0, float(daily_trend)
    for _ in range(days_left):
        growth += cd
        cd *= faktor
        if aufsteiger:
            # Beschleunigung begrenzen - aber relativ zum AUSGANGSTREND,
            # nicht zum Marktwert. Der 5%-Marktwert-Deckel ist fuer die
            # Erkennung von Datenluecken gedacht; bei einem 600k-Spieler
            # laege er bei 30k und wuerde einen realen Trend von 50k/Tag
            # ausbremsen - also genau das Gegenteil bewirken.
            cd = min(cd, daily_trend * CONFIG["LOW_MV_MAX_ACCEL"])

    # Gesamtanstieg begrenzen - ein Spieler verdoppelt sich nicht in Tagen
    growth = min(growth, current_mv * CONFIG["MAX_TOTAL_GROWTH"])
    proj_mv = int(current_mv + growth)
    max_bid = int(current_mv + growth * overpay_factor)
    return proj_mv, max_bid, int(growth)

def evaluate_prediction_hit(prediction_check):
    if not prediction_check:
        return None
    predicted_move = prediction_check["predicted_move"]
    actual_move = prediction_check["actual_move"]
    if predicted_move == 0:
        return None
    same_direction = (predicted_move > 0) == (actual_move > 0)
    ratio = actual_move / predicted_move if predicted_move != 0 else 0
    tol = CONFIG["PREDICTION_HIT_TOLERANCE"]
    is_hit = same_direction and (1 - tol) <= ratio <= (1 + tol)
    return is_hit


def record_prediction_results(evaluated, history, today_str):
    """
    Schreibt die Prognose-Ergebnisse dieses Laufs dauerhaft in die History.

    Damit laesst sich ueber Tage hinweg beantworten, ob die Prognosen
    ueberhaupt taugen - und wie stark sie systematisch daneben liegen.
    Gespeichert wird pro Tag:
      n              Anzahl geprüfter Prognosen
      hits           davon im Toleranzbereich
      direction_ok   Richtung stimmte (rauf/runter)
      sum_pred       Summe der vorhergesagten Bewegungen
      sum_act        Summe der tatsaechlichen Bewegungen
    sum_act/sum_pred ist der KALIBRIERUNGSFAKTOR: liegt er bei 0,7, sagt die
    Prognose systematisch 30% zu viel Anstieg voraus.
    """
    checks = [(p.get("prediction_check"), evaluate_prediction_hit(p.get("prediction_check")))
              for p in evaluated if p.get("prediction_check")]
    checks = [(c, h) for c, h in checks if c and h is not None]
    if not checks:
        return None

    store = history.setdefault("_predictions", {})
    day = {
        "n": len(checks),
        "hits": sum(1 for _, h in checks if h),
        "direction_ok": sum(1 for c, _ in checks
                            if (c["predicted_move"] > 0) == (c["actual_move"] > 0)),
        "sum_pred": sum(c["predicted_move"] for c, _ in checks),
        "sum_act": sum(c["actual_move"] for c, _ in checks),
    }
    store[today_str] = day
    # Nur die letzten 30 Tage behalten
    for old in sorted(store)[:-30]:
        del store[old]
    return day


def prediction_accuracy_summary(history, days=7):
    """
    Fasst die gespeicherten Prognose-Ergebnisse der letzten N Tage zusammen.
    Rueckgabe: None, wenn noch keine Daten vorliegen.
    """
    store = history.get("_predictions", {})
    if not store:
        return None
    recent = [store[d] for d in sorted(store)[-days:]]
    n = sum(x.get("n", 0) for x in recent)
    if n == 0:
        return None
    hits = sum(x.get("hits", 0) for x in recent)
    direction_ok = sum(x.get("direction_ok", 0) for x in recent)
    sum_pred = sum(x.get("sum_pred", 0) for x in recent)
    sum_act = sum(x.get("sum_act", 0) for x in recent)
    calibration = (sum_act / sum_pred) if sum_pred else None
    return {
        "days": len(recent),
        "n": n,
        "hits": hits,
        "hit_rate": hits / n,
        "direction_rate": direction_ok / n,
        "sum_pred": sum_pred,
        "sum_act": sum_act,
        "calibration": calibration,
    }


def format_accuracy_line(acc):
    """Baut die Report-Zeile zur Prognose-Guete."""
    if not acc:
        return None
    parts = [
        f"🎯 <b>Prognose-Güte</b> (letzte {acc['days']} Tage, {acc['n']} Vorhersagen)",
        f"   Richtung korrekt: {acc['direction_rate']*100:.0f}% · "
        f"Treffer im Toleranzbereich: {acc['hit_rate']*100:.0f}%",
    ]
    cal = acc.get("calibration")
    # Ausreisser-Hinweis: Einzelne extreme Fehlprognosen (typisch bei
    # Neuzugaengen, deren erster Kickbase-Trend gegen einen nicht
    # existierenden Vorwert gerechnet wird) verzerren den Schnitt tagelang,
    # weil sie im 7-Tage-Fenster haengen bleiben.
    if acc["n"] >= 20 and acc["direction_rate"] >= 0.85 and cal is not None and cal < 0.7:
        parts.append("   <i>Richtung stimmt zuverlässig — der niedrige Wert kommt "
                     "von wenigen Ausreißern, die aus dem 7-Tage-Fenster laufen.</i>")
    if cal is not None:
        if cal < 0.8:
            hinweis = f"Prognose zu optimistisch — reale Anstiege nur {cal*100:.0f}% der Vorhersage"
        elif cal > 1.2:
            hinweis = f"Prognose zu vorsichtig — reale Anstiege {cal*100:.0f}% der Vorhersage"
        else:
            hinweis = f"gut kalibriert ({cal*100:.0f}%)"
        parts.append(f"   📐 {hinweis}")
    return "\n".join(parts)

# Beschriftung exakt nach der Legende der Kickbase-App:
#   Sicher / Erwartet / Unsicher / Unwahrscheinlich / Ausgeschlossen
# ACHTUNG: Welche Zahl welcher Stufe entspricht, ist aus Rohdaten abgeleitet
# und nicht dokumentiert. diagnose_status_values() gibt eine Stichprobe ins
# Log, damit sich das gegen die App gegenpruefen laesst.
# Kickbase zaehlt von 1 bis 5, nicht ab 0 - belegt durch die Rohwerte im Log
# (Drewes=5, Stark=5, keine einzige 0 in der Stichprobe). Die alte 0-4-Annahme
# hat alle Spieler mit Wert 5 als "Status unbekannt" verworfen.
STATUS_ICONS = {1: "🔵", 2: "🟢", 3: "🟠", 4: "🔴", 5: "⚫"}
STATUS_LABELS = {1: "Sicher", 2: "Erwartet", 3: "Unsicher",
                 4: "Unwahrscheinlich", 5: "Ausgeschlossen"}
STATUS_FACTORS = {1: 1.15, 2: 1.10, 3: 1.00, 4: 0.90, 5: 0.70}


def get_status_code(player, detail_stats=None):
    """
    Startelf-Status ermitteln.

    Wichtig: "prob" steht bereits in den MARKTDATEN jedes Spielers - die
    Detail-Abfrage wird nur fuer freie Spieler gemacht. Wer den Status nur
    aus dem Detail liest, bekommt bei allen uebrigen Spielern faelschlich
    "unbekannt", obwohl der Wert direkt vorliegt.
    """
    if detail_stats and detail_stats.get("status_code") is not None:
        return detail_stats["status_code"]
    v = player.get("prob")
    if isinstance(v, int) and 1 <= v <= 5:
        return v
    return None


def discover_marketvalue_endpoint(token, league_id, player_id, player_name="?"):
    """
    Sucht den Endpunkt fuer den historischen Marktwert-Verlauf.

    Die App zeigt bis zu 1 Jahr Verlauf (3M/1J-Umschalter), der Detail-
    Endpunkt liefert ihn aber NICHT mit. Der Bot sammelt die Historie deshalb
    bisher selbst - mit 8-25 Messpunkten statt 365.

    Diese Funktion probiert bekannte Pfadmuster durch und protokolliert, was
    zurueckkommt. Sie veraendert nichts an der Bewertung; sie liefert nur die
    Information, die noetig ist, um den Verlauf spaeter sauber anzubinden.
    """
    kandidaten = [
        f"leagues/{league_id}/players/{player_id}/marketvalue/365",
        f"leagues/{league_id}/players/{player_id}/marketvalue/92",
        f"leagues/{league_id}/players/{player_id}/marketvalue",
        f"leagues/{league_id}/players/{player_id}/marketValues",
        f"leagues/{league_id}/players/{player_id}/mv",
        f"leagues/{league_id}/players/{player_id}/stats",
        f"leagues/{league_id}/players/{player_id}/performance",
        f"players/{player_id}/marketvalue/365",
        f"players/{player_id}/marketvalue",
    ]
    print(f"🔎 Suche Marktwert-Verlauf fuer '{player_name}' (ID {player_id}) ...")
    gefunden = []
    for pfad in kandidaten:
        try:
            r = requests.get(f"https://api.kickbase.com/v4/{pfad}",
                             headers=_kb(token), timeout=CONFIG["REQUEST_TIMEOUT"])
            if r.status_code != 200:
                continue
            data = r.json()
            # Wie viele Werte stecken drin? Groesste Liste im JSON suchen.
            groesste, schluessel = 0, None
            if isinstance(data, list):
                groesste, schluessel = len(data), "(Wurzel)"
            elif isinstance(data, dict):
                for k, v in data.items():
                    if isinstance(v, list) and len(v) > groesste:
                        groesste, schluessel = len(v), k
            print(f"   ✅ {pfad} -> HTTP 200, groesste Liste: '{schluessel}' mit {groesste} Eintraegen")
            if groesste >= 20:
                probe = (data[schluessel] if schluessel != "(Wurzel)" else data)[:2]
                print(f"      Beispieleintraege: {json.dumps(probe, ensure_ascii=False)[:300]}")
                gefunden.append((pfad, schluessel, groesste))
        except Exception:
            continue
        time.sleep(0.2)

    if not gefunden:
        print("   ❌ Kein Endpunkt mit Verlaufsdaten gefunden - Bot sammelt weiter selbst.")
    return gefunden


def diagnose_status_values(players, limit=8):
    """
    Listet Name + Rohwert des Startelf-Status, damit sich die Zuordnung
    gegen die App pruefen laesst (Legende: 0=Sicher ... 4=Ausgeschlossen).
    """
    rows = []
    for p in players:
        v = p.get("prob")
        if isinstance(v, int):
            rows.append((p.get("n") or p.get("pn") or "?", v))
        if len(rows) >= limit:
            break
    if rows:
        txt = ", ".join(f"{n}={v}" for n, v in rows)
        print(f"🔬 Startelf-Rohwerte (zum Abgleich mit der App): {txt}")
    else:
        print("🔬 Startelf-Rohwerte: kein 'prob'-Feld in den Marktdaten gefunden")

def lineup_factor_from_status(status_code):
    return STATUS_FACTORS.get(status_code, 1.0)

def score_relative_value(mv, avg_points, pos_code):
    if not avg_points or avg_points <= 0 or not mv or mv <= 0:
        return None
    pos_mult = {1: 80_000, 2: 100_000, 3: 120_000, 4: 150_000}.get(pos_code, 100_000)
    expected_mv = avg_points * pos_mult
    return (expected_mv - mv) / max(mv, 1_000_000)

def confidence_weighted_score(rv_score, matchdays_played):
    if rv_score is None:
        return None
    full_at = CONFIG["RV_CONFIDENCE_FULL_AT_MATCHDAY"]
    base = CONFIG["RV_CONFIDENCE_BASE"]
    if matchdays_played is None or matchdays_played <= 0 or matchdays_played >= full_at:
        return rv_score
    step = (1.0 - base) / full_at
    damping = base + (matchdays_played * step)
    return rv_score * damping

def is_teamwert_candidate(player_result):
    if player_result["category"] in ("blocked", "market_offer"):
        return False
    sc = player_result.get("status_code")
    if sc is not None and sc > CONFIG["MIN_TEAMWERT_STATUS"]:
        return False
    avg_pts = player_result.get("avg_points")
    pos = player_result.get("pos_code")
    min_pts = TEAMWERT_MIN_AVG_POINTS.get(pos, TEAMWERT_MIN_AVG_POINTS_DEFAULT)
    if avg_pts is not None and avg_pts >= min_pts:
        return True
    rv = player_result.get("relative_value_score")
    if rv is not None and rv > 0:
        return True
    # Skala 1-5: 1=Sicher, 2=Erwartet -> als gesetzt behandeln
    if avg_pts is None and sc is not None and sc <= 2:
        return True
    return False

def teamwert_confidence(player_result):
    avg_pts = player_result.get("avg_points")
    rv = player_result.get("relative_value_score")
    if avg_pts is not None or (rv is not None and rv > 0):
        return "verified"
    return "unverified"

def evaluate_player(player, history, injured_list, headlines, days_left,
                    my_team_counts=None, blocked_teams=None, my_budget=None,
                    fixture_info=None, win_prob=None, today_str=None,
                    detail=None, matchdays_played=None, mv_range=None,
                    season_stats=None):
    pid = player.get("id", player.get("i"))
    tid = player.get("tid")
    pos_code = None
    try:
        pos_code = int(player.get("pos", player.get("p", 0))) or None
    except (TypeError, ValueError):
        pass
    name = player.get("n", player.get("lastName", ""))
    if not name and "fn" in player and "ln" in player:
        name = f"{player['fn']} {player['ln']}"
    if not name:
        name = "Unbekannt"
    mv = int(player.get("mv", player.get("m", 0)))
    exs = int(player.get("exs", 0))

    stats = extract_player_stats(detail) if detail else {
        "daily_trend_api": None, "status_code": None, "mdsum": None,
        "mvt_flag": None, "avg_points": None,
        "appearances": None, "total_points": None}
    # Status aus Detail ODER Marktdaten - "prob" liegt in beiden vor,
    # aber die Detail-Abfrage laeuft nur fuer freie Spieler.
    status_code = get_status_code(player, stats)
    kb_daily_trend = stats["daily_trend_api"]
    mdsum = stats["mdsum"]
    avg_points = stats["avg_points"]
    # Echte Saisondaten aus /performance haben Vorrang - dort sind die
    # Einsaetze exakt gezaehlt statt aus unklaren Feldern geraten.
    if season_stats:
        appearances = season_stats.get("appearances")
        total_points_season = season_stats.get("total_points")
        if season_stats.get("avg_points"):
            avg_points = season_stats["avg_points"]
    else:
        appearances = stats.get("appearances")
        total_points_season = stats.get("total_points")
    # Wie belastbar ist der Oe-Wert? 100 Punkte aus 29 Spielen sind etwas
    # anderes als 100 aus 2 Spielen.
    apps_confidence = appearance_confidence(appearances)

    deltas, has_real, prediction_check, beobachtungstage = update_history(
        pid, mv, history, today_str)
    calculated_trend, _ = compute_trend(deltas)
    daily_trend = kb_daily_trend if kb_daily_trend is not None else calculated_trend
    if kb_daily_trend is not None:
        has_real = True
    momentum = compute_momentum(deltas) if len(deltas) >= 3 else "neutral"

    # Neuzugaenge: Kickbase meldet am ersten Tag in der App einen
    # unbrauchbaren Tagestrend (z.B. +9,3 Mio bei Bornauw), weil gegen einen
    # nicht existierenden Vorwert gerechnet wird. Solange wir den Spieler
    # nicht selbst ein paar Tage beobachtet haben, gibt es keine Prognose -
    # ein ehrliches "noch unbekannt" ist besser als eine Fantasiezahl.
    # Neuzugang anhand der KICKBASE-Historie bestimmen, nicht anhand unserer
    # eigenen Beobachtungsdauer.
    #
    # Grund: Spieler wechseln staendig zwischen Kadern und Markt. Heuer
    # Fernandes (33 Einsaetze, seit Jahren im HSV-Tor) tauchte erst gestern
    # im Markt auf - unsere Historie kannte ihn also 1 Tag und stufte ihn als
    # "neu in der Liga" ein. In der Folge blockierte der Bot 18 Spieler und
    # meldete "keine Kaufchancen", obwohl 45 Mio Kaufkraft bereitstanden.
    # Kickbase' eigene Historie unterscheidet sauber: etablierte Spieler haben
    # 365 Eintraege, echte Neuzugaenge deutlich weniger.
    # Muss VOR der Neuzugangs-Pruefung stehen: die Laenge der
    # Kickbase-Historie entscheidet, ob ein Spieler wirklich neu ist.
    range_pos, range_lo, range_hi, kb_hist_len = (mv_range or (None, None, None, None))
    if kb_hist_len is not None:
        ist_neuzugang = kb_hist_len < CONFIG["MIN_KB_HISTORY_DAYS"]
    else:
        # Ohne Kickbase-Historie (z.B. angebotene Spieler ohne Detailabruf)
        # bleibt die eigene Beobachtungsdauer als Notbehelf.
        ist_neuzugang = beobachtungstage < CONFIG["MIN_OBSERVED_DAYS"]
    if ist_neuzugang:
        daily_trend = 0
        has_real = False
    else:
        # Plausibilitaetsgrenze fuer alle uebrigen: Kickbase bewegt
        # Marktwerte um wenige Prozent pro Tag.
        grenze = int(mv * CONFIG["MAX_DAILY_TREND_PCT"]) if mv else 0
        if grenze and abs(daily_trend) > grenze:
            daily_trend = grenze if daily_trend > 0 else -grenze

    if daily_trend and daily_trend > 0:
        store_prediction(pid, history, mv + daily_trend)

    if mdsum and not fixture_info:
        fixture_info = parse_mdsum(mdsum)

    mf = matchup_factor(win_prob)
    mf_momentum = (CONFIG["MOMENTUM_BOOST"] if momentum == "beschleunigt"
                   else CONFIG["MOMENTUM_BRAKE"] if momentum == "verlangsamt"
                   else 1.0)
    mf_lineup = lineup_factor_from_status(status_code)
    # Lage in der Jahresspanne: nahe am Hoch = wenig Luft nach oben.
    # Spanne nur verwenden, wenn sie nicht am 500k-Einstiegswert klebt.
    if not range_is_meaningful(range_lo, range_hi):
        range_pos = None
    mf_range = range_position_factor(range_pos)
    overpay_factor = (CONFIG["OVERPAY_BASE_FACTOR"]
                      * mf * mf_momentum * mf_lineup * mf_range)

    proj_mv, max_bid, raw_profit = predict(mv, daily_trend, days_left, overpay_factor)

    is_injured = name_in_set(name, injured_list) if injured_list else False
    matched_news = match_news(name, headlines)[:CONFIG["NEWS_MAX_PER_PLAYER"]]
    negative = has_neg_news(matched_news)

    my_team_counts = my_team_counts or {}
    blocked_teams = blocked_teams or {}
    violates = tid is not None and my_team_counts.get(tid, 0) >= CONFIG["MAX_PER_TEAM"]
    opp_blocked = blocked_teams.get(tid, []) if tid is not None else []

    urgency_minutes = exs // 60 if exs > 0 else None
    is_urgent = urgency_minutes is not None and urgency_minutes <= CONFIG["URGENCY_THRESHOLD_MINUTES"]

    if violates:
        category, reason = "blocked", "2-Spieler-Regel: du hast schon 2 aus diesem Verein"
    elif is_injured:
        category, reason = "blocked", "verletzt / gesperrt (LigaInsider)"
    elif negative:
        category, reason = "blocked", "negative Schlagzeile — erst prüfen"
    elif ist_neuzugang:
        category = "pending"
        tage_txt = (f"{kb_hist_len} Tage Kickbase-Historie" if kb_hist_len is not None
                    else f"erst {beobachtungstage} Tag(e) beobachtet")
        reason = f"neu in der Liga — Trend noch unbrauchbar ({tage_txt})"
    elif not has_real or max_bid is None:
        category, reason = "pending", "noch kein Tagesvergleich — morgen entscheiden"
    elif my_budget is not None and max_bid > my_budget:
        category, reason = "blocked", f"Gebot {fmt_money(max_bid)} über Budget"
    elif raw_profit >= 1_500_000:
        # Skala 1-5: ab 4 (Unwahrscheinlich) bzw. 5 (Ausgeschlossen) kein
        # Top-Kandidat mehr, egal wie gut der Marktwert-Trend aussieht.
        if status_code == 5:
            # "Ausgeschlossen" heisst laut Kickbase-Legende: keine realistische
            # Chance auf die Startelf. Ein solcher Spieler holt keine Punkte,
            # egal wie gut sein Marktwert-Trend aussieht. Er gehoert nicht in
            # den Kaufteil - auch nicht in die zweite Reihe mit Gebotsgrenze.
            category = "skip"
            reason = "ausgeschlossen — keine Aussicht auf Einsatz"
        elif status_code == 4:
            category = "watch"
            reason = "hoher Trend, aber Einsatz unwahrscheinlich"
        else:
            category, reason = "top", "hoher erwarteter Gewinn"
    elif raw_profit >= CONFIG["MIN_PROFIT_FOR_TOP"]:
        if status_code == 5:
            category, reason = "skip", "ausgeschlossen — keine Aussicht auf Einsatz"
        else:
            category, reason = "watch", "solider Aufwärtstrend"
    else:
        category, reason = "skip", "kein nennenswerter Trend"

    budget_ok = max_bid is None or my_budget is None or max_bid <= my_budget
    relative_value_score = score_relative_value(mv, avg_points, pos_code)
    relative_value_score = confidence_weighted_score(relative_value_score, matchdays_played)
    # Zusaetzlich nach Einsatzzahl der VORSAISON daempfen: Ein Oe-Wert aus
    # 2 Spielen ist kein verlaesslicher Massstab, einer aus 29 schon.
    # Wirkt nur, wenn die Feldzuordnung bestaetigt wurde (siehe unten).
    # Daempfung immer anwenden, wenn die Einsatzzahl aus /performance stammt -
    # die ist gezaehlt, nicht geraten, und braucht keinen Bestaetigungsschalter.
    if relative_value_score is not None and (season_stats or CONFIG.get("USE_APPEARANCE_WEIGHT")):
        relative_value_score *= apps_confidence

    result = {
        "id": pid, "name": name, "mv": mv, "proj_mv": proj_mv,
        "max_bid": max_bid, "exp_profit": raw_profit,
        "range_pos": range_pos, "range_lo": range_lo, "range_hi": range_hi,
        "season_label": (season_stats or {}).get("season"),
        "is_newcomer": ist_neuzugang, "observed_days": beobachtungstage,
        "kb_history_days": kb_hist_len,
        "appearances": appearances, "total_points_season": total_points_season,
        "apps_confidence": apps_confidence,
        "daily_trend": daily_trend, "kb_trend": kb_daily_trend,
        "momentum": momentum, "has_real": has_real,
        "is_injured": is_injured, "matched_news": matched_news,
        "negative_news": negative,
        "violates": violates, "opp_blocked": opp_blocked, "budget_ok": budget_ok,
        "category": category, "reason": reason,
        "fixture": fixture_info, "win_prob": win_prob,
        "status_code": status_code,
        "pos_code": pos_code,
        "avg_points": avg_points,
        "relative_value_score": relative_value_score,
        "overpay_factor": overpay_factor,
        "urgency_minutes": urgency_minutes, "is_urgent": is_urgent,
        "exs": exs,
        "prediction_check": prediction_check,
    }
    return result

def build_report(evaluated, my_budget, days_left, opponent_profiles,
                 transfer_summary, lineups_ok=True, odds_ok=True,
                 my_gap_codes=None, cash_by_name=None, accuracy=None,
                 buying_power=None, overdraft=None, field_warnings=None):
    lines = []
    lines.append(f"⚽ Kickbase Markt-Report — noch {days_left} Tage")
    if my_budget is not None:
        if buying_power is not None and overdraft:
            # Kickbase erlaubt 33% des Teamwerts als Ueberziehung. Ein
            # negativer Kontostand bedeutet also NICHT, dass nichts mehr geht.
            lines.append(
                f"💳 Kaufkraft: <b>{fmt_money(buying_power)} €</b>"
                f"  (Konto {fmt_money(my_budget)} + Rahmen {fmt_money(overdraft)})")
            if buying_power < 1_000_000:
                lines.append("🚨 <b>Rahmen fast ausgeschöpft</b> — verkaufen schafft Luft")
        elif my_budget < 0:
            lines.append(f"🚨 <b>Konto im Minus: {fmt_money(my_budget)} €</b>")
        else:
            lines.append(f"💳 Budget: {fmt_money(my_budget)} €")
    # Startelf-Warnung nur, wenn tatsaechlich kein einziger Spieler einen
    # Status hat. Frueher war das fest auf "nicht verfuegbar" gesetzt (Relikt
    # aus der Zeit, als LigaInsider die Quelle war) - inzwischen liefert
    # Kickbase den Status selbst ueber "prob".
    for w in (field_warnings or []):
        lines.append(f"🚨 <b>Datenausfall:</b> {esc(w)}")

    # Negative Schlagzeilen GANZ NACH OBEN - unabhaengig davon, ob es
    # gerade Kaufchancen gibt. Bisher erschienen sie nur im Detailblock von
    # Top-Kandidaten; gab es keine, blieben 76 geladene Schlagzeilen komplett
    # unsichtbar. Eine Verletzungsmeldung ist aber immer relevant.
    alarm_news = []
    for p in evaluated:
        if p.get("category") == "market_offer":
            continue
        if p.get("negative_news") and p.get("matched_news"):
            alarm_news.append((p["name"], p["matched_news"][0]))
    if alarm_news:
        lines.append("\n📰 <b>WICHTIGE MELDUNGEN</b>")
        for name, schlagzeile in alarm_news[:5]:
            lines.append(f"• <b>{esc(name)}</b>: {esc(schlagzeile[:110])}")
    if not any(p.get("status_code") is not None for p in evaluated):
        lines.append("⚠️ Startelf-Daten aktuell nicht verfügbar.")
    if not odds_ok:
        lines.append("⚠️ Wettquoten nicht verfügbar (ODDS_API_KEY prüfen).")

    # Kumulative Guete zuerst - eine einzelne Tagesquote schwankt stark,
    # erst der Mehrtages-Schnitt sagt etwas ueber die Verlaesslichkeit aus.
    acc_line = format_accuracy_line(accuracy)
    if acc_line:
        lines.append("\n" + acc_line)

    checks = [p["prediction_check"] for p in evaluated if p.get("prediction_check")]
    if checks:
        hits = [evaluate_prediction_hit(c) for c in checks]
        valid_hits = [h for h in hits if h is not None]
        if valid_hits:
            hit_count = sum(1 for h in valid_hits if h)
            lines.append(f"\n📊 PROGNOSE-CHECK (gestern -> heute)")
            lines.append(f"Trefferquote: {hit_count}/{len(valid_hits)} ({hit_count/len(valid_hits)*100:.0f}%)")
            examples = sorted(
                [(p, c) for p, c in zip(
                    [pp for pp in evaluated if pp.get("prediction_check")], checks)],
                key=lambda x: abs(x[1]["actual_move"] - x[1]["predicted_move"]),
                reverse=True,
            )[:3]
            for p, c in examples:
                hit = evaluate_prediction_hit(c)
                icon = "✅" if hit else ("❌" if hit is False else "➖")
                lines.append(
                    f"{icon} {esc(p['name'])}: prognostiziert {fmt_profit(c['predicted_move'])} €"
                    f", tatsächlich {fmt_profit(c['actual_move'])} €"
                )

    tops = sorted([p for p in evaluated if p["category"] == "top"],
                  key=lambda x: (x["is_urgent"], x["exp_profit"]), reverse=True)
    watch = sorted([p for p in evaluated if p["category"] == "watch"],
                   key=lambda x: (x["is_urgent"], x["exp_profit"]), reverse=True)
    blocked = [p for p in evaluated if p["category"] == "blocked"]
    pending = [p for p in evaluated if p["category"] == "pending"]
    skipped = [p for p in evaluated if p["category"] == "skip"]
    offered = [p for p in evaluated if p["category"] == "market_offer"]

    urgent = [p for p in tops + watch if p["is_urgent"]]
    detail_list = [p for p in tops + watch if not p["is_urgent"]][:CONFIG["TOP_DETAIL_COUNT"]]

    for p in urgent + detail_list:
        competitors = assess_competition(
            p.get("pos_code"), opponent_profiles or [], cash_by_name or {}, p.get("max_bid") or 0)
        p["deep_analysis"] = build_deep_analysis(p, my_gap_codes or [], competitors)

    if urgent:
        lines.append("\n⏰ JETZT HANDELN (Auktion läuft bald ab!)")
        for p in urgent:
            lines.append(
                f"• {esc(p['name'])} — {fmt_money(p['mv'])} €"
                f" | endet in {fmt_minutes(p['exs'])}"
                f" | Gewinn: {fmt_profit(p['exp_profit'])} €"
                f" | Gebot bis {fmt_money(p['max_bid'])} €"
            )
            if p.get("deep_analysis"):
                lines.append(f"  🧠 {esc(p['deep_analysis'])}")

    if detail_list:
        detail_top = [p for p in detail_list if p["category"] == "top"]
        detail_watch = [p for p in detail_list if p["category"] == "watch"]

        def render_candidate(p, label):
            momentum_icon = " 🚀" if p["momentum"] == "beschleunigt" else (" 📉" if p["momentum"] == "verlangsamt" else "")
            lines.append(
                f"\n{label} {esc(p['name'])} — {fmt_money(p['mv'])} €{momentum_icon}\n"
                f"   Erwartet: {fmt_money(p['proj_mv'])} € ({fmt_profit(p['exp_profit'])} €)\n"
                f"   👉 Max. Gebot: {fmt_money(p['max_bid'])} €"
            )
            extras = []
            sc = p.get("status_code")
            if sc is not None and sc in STATUS_ICONS:
                extras.append(f"{STATUS_ICONS[sc]} {STATUS_LABELS[sc]}")
            if p.get("is_newcomer"):
                extras.append("🆕 neu in der Liga — Trend noch unbrauchbar")
            elif p.get("kb_trend") is not None:
                extras.append(f"📈 KB-Trend {fmt_profit(p['kb_trend'])} €/Tag")
            # Lage in der Jahresspanne - beantwortet "teuer oder guenstig?",
            # was der Tagestrend allein nicht sagt.
            # range_pos wird in evaluate_player bereits auf None gesetzt,
            # wenn die Spanne am 500k-Einstiegswert klebt - dann steht hier
            # bewusst nichts statt einer irrefuehrenden Prozentzahl.
            if p.get("range_pos") is not None:
                pct = p["range_pos"] * 100
                if pct > 75:
                    marke = "⚠️ nahe Jahreshoch"
                elif pct < 35:
                    marke = "💎 günstig im Jahresvergleich"
                else:
                    marke = "Mittelfeld der Jahresspanne"
                extras.append(
                    f"📏 {pct:.0f}% ({fmt_money(p['range_lo'])}–{fmt_money(p['range_hi'])}) · {marke}")
            if p["fixture"]:
                opps = ", ".join(p["fixture"]["opponents"])
                extras.append(f"📅 {p['fixture']['label']} ({opps})")
            if p["win_prob"] is not None:
                extras.append(f"🎰 {p['win_prob']*100:.0f}% Sieg")
            if p["opp_blocked"]:
                extras.append(f"🔓 Verein bei {len(p['opp_blocked'])} Gegner(n) voll")
            if extras:
                lines.append("   " + " · ".join(extras))
            for h in p["matched_news"]:
                lines.append(f"   📰 {esc(h[:120])}")
            if p.get("deep_analysis"):
                lines.append(f"   🧠 {esc(p['deep_analysis'])}")

        if detail_top:
            lines.append("\n🔥 BESTE CHANCEN (sicherer Einsatz + hoher Gewinn)")
            for i, p in enumerate(detail_top, start=1):
                render_candidate(p, f"{i}.")

        if detail_watch:
            lines.append("\n📋 ZWEITE REIHE (Trend gut, aber mit Vorbehalt)")
            for p in detail_watch:
                render_candidate(p, "•")
    else:
        lines.append("\n😐 Keine lohnenden Kaufchancen gefunden.")

    rest = [p for p in tops + watch if not p["is_urgent"]][CONFIG["TOP_DETAIL_COUNT"]:]
    if rest:
        lines.append("\n👀 Weitere")
        for p in rest:
            lines.append(f"• {esc(p['name'])} — {fmt_money(p['mv'])} € ({fmt_profit(p['exp_profit'])} €)")

    teamwert_pool = [p for p in evaluated if is_teamwert_candidate(p)]
    beste_chancen_ids = {p["id"] for p in detail_list + rest}
    teamwert_neu = [p for p in teamwert_pool if p["id"] not in beste_chancen_ids]
    teamwert_neu.sort(key=lambda x: (
        x.get("status_code") if x.get("status_code") is not None else 99,
        -(x.get("avg_points") or 0),
    ))
    if teamwert_neu:
        verified = [p for p in teamwert_neu if teamwert_confidence(p) == "verified"]
        unverified = [p for p in teamwert_neu if teamwert_confidence(p) == "unverified"]

        if verified:
            lines.append("\n⭐ TEAMWERT-EMPFEHLUNGEN (Qualität unabhängig vom Trend)")
            for p in verified[:8]:
                sc = p.get("status_code")
                status_str = f"{STATUS_ICONS.get(sc, '')} {STATUS_LABELS.get(sc, '?')}" if sc is not None else "❓ Status unbekannt"
                avg_str = ""
                if p.get("avg_points"):
                    avg_str = f" · ⚽ Ø {p['avg_points']:.1f} Pkt"
                    # Einsatzzahl mit anzeigen: 100 Pkt aus 29 Spielen sind
                    # etwas anderes als 100 aus 2 Spielen.
                    # Nur anzeigen, wenn die Feldzuordnung gegengeprueft wurde -
                    # sonst stuende dort eine Zahl, die nichts bedeutet.
                    if p.get("appearances"):
                        n_app = p["appearances"]
                        avg_str += f" aus {n_app} {'Einsatz' if n_app == 1 else 'Einsätzen'}"
                rv = p.get("relative_value_score")
                rv_str = f" · 💎 Wert +{rv:.2f}" if rv is not None and rv > 0 else ""
                # Bei Neuzugaengen keinen Kickbase-Trend zeigen: er wird gegen
                # einen nicht existierenden Vorwert gerechnet (Bornauw: +8,9
                # Mio/Tag). Die Prognose ignoriert ihn schon - der Report darf
                # ihn dann nicht doch als Tatsache ausgeben.
                if p.get("is_newcomer"):
                    trend_str = " · 🆕 neu in der Liga, Trend noch unbrauchbar"
                elif p.get("kb_trend") is not None:
                    trend_str = f" · 📈 {fmt_profit(p['kb_trend'])} €/Tag"
                else:
                    trend_str = ""
                bid_str = f"\n   👉 Max. Gebot: {fmt_money(p['max_bid'])} €" if p.get("max_bid") else ""
                lines.append(
                    f"\n• {esc(p['name'])} — {fmt_money(p['mv'])} €\n"
                    f"   {status_str}{avg_str}{rv_str}{trend_str}{bid_str}"
                )
                if p["fixture"]:
                    opps = ", ".join(p["fixture"]["opponents"])
                    lines.append(f"   📅 {p['fixture']['label']} ({opps})")

        if unverified:
            lines.append("\n❓ UNGEPRÜFT (kein Punkteschnitt verfügbar — Status-basiert)")
            for p in unverified[:5]:
                sc = p.get("status_code")
                status_str = f"{STATUS_ICONS.get(sc, '')} {STATUS_LABELS.get(sc, '?')}" if sc is not None else "?"
                # Bei Neuzugaengen keinen Kickbase-Trend zeigen: er wird gegen
                # einen nicht existierenden Vorwert gerechnet (Bornauw: +8,9
                # Mio/Tag). Die Prognose ignoriert ihn schon - der Report darf
                # ihn dann nicht doch als Tatsache ausgeben.
                if p.get("is_newcomer"):
                    trend_str = " · 🆕 neu in der Liga, Trend noch unbrauchbar"
                elif p.get("kb_trend") is not None:
                    trend_str = f" · 📈 {fmt_profit(p['kb_trend'])} €/Tag"
                else:
                    trend_str = ""
                lines.append(f"• {esc(p['name'])} — {fmt_money(p['mv'])} € · {status_str}{trend_str}")

    if blocked:
        lines.append("\n🚫 Nicht bieten")
        for p in blocked:
            lines.append(f"• {esc(p['name'])} — {p['reason']}")

    if pending:
        neulinge = [p for p in pending if p.get("is_newcomer")]
        rest = len(pending) - len(neulinge)
        teile = []
        if rest:
            teile.append(f"{rest} ohne Trendvergleich")
        if neulinge:
            namen = ", ".join(esc(p["name"]) for p in neulinge[:4])
            weitere = f" +{len(neulinge)-4}" if len(neulinge) > 4 else ""
            teile.append(f"{len(neulinge)} neu in der Liga ({namen}{weitere})")
        lines.append("\n⏳ Noch beobachten: " + " · ".join(teile))

    footer_parts = []
    if skipped:
        footer_parts.append(f"{len(skipped)} ohne nennenswerten Trend")
    if offered:
        footer_parts.append(f"{len(offered)} von Mitgliedern angeboten (getrackt)")
    if footer_parts:
        lines.append(f"\nℹ️ Ausgeblendet aber getrackt: {' · '.join(footer_parts)}.")

    if opponent_profiles:
        lines.append("\n🕵️ Konkurrenz-Radar")
        # Bereits in main() berechnete Werte verwenden - die enthalten die
        # Verkaufserloese. Neu berechnen wuerde die wieder verlieren.
        radar_cash = cash_by_name or {}

        unsicher = False
        for prof in sorted(opponent_profiles, key=lambda x: x["squad_size"]):
            gaps = ", ".join(prof["gaps"]) if prof["gaps"] else "vollständig"
            cash = radar_cash.get(prof["name"])
            cash_text = ""

            if cash:
                sell_txt = ""
                if cash.get("known_sell_count"):
                    sell_txt = f" · Verkäufe {cash['known_sell_count']}×"
                # Verlaesslichkeit kennzeichnen: Der Feed zeigt nur ~13
                # Eintraege fuer die ganze Liga, die Kaderbeobachtung greift
                # erst ab Beobachtungsbeginn. Wenige erfasste Vorgaenge bei
                # vollem Kader heissen: da fehlt Historie.
                erfasst = cash["known_buy_count"] + cash.get("known_sell_count", 0)
                marker = "≈" if erfasst >= 6 else "≈?"
                cash_text = (
                    f" · Cash {marker} {fmt_money(cash['min_cash'])} €"
                    f" · Käufe {cash['known_buy_count']}×{sell_txt}"
                )

            if cash and (cash["known_buy_count"] + cash.get("known_sell_count", 0)) < 6:
                unsicher = True
            lines.append(
                f"-  {esc(prof['name'])}: {prof['squad_size']} Spieler"
                f" · {gaps}{cash_text}"
            )
        if unsicher:
            lines.append("   <i>≈? = wenige Transfers erfasst, Cash-Wert unsicher</i>")

        if transfer_summary:
            lines.append("\n💸 Transfer-Aktivität")
            for mgr, info in sorted(transfer_summary.items(), key=lambda x: x[1]["total"], reverse=True)[:7]:
                pl = ", ".join(info["players"])
                lines.append(f"• {esc(mgr)}: {info['count']}×, {fmt_money(info['total'])} € ({esc(pl)})")

    return "\n".join(lines)

def split_message(message, maxlen):
    chunks, current = [], ""
    for line in message.splitlines():
        line = line.rstrip()
        while len(line) > maxlen:
            if current:
                chunks.append(current)
                current = ""
            sp = line.rfind(" ", 0, maxlen)
            if sp <= 0:
                sp = maxlen
            chunks.append(line[:sp])
            line = line[sp:].lstrip()
        cand = line if not current else f"{current}\n{line}"
        if len(cand) > maxlen:
            if current:
                chunks.append(current)
            current = line
        else:
            current = cand
    if current:
        chunks.append(current)
    return [c for c in chunks if c.strip()]

def send_telegram(message):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        raise ValueError("Telegram-Zugangsdaten fehlen.")
    chunks = split_message(message, CONFIG["TELEGRAM_MAX_LEN"])
    print(f"DEBUG Telegram {len(chunks)} Teile...")
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    for i, chunk in enumerate(chunks, 1):
        resp = requests.post(url, json={
            "chat_id": TELEGRAM_CHAT_ID, "text": chunk,
            "parse_mode": "HTML", "disable_web_page_preview": True,
        }, timeout=CONFIG["REQUEST_TIMEOUT"])
        print(f"DEBUG Telegram {i}/{len(chunks)} {len(chunk)}Z. HTTP {resp.status_code}")
        if resp.ok:
            time.sleep(0.25)
            continue
        plain = re.sub(r"<[^>]+>", "", chunk)
        requests.post(url, json={
            "chat_id": TELEGRAM_CHAT_ID, "text": plain,
        }, timeout=CONFIG["REQUEST_TIMEOUT"])
        time.sleep(0.25)

def main():
    print("Starte Kickbase Bot Engine...")
    token, league_id, my_manager_id = kickbase_login()
    all_market = get_market_players(token, league_id)
    diagnose_market_fields(all_market)

    free_players = [p for p in all_market if is_free_market_player(p)]
    offered_players = [p for p in all_market if not is_free_market_player(p)]
    print(f"{len(all_market)} Spieler am Markt: {len(free_players)} frei, "
          f"{len(offered_players)} von Mitgliedern angeboten (getrackt, ausgeblendet).")

    injured = get_ligainsider_injuries()
    headlines = get_rss_headlines()
    print(f"{len(headlines)} Schlagzeilen, {len(injured)} Verletzten-Eintraege.")

    history, history_sha = load_history()
    manuell = import_manual_transfers(history)
    if manuell:
        print(f"📋 {manuell} manuell erfasste Transfers eingetragen "
              f"(Lücke vor dem ersten Bot-Lauf)")

    removed = migrate_transfer_store(history)
    if removed:
        print(f"🧹 Transfer-Speicher bereinigt: {removed} doppelte Eintraege entfernt "
              f"(Kaeufe wurden zuvor doppelt gezaehlt)")

    my_budget = get_my_budget(token, league_id)
    print(f"Budget: {fmt_money(my_budget)}" if my_budget else "Budget unbekannt.")

    season_fixtures = get_season_fixtures()
    days_left = days_until_next_matchday(season_fixtures, CONFIG["DAYS_UNTIL_MATCHDAY"])
    print(f"Tage bis zum naechsten Spiel: {days_left}")

    matchdays_played = count_matchdays_played(season_fixtures)
    if matchdays_played < CONFIG["MAX_PER_TEAM_FROM_MATCHDAY"]:
        CONFIG["MAX_PER_TEAM"] = CONFIG["MAX_PER_TEAM_EARLY"]
    else:
        CONFIG["MAX_PER_TEAM"] = 2
    print(f"Spieltage absolviert: {matchdays_played} -> MAX_PER_TEAM = {CONFIG['MAX_PER_TEAM']}")

    ranking_res = get_ranking(token, league_id)
    # Teamwerte aus der Rangliste (Feld "tv") - Grundlage fuer die
    # 33%-Ueberziehungsregel.
    team_values = extract_team_values(ranking_res)
    my_team_value = team_values.get(str(my_manager_id))
    buying_power, overdraft = effective_buying_power(my_budget, my_team_value)
    if my_team_value:
        print(f"💪 Teamwert {fmt_money(my_team_value)} -> Überziehungsrahmen "
              f"{fmt_money(overdraft)} | Kaufkraft {fmt_money(buying_power)}")
    else:
        print("⚠️ Kein Teamwert in der Rangliste - rechne nur mit dem Kontostand.")
    my_team_counts = get_my_team_counts(token, league_id, my_manager_id)
    today_str = datetime.now(timezone.utc).date().isoformat()
    blocked_teams, opponent_profiles, opponent_squads = analyze_opponents(
        token, league_id, ranking_res, my_manager_id,
        history=history, today_str=today_str)

    my_squad_items = get_squad(token, league_id, my_manager_id) if my_manager_id else []
    my_gap_codes = missing_position_codes(count_by_pos(my_squad_items))

    feed_items = get_league_feed(token, league_id)
    diagnose_feed_types(feed_items)
    transfers = parse_feed(feed_items)
    new_tx = merge_transfers_into_history(transfers, history)
    transfer_summary = summarize_transfers_from_history(history)
    total_tx = len(history.get("_transfers", {}))
    print(f"Transfers im Feed: {len(transfers)} ({new_tx} neu) | gespeichert gesamt: {total_tx}")

    # Kaeufe: Feed (echte Preise, lueckenhaft) + Kaderbeobachtung (lueckenlos
    # ab Beobachtungsbeginn, Marktwert als Naeherung)
    buys_squad = get_squadbuys_summary(history)
    transfer_summary = combine_buys(transfer_summary, buys_squad)
    kader_quellen = sum(1 for v in transfer_summary.values() if v.get("source") == "Kader")
    if kader_quellen:
        print(f"🛒 Käufe: bei {kader_quellen} Manager(n) liefert die "
              f"Kaderbeobachtung mehr als der Feed")

    sales_feed = sales_from_feed_history(history)
    sales_squad = get_sales_summary(history)
    sales_summary = combine_sales(sales_feed, sales_squad)
    if sales_summary:
        feed_n = sum(1 for v in sales_summary.values() if v.get("source") == "Feed")
        print(f"Verkäufe erfasst: {len(sales_summary)} Manager "
              f"({feed_n}× aus Feed mit echten Preisen, Rest geschätzt)")

    cash_debug_rows = build_opponent_cash_tracker(
        opponent_profiles,
        transfer_summary,
        sales_summary=sales_summary,
    )
    print_cash_debug(cash_debug_rows)
    cash_by_name = {row["name"]: row for row in cash_debug_rows}

    win_probs = get_bundesliga_odds()
    diagnose_status_values(all_market)
    field_warnings = check_field_coverage(all_market)
    for w in field_warnings:
        print(f"🚨 FELD-AUSFALL: {w}")

    # Einmalige Suche nach dem Verlaufs-Endpunkt (nur ein Spieler, ~2 Sek.).
    # Nach erfolgreicher Identifikation kann das abgeschaltet werden.
    if CONFIG.get("DISCOVER_BUDGET"):
        dump_ranking_structure(ranking_res, my_manager_id, my_budget)
        discover_manager_budget(token, league_id, my_manager_id, my_budget,
                                extract_league_users(ranking_res))

    if CONFIG.get("DIAGNOSE_PERFORMANCE"):
        probe = next((p for p in all_market if is_free_market_player(p)), None)
        if probe:
            diagnose_performance_endpoint(
                token, league_id,
                probe.get("id", probe.get("i")),
                probe.get("n", "?"))

    if CONFIG.get("DISCOVER_MV_ENDPOINT"):
        probe = next((p for p in all_market if is_free_market_player(p)), None)
        if probe:
            discover_marketvalue_endpoint(
                token, league_id,
                probe.get("id", probe.get("i")),
                probe.get("n", "?"))

    # Die frueheren Rate-Kandidaten (smdc/stud) werden nicht mehr gebraucht:
    # /performance liefert die Einsaetze exakt gezaehlt.
    CONFIG["USE_APPEARANCE_WEIGHT"] = False

    fixture_cache, unmapped = {}, set()

    evaluated = []
    for p in all_market:
        tid_str = str(p.get("tid")) if p.get("tid") else None
        team_key = KICKBASE_TID_TO_TEAM.get(tid_str) if tid_str else None
        if tid_str and not team_key and tid_str not in unmapped:
            print(f"tid {tid_str} nicht zugeordnet: Spieler {p.get('n')}.")
            unmapped.add(tid_str)
        if team_key and team_key not in fixture_cache:
            fixture_cache[team_key] = fixture_difficulty(season_fixtures, team_key)
        fixture_info = fixture_cache.get(team_key) if team_key else None
        win_prob = win_probs.get(team_key) if team_key else None

        detail = None
        mv_range = None
        season_stats = None
        if is_free_market_player(p):
            pid_str = p.get("id", p.get("i"))
            if pid_str:
                detail = get_player_detail(token, league_id, pid_str)
                # Jahresspanne nur fuer freie Spieler - nur die stehen im Report.
                # Der Verlauf wird pro Spieler und Tag zwischengespeichert.
                mv_range = get_cached_mv_range(
                    token, league_id, pid_str,
                    int(p.get("mv", p.get("m", 0))), history, today_str)
                season_stats = fetch_season_stats(
                    token, league_id, pid_str, history, today_str)
            time.sleep(0.2)

        res = evaluate_player(p, history, injured, headlines, days_left,
                               my_team_counts, blocked_teams, buying_power,
                               fixture_info, win_prob, today_str, detail=detail,
                               matchdays_played=matchdays_played, mv_range=mv_range,
                               season_stats=season_stats)
        if not is_free_market_player(p):
            seller = get_seller_name(p) or "?"
            res["category"] = "market_offer"
            res["reason"] = f"angeboten von {seller}"
        evaluated.append(res)

    real_count = sum(1 for p in evaluated if p["has_real"])
    urgent_count = sum(1 for p in evaluated if p["is_urgent"])
    print(f"Echte Trendsdaten: {real_count}/{len(evaluated)}, Urgency-Alerts: {urgent_count}")

    # --- Rotierender Crawler: Verlauf fuer Spieler ausserhalb des Marktes ---
    # Baut ueber mehrere Laeufe die Datenbasis fuer alle bekannten Spieler auf,
    # damit neu auf dem Markt erscheinende Spieler sofort einen echten Trend
    # haben statt erst zwei Tage "kein Trendvergleich" zu zeigen.
    if CONFIG["CRAWL_BATCH_SIZE"] > 0:
        # Kader wiederverwenden, die analyze_opponents bereits geholt hat
        known_ids = collect_known_player_ids(
            all_market, opponent_squads + [my_squad_items], history)
        batch = pick_crawl_batch(known_ids, history,
                                 CONFIG["CRAWL_BATCH_SIZE"], today_str)
        if batch:
            ok, failed = crawl_players(token, league_id, batch, history,
                                       today_str, CONFIG["CRAWL_SLEEP"])
            tracked = sum(1 for k in history if not k.startswith("_"))
            print(f"Crawler: {len(known_ids)} IDs bekannt, {len(batch)} aufgefrischt "
                  f"({ok} ok, {failed} fehlgeschlagen) | Verlauf gesamt: {tracked} Spieler")
        else:
            print(f"Crawler: alle {len(known_ids)} bekannten Spieler heute bereits erfasst.")

    # Prognose-Ergebnisse dauerhaft festhalten und Mehrtages-Guete berechnen
    heute = record_prediction_results(evaluated, history, today_str)
    if heute:
        print(f"Prognose heute geprüft: {heute['n']} Vorhersagen, "
              f"{heute['hits']} Treffer, Richtung {heute['direction_ok']}x korrekt")
    accuracy = prediction_accuracy_summary(history, days=7)
    if accuracy:
        cal = accuracy.get("calibration")
        cal_txt = f", Kalibrierung {cal*100:.0f}%" if cal is not None else ""
        print(f"Prognose-Güte (7 Tage): {accuracy['n']} Vorhersagen, "
              f"Richtung {accuracy['direction_rate']*100:.0f}%, "
              f"Treffer {accuracy['hit_rate']*100:.0f}%{cal_txt}")
    else:
        print("Prognose-Güte: noch keine ausgewerteten Vorhersagen (ab morgen verfügbar).")

    save_history(history, history_sha)
    report = build_report(evaluated, my_budget, days_left, opponent_profiles,
                          transfer_summary, lineups_ok=False, odds_ok=bool(win_probs),
                          my_gap_codes=my_gap_codes, cash_by_name=cash_by_name,
                          accuracy=accuracy, buying_power=buying_power,
                          overdraft=overdraft, field_warnings=field_warnings)
    send_telegram(report)
    print("Pipeline-Durchlauf vollkommen erfolgreich beendet!")

if __name__ == "__main__":
    main()
