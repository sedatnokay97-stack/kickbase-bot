import os
import sys
import re
import json
import time
import base64
import requests
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, date
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
    "MV_HISTORY_DAYS": 8,
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
                "mvt_flag": None, "avg_points": None}
    daily_trend_api = None
    v = detail.get("tfhmvt")
    if isinstance(v, (int, float)) and v != 0:
        daily_trend_api = int(v)
    status_code = None
    v = detail.get("prob")
    if isinstance(v, int) and 0 <= v <= 4:
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
    return {
        "daily_trend_api": daily_trend_api,
        "status_code": status_code,
        "mdsum": mdsum,
        "mvt_flag": mvt_flag,
        "avg_points": avg_points,
    }

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
    """Wie pos_gaps(), aber als rohe Positions-Codes statt Anzeige-Text -
    fuer den programmatischen Abgleich (z.B. 'braucht Gegner X Position Y?')."""
    return [p for p, n in POSITION_MIN_NEEDED.items() if pos_counts.get(p, 0) < n]

def analyze_opponents(token, league_id, ranking_res, my_manager_id,
                      league_start_budget=None):
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
        pc = count_by_pos(squad)
        profiles.append({
            "name": name, "squad_size": len(squad),
            "gaps": pos_gaps(pc),
            "missing_positions": missing_position_codes(pc),
        })
        time.sleep(CONFIG["OPPONENT_FETCH_SLEEP"])
    print(f"🕵️ Konkurrenten analysiert: {len(profiles)}")
    return blocked, profiles

def get_my_team_counts(token, league_id, my_manager_id):
    if not my_manager_id:
        return {}
    return count_by_team(get_squad(token, league_id, my_manager_id))

