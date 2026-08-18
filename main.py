import os
import sys
import re
import json
import time
import base64
import requests
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from bs4 import BeautifulSoup

# ==========================================
# CONFIG & ENVIRONMENT SECRETS
# ==========================================
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
    "DAYS_UNTIL_MATCHDAY": 11,       # Fallback, wird aus echten Spieldaten abgeleitet
    "OVERPAY_BASE_FACTOR": 0.60,     # Basiswert; Quoten und Momentum skalieren diesen
    "HISTORY_FILE": "history.json",
    "MV_HISTORY_DAYS": 8,            # Mehr Tage = stabiler Median und bessere Momentum-Erkennung
    "MAX_PER_TEAM": 2,
    "OPPONENT_FETCH_SLEEP": 0.3,
    "SEASON_YEAR": 2026,
    "FIXTURE_LOOKAHEAD": 3,
    "TOP_DETAIL_COUNT": 10,
    "MIN_PROFIT_FOR_TOP": 300_000,
    "NEWS_MAX_PER_PLAYER": 2,
    "FEED_DEBUG_DUMP": False,         # nach erstem erfolgreichen Lauf auf False
    # Urgency: Auktionen mit weniger als X Minuten Restlaufzeit kommen ganz oben
    "URGENCY_THRESHOLD_MINUTES": 180,
    # Odds API
    "ODDS_SPORT": "soccer_germany_bundesliga",
    "ODDS_REGION": "eu",
    "ODDS_MARKET": "h2h",            # Head-to-Head = Siegquoten
    # Matchup-Faktoren (Siegwahrscheinlichkeit -> Overpay-Multiplikator)
    "MATCHUP_FACTOR_STRONG": 1.30,   # > 75% Siegchance
    "MATCHUP_FACTOR_NEUTRAL": 1.00,  # 40-75%
    "MATCHUP_FACTOR_WEAK": 0.75,     # < 40%
    # Momentum-Faktoren
    "MOMENTUM_BOOST": 1.25,          # Trend beschleunigt sich
    "MOMENTUM_BRAKE": 0.80,          # Trend verlangsamt sich
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
# OpenLigaDB-Namen -> Team-Keys (fuer Fixtures)
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


# ==========================================
# HILFSFUNKTIONEN
# ==========================================
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


# ==========================================
# KNOTEN 1: KICKBASE LIVE API
# ==========================================
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


# Felder, die bei Kickbase ueblicherweise einen ANBIETENDEN Manager kennzeichnen.
# Ist eins davon gesetzt/nicht-leer, wird der Spieler von einem Mitglied (oder dir)
# angeboten - also KEIN freier Kickbase-Spieler.
SELLER_FIELD_CANDIDATES = ["u", "unm", "unn", "usm", "uid", "seller", "ownerId", "oid", "own"]

def is_free_market_player(player):
    """
    True = von Kickbase auf den Markt gestellt (frei bietbar).
    False = von einem Liga-Mitglied (inkl. dir selbst) angeboten.
    Bewusst konservativ: nur wenn ein Verkaeufer-Feld eindeutig gefuellt ist,
    gilt der Spieler als 'nicht frei'.
    """
    for k in SELLER_FIELD_CANDIDATES:
        v = player.get(k)
        if v not in (None, "", 0, "0"):
            return False
    return True

def diagnose_market_fields(players):
    """Loggt einmalig die Feldnamen eines angebotenen und eines freien Spielers,
    damit die Verkaeufer-Erkennung ohne Raten verifiziert werden kann."""
    all_keys = set()
    for p in players[:5]:
        all_keys.update(p.keys())
    print(f"🔬 MARKT-FELDER (erste Spieler): {sorted(all_keys)}")
    # Beispiel eines Spielers mit potenziellem Verkaeufer-Feld
    for p in players:
        hits = {k: p.get(k) for k in SELLER_FIELD_CANDIDATES if p.get(k) not in (None, "", 0, "0")}
        if hits:
            print(f"🔬 Beispiel ANGEBOTENER Spieler '{p.get('n')}': Verkaeufer-Felder = {hits}")
            break
    else:
        print("🔬 Kein Spieler mit bekanntem Verkaeufer-Feld gefunden - evtl. anderes Feld, siehe Feldliste oben.")

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

