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

CONFIG = {
    "DRY_RUN": False,
    "TELEGRAM_MAX_LEN": 4000,
    "REQUEST_TIMEOUT": 15,
    "DAYS_UNTIL_MATCHDAY": 11,
    "OVERPAY_RISK_FACTOR": 0.60,
    "HISTORY_FILE": "history.json",
    "MV_HISTORY_DAYS": 6,
    "MAX_PER_TEAM": 2,
    "OPPONENT_FETCH_SLEEP": 0.3,
    "SEASON_YEAR": 2026,
    "FIXTURE_LOOKAHEAD": 3,
    "TOP_DETAIL_COUNT": 10,
    "MIN_PROFIT_FOR_TOP": 300_000,
    "NEWS_MAX_PER_PLAYER": 2,
    "FEED_DEBUG_DUMP": True,   # schreibt die Rohstruktur des Liga-Feeds ins Log
}

NEWS_FEEDS = [
    "https://newsfeed.kicker.de/news/bundesliga",
    "https://www.sportschau.de/fussball/bundesliga/index~rss2.xml",
    "https://feeds.t-online.de/rss/transfermarkt",
]

# Mehrere Kandidaten, weil LigaInsider seine URLs schon mehrfach geaendert hat.
# Im Log steht anschliessend, welche tatsaechlich Daten geliefert hat.
LIGAINSIDER_INJURY_URLS = [
    "https://www.ligainsider.de/bundesliga/verletzte-und-gesperrte-spieler/",
    "https://www.ligainsider.de/verletzungen-sperren/",
]
LIGAINSIDER_LINEUP_URLS = [
    "https://www.ligainsider.de/bundesliga/voraussichtliche-aufstellungen/",
    "https://www.ligainsider.de/bundesliga/aufstellung/",
]

BROWSER_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"),
    "Accept-Language": "de-DE,de;q=0.9",
}

NEGATIVE_NEWS_PATTERN = r"(verletz|muskul|ausfall|gesperrt|rote karte|kreuzband|bruch|reha|op\b|operation|trainingsr(ü|ue)ckstand|f(ä|ae)llt aus|bank)"

# ==========================================
# VEREINS-ZUORDNUNG
# ==========================================
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

TEAM_STRENGTH_LAST_SEASON = {
    "bayern": 89, "dortmund": 73, "leipzig": 65, "stuttgart": 62,
    "hoffenheim": 61, "leverkusen": 59, "freiburg": 47, "frankfurt": 44,
    "augsburg": 43, "mainz": 40, "union berlin": 39, "gladbach": 38,
    "hamburg": 38, "koeln": 32, "werder": 32, "paderborn": 22,
    "schalke": 20, "elversberg": 20,
}
TEAM_STRENGTH_DEFAULT = 45
BUNDESLIGA_TEAM_KEYS = list(TEAM_STRENGTH_LAST_SEASON.keys())

# Kickbase-Positionscodes (in der API als "pos")
POSITION_NAMES = {1: "TW", 2: "ABW", 3: "MF", 4: "ANG"}
# Grober Mindestbedarf fuer eine spielbare Aufstellung
POSITION_MIN_NEEDED = {1: 1, 2: 3, 3: 3, 4: 1}


# ==========================================
# HILFSFUNKTIONEN
# ==========================================
def fmt_money(value):
    """12500000 -> '12,5 Mio'"""
    try:
        value = int(value)
    except (TypeError, ValueError):
        return "?"
    sign = "-" if value < 0 else ""
    v = abs(value)
    if v >= 1_000_000:
        return f"{sign}{v / 1_000_000:.1f}".replace(".", ",") + " Mio"
    if v >= 1_000:
        return f"{sign}{v / 1_000:.0f}k"
    return f"{sign}{v}"


def fmt_profit(value):
    if value is None:
        return "?"
    return ("+" + fmt_money(value)) if value > 0 else fmt_money(value)