# ==========================================
# TIEFENANALYSE (reine Code-Logik, keine externe KI)
# ==========================================
def assess_competition(pos_code, opponent_profiles, cash_by_name, price_threshold):
    """
    Findet Konkurrenten, die diese Position brauchen, sortiert nach
    geschaetztem Cash (staerkster zuerst).

    WICHTIG zur Interpretation: min_cash ist eine UNTERGRENZE (Startkapital +
    Boni - bekannte Kaeufe, ohne Verkaufserloese). Daraus folgt:
    - "Rivale kann dich ueberbieten" ist sicher, wenn min_cash > dein Limit.
    - "Rivale kann NICHT mitbieten" ist NIE sicher, weil er mehr haben kann
      als die Untergrenze zeigt. Deshalb wird das bewusst nie behauptet.
    Rueckgabe: Liste von (name, min_cash), absteigend nach min_cash.
    """
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
    Verbindet Signale zu einer Einschaetzung - reine Code-Logik, kein LLM.
    Zeigt bewusst NUR, was die Extras-Zeile darueber noch nicht sagt.
    """
    parts = []
    pos_code = p.get("pos_code")
    pos_name = POSITION_NAMES.get(pos_code, pos_code)

    if pos_code is not None and pos_code in (my_gap_codes or []):
        parts.append(f"füllt deine eigene Lücke auf {pos_name}")

    max_bid = p.get("max_bid")
    if not competitors:
        parts.append(f"kein Rivale mit Bedarf auf {pos_name}")
    else:
        top_name, top_cash = competitors[0]
        if top_cash is not None and max_bid is not None and top_cash > max_bid:
            # Sichere Aussage: Untergrenze liegt bereits ueber deinem Limit
            parts.append(
                f"⚠️ {top_name} braucht {pos_name} und hat mind. {fmt_money(top_cash)} "
                f"— kann dein Limit von {fmt_money(max_bid)} überbieten"
            )
        else:
            others = f" (+{len(competitors)-1} weitere)" if len(competitors) > 1 else ""
            parts.append(f"{len(competitors)} Rivale(n) mit Bedarf auf {pos_name}: {top_name}{others}")

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
    
LEAGUE_START_DATE = date(2026, 8, 15)
OPPONENT_START_CASH = 50_000_000


def cumulative_login_bonus_per_player(today=None):
    """Kumulierter Kickbase-Login-Bonus pro Spieler seit dem Liga-Start."""
    today = today or date.today()
    days = max(1, (today - LEAGUE_START_DATE).days + 1)

    if days <= 9:
        return 10_000 * days * (days + 1) // 2

    return 450_000 + (days - 9) * 100_000


def build_opponent_cash_tracker(opponent_profiles, transfer_summary, today=None):
    """
    Konservative Untergrenze:
    50 Mio Startcash + Login-Boni − bekannte Käufe.

    Verkaufserlöse werden noch nicht eingerechnet.
    """
    bonus_per_player = cumulative_login_bonus_per_player(today)
    rows = []

    for profile in opponent_profiles:
        name = profile["name"]
        squad_size = profile["squad_size"]
        buys = transfer_summary.get(name, {"count": 0, "total": 0})
        login_bonus = bonus_per_player * squad_size
        min_cash = OPPONENT_START_CASH + login_bonus - buys["total"]

        rows.append({
            "name": name,
            "squad_size": squad_size,
            "known_buy_count": buys["count"],
            "known_buy_total": buys["total"],
            "login_bonus": login_bonus,
            "min_cash": min_cash,
        })

    return rows


def print_cash_debug(cash_rows):
    """Schreibt die Berechnung in die GitHub-Actions-Logs."""
    print("\n💰 CASH DEBUG — Untergrenze ohne bestätigte Verkaufserlöse")

    for row in sorted(cash_rows, key=lambda item: item["min_cash"]):
        print(
            f"• {row['name']}: Cash ≥ {fmt_money(row['min_cash'])} "
            f"| Start 50,0 Mio "
            f"| Boni +{fmt_money(row['login_bonus'])} "
            f"| Käufe -{fmt_money(row['known_buy_total'])} "
            f"({row['known_buy_count']}×)"
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

    prediction_check = None
    if days and days[-1].get("d") != today_str and days[-1].get("pred") is not None:
        predicted_mv = days[-1]["pred"]
        prev_mv = days[-1]["mv"]
        actual_move = current_mv - prev_mv
        predicted_move = predicted_mv - prev_mv
        prediction_check = {
            "predicted_mv": predicted_mv,
            "actual_mv": current_mv,
            "predicted_move": predicted_move,
            "actual_move": actual_move,
        }

    if days and days[-1].get("d") == today_str:
        days[-1]["mv"] = current_mv
    else:
        days.append({"d": today_str, "mv": current_mv})
    days = days[-CONFIG["MV_HISTORY_DAYS"]:]
    history[pid] = {"days": days}
    mvs = [d["mv"] for d in days]
    if len(mvs) >= 2:
        deltas = [mvs[i] - mvs[i-1] for i in range(1, len(mvs))]
        return deltas, True, prediction_check
    return [], False, prediction_check

def store_prediction(pid, history, predicted_next_mv):
    pid = str(pid)
    if pid in history and history[pid].get("days"):
        history[pid]["days"][-1]["pred"] = int(predicted_next_mv)

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
    if daily_trend <= 0 or days_left <= 0:
        return current_mv, None, 0
    damping, growth, cd = 0.96, 0.0, float(daily_trend)
    for _ in range(days_left):
        growth += cd
        cd *= damping
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

STATUS_ICONS = {0: "🔵", 1: "🟢", 2: "🟡", 3: "🟠", 4: "🔴"}
STATUS_LABELS = {0: "garantiert", 1: "sicher", 2: "Rotation", 3: "unwahrscheinlich", 4: "bank/keine Chance"}
STATUS_FACTORS = {0: 1.15, 1: 1.10, 2: 1.00, 3: 0.90, 4: 0.75}

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
    if avg_pts is None and sc is not None and sc <= 1:
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
                    detail=None, matchdays_played=None):
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
        "mvt_flag": None, "avg_points": None}
    status_code = stats["status_code"]
    kb_daily_trend = stats["daily_trend_api"]
    mdsum = stats["mdsum"]
    avg_points = stats["avg_points"]

    deltas, has_real, prediction_check = update_history(pid, mv, history, today_str)
    calculated_trend, _ = compute_trend(deltas)
    daily_trend = kb_daily_trend if kb_daily_trend is not None else calculated_trend
    if kb_daily_trend is not None:
        has_real = True
    momentum = compute_momentum(deltas) if len(deltas) >= 3 else "neutral"

    if daily_trend and daily_trend > 0:
        store_prediction(pid, history, mv + daily_trend)

    if mdsum and not fixture_info:
        fixture_info = parse_mdsum(mdsum)

    mf = matchup_factor(win_prob)
    mf_momentum = (CONFIG["MOMENTUM_BOOST"] if momentum == "beschleunigt"
                   else CONFIG["MOMENTUM_BRAKE"] if momentum == "verlangsamt"
                   else 1.0)
    mf_lineup = lineup_factor_from_status(status_code)
    overpay_factor = CONFIG["OVERPAY_BASE_FACTOR"] * mf * mf_momentum * mf_lineup

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
    elif not has_real or max_bid is None:
        category, reason = "pending", "noch kein Tagesvergleich — morgen entscheiden"
    elif my_budget is not None and max_bid > my_budget:
        category, reason = "blocked", f"Gebot {fmt_money(max_bid)} über Budget"
    elif raw_profit >= 1_500_000:
        # Hoher Marktwert-Trend allein reicht nicht fuer "top": ein Spieler ohne
        # realistische Einsatzchance (unwahrscheinlich/bank) wird bewusst auf
        # "watch" gedeckelt, damit er echte sichere Kandidaten nicht von Platz 1
        # verdraengt. Der Trend bleibt sichtbar, aber die Rangfolge stimmt.
        if status_code is not None and status_code >= 3:
            category = "watch"
            reason = f"hoher Trend, aber {STATUS_LABELS.get(status_code, 'unsicherer Status')} — Einsatz fraglich"
        else:
            category, reason = "top", "hoher erwarteter Gewinn"
    elif raw_profit >= CONFIG["MIN_PROFIT_FOR_TOP"]:
        category, reason = "watch", "solider Aufwärtstrend"
    else:
        category, reason = "skip", "kein nennenswerter Trend"

    budget_ok = max_bid is None or my_budget is None or max_bid <= my_budget
    relative_value_score = score_relative_value(mv, avg_points, pos_code)
    relative_value_score = confidence_weighted_score(relative_value_score, matchdays_played)

    result = {
        "id": pid, "name": name, "mv": mv, "proj_mv": proj_mv,
        "max_bid": max_bid, "exp_profit": raw_profit,
        "daily_trend": daily_trend, "kb_trend": kb_daily_trend,
        "momentum": momentum, "has_real": has_real,
        "is_injured": is_injured, "matched_news": matched_news,
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
                 my_gap_codes=None, cash_by_name=None):
    lines = []
    lines.append(f"⚽ Kickbase Markt-Report — noch {days_left} Tage")
    if my_budget is not None:
        lines.append(f"💳 Budget: {fmt_money(my_budget)} €")
    if not lineups_ok:
        lines.append("⚠️ Startelf-Daten nicht verfügbar.")
    if not odds_ok:
        lines.append("⚠️ Wettquoten nicht verfügbar (ODDS_API_KEY prüfen).")

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

    # Tiefenanalyse fuer GENAU die Kandidaten, die tatsaechlich angezeigt werden -
    # eilige zuerst, denn dort ist eine fundierte Einschaetzung am wertvollsten
    # (wenig Zeit zum Nachdenken). Kein Berechnen fuer Kandidaten, die eh nicht
    # im Report auftauchen.
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
        # Top und Watch getrennt ausgeben: sonst sieht ein wegen Bank-Status
        # herabgestufter Spieler durch die durchlaufende Nummerierung trotzdem
        # aus wie eine Top-Empfehlung.
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
            if p.get("kb_trend") is not None:
                extras.append(f"📈 KB-Trend {fmt_profit(p['kb_trend'])} €/Tag")
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
                avg_str = f" · ⚽ Ø {p['avg_points']:.1f} Pkt" if p.get("avg_points") else ""
                rv = p.get("relative_value_score")
                rv_str = f" · 💎 Wert +{rv:.2f}" if rv is not None and rv > 0 else ""
                trend_str = f" · 📈 {fmt_profit(p['kb_trend'])} €/Tag" if p.get("kb_trend") is not None else ""
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
                trend_str = f" · 📈 {fmt_profit(p['kb_trend'])} €/Tag" if p.get("kb_trend") is not None else ""
                lines.append(f"• {esc(p['name'])} — {fmt_money(p['mv'])} € · {status_str}{trend_str}")

    if blocked:
        lines.append("\n🚫 Nicht bieten")
        for p in blocked:
            lines.append(f"• {esc(p['name'])} — {p['reason']}")

    if pending:
        lines.append(f"\n⏳ Noch beobachten ({len(pending)} Spieler ohne Trendvergleich)")

    footer_parts = []
    if skipped:
        footer_parts.append(f"{len(skipped)} ohne nennenswerten Trend")
    if offered:
        footer_parts.append(f"{len(offered)} von Mitgliedern angeboten (getrackt)")
    if footer_parts:
        lines.append(f"\nℹ️ Ausgeblendet aber getrackt: {' · '.join(footer_parts)}.")

    if opponent_profiles:
        lines.append("\n🕵️ Konkurrenz-Radar")
        for prof in sorted(opponent_profiles, key=lambda x: x["squad_size"]):
            g = ", ".join(prof["gaps"]) if prof["gaps"] else "vollständig"
            lines.append(f"• {esc(prof['name'])}: {prof['squad_size']} Spieler · {g}")

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
    my_team_counts = get_my_team_counts(token, league_id, my_manager_id)
    blocked_teams, opponent_profiles = analyze_opponents(
        token, league_id, ranking_res, my_manager_id)

    # Eigene Positionsluecken (fuer die Tiefenanalyse: "fuellt dieser Kauf eine
    # echte Luecke in meinem Kader?"). Separater Squad-Abruf, damit
    # get_my_team_counts() unangetastet bleibt.
    my_squad_items = get_squad(token, league_id, my_manager_id) if my_manager_id else []
    my_gap_codes = missing_position_codes(count_by_pos(my_squad_items))

    feed_items = get_league_feed(token, league_id)
    transfers = parse_feed(feed_items)
    transfer_summary = summarize_transfers(transfers)
    print(f"Transfers im Feed: {len(transfers)}")

    cash_debug_rows = build_opponent_cash_tracker(
        opponent_profiles,
        transfer_summary,
    )
    print_cash_debug(cash_debug_rows)
    cash_by_name = {row["name"]: row for row in cash_debug_rows}

    win_probs = get_bundesliga_odds()
    fixture_cache, unmapped = {}, set()
    today_str = datetime.now(timezone.utc).date().isoformat()

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
        if is_free_market_player(p):
            pid_str = p.get("id", p.get("i"))
            if pid_str:
                detail = get_player_detail(token, league_id, pid_str)
            time.sleep(0.2)

        res = evaluate_player(p, history, injured, headlines, days_left,
                               my_team_counts, blocked_teams, my_budget,
                               fixture_info, win_prob, today_str, detail=detail,
                               matchdays_played=matchdays_played)
        if not is_free_market_player(p):
            seller = get_seller_name(p) or "?"
            res["category"] = "market_offer"
            res["reason"] = f"angeboten von {seller}"
        evaluated.append(res)

    real_count = sum(1 for p in evaluated if p["has_real"])
    urgent_count = sum(1 for p in evaluated if p["is_urgent"])
    print(f"Echte Trendsdaten: {real_count}/{len(evaluated)}, Urgency-Alerts: {urgent_count}")

    save_history(history, history_sha)
    report = build_report(evaluated, my_budget, days_left, opponent_profiles,
                          transfer_summary, lineups_ok=False, odds_ok=bool(win_probs),
                          my_gap_codes=my_gap_codes, cash_by_name=cash_by_name)
    send_telegram(report)
    print("Pipeline-Durchlauf vollkommen erfolgreich beendet!")

if __name__ == "__main__":
    main()