def analyze_opponents(token, league_id, ranking_res, my_manager_id,
                      league_start_budget=None):
    """
    Holt alle Gegner-Kader und berechnet:
    - blocked_teams: 2-Spieler-Regel je Gegner ausgeschoepft
    - profiles: Kadergroesse, Positionsluecken, geschaetztes Restbudget
    league_start_budget: falls bekannt (aus Liga-Einstellungen), wird das
    Restbudget jedes Gegners geschaetzt. Fehlt der Wert, bleibt das Feld leer.
    """
    blocked, profiles = {}, []
    for uid, name in extract_league_users(ranking_res):
        if str(uid) == str(my_manager_id):
            continue
        squad = get_squad(token, league_id, uid)
        if not squad:
            continue
        for tid, cnt in count_by_team(squad).items():
            if cnt >= CONFIG["MAX_PER_TEAM"]:
                blocked.setdefault(tid, []).append(name)
        profiles.append({
            "name": name, "squad_size": len(squad),
            "gaps": pos_gaps(count_by_pos(squad)),
        })
        time.sleep(CONFIG["OPPONENT_FETCH_SLEEP"])
    print(f"🕵️ Konkurrenten analysiert: {len(profiles)}")
    return blocked, profiles

def get_my_team_counts(token, league_id, my_manager_id):
    if not my_manager_id:
        return {}
    return count_by_team(get_squad(token, league_id, my_manager_id))