def esc(value):
    """HTML-escaping fuer Telegram parse_mode=HTML."""
    return (str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


# ==========================================
# KNOTEN 1: KICKBASE LIVE API
# ==========================================
def kickbase_login():
    print("🚀 Starte Kickbase Login mit Android-Header...")
    headers = {"User-Agent": "Kickbase/8.10.0 (Android)", "Content-Type": "application/json"}
    body = {"em": KB_EMAIL, "pass": KB_PASSWORD, "loy": False, "rep": {}}
    resp = requests.post("https://api.kickbase.com/v4/user/login", json=body, headers=headers)
    if resp.status_code != 200:
        raise ValueError(f"❌ Kickbase API Login-Fehler (HTTP {resp.status_code}): {resp.text}")

    data = resp.json()
    token = data.get("tkn")
    league_id = None
    if "lins" in data and len(data["lins"]) > 0:
        league_id = data["lins"][0]["i"]
    if not token or not league_id:
        raise ValueError("❌ Konnte Token oder Liga-ID nicht auslesen.")

    user_obj = data.get("u", {}) or {}
    my_user_id = user_obj.get("id") or user_obj.get("i")
    my_manager_id = user_obj.get("mid") or my_user_id
    if not my_manager_id:
        print("⚠️ Konnte eigene Manager-ID nicht auslesen.")
    return token, str(league_id), my_manager_id


def _kb_headers(token):
    return {
        "Authorization": f"Bearer {token}",
        "User-Agent": "Kickbase/8.10.0 (Android)",
        "Accept": "application/json",
    }


def get_market_players(token, league_id):
    url = f"https://api.kickbase.com/v4/leagues/{league_id}/market"
    resp = requests.get(url, headers=_kb_headers(token), timeout=CONFIG["REQUEST_TIMEOUT"])
    resp.raise_for_status()
    data = resp.json()

    if "players" in data and data["players"]:
        return data["players"]
    for key, value in data.items():
        if isinstance(value, list) and len(value) > 0 and isinstance(value[0], dict):
            if any(k in value[0] for k in ["id", "i", "n", "lastName", "mv"]):
                print(f"🔍 Kickbase API Update umgangen: Spieler im neuen Ordner '{key}' gefunden!")
                return value
    print(f"⚠️ DEBUG RAW MARKTDATEN: {json.dumps(data)[:800]}")
    return []


def get_my_budget(token, league_id):
    for path in ["me/budget", "me"]:
        try:
            resp = requests.get(f"https://api.kickbase.com/v4/leagues/{league_id}/{path}",
                                headers=_kb_headers(token), timeout=CONFIG["REQUEST_TIMEOUT"])
            resp.raise_for_status()
            data = resp.json()
            for key in ["budget", "b", "amount"]:
                if key in data and data[key] is not None:
                    return int(data[key])
        except Exception as e:
            print(f"⚠️ Budget ueber /{path} nicht ladbar: {e}")
    return None


def get_ranking(token, league_id):
    try:
        resp = requests.get(f"https://api.kickbase.com/v4/leagues/{league_id}/ranking",
                            headers=_kb_headers(token), timeout=CONFIG["REQUEST_TIMEOUT"])
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        print(f"⚠️ Liga-Rangliste nicht ladbar: {e}")
        return {}


def extract_league_users(ranking_res):
    """
    Findet die Teilnehmerliste in der Ranglisten-Antwort.
    Kickbase hat den Schluessel schon mehrfach geaendert ("users", "us", "it"...),
    deshalb Smart-Scan wie beim Transfermarkt statt fester Annahme.
    Rueckgabe: Liste von (manager_id, name).
    """
    if not isinstance(ranking_res, dict):
        return []

    candidates = []
    for key, value in ranking_res.items():
        if isinstance(value, list) and value and isinstance(value[0], dict):
            candidates.append((key, value))

    id_keys = ["mid", "id", "i", "u", "uid"]
    name_keys = ["name", "n", "unm", "userName"]

    for key, items in candidates:
        users = []
        for item in items:
            uid = next((item[k] for k in id_keys if item.get(k)), None)
            uname = next((item[k] for k in name_keys
                          if isinstance(item.get(k), str) and item[k].strip()), None)
            if uid:
                users.append((str(uid), uname or "?"))
        if users:
            print(f"🔍 Liga-Teilnehmer gefunden im Ordner '{key}': {len(users)}")
            return users

    print("⚠️ Keine Teilnehmerliste in der Rangliste erkannt.")
    print(f"🔬 RANGLISTE-ROHSTRUKTUR: {json.dumps(ranking_res, ensure_ascii=False)[:900]}")
    return []


def get_squad(token, league_id, manager_id):
    try:
        resp = requests.get(
            f"https://api.kickbase.com/v4/leagues/{league_id}/managers/{manager_id}/squad",
            headers=_kb_headers(token), timeout=CONFIG["REQUEST_TIMEOUT"])
        resp.raise_for_status()
        return resp.json().get("it", [])
    except Exception as e:
        print(f"⚠️ Kader von Manager {manager_id} nicht ladbar: {e}")
        return []


def count_players_by_team(squad_items):
    counts = {}
    for sp in squad_items:
        tid = sp.get("tid")
        if tid:
            counts[tid] = counts.get(tid, 0) + 1
    return counts


def count_players_by_position(squad_items):
    """Zaehlt Spieler je Position (Kickbase: 1=TW, 2=ABW, 3=MF, 4=ANG)."""
    counts = {}
    for sp in squad_items:
        pos = sp.get("pos", sp.get("p"))
        try:
            pos = int(pos)
        except (TypeError, ValueError):
            continue
        counts[pos] = counts.get(pos, 0) + 1
    return counts


def find_position_gaps(pos_counts):
    """Gibt die Positionen zurueck, an denen ein Manager unter dem Mindestbedarf liegt."""
    gaps = []
    for pos, needed in POSITION_MIN_NEEDED.items():
        have = pos_counts.get(pos, 0)
        if have < needed:
            gaps.append(f"{POSITION_NAMES.get(pos, pos)} ({have}/{needed})")
    return gaps


def analyze_opponents(token, league_id, ranking_res, my_manager_id):
    """
    Holt die Kader aller Konkurrenten EINMAL und leitet daraus ab:
      - blocked_teams: wo die 2-Spieler-Regel bei Gegnern schon ausgereizt ist
      - opponent_profiles: Kadergroesse + Positionsluecken je Gegner
    """
    blocked = {}
    profiles = []
    users = extract_league_users(ranking_res)
    for opponent_id, name in users:
        if not opponent_id or str(opponent_id) == str(my_manager_id):
            continue
        squad = get_squad(token, league_id, opponent_id)
        if not squad:
            continue

        for tid, count in count_players_by_team(squad).items():
            if count >= CONFIG["MAX_PER_TEAM"]:
                blocked.setdefault(tid, []).append(name)

        pos_counts = count_players_by_position(squad)
        profiles.append({
            "name": name,
            "squad_size": len(squad),
            "positions": pos_counts,
            "gaps": find_position_gaps(pos_counts),
        })
        time.sleep(CONFIG["OPPONENT_FETCH_SLEEP"])
    return blocked, profiles


def get_my_team_counts(token, league_id, my_manager_id):
    if not my_manager_id:
        return {}
    return count_players_by_team(get_squad(token, league_id, my_manager_id))


# ==========================================
# KNOTEN 1b: LIGA-FEED (EXPERIMENTELL)
# ==========================================
def get_league_feed(token, league_id):
    """Holt den Liga-Feed (Transfer-Historie). Struktur ist nicht dokumentiert,
    deshalb defensiv: bei Fehlern leere Liste, kein Abbruch."""
    for path in ["feed", "activitiesFeed", "activity"]:
        try:
            resp = requests.get(f"https://api.kickbase.com/v4/leagues/{league_id}/{path}",
                                headers=_kb_headers(token), timeout=CONFIG["REQUEST_TIMEOUT"])
            if resp.status_code != 200:
                continue
            data = resp.json()
            items = None
            if isinstance(data, list):
                items = data
            elif isinstance(data, dict):
                for key, value in data.items():
                    if isinstance(value, list) and value and isinstance(value[0], dict):
                        items = value
                        print(f"🔍 Liga-Feed gefunden unter /{path} im Ordner '{key}'.")
                        break
            if items:
                if CONFIG["FEED_DEBUG_DUMP"]:
                    print("🔬 FEED-ROHSTRUKTUR (erster Eintrag, zur Feldnamen-Pruefung):")
                    print(json.dumps(items[0], ensure_ascii=False, indent=2)[:1200])
                return items
        except Exception as e:
            print(f"⚠️ Liga-Feed /{path} nicht ladbar: {e}")
    print("ℹ️ Kein Liga-Feed gefunden - Transfer-Historie der Mitglieder nicht verfuegbar.")
    return []


def parse_feed_transfers(feed_items):
    """
    Liest abgeschlossene Transfers aus dem Feed.
    Schema anhand echter Live-Daten verifiziert (Stand: dieser Lauf):
        data.byr = Kaeufer-Name, data.pn = Spielername, data.trp = Preis
    Die uebrigen Schluessel bleiben als Fallback stehen, falls Kickbase
    die Bezeichnungen erneut aendert.
    """
    transfers = []
    name_keys = ["byr", "buyerName", "bn", "userName", "un", "managerName"]
    seller_keys = ["slr", "sen", "sellerName"]
    player_keys = ["pn", "playerName", "playerFirstName", "pfn", "lastName"]
    price_keys = ["trp", "price", "value"]

    for item in feed_items:
        if not isinstance(item, dict):
            continue
        flat = dict(item)
        for nest_key in ["data", "d", "meta", "m"]:
            nested = item.get(nest_key)
            if isinstance(nested, dict):
                flat.update(nested)

        buyer = next((flat[k].strip() for k in name_keys
                      if isinstance(flat.get(k), str) and flat[k].strip()), None)
        seller = next((flat[k].strip() for k in seller_keys
                       if isinstance(flat.get(k), str) and flat[k].strip()), None)
        player = next((flat[k].strip() for k in player_keys
                       if isinstance(flat.get(k), str) and flat[k].strip()), None)
        price = None
        for k in price_keys:
            v = flat.get(k)
            if isinstance(v, (int, float)) and v > 0:
                price = int(v)
                break

        if buyer and player and price:
            transfers.append({"buyer": buyer, "seller": seller,
                              "player": player, "price": price,
                              "date": item.get("dt")})
    return transfers


def summarize_transfers(transfers):
    """Fasst Ausgaben je Manager zusammen."""
    per_manager = {}
    for t in transfers:
        entry = per_manager.setdefault(t["buyer"], {"count": 0, "total": 0, "players": []})
        entry["count"] += 1
        entry["total"] += t["price"]
        if len(entry["players"]) < 3:
            entry["players"].append(t["player"])
    return per_manager


# ==========================================
# KNOTEN 2: NEWS (RSS) + LIGAINSIDER
# ==========================================
def get_rss_headlines():
    """Sammelt Schlagzeilen aus mehreren RSS-Feeds."""
    headlines = []
    for url in NEWS_FEEDS:
        try:
            resp = requests.get(url, headers=BROWSER_HEADERS, timeout=CONFIG["REQUEST_TIMEOUT"])
            resp.raise_for_status()
            resp.encoding = "utf-8"
            try:
                root = ET.fromstring(resp.text)
            except ET.ParseError:
                print(f"⚠️ RSS nicht parsebar: {url}")
                continue
            count = 0
            for item in root.findall(".//item"):
                title_el = item.find("title")
                if title_el is not None and title_el.text:
                    headlines.append(title_el.text.strip())
                    count += 1
            print(f"ℹ️ RSS geladen ({count} Schlagzeilen): {url}")
        except Exception as e:
            print(f"⚠️ RSS-Feed nicht ladbar ({url}): {e}")
    # Duplikate entfernen, Reihenfolge behalten
    seen, unique = set(), []
    for h in headlines:
        if h not in seen:
            seen.add(h)
            unique.append(h)
    return unique


def _scrape_first_working(urls, extractor, label, diagnose=False):
    """Probiert mehrere URLs durch und nimmt die erste, die Daten liefert."""
    last_soup = None
    for url in urls:
        try:
            resp = requests.get(url, headers=BROWSER_HEADERS, timeout=CONFIG["REQUEST_TIMEOUT"])
            if resp.status_code != 200:
                print(f"⚠️ {label}: HTTP {resp.status_code} bei {url}")
                continue
            resp.encoding = "utf-8"
            soup = BeautifulSoup(resp.text, "html.parser")
            last_soup = soup
            result = extractor(soup)
            if result:
                print(f"✅ {label}: {len(result)} Eintraege von {url}")
                return result
            print(f"⚠️ {label}: 0 Eintraege bei {url}")
        except Exception as e:
            print(f"⚠️ {label}: Fehler bei {url}: {e}")

    print(f"❌ {label}: keine der bekannten URLs lieferte Daten - Feature bleibt inaktiv.")
    if diagnose and last_soup is not None:
        # Zeigt, welche Link-Muster die Seite tatsaechlich hat, damit der
        # richtige Selektor beim naechsten Mal ohne Raten gefunden werden kann.
        hrefs = [a["href"] for a in last_soup.find_all("a", href=True)]
        print(f"🔬 {label} DIAGNOSE: {len(hrefs)} Links auf der Seite.")
        interesting = [h for h in hrefs if any(w in h.lower() for w in
                       ["spieler", "player", "aufstell", "lineup", "team", "profil"])]
        print(f"🔬 Beispiel-Links (gefiltert): {interesting[:12]}")
        if not interesting:
            print(f"🔬 Beispiel-Links (ungefiltert): {hrefs[:12]}")
    return set()


def get_ligainsider_lineups():
    def extract(soup):
        names = set()
        for link in soup.find_all("a", href=True):
            if "/spieler/" in link["href"]:
                name = link.get_text(strip=True)
                if name and len(name) > 2:
                    names.add(name.lower())
        return names
    return _scrape_first_working(LIGAINSIDER_LINEUP_URLS, extract, "LigaInsider Startelf", diagnose=True)


def get_ligainsider_injuries():
    def extract(soup):
        names = set()
        # Variante A: Spielerlinks (robusteste Struktur)
        for link in soup.find_all("a", href=True):
            if "/spieler/" in link["href"]:
                name = link.get_text(strip=True)
                if name and len(name) > 2:
                    names.add(name.lower())
        if names:
            return names
        # Variante B: fettgedruckte Namen mit mind. zwei Wortteilen
        for tag in soup.find_all(["b", "strong"]):
            text = tag.get_text(strip=True)
            if text and 2 <= len(text.split()) <= 3 and not any(c.isdigit() for c in text):
                names.add(text.lower())
        return names
    return _scrape_first_working(LIGAINSIDER_INJURY_URLS, extract, "LigaInsider Verletzte")


def name_in_collection(lastname, collection):
    """
    Prueft, ob ein Spielername als eigenstaendiges Wort in der Sammlung vorkommt.
    Wortgrenzen statt Teilstring-Vergleich - sonst wuerde z.B. "Jung" auch in
    "Jungwirth" treffen und bei 50+ Eintraegen viele Fehlalarme erzeugen.
    """
    if not lastname or len(lastname) < 3 or not collection:
        return False
    pattern = re.compile(r"\b" + re.escape(lastname.lower()) + r"\b")
    return any(pattern.search(entry) for entry in collection)


def match_news_for_player(lastname, headlines):
    """Findet Schlagzeilen, die den Nachnamen als eigenstaendiges Wort enthalten."""
    if not lastname or len(lastname) < 3:
        return []
    pattern = r"\b" + re.escape(lastname) + r"\b"
    return [h for h in headlines if re.search(pattern, h, re.IGNORECASE)]


def has_negative_news(matched):
    return any(re.search(NEGATIVE_NEWS_PATTERN, h, re.IGNORECASE) for h in matched)


# ==========================================
# KNOTEN 3: RESTPROGRAMM (OpenLigaDB)
# ==========================================
def get_season_fixtures():
    url = f"https://api.openligadb.de/getmatchdata/bl1/{CONFIG['SEASON_YEAR']}"
    try:
        resp = requests.get(url, timeout=CONFIG["REQUEST_TIMEOUT"])
        resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, list) else []
    except Exception as e:
        print(f"⚠️ OpenLigaDB-Spielplan nicht ladbar: {e}")
        return []


def get_team_key(team_name):
    if not team_name:
        return None
    n = team_name.lower()
    if "köln" in n or "koeln" in n:
        return "koeln"
    for key in BUNDESLIGA_TEAM_KEYS:
        if key in n:
            return key
    return None


def days_until_next_matchday(fixtures, fallback):
    if not fixtures:
        return fallback
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
        except ValueError:
            continue
        if d >= today:
            upcoming.append(d)
    return max(0, (min(upcoming) - today).days) if upcoming else fallback


def get_upcoming_opponents(fixtures, team_key, count):
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
    return upcoming[:count]


def fixture_difficulty(fixtures, team_key):
    if not team_key or not fixtures:
        return None
    opponents = get_upcoming_opponents(fixtures, team_key, CONFIG["FIXTURE_LOOKAHEAD"])
    if not opponents:
        return None
    strengths = [TEAM_STRENGTH_LAST_SEASON.get(k, TEAM_STRENGTH_DEFAULT) for _, k in opponents]
    avg = sum(strengths) / len(strengths)
    names = [TEAM_DISPLAY_NAMES.get(k, k.title()) for _, k in opponents]
    label = "leicht" if avg <= 35 else ("mittel" if avg <= 55 else "schwer")
    return {"label": label, "opponents": names}


# ==========================================
# KNOTEN 4: HISTORY & PROGNOSE
# ==========================================
def load_history():
    if GITHUB_TOKEN and GITHUB_REPOSITORY:
        url = f"https://api.github.com/repos/{GITHUB_REPOSITORY}/contents/{CONFIG['HISTORY_FILE']}"
        headers = {"Authorization": f"token {GITHUB_TOKEN}", "Accept": "application/vnd.github+json"}
        try:
            resp = requests.get(url, headers=headers, timeout=CONFIG["REQUEST_TIMEOUT"])
            if resp.status_code == 404:
                print("ℹ️ Noch kein Verlauf im Repo vorhanden.")
                return {}, None
            resp.raise_for_status()
            data = resp.json()
            history = json.loads(base64.b64decode(data["content"]).decode("utf-8"))
            history.pop("players", None)
            print(f"✅ Verlauf aus GitHub geladen ({len(history)} Spieler bekannt).")
            return history, data.get("sha")
        except Exception as e:
            print(f"⚠️ Verlauf (GitHub) nicht ladbar: {e}")
            return {}, None

    print("ℹ️ Kein GITHUB_TOKEN/GITHUB_REPOSITORY - nutze lokale Datei.")
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
    content_str = json.dumps(history, indent=2, ensure_ascii=False)
    if GITHUB_TOKEN and GITHUB_REPOSITORY:
        url = f"https://api.github.com/repos/{GITHUB_REPOSITORY}/contents/{CONFIG['HISTORY_FILE']}"
        headers = {"Authorization": f"token {GITHUB_TOKEN}", "Accept": "application/vnd.github+json"}
        payload = {
            "message": f"Update Kickbase-Verlauf {int(time.time())}",
            "content": base64.b64encode(content_str.encode("utf-8")).decode("utf-8"),
        }
        if sha:
            payload["sha"] = sha
        try:
            resp = requests.put(url, headers=headers, json=payload, timeout=CONFIG["REQUEST_TIMEOUT"])
            resp.raise_for_status()
            print("✅ Verlauf erfolgreich ins GitHub-Repo geschrieben.")
            return
        except Exception as e:
            print(f"⚠️ Verlauf (GitHub) nicht speicherbar: {e}")
            return
    try:
        with open(CONFIG["HISTORY_FILE"], "w", encoding="utf-8") as f:
            f.write(content_str)
        print("✅ Verlauf lokal gespeichert.")
    except Exception as e:
        print(f"⚠️ History Speicherung fehlgeschlagen: {e}")