# ==========================================
# KNOTEN 1b: LIGA-FEED
# ==========================================
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
    Liest Transfers und Verkauefe aus dem Feed.
    Bekannte Feldnamen (aus Live-Daten verifiziert): data.byr=Kaeufer, data.pn=Spieler,
    data.trp=Preis. Typ 15 = Kauf vom Markt, andere Typen werden ebenfalls mitgenommen.
    """
    transfers = []
    name_keys = ["byr", "buyerName", "bn", "userName"]
    player_keys = ["pn", "playerName", "pfn"]
    price_keys = ["trp", "price", "value", "mv"]
    for item in feed_items:
        if not isinstance(item, dict):
            continue
        flat = dict(item)
        for nk in ["data", "d", "meta"]:
            if isinstance(item.get(nk), dict):
                flat.update(item[nk])
        buyer = next((flat[k].strip() for k in name_keys
                      if isinstance(flat.get(k), str) and flat[k].strip()), None)
        player = next((flat[k].strip() for k in player_keys
                       if isinstance(flat.get(k), str) and flat[k].strip()), None)
        price = next((int(flat[k]) for k in price_keys
                      if isinstance(flat.get(k), (int, float)) and flat[k] > 0), None)
        if buyer and player and price:
            transfers.append({"buyer": buyer, "player": player, "price": price,
                              "date": item.get("dt")})
    return transfers

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
# KNOTEN 2: WETTQUOTEN (The Odds API)
# ==========================================
def get_bundesliga_odds():
    """
    Holt Siegquoten fuer alle Bundesliga-Spiele der naechsten Spielrunde.
    Rueckgabe: dict team_name_lower -> win_probability (0-1)
    oder {} bei Fehler/fehlendem Key.
    """
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
            for book in event.get("bookmakers", [])[:1]:  # nur erster Anbieter
                for market in book.get("markets", []):
                    if market.get("key") != "h2h":
                        continue
                    for outcome in market.get("outcomes", []):
                        tname = outcome.get("name", "").lower()
                        odds = outcome.get("price", 0)
                        if odds > 1:
                            prob = 1.0 / odds
                            # Finde den passenden Team-Key
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
    """
    Wandelt Siegwahrscheinlichkeit in Overpay-Multiplikator um.
    0.75+ -> 1.30 (starker Favorit, Punkte fast sicher)
    0.40-0.75 -> 1.00 (ausgeglichenes Spiel)
    <0.40 -> 0.75 (Aussenseiter, Punkterisiko)
    """
    if win_prob is None:
        return 1.0
    if win_prob >= 0.75:
        return CONFIG["MATCHUP_FACTOR_STRONG"]
    if win_prob >= 0.40:
        return CONFIG["MATCHUP_FACTOR_NEUTRAL"]
    return CONFIG["MATCHUP_FACTOR_WEAK"]


# ==========================================
# KNOTEN 3: NEWS (RSS) + LIGAINSIDER
# ==========================================
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


# ==========================================
# KNOTEN 4: RESTPROGRAMM (OpenLigaDB)
# ==========================================
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


# ==========================================
# KNOTEN 5: HISTORY, TREND & MOMENTUM
# ==========================================
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
            print(f"✅ Verlauf geladen ({len(h)} Spieler).")
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
    """
    Speichert einen Messpunkt pro Kalendertag (nicht pro Lauf).
    Rueckgabe: (tages_deltas_liste, has_real_data)
    """
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
    if days and days[-1].get("d") == today_str:
        days[-1]["mv"] = current_mv
    else:
        days.append({"d": today_str, "mv": current_mv})
    days = days[-CONFIG["MV_HISTORY_DAYS"]:]
    history[pid] = {"days": days}
    mvs = [d["mv"] for d in days]
    if len(mvs) >= 2:
        deltas = [mvs[i] - mvs[i-1] for i in range(1, len(mvs))]
        return deltas, True
    return [], False

def compute_trend(deltas):
    """Median der Tages-Deltas als stabiler Basistrend."""
    if not deltas:
        return 0, False
    s = sorted(deltas)
    return s[len(s) // 2], True

def compute_momentum(deltas):
    """
    Erkennt, ob sich der Trend beschleunigt oder verlangsamt.
    Braucht mind. 3 Deltas. Vergleicht die letzten 2 Deltas gegen den Median.
    """
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
    Prognose mit Daempfung. overpay_factor integriert bereits Quoten+Momentum.
    """
    if daily_trend <= 0 or days_left <= 0:
        return current_mv, None, 0   # kein positiver Trend -> kein max_bid
    damping, growth, cd = 0.96, 0.0, float(daily_trend)
    for _ in range(days_left):
        growth += cd
        cd *= damping
    proj_mv = int(current_mv + growth)
    max_bid = int(current_mv + growth * overpay_factor)
    return proj_mv, max_bid, int(growth)


# ==========================================
# KNOTEN 6: SPIELER-BEWERTUNG
# ==========================================
def evaluate_player(player, history, injured_list, headlines, days_left,
                    my_team_counts=None, blocked_teams=None, my_budget=None,
                    fixture_info=None, win_prob=None, today_str=None):
    pid = player.get("id", player.get("i"))
    tid = player.get("tid")
    name = player.get("n", player.get("lastName", ""))
    if not name and "fn" in player and "ln" in player:
        name = f"{player['fn']} {player['ln']}"
    if not name:
        name = "Unbekannt"
    mv = int(player.get("mv", player.get("m", 0)))
    exs = int(player.get("exs", 0))   # Restlaufzeit der Auktion in Sekunden

    deltas, has_real = update_history(pid, mv, history, today_str)
    daily_trend, _ = compute_trend(deltas)
    momentum = compute_momentum(deltas) if has_real else "neutral"

    # Overpay-Faktor = Basis × Quoten-Multiplikator × Momentum-Multiplikator
    mf = matchup_factor(win_prob)
    mf_momentum = (CONFIG["MOMENTUM_BOOST"] if momentum == "beschleunigt"
                   else CONFIG["MOMENTUM_BRAKE"] if momentum == "verlangsamt"
                   else 1.0)
    overpay_factor = CONFIG["OVERPAY_BASE_FACTOR"] * mf * mf_momentum

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

    # Reihenfolge wichtig: harte Blocker zuerst, DANN "kein Trend" (pending),
    # erst danach Budget/Kaufkategorien. So landet ein Spieler ohne Tagesvergleich
    # (max_bid == None) in "pending" und nicht faelschlich in "Budget zu teuer".
    if violates:
        category, reason = "blocked", "2-Spieler-Regel: du hast schon 2 aus diesem Verein"
    elif is_injured:
        category, reason = "blocked", "verletzt / gesperrt (LigaInsider)"
    elif negative:
        category, reason = "blocked", "negative Schlagzeile — erst prüfen"
    elif not has_real or max_bid is None:
        category, reason = "pending", "noch kein Tagesvergleich — morgen entscheiden"
    elif my_budget is not None and max_bid > my_budget:
        category, reason = "blocked", f"Gebot {fmt_money(max_bid)} über Budget"
    elif raw_profit >= 1_500_000:
        category, reason = "top", "hoher erwarteter Gewinn"
    elif raw_profit >= CONFIG["MIN_PROFIT_FOR_TOP"]:
        category, reason = "watch", "solider Aufwärtstrend"
    else:
        category, reason = "skip", "kein nennenswerter Trend"

    budget_ok = max_bid is None or my_budget is None or max_bid <= my_budget

    return {
        "id": pid, "name": name, "mv": mv, "proj_mv": proj_mv,
        "max_bid": max_bid, "exp_profit": raw_profit,
        "daily_trend": daily_trend, "momentum": momentum,
        "has_real": has_real, "is_injured": is_injured,
        "matched_news": matched_news, "violates": violates,
        "opp_blocked": opp_blocked, "budget_ok": budget_ok,
        "category": category, "reason": reason,
        "fixture": fixture_info, "win_prob": win_prob,
        "overpay_factor": overpay_factor,
        "urgency_minutes": urgency_minutes, "is_urgent": is_urgent,
        "exs": exs,
    }