def calculate_real_daily_trend(player_id, current_mv, history, today_str=None):
    """
    Taegliche Marktwert-Veraenderung. Pro KALENDERTAG nur ein Messpunkt, weil
    der Bot mehrmals taeglich laeuft, Kickbase die Werte aber nur einmal
    taeglich aktualisiert.
    Rueckgabe: (daily_trend, has_real_data)
    """
    player_id_str = str(player_id)
    today_str = today_str or datetime.now(timezone.utc).date().isoformat()
    entry = history.get(player_id_str, {})

    days = entry.get("days")
    if days is None:
        days = []
        legacy_list = entry.get("mv_history")
        if isinstance(legacy_list, list) and legacy_list:
            days = [{"d": "legacy", "mv": legacy_list[-1]}]
        elif entry.get("mv"):
            days = [{"d": "legacy", "mv": entry["mv"]}]

    days = [d for d in days if isinstance(d, dict) and d.get("mv")]

    if days and days[-1].get("d") == today_str:
        days[-1]["mv"] = current_mv
    else:
        days.append({"d": today_str, "mv": current_mv})
    days = days[-CONFIG["MV_HISTORY_DAYS"]:]
    history[player_id_str] = {"days": days}

    mvs = [d["mv"] for d in days]
    if len(mvs) >= 3:
        deltas = sorted(mvs[i] - mvs[i - 1] for i in range(1, len(mvs)))
        return deltas[len(deltas) // 2], True
    if len(mvs) == 2:
        return mvs[1] - mvs[0], True
    return (150000 if current_mv > 5_000_000 else 50000), False


def predict_mv_and_overpay(current_mv, daily_trend, days_left):
    if daily_trend <= 0 or days_left <= 0:
        return current_mv, int(current_mv * 1.02), 0
    damping = 0.96
    growth, current_daily = 0, daily_trend
    for _ in range(days_left):
        growth += current_daily
        current_daily *= damping
    return int(current_mv + growth), current_mv + int(growth * CONFIG["OVERPAY_RISK_FACTOR"]), int(growth)


def evaluate_player(player, history, starters, injured_list, headlines, days_left,
                    my_team_counts=None, blocked_teams=None, my_budget=None,
                    fixture_info=None, today_str=None):
    player_id = player.get("id", player.get("i"))
    tid = player.get("tid")

    name = player.get("n", player.get("lastName", ""))
    if not name and "fn" in player and "ln" in player:
        name = f"{player['fn']} {player['ln']}"
    if not name:
        name = "Unbekannt"

    mv = int(player.get("mv", player.get("m", 0)))
    daily_trend, has_real_data = calculate_real_daily_trend(player_id, mv, history, today_str)
    proj_mv, max_bid, raw_profit = predict_mv_and_overpay(mv, daily_trend, days_left)

    name_lower = name.lower()
    is_starter = name_in_collection(name, starters) if starters else None
    is_injured = name_in_collection(name, injured_list) if injured_list else False

    matched_news = match_news_for_player(name, headlines)[:CONFIG["NEWS_MAX_PER_PLAYER"]]
    negative_news = has_negative_news(matched_news)

    my_team_counts = my_team_counts or {}
    blocked_teams = blocked_teams or {}
    violates_my_limit = tid is not None and my_team_counts.get(tid, 0) >= CONFIG["MAX_PER_TEAM"]
    opponents_blocked = blocked_teams.get(tid, []) if tid is not None else []
    budget_exceeded = my_budget is not None and max_bid > my_budget

    if violates_my_limit:
        category, reason = "blocked", "2-Spieler-Regel: du hast schon 2 aus diesem Verein"
    elif is_injured:
        category, reason = "blocked", "verletzt / gesperrt (LigaInsider)"
    elif negative_news:
        category, reason = "blocked", "negative Schlagzeile - erst prüfen"
    elif budget_exceeded:
        category, reason = "blocked", f"Gebot {fmt_money(max_bid)} über deinem Budget"
    elif raw_profit >= 1_500_000:
        category, reason = "top", "hoher erwarteter Marktwert-Gewinn"
    elif raw_profit >= CONFIG["MIN_PROFIT_FOR_TOP"]:
        category, reason = "watch", "solider Aufwärtstrend"
    else:
        category, reason = "skip", "kein nennenswerter Trend"

    return {
        "id": player_id, "name": name, "mv": mv, "proj_mv": proj_mv,
        "max_bid": max_bid, "exp_profit": raw_profit, "daily_trend": daily_trend,
        "has_real_data": has_real_data, "is_starter": is_starter, "is_injured": is_injured,
        "matched_news": matched_news, "negative_news": negative_news,
        "violates_my_limit": violates_my_limit, "opponents_blocked": opponents_blocked,
        "budget_exceeded": budget_exceeded, "category": category, "reason": reason,
        "fixture_difficulty": fixture_info["label"] if fixture_info else None,
        "fixture_opponents": fixture_info["opponents"] if fixture_info else [],
    }


# ==========================================
# KNOTEN 5: REPORT
# ==========================================
def build_report(evaluated, my_budget, days_left, any_real_data,
                 opponent_profiles=None, transfer_summary=None, lineups_available=True):
    lines = []
    lines.append(f"⚽ <b>Kickbase Markt-Report</b> — noch {days_left} Tage bis zum nächsten Spiel")
    if my_budget is not None:
        lines.append(f"💳 Budget: <b>{fmt_money(my_budget)} €</b>")

    if not any_real_data:
        lines.append("\n⚠️ <i>Noch keine echten Tages-Trends — Prognosen sind Startwerte.</i>")
    if not lineups_available:
        lines.append("\n⚠️ <i>Startelf-Daten aktuell nicht verfügbar (Quelle liefert nichts).</i>")

    tops = sorted([p for p in evaluated if p["category"] == "top"], key=lambda x: x["exp_profit"], reverse=True)
    watch = sorted([p for p in evaluated if p["category"] == "watch"], key=lambda x: x["exp_profit"], reverse=True)
    blocked = [p for p in evaluated if p["category"] == "blocked"]
    skipped = [p for p in evaluated if p["category"] == "skip"]

    detail = (tops + watch)[:CONFIG["TOP_DETAIL_COUNT"]]
    if detail:
        lines.append("\n🔥 <b>BESTE CHANCEN</b> (nach erwartetem Gewinn)")
        for i, p in enumerate(detail, start=1):
            note = "" if p["has_real_data"] else " <i>(geschätzt)</i>"
            lines.append(
                f"\n<b>{i}. {esc(p['name'])}</b> — {fmt_money(p['mv'])} €{note}\n"
                f"   Erwartet zum Spieltag: {fmt_money(p['proj_mv'])} € "
                f"(<b>{fmt_profit(p['exp_profit'])} €</b>)\n"
                f"   👉 Bieten bis max. <b>{fmt_money(p['max_bid'])} €</b>"
            )
            extras = []
            if p["is_starter"] is True:
                extras.append("✅ Startelf")
            elif p["is_starter"] is False:
                extras.append("❓ Rotation")
            if p["fixture_difficulty"]:
                extras.append(f"📅 Restprogramm {p['fixture_difficulty']} ({', '.join(p['fixture_opponents'])})")
            if p["opponents_blocked"]:
                extras.append(f"🔓 Verein bei {len(p['opponents_blocked'])} Gegner(n) voll")
            if extras:
                lines.append("   " + " · ".join(extras))
            for h in p["matched_news"]:
                lines.append(f"   📰 {esc(h[:120])}")
    else:
        lines.append("\n😐 <b>Aktuell keine lohnenden Kaufchancen auf dem Markt.</b>")

    rest = (tops + watch)[CONFIG["TOP_DETAIL_COUNT"]:]
    if rest:
        lines.append("\n👀 <b>Weitere Beobachtungen</b>")
        for p in rest:
            lines.append(f"• {esc(p['name'])} — {fmt_money(p['mv'])} € ({fmt_profit(p['exp_profit'])} €)")

    if blocked:
        lines.append("\n🚫 <b>Nicht bieten</b>")
        for p in blocked:
            lines.append(f"• {esc(p['name'])} — {p['reason']}")

    if skipped:
        lines.append(f"\nℹ️ {len(skipped)} weitere Spieler ohne nennenswerten Trend ausgeblendet.")

    # ---- Konkurrenz-Radar ----
    if opponent_profiles:
        lines.append("\n🕵️ <b>Konkurrenz-Radar</b>")
        for prof in sorted(opponent_profiles, key=lambda x: x["squad_size"]):
            gap_txt = ", ".join(prof["gaps"]) if prof["gaps"] else "keine Lücken"
            lines.append(f"• {esc(prof['name'])}: {prof['squad_size']} Spieler · Bedarf: {gap_txt}")

    if transfer_summary:
        lines.append("\n💸 <b>Transfer-Aktivität (Liga-Feed)</b>")
        for mgr, info in sorted(transfer_summary.items(), key=lambda x: x[1]["total"], reverse=True)[:6]:
            plist = ", ".join(info["players"])
            lines.append(f"• {esc(mgr)}: {info['count']} Käufe, {fmt_money(info['total'])} € ({esc(plist)})")

    return "\n".join(lines)


# ==========================================
# KNOTEN 6: TELEGRAM
# ==========================================
def split_telegram_message(message, max_len):
    chunks, current = [], ""
    for line in message.splitlines():
        line = line.rstrip()
        while len(line) > max_len:
            if current:
                chunks.append(current)
                current = ""
            split_at = line.rfind(" ", 0, max_len)
            if split_at <= 0:
                split_at = max_len
            chunks.append(line[:split_at])
            line = line[split_at:].lstrip()
        candidate = line if not current else f"{current}\n{line}"
        if len(candidate) > max_len:
            if current:
                chunks.append(current)
            current = line
        else:
            current = candidate
    if current:
        chunks.append(current)
    return [c for c in chunks if c.strip()]


def send_telegram_messages(message):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        raise ValueError("❌ TELEGRAM_TOKEN oder TELEGRAM_CHAT_ID fehlt in den Secrets!")

    chunks = split_telegram_message(message, CONFIG["TELEGRAM_MAX_LEN"])
    print(f"DEBUG Telegram: Sende {len(chunks)} Teil(e)...")
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"

    for index, chunk in enumerate(chunks, start=1):
        response = requests.post(url, json={
            "chat_id": TELEGRAM_CHAT_ID, "text": chunk,
            "parse_mode": "HTML", "disable_web_page_preview": True,
        }, timeout=CONFIG["REQUEST_TIMEOUT"])
        print(f"DEBUG Telegram HTML {index}/{len(chunks)} ({len(chunk)} Zeichen): HTTP {response.status_code}")
        if response.ok:
            time.sleep(0.25)
            continue
        plain = re.sub(r"<[^>]+>", "", chunk)
        fallback = requests.post(url, json={
            "chat_id": TELEGRAM_CHAT_ID, "text": plain, "disable_web_page_preview": True,
        }, timeout=CONFIG["REQUEST_TIMEOUT"])
        print(f"DEBUG Telegram Plaintext Fallback {index}/{len(chunks)}: HTTP {fallback.status_code}")
        fallback.raise_for_status()
        time.sleep(0.25)


# ==========================================
# HAUPTABLAUF
# ==========================================
def main():
    print("🚀 Starte Kickbase Bot Engine...")

    token, league_id, my_manager_id = kickbase_login()
    raw_players = get_market_players(token, league_id)
    print(f"📊 {len(raw_players)} Spieler auf dem Transfermarkt gefunden.")

    starters = get_ligainsider_lineups()
    injured = get_ligainsider_injuries()
    headlines = get_rss_headlines()
    print(f"📰 Schlagzeilen gesamt: {len(headlines)}")

    history, history_sha = load_history()

    my_budget = get_my_budget(token, league_id)
    print(f"💳 Verfuegbares Budget: {fmt_money(my_budget)} €" if my_budget is not None
          else "⚠️ Budget nicht ermittelbar.")

    ranking_res = get_ranking(token, league_id)
    my_team_counts = get_my_team_counts(token, league_id, my_manager_id)
    blocked_teams, opponent_profiles = analyze_opponents(token, league_id, ranking_res, my_manager_id)
    print(f"🕵️ Konkurrenten analysiert: {len(opponent_profiles)}")

    feed_items = get_league_feed(token, league_id)
    transfers = parse_feed_transfers(feed_items) if feed_items else []
    transfer_summary = summarize_transfers(transfers) if transfers else {}
    print(f"💸 Transfers aus Feed erkannt: {len(transfers)}")

    season_fixtures = get_season_fixtures()
    days_left = days_until_next_matchday(season_fixtures, CONFIG["DAYS_UNTIL_MATCHDAY"])
    print(f"📅 Tage bis zum naechsten Spiel: {days_left}")

    fixture_cache, unmapped = {}, set()
    today_str = datetime.now(timezone.utc).date().isoformat()

    evaluated = []
    for p in raw_players:
        tid_str = str(p.get("tid")) if p.get("tid") else None
        team_key = KICKBASE_TID_TO_TEAM.get(tid_str) if tid_str else None
        if tid_str and not team_key and tid_str not in unmapped:
            print(f"ℹ️ Team-ID {tid_str} nicht zugeordnet (Spieler: {p.get('n')}).")
            unmapped.add(tid_str)
        if team_key and team_key not in fixture_cache:
            fixture_cache[team_key] = fixture_difficulty(season_fixtures, team_key)
        fixture_info = fixture_cache.get(team_key) if team_key else None

        evaluated.append(evaluate_player(
            p, history, starters, injured, headlines, days_left,
            my_team_counts, blocked_teams, my_budget, fixture_info, today_str))

    real_count = sum(1 for p in evaluated if p["has_real_data"])
    news_hits = sum(1 for p in evaluated if p["matched_news"])
    print(f"📈 Spieler mit echten Tages-Trenddaten: {real_count}/{len(evaluated)}")
    print(f"📰 Spieler mit News-Treffern: {news_hits}")

    save_history(history, history_sha)

    report = build_report(evaluated, my_budget, days_left, real_count > 0,
                          opponent_profiles, transfer_summary, bool(starters))
    send_telegram_messages(report)
    print("✅ Pipeline-Durchlauf vollkommen erfolgreich beendet!")


if __name__ == "__main__":
    main()