# ==========================================
# KNOTEN 7: REPORT
# ==========================================
def build_report(evaluated, my_budget, days_left, opponent_profiles,
                 transfer_summary, lineups_ok=True, odds_ok=True):
    lines = []
    lines.append(f"⚽ <b>Kickbase Markt-Report</b> — noch {days_left} Tage")
    if my_budget is not None:
        lines.append(f"💳 Budget: <b>{fmt_money(my_budget)} €</b>")
    if not lineups_ok:
        lines.append("⚠️ <i>Startelf-Daten nicht verfügbar.</i>")
    if not odds_ok:
        lines.append("⚠️ <i>Wettquoten nicht verfügbar (ODDS_API_KEY prüfen).</i>")

    tops = sorted([p for p in evaluated if p["category"] == "top"],
                  key=lambda x: (x["is_urgent"], x["exp_profit"]), reverse=True)
    watch = sorted([p for p in evaluated if p["category"] == "watch"],
                   key=lambda x: (x["is_urgent"], x["exp_profit"]), reverse=True)
    blocked = [p for p in evaluated if p["category"] == "blocked"]
    pending = [p for p in evaluated if p["category"] == "pending"]
    skipped = [p for p in evaluated if p["category"] == "skip"]

    # --- URGENCY-Alerts zuerst (Auktion läuft bald ab!) ---
    urgent = [p for p in tops + watch if p["is_urgent"]]
    if urgent:
        lines.append("\n⏰ <b>JETZT HANDELN</b> (Auktion läuft bald ab!)")
        for p in urgent:
            lines.append(
                f"• <b>{esc(p['name'])}</b> — {fmt_money(p['mv'])} €"
                f" | endet in <b>{fmt_minutes(p['exs'])}</b>"
                f" | Gewinn: <b>{fmt_profit(p['exp_profit'])} €</b>"
                f" | Gebot bis <b>{fmt_money(p['max_bid'])} €</b>"
            )

    detail = [p for p in tops + watch if not p["is_urgent"]][:CONFIG["TOP_DETAIL_COUNT"]]
    if detail:
        lines.append("\n🔥 <b>BESTE CHANCEN</b> (nach erwartetem Gewinn)")
        for i, p in enumerate(detail, start=1):
            momentum_icon = " 🚀" if p["momentum"] == "beschleunigt" else (" 📉" if p["momentum"] == "verlangsamt" else "")
            lines.append(
                f"\n<b>{i}. {esc(p['name'])}</b> — {fmt_money(p['mv'])} €{momentum_icon}\n"
                f"   Erwartet: {fmt_money(p['proj_mv'])} € (<b>{fmt_profit(p['exp_profit'])} €</b>)\n"
                f"   👉 Max. Gebot: <b>{fmt_money(p['max_bid'])} €</b>"
            )
            extras = []
            if p["fixture"]:
                opps = ", ".join(p["fixture"]["opponents"])
                extras.append(f"📅 {p['fixture']['label']} ({opps})")
            if p["win_prob"] is not None:
                extras.append(f"🎰 {p['win_prob']*100:.0f}% Siegchance")
            if p["opp_blocked"]:
                extras.append(f"🔓 Verein bei {len(p['opp_blocked'])} Gegner(n) voll")
            if extras:
                lines.append("   " + " · ".join(extras))
            for h in p["matched_news"]:
                lines.append(f"   📰 {esc(h[:120])}")
    else:
        lines.append("\n😐 Keine lohnenden Kaufchancen gefunden.")

    rest = [p for p in tops + watch if not p["is_urgent"]][CONFIG["TOP_DETAIL_COUNT"]:]
    if rest:
        lines.append("\n👀 <b>Weitere</b>")
        for p in rest:
            lines.append(f"• {esc(p['name'])} — {fmt_money(p['mv'])} € ({fmt_profit(p['exp_profit'])} €)")

    if blocked:
        lines.append("\n🚫 <b>Nicht bieten</b>")
        for p in blocked:
            lines.append(f"• {esc(p['name'])} — {p['reason']}")

    if pending:
        lines.append(f"\n⏳ <b>Noch beobachten</b> ({len(pending)} Spieler ohne Trendvergleich)")

    if skipped:
        lines.append(f"\nℹ️ {len(skipped)} Spieler ohne nennenswerten Trend ausgeblendet.")

    if opponent_profiles:
        lines.append("\n🕵️ <b>Konkurrenz-Radar</b>")
        for prof in sorted(opponent_profiles, key=lambda x: x["squad_size"]):
            g = ", ".join(prof["gaps"]) if prof["gaps"] else "vollständig"
            lines.append(f"• {esc(prof['name'])}: {prof['squad_size']} Spieler · {g}")

    if transfer_summary:
        lines.append("\n💸 <b>Transfer-Aktivität</b>")
        for mgr, info in sorted(transfer_summary.items(),
                                 key=lambda x: x[1]["total"], reverse=True)[:7]:
            pl = ", ".join(info["players"])
            lines.append(f"• {esc(mgr)}: {info['count']}×, {fmt_money(info['total'])} € ({esc(pl)})")

    return "\n".join(lines)


# ==========================================
# KNOTEN 8: TELEGRAM
# ==========================================
def split_message(message, max_len):
    chunks, current = [], ""
    for line in message.splitlines():
        line = line.rstrip()
        while len(line) > max_len:
            if current:
                chunks.append(current)
                current = ""
            sp = line.rfind(" ", 0, max_len)
            if sp <= 0:
                sp = max_len
            chunks.append(line[:sp])
            line = line[sp:].lstrip()
        cand = line if not current else f"{current}\n{line}"
        if len(cand) > max_len:
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
        raise ValueError("❌ Telegram-Zugangsdaten fehlen.")
    chunks = split_message(message, CONFIG["TELEGRAM_MAX_LEN"])
    print(f"DEBUG Telegram: {len(chunks)} Teil(e)...")
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    for i, chunk in enumerate(chunks, 1):
        resp = requests.post(url, json={
            "chat_id": TELEGRAM_CHAT_ID, "text": chunk,
            "parse_mode": "HTML", "disable_web_page_preview": True,
        }, timeout=CONFIG["REQUEST_TIMEOUT"])
        print(f"DEBUG Telegram {i}/{len(chunks)} ({len(chunk)} Z.): HTTP {resp.status_code}")
        if resp.ok:
            time.sleep(0.25)
            continue
        plain = re.sub(r"<[^>]+>", "", chunk)
        requests.post(url, json={
            "chat_id": TELEGRAM_CHAT_ID, "text": plain,
        }, timeout=CONFIG["REQUEST_TIMEOUT"])
        time.sleep(0.25)


# ==========================================
# HAUPTABLAUF
# ==========================================
def main():
    print("🚀 Starte Kickbase Bot Engine...")

    token, league_id, my_manager_id = kickbase_login()
    all_market = get_market_players(token, league_id)
    diagnose_market_fields(all_market)
    raw_players = [p for p in all_market if is_free_market_player(p)]
    filtered_out = len(all_market) - len(raw_players)
    print(f"📊 {len(all_market)} Spieler am Markt, davon {len(raw_players)} frei "
          f"({filtered_out} von Mitgliedern/dir angeboten und ausgeblendet).")

    injured = get_ligainsider_injuries()
    headlines = get_rss_headlines()
    print(f"📰 {len(headlines)} Schlagzeilen, {len(injured)} Verletzten-Eintraege.")

    history, history_sha = load_history()

    my_budget = get_my_budget(token, league_id)
    print(f"💳 Budget: {fmt_money(my_budget)} €" if my_budget else "⚠️ Budget unbekannt.")

    ranking_res = get_ranking(token, league_id)
    my_team_counts = get_my_team_counts(token, league_id, my_manager_id)
    blocked_teams, opponent_profiles = analyze_opponents(
        token, league_id, ranking_res, my_manager_id)

    feed_items = get_league_feed(token, league_id)
    transfers = parse_feed(feed_items)
    transfer_summary = summarize_transfers(transfers)
    print(f"💸 Transfers im Feed: {len(transfers)}")

    season_fixtures = get_season_fixtures()
    days_left = days_until_next_matchday(season_fixtures, CONFIG["DAYS_UNTIL_MATCHDAY"])
    print(f"📅 Tage bis zum naechsten Spiel: {days_left}")

    win_probs = get_bundesliga_odds()

    fixture_cache, unmapped = {}, set()
    today_str = datetime.now(timezone.utc).date().isoformat()
    evaluated = []

    for p in raw_players:
        tid_str = str(p.get("tid")) if p.get("tid") else None
        team_key = KICKBASE_TID_TO_TEAM.get(tid_str) if tid_str else None
        if tid_str and not team_key and tid_str not in unmapped:
            print(f"ℹ️ tid {tid_str} nicht zugeordnet (Spieler: {p.get('n')}).")
            unmapped.add(tid_str)
        if team_key and team_key not in fixture_cache:
            fixture_cache[team_key] = fixture_difficulty(season_fixtures, team_key)
        fixture_info = fixture_cache.get(team_key) if team_key else None

        # Wettquote des Vereins fuer das naechste Spiel
        win_prob = win_probs.get(team_key) if team_key else None

        evaluated.append(evaluate_player(
            p, history, injured, headlines, days_left,
            my_team_counts, blocked_teams, my_budget,
            fixture_info, win_prob, today_str))

    real_count = sum(1 for p in evaluated if p["has_real"])
    urgent_count = sum(1 for p in evaluated if p["is_urgent"])
    print(f"📈 Echte Trendsdaten: {real_count}/{len(evaluated)}, Urgency-Alerts: {urgent_count}")

    save_history(history, history_sha)

    report = build_report(
        evaluated, my_budget, days_left,
        opponent_profiles, transfer_summary,
        lineups_ok=False,
        odds_ok=bool(win_probs))
    send_telegram(report)
    print("✅ Pipeline-Durchlauf vollkommen erfolgreich beendet!")


if __name__ == "__main__":
    main()
