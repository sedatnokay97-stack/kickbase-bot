import os
import sys
import re
import json
import html
import time
import traceback
from datetime import datetime, timezone, date
import xml.etree.ElementTree as ET

import requests
from google import genai
from google.genai import types
from bs4 import BeautifulSoup

KB_EMAIL = os.environ.get("KB_EMAIL")
KB_PASSWORD = os.environ.get("KB_PASSWORD")
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

REQUIRED_SECRETS = {
    "KB_EMAIL": KB_EMAIL,
    "KB_PASSWORD": KB_PASSWORD,
    "TELEGRAM_TOKEN": TELEGRAM_TOKEN,
    "TELEGRAM_CHAT_ID": TELEGRAM_CHAT_ID,
}

CONFIG = {
    "MAX_PER_TEAM": 2,
    "FIRST_MATCHDAY": date(2026, 8, 28),
    "WINTER_RESET_DATE": date(2027, 1, 15),
    "WINTER_WARNING_DAYS": 14,
    "MW_UPDATE_HOUR_UTC": 20,
    "PLAYERS_TO_SHOW": 15,
    "REQUEST_TIMEOUT": 10,
    "TELEGRAM_MAX_LEN": 3900,
    "GEMINI_MODEL": "gemini-3.7-flash",
    "GEMINI_MAX_RETRIES": 1,
    "GEMINI_RETRY_DELAY": 5,
    "DEBUG_PLAYER_DETAIL": True,
    "STARTELF_HEURISTIC_ENABLED": True,
    "FIXTURE_DIFFICULTY_ENABLED": True,
    "FIXTURE_LOOKAHEAD": 3,
    "SEASON_YEAR": 2026,
    "COMPETITION_ID": "1",
    "OVERPAY_TIERS": [
        (110, 0.30),
        (60, 0.15),
        (0, 0.08),
    ],
    "OVERPAY_CAP_RISKANT": 0.05,
}

NEWS_FEEDS = [
    "https://newsfeed.kicker.de/news/bundesliga",
    "https://feeds.t-online.de/rss/transfermarkt",
    "https://www.sportschau.de/fussball/bundesliga/index~rss2.xml",
]

LIGAINSIDER_INJURY_URL = "https://www.ligainsider.de/bundesliga/verletzte-und-gesperrte-spieler/"

TEAM_LINEUP_SLUGS = {
    "bayern": "fc-bayern-muenchen/1",
    "werder": "sv-werder-bremen/2",
    "frankfurt": "eintracht-frankfurt/3",
    "leverkusen": "bayer-04-leverkusen/4",
    "gladbach": "borussia-moenchengladbach/5",
    "hamburg": "hamburger-sv/9",
    "hoffenheim": "tsg-hoffenheim/10",
    "stuttgart": "vfb-stuttgart/12",
    "schalke": "fc-schalke-04/13",
    "dortmund": "borussia-dortmund/14",
    "k\u00f6ln": "1-fc-koeln/15",
    "koeln": "1-fc-koeln/15",
    "mainz": "1-fsv-mainz-05/17",
    "freiburg": "sc-freiburg/18",
    "augsburg": "fc-augsburg/21",
    "union berlin": "1-fc-union-berlin/1246",
    "paderborn": "sc-paderborn-07/1249",
    "leipzig": "rb-leipzig/1311",
    "elversberg": "sv-07-elversberg/1331",
}

BUNDESLIGA_TEAM_KEYS = list(TEAM_LINEUP_SLUGS.keys())

# Manuell verifizierte Zuordnung Kickbase-interne tid -> Team-Key.
# 12 von 18 Vereinen bestaetigt. Offen: dortmund, schalke, hamburg,
# augsburg, paderborn, elversberg.
KICKBASE_TID_TO_TEAM = {
    2: "bayern",
    4: "frankfurt",
    5: "freiburg",
    7: "leverkusen",
    9: "stuttgart",
    10: "werder",
    14: "hoffenheim",
    15: "gladbach",
    18: "mainz",
    28: "koeln",
    40: "union berlin",
    43: "leipzig",
}

TEAM_STRENGTH_LAST_SEASON = {
    "bayern": 89,
    "dortmund": 73,
    "leipzig": 65,
    "stuttgart": 62,
    "hoffenheim": 61,
    "leverkusen": 59,
    "freiburg": 47,
    "frankfurt": 44,
    "augsburg": 43,
    "mainz": 40,
    "union berlin": 39,
    "gladbach": 38,
    "hamburg": 38,
    "k\u00f6ln": 32,
    "koeln": 32,
    "werder": 32,
    "paderborn": 22,
    "schalke": 20,
    "elversberg": 20,
}
TEAM_STRENGTH_DEFAULT = 45

LINEUP_SKIP_PHRASES = {
    "auf dieser seite", "wir aktualisieren", "voraussichtliche aufstellung",
    "spieler stand in der startelf", "spieler wurde eingewechselt",
    "spieler sa\u00df auf der bank", "spieler war nicht im", "zur detailansicht",
    "kader", "heimspiel gegen", "ausw\u00e4rtsspiel gegen", "torh\u00fcter",
    "abwehrspieler", "mittelfeldspieler", "st\u00fcrmer", "legende",
}

NEWS_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
    )
}

session = requests.Session()
session.headers.update({
    "User-Agent": "Kickbase/6.8.0 (iPhone; iOS 16.5; Scale/3.00)",
    "Content-Type": "application/json",
})


def esc(value) -> str:
    return html.escape(str(value), quote=False)


def get_ligainsider_injury_news():
    headlines = []
    skip_labels = {"spieler", "grund", "letzte news zu diesem status", "fehlt seit:", "fehlt seit"}
    try:
        res = requests.get(LIGAINSIDER_INJURY_URL, headers=NEWS_HEADERS, timeout=CONFIG["REQUEST_TIMEOUT"])
        res.raise_for_status()
        res.encoding = "utf-8"
        soup = BeautifulSoup(res.text, "html.parser")
        bold_tags = soup.find_all(["b", "strong"])
        for tag in bold_tags:
            name = tag.get_text(strip=True)
            if not name or name.lower() in skip_labels:
                continue
            if len(name.split()) < 2:
                continue
            parent = tag.find_parent(["div", "li", "tr", "p"]) or tag.parent
            full_text = parent.get_text(" ", strip=True)
            if full_text:
                headlines.append(full_text)
    except Exception as e:
        print(f"Warnung: LigaInsider-Verletztenseite konnte nicht geladen werden: {e}")
    return headlines


def get_news():
    headlines = []
    for url in NEWS_FEEDS:
        try:
            res = requests.get(url, headers=NEWS_HEADERS, timeout=CONFIG["REQUEST_TIMEOUT"])
            res.raise_for_status()
            res.encoding = "utf-8"
            try:
                root = ET.fromstring(res.text)
            except ET.ParseError:
                print(f"Warnung: RSS-Feed konnte nicht geparst werden: {url}")
                continue
            for item in root.findall(".//item"):
                title_el = item.find("title")
                if title_el is not None and title_el.text:
                    headlines.append(title_el.text)
        except Exception as e:
            print(f"Warnung: News-Feed konnte nicht geladen werden: {url} -> {e}")
    try:
        li_headlines = get_ligainsider_injury_news()
        headlines.extend(li_headlines)
    except Exception as e:
        print(f"Warnung: LigaInsider-News konnten nicht per HTML-Scraping geladen werden: {e}")
    unique = []
    seen = set()
    for h in headlines:
        if h not in seen:
            seen.add(h)
            unique.append(h)
    return unique


def count_by_team(squad_items):
    counts = {}
    for sp in squad_items:
        tid = sp.get("tid")
        if not tid:
            continue
        counts[tid] = counts.get(tid, 0) + 1
    return counts


def build_blocked_teams(liga_id, ranking_res, my_manager_id):
    blocked_teams = {}
    for user in ranking_res.get("users", []):
        opponent_manager_id = user.get("mid") or user.get("id")
        if opponent_manager_id == my_manager_id:
            continue
        if not opponent_manager_id:
            continue
        try:
            res = session.get(
                f"https://api.kickbase.com/v4/leagues/{liga_id}/managers/{opponent_manager_id}/squad",
                timeout=CONFIG["REQUEST_TIMEOUT"],
            )
            res.raise_for_status()
            squad = res.json().get("it", [])
        except Exception as e:
            print(f"Warnung: Squad von {user.get('name')} konnte nicht geladen werden: {e}")
            squad = []
        counts = count_by_team(squad)
        for tid, count in counts.items():
            if count >= CONFIG["MAX_PER_TEAM"]:
                blocked_teams.setdefault(tid, []).append(user.get("name"))
    return blocked_teams


def get_my_team_counts(liga_id, my_manager_id):
    if not my_manager_id:
        print("Warnung: my_manager_id ist None – eigener Kader kann nicht geladen werden.")
        return {}
    try:
        res = session.get(
            f"https://api.kickbase.com/v4/leagues/{liga_id}/managers/{my_manager_id}/squad",
            timeout=CONFIG["REQUEST_TIMEOUT"],
        )
        res.raise_for_status()
        my_squad = res.json().get("it", [])
    except Exception as e:
        print(f"Warnung: Eigener Kader konnte nicht geladen werden: {e}")
        my_squad = []
    return count_by_team(my_squad)


def get_my_budget(liga_id):
    """Liest den verfuegbaren Kontostand ueber /me/budget aus, mit Fallback auf /me."""
    try:
        res = session.get(f"https://api.kickbase.com/v4/leagues/{liga_id}/me/budget", timeout=CONFIG["REQUEST_TIMEOUT"])
        res.raise_for_status()
        data = res.json()
        for key in ["budget", "b", "amount"]:
            if key in data and data[key] is not None:
                return int(data[key])
    except Exception as e:
        print(f"Warnung: /me/budget nicht verfuegbar, versuche Fallback: {e}")
    try:
        res = session.get(f"https://api.kickbase.com/v4/leagues/{liga_id}/me", timeout=CONFIG["REQUEST_TIMEOUT"])
        res.raise_for_status()
        data = res.json()
        for key in ["budget", "b", "amount"]:
            if key in data and data[key] is not None:
                return int(data[key])
    except Exception as e:
        print(f"Warnung: Kontostand konnte nicht geladen werden: {e}")
    return None


def get_team_names():
    """
    Liefert tid -> Team-Key. Da Kickbase v4 keinen dokumentierten Team-Namen-Endpoint
    hat, nutzen wir primaer eine manuell verifizierte statische Zuordnung
    (KICKBASE_TID_TO_TEAM) und versuchen zusaetzlich einen (evtl. nicht funktionierenden)
    API-Call als Fallback/Erweiterung.
    """
    names = {}
    try:
        res = session.get(
            f"https://api.kickbase.com/v4/competitions/{CONFIG['COMPETITION_ID']}/teams",
            timeout=CONFIG["REQUEST_TIMEOUT"],
        )
        res.raise_for_status()
        data = res.json()
        items = data.get("it", data) if isinstance(data, dict) else data
        if isinstance(items, list):
            for item in items:
                tid = item.get("id") or item.get("tid")
                name = item.get("name") or item.get("nm") or item.get("n")
                if tid and name:
                    names[tid] = name
    except Exception as e:
        print(f"Warnung: Team-Namen ueber API nicht ladbar (nutze statische Zuordnung): {e}")

    # Statische, manuell verifizierte Zuordnung ueberschreibt/ergaenzt die API-Antwort
    for tid, team_key in KICKBASE_TID_TO_TEAM.items():
        names[tid] = team_key

    return names


def get_team_key(team_name):
    """Matched einen beliebigen Vereinsnamen ODER bereits einen Team-Key auf einen der 18 festen Team-Keys."""
    if not team_name:
        return None
    name_lower = team_name.lower()
    for key in BUNDESLIGA_TEAM_KEYS:
        if key in name_lower:
            return key
    return None


def find_ligainsider_slug(team_name):
    key = get_team_key(team_name)
    return TEAM_LINEUP_SLUGS.get(key) if key else None


def get_predicted_lineup(slug_id):
    """
    Scraped die voraussichtliche Startelf (naechstes Spiel) von LigaInsider.
    Heuristik: erste bis zu 11 fettgedruckte Namen ohne bekannte Label-Phrasen.
    Kein Ersatz fuer die exakte Ampelfarbe aus der Kickbase-App.
    """
    url = f"https://www.ligainsider.de/bundesliga/team/{slug_id}/"
    names = []
    try:
        res = requests.get(url, headers=NEWS_HEADERS, timeout=CONFIG["REQUEST_TIMEOUT"])
        res.raise_for_status()
        res.encoding = "utf-8"
        soup = BeautifulSoup(res.text, "html.parser")
        for tag in soup.find_all(["b", "strong"]):
            text = tag.get_text(strip=True)
            if not text or len(text.split()) > 3:
                continue
            low = text.lower()
            if any(p in low for p in LINEUP_SKIP_PHRASES):
                continue
            if any(ch.isdigit() for ch in text):
                continue
            if text not in names:
                names.append(text)
            if len(names) >= 11:
                break
    except Exception as e:
        print(f"Warnung: Lineup-Vorschau fuer {slug_id} nicht ladbar: {e}")
    return names


def get_season_fixtures():
    """Laedt den kompletten Saison-Spielplan (alle Spieltage) ueber die freie OpenLigaDB-API."""
    url = f"https://api.openligadb.de/getmatchdata/bl1/{CONFIG['SEASON_YEAR']}"
    try:
        res = requests.get(url, timeout=CONFIG["REQUEST_TIMEOUT"])
        res.raise_for_status()
        data = res.json()
        return data if isinstance(data, list) else []
    except Exception as e:
        print(f"Warnung: OpenLigaDB-Spielplan konnte nicht geladen werden: {e}")
        return []


def get_team_strength(team_key):
    return TEAM_STRENGTH_LAST_SEASON.get(team_key, TEAM_STRENGTH_DEFAULT)


def get_upcoming_opponents(fixtures, team_key, count):
    upcoming = []
    for m in fixtures:
        if m.get("matchIsFinished"):
            continue
        t1 = (m.get("team1") or {}).get("teamName", "")
        t2 = (m.get("team2") or {}).get("teamName", "")
        k1 = get_team_key(t1)
        k2 = get_team_key(t2)
        if k1 == team_key and k2:
            upcoming.append((m.get("matchDateTimeUTC", ""), k2, t2))
        elif k2 == team_key and k1:
            upcoming.append((m.get("matchDateTimeUTC", ""), k1, t1))
    upcoming.sort(key=lambda x: x[0])
    return upcoming[:count]


def fixture_difficulty(fixtures, team_key):
    """Bewertet das Restprogramm eines Vereins anhand der Staerke (Vorsaison-Punkte) der naechsten Gegner."""
    if not team_key or not fixtures:
        return None
    opponents = get_upcoming_opponents(fixtures, team_key, CONFIG["FIXTURE_LOOKAHEAD"])
    if not opponents:
        return None
    strengths = [get_team_strength(k) for _, k, _ in opponents]
    avg_strength = sum(strengths) / len(strengths)
    opponent_names = [name.split()[-1] for _, _, name in opponents if name]
    if avg_strength <= 35:
        label = "leicht"
    elif avg_strength <= 55:
        label = "mittel"
    else:
        label = "schwer"
    return {"label": label, "opponents": opponent_names, "avg_strength": round(avg_strength, 1)}


def market_updates_until_first_matchday():
    now_utc = datetime.now(timezone.utc)
    matchday_start = datetime.combine(CONFIG["FIRST_MATCHDAY"], datetime.min.time(), tzinfo=timezone.utc)
    if now_utc >= matchday_start:
        return 0
    today_update = now_utc.replace(hour=CONFIG["MW_UPDATE_HOUR_UTC"], minute=0, second=0, microsecond=0)
    remaining_days = (CONFIG["FIRST_MATCHDAY"] - now_utc.date()).days
    return remaining_days if now_utc < today_update else max(0, remaining_days - 1)


def winter_reset_warning():
    """Gibt eine Warnmeldung zurueck, falls das Winter-Reset-Datum bald erreicht ist, sonst None."""
    today = datetime.now(timezone.utc).date()
    reset_date = CONFIG["WINTER_RESET_DATE"]
    days_left = (reset_date - today).days
    if 0 <= days_left <= CONFIG["WINTER_WARNING_DAYS"]:
        return (
            f"⚠️ Nur noch {days_left} Tage bis zum Winter-Reset ({reset_date.strftime('%d.%m.%Y')}). "
            "Denk daran, Spieler rechtzeitig zu verkaufen (Regel: bis auf 1 Spieler abstossen)."
        )
    return None


def match_news_for_player(lastname, news_headlines):
    matched = []
    for headline in news_headlines:
        if re.search(r'\b' + re.escape(lastname) + r'\b', headline, re.IGNORECASE):
            matched.append(headline)
    return matched


def extract_points(p, p_detail):
    total_candidates = ["totalPoints", "p", "tp", "points", "sp"]
    avg_candidates = ["averagePoints", "ap", "avp", "avgPoints", "sap"]
    sources = [p or {}, p_detail or {}]
    total_points = 0
    avg_points = 0
    for src in sources:
        for k in total_candidates:
            if k in src and src[k] is not None:
                total_points = src[k]
                break
        if total_points:
            break
    for src in sources:
        for k in avg_candidates:
            if k in src and src[k] is not None:
                avg_points = src[k]
                break
        if avg_points:
            break
    return total_points, avg_points


def get_overpay_pct(avg_points):
    """Staffelt das erlaubte Overpay-Limit nach Punkte-Tier statt starrem Prozentwert."""
    for threshold, pct in CONFIG["OVERPAY_TIERS"]:
        if avg_points >= threshold:
            return pct
    return CONFIG["OVERPAY_TIERS"][-1][1]


def evaluate_player(p, p_detail, news_headlines, blocked_teams, my_team_counts, mw_cycles, my_budget, lineup_names=None, fixture_info=None):
    lastname = p.get("n", "Unbekannt")
    mv = p.get("mv", 0)
    trend_flag = p.get("mvt")
    tid = p.get("tid")

    st_market = str(p.get("st", "0"))
    st_detail = str(p_detail.get("status", p_detail.get("st", "0")) if p_detail else "0")
    is_injured = st_market in ["1", "2", "4", "8"] or st_detail in ["1", "2", "4", "8"]

    total_points, avg_points = extract_points(p, p_detail)

    matched_news = match_news_for_player(lastname, news_headlines)
    has_negative_news = any(
        re.search(r"(verletzt|muskul|ausfall|gesperrt|rot|kreuzband|bruch|reha|trainingsrückstand)", h, re.IGNORECASE)
        for h in matched_news
    )

    my_count_for_team = my_team_counts.get(tid, 0)
    violates_my_limit = my_count_for_team >= CONFIG["MAX_PER_TEAM"]
    opponents_blocked = tid in blocked_teams

    predicted_starter = None
    if lineup_names:
        predicted_starter = any(
            re.search(r'\b' + re.escape(lastname) + r'\b', n, re.IGNORECASE) for n in lineup_names
        )

    score = 0
    if mv < 2_000_000:
        score += 1
    elif mv < 6_000_000:
        score += 2
    elif mv < 12_000_000:
        score += 3
    else:
        score += 4

    if trend_flag == 1:
        score += 2
    elif trend_flag == 2:
        score -= 1

    if mw_cycles >= 15:
        score += 2
    elif mw_cycles >= 7:
        score += 1

    if avg_points >= 100:
        score += 3
    elif avg_points >= 60:
        score += 2
    elif avg_points >= 30:
        score += 1
    elif 0 < avg_points < 15:
        score -= 1

    if predicted_starter is True:
        score += 1
    elif predicted_starter is False:
        score -= 1

    if fixture_info:
        if fixture_info["label"] == "leicht":
            score += 1
        elif fixture_info["label"] == "schwer":
            score -= 1

    if is_injured:
        score -= 3
    if has_negative_news:
        score -= 2
    if violates_my_limit:
        score -= 4
    if opponents_blocked:
        score -= 1

    if score >= 6 and not violates_my_limit and not is_injured and not has_negative_news:
        label = "TOP-KAUF"
    elif score >= 3:
        label = "SOLIDE"
    else:
        label = "RISKANT"

    overpay_pct = get_overpay_pct(avg_points)
    if label == "RISKANT":
        overpay_pct = min(overpay_pct, CONFIG["OVERPAY_CAP_RISKANT"])

    max_bid = int(mv * (1 + overpay_pct))
    max_bid = max(max_bid, mv)

    budget_exceeded = False
    if my_budget is not None and max_bid > my_budget:
        budget_exceeded = True
        max_bid = min(max_bid, my_budget) if my_budget >= mv else max_bid

    reasons = []
    if trend_flag == 1:
        reasons.append("positiver Marktwert-Trend")
    elif trend_flag == 2:
        reasons.append("fallender Marktwert-Trend")
    if avg_points >= 60:
        reasons.append(f"starke Punkteform (Ø {avg_points})")
    elif 0 < avg_points < 15:
        reasons.append(f"schwache Punkteform (Ø {avg_points})")
    reasons.append(f"Overpay-Limit {int(round(overpay_pct * 100))}% (Punkte-Tier)")
    if predicted_starter is True:
        reasons.append("voraussichtlich in Startelf (LigaInsider, Heuristik)")
    elif predicted_starter is False:
        reasons.append("laut LigaInsider evtl. nicht in Startelf (Heuristik)")
    if fixture_info:
        opp_str = ", ".join(fixture_info["opponents"]) if fixture_info["opponents"] else "?"
        reasons.append(f"Restprogramm {fixture_info['label']} (naechste Gegner: {opp_str})")
    if is_injured:
        reasons.append("Verletzungs-/Sperrrisiko (Kickbase)")
    if has_negative_news:
        reasons.append("kritische News aus mehreren Quellen")
    if mw_cycles > 0:
        reasons.append(f"{mw_cycles} Marktwert-Updates bis 1. Spieltag")
    if violates_my_limit:
        reasons.append("überschreitet 2-Spieler-Regel")
    if opponents_blocked:
        reasons.append("Gegner haben den Verein blockiert")
    if budget_exceeded:
        reasons.append("Gebot uebersteigt dein verfuegbares Budget")

    return {
        "label": label,
        "reason": ", ".join(reasons) if reasons else "keine besonderen Risiken",
        "max_bid": max_bid,
        "matched_news": matched_news[:3],
        "is_injured": is_injured,
        "total_points": total_points,
        "avg_points": avg_points,
        "budget_exceeded": budget_exceeded,
        "predicted_starter": predicted_starter,
        "fixture_difficulty": fixture_info["label"] if fixture_info else None,
    }


def get_llm_analysis(scored_players, cycles_info):
    if not GEMINI_API_KEY:
        return "FEHLER: Kein GEMINI_API_KEY gefunden!"
    players_json = json.dumps(scored_players, ensure_ascii=False, indent=2)
    prompt = f"""Du bist ein Kickbase-Experte. Ziel: Herbstmeister werden.
Noch {cycles_info} Marktwert-Updates. Max 2 Spieler per Verein, nie unter Marktwert bieten.

Hier sind die aktuellen Marktspieler mit Analyse-Daten (inkl. Gesamtpunkte, Ø-Punkte, Startelf-Tipp und Restprogramm):
{players_json}

Gib fuer die 3-5 spannendsten Spieler eine kurze Empfehlung ab (Kauf oder Risiko).
Beruecksichtige dabei sowohl Marktwert-Potenzial als auch Punkteform, Startelf-Wahrscheinlichkeit und Restprogramm-Schwierigkeit.
Antworte in klarem Fliesstext ohne Markdown-Formatierung (keine Sternchen, keine Unterstriche, keine Rauten)."""

    client = genai.Client(api_key=GEMINI_API_KEY)
    attempts = CONFIG["GEMINI_MAX_RETRIES"] + 1
    last_error = None

    for attempt in range(attempts):
        try:
            response = client.models.generate_content(
                model=CONFIG["GEMINI_MODEL"],
                contents=prompt,
                config=types.GenerateContentConfig(
                    temperature=0.4,
                    max_output_tokens=700,
                    automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
                ),
            )
            text = (response.text or "").strip()
            return text if text else "KI hat keine Analyse geliefert."
        except Exception as e:
            last_error = e
            print(f"Warnung: Gemini-Aufruf fehlgeschlagen (Versuch {attempt + 1}/{attempts}): {e}")
            if attempt < attempts - 1:
                time.sleep(CONFIG["GEMINI_RETRY_DELAY"])

    return f"KI-Analyse aktuell nicht verfuegbar ({type(last_error).__name__})."


def send_telegram_messages(full_text):
    tg_url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    limit = CONFIG["TELEGRAM_MAX_LEN"]
    chunks = []
    current = ""
    for line in full_text.split("\n"):
        if len(current) + len(line) + 1 > limit:
            chunks.append(current)
            current = line
        else:
            current = f"{current}\n{line}" if current else line
    if current:
        chunks.append(current)
    for chunk in chunks:
        try:
            resp = requests.post(
                tg_url,
                json={"chat_id": TELEGRAM_CHAT_ID, "text": chunk, "parse_mode": "HTML"},
                timeout=CONFIG["REQUEST_TIMEOUT"],
            )
            if resp.status_code != 200:
                print(f"Warnung: Telegram HTML-Versand fehlgeschlagen ({resp.status_code}): {resp.text}")
                clean = re.sub(r"<[^>]+>", "", chunk)
                requests.post(tg_url, json={"chat_id": TELEGRAM_CHAT_ID, "text": clean}, timeout=CONFIG["REQUEST_TIMEOUT"])
        except Exception as e:
            print(f"Fehler beim Telegram-Versand: {e}")


def main():
    missing = [k for k, v in REQUIRED_SECRETS.items() if not v]
    if missing:
        print(f"Fehler: Folgende Secrets fehlen: {', '.join(missing)}")
        sys.exit(1)

    payload = {"em": KB_EMAIL, "pass": KB_PASSWORD, "loy": False, "rep": {}}
    login_res_raw = session.post("https://api.kickbase.com/v4/user/login", json=payload, timeout=CONFIG["REQUEST_TIMEOUT"])
    login_res_raw.raise_for_status()
    login_res = login_res_raw.json()

    token = login_res.get("tkn")
    if not token:
        raise ValueError("Login fehlgeschlagen! Kein Token erhalten.")

    liga_id = login_res.get("srvl", [])[0].get("id")

    user_obj = login_res.get("u", {}) or {}
    my_user_id = user_obj.get("id") or user_obj.get("i")
    if not my_user_id:
        raise ValueError(f"Keine User-ID im Login-Response gefunden: {user_obj}")

    my_manager_id = user_obj.get("mid") or my_user_id
    if not my_manager_id:
        raise ValueError(f"Keine Manager-ID im Login-Response gefunden: {user_obj}")

    session.headers["Authorization"] = f"Bearer {token}"

    news_headlines = get_news()

    ranking_res = session.get(f"https://api.kickbase.com/v4/leagues/{liga_id}/ranking", timeout=CONFIG["REQUEST_TIMEOUT"]).json()
    market_res = session.get(f"https://api.kickbase.com/v4/leagues/{liga_id}/market", timeout=CONFIG["REQUEST_TIMEOUT"]).json()

    players = sorted(market_res.get("it", []), key=lambda x: x.get("exs", 999999))

    blocked_teams = build_blocked_teams(liga_id, ranking_res, my_manager_id)
    my_team_counts = get_my_team_counts(liga_id, my_manager_id)
    my_budget = get_my_budget(liga_id)
    mw_cycles = market_updates_until_first_matchday()

    team_names = get_team_names() if CONFIG["STARTELF_HEURISTIC_ENABLED"] or CONFIG["FIXTURE_DIFFICULTY_ENABLED"] else {}
    lineup_cache = {}

    season_fixtures = get_season_fixtures() if CONFIG["FIXTURE_DIFFICULTY_ENABLED"] else []
    fixture_cache = {}

    if CONFIG["DEBUG_PLAYER_DETAIL"]:
        alle_tids = [{"name": p.get("n"), "tid": p.get("tid")} for p in players[:CONFIG["PLAYERS_TO_SHOW"]]]
        print("DEBUG Alle Name->tid dieser Marktliste:", json.dumps(alle_tids, ensure_ascii=False))

    msg = "⚽ <b>Markt-Update: Kickbase Elite</b>\n\n"

    winter_warning = winter_reset_warning()
    if winter_warning:
        msg += f"{esc(winter_warning)}\n\n"

    if my_budget is not None:
        budget_str = f"{my_budget:,}".replace(",", ".")
        msg += f"💳 <b>Verfuegbares Budget:</b> {budget_str} €\n\n"

    scored_players = []
    debug_done = False

    for p in players[:CONFIG["PLAYERS_TO_SHOW"]]:
        nachname = p.get("n", "Unbekannt")
        mw_str = f"{p.get('mv', 0):,}".replace(",", ".")
        trend = "📈" if p.get("mvt") == 1 else "📉" if p.get("mvt") == 2 else "➖"
        exs = p.get("exs", 0)
        time_str = f"{exs//3600}h {(exs%3600)//60}m" if exs > 0 else "Abgelaufen"

        player_id = p.get("id")
        p_detail = {}
        if player_id:
            try:
                res = session.get(f"https://api.kickbase.com/v4/leagues/{liga_id}/players/{player_id}", timeout=CONFIG["REQUEST_TIMEOUT"])
                res.raise_for_status()
                p_detail = res.json()
                if CONFIG["DEBUG_PLAYER_DETAIL"] and not debug_done:
                    debug_done = True
            except Exception as e:
                print(f"Warnung: Detaildaten fuer {nachname} nicht geladen: {e}")

        team_name = team_names.get(p.get("tid"))
        team_key = get_team_key(team_name)

        lineup_names = None
        if CONFIG["STARTELF_HEURISTIC_ENABLED"] and team_key:
            slug = TEAM_LINEUP_SLUGS.get(team_key)
            if slug:
                if slug not in lineup_cache:
                    lineup_cache[slug] = get_predicted_lineup(slug)
                lineup_names = lineup_cache[slug]

        fixture_info = None
        if CONFIG["FIXTURE_DIFFICULTY_ENABLED"] and team_key:
            if team_key not in fixture_cache:
                fixture_cache[team_key] = fixture_difficulty(season_fixtures, team_key)
            fixture_info = fixture_cache[team_key]

        eval_result = evaluate_player(
            p, p_detail, news_headlines, blocked_teams, my_team_counts, mw_cycles, my_budget, lineup_names, fixture_info
        )
        max_bid_str = f"{eval_result['max_bid']:,}".replace(",", ".")

        msg += f"• <b>{esc(nachname)}</b> {trend} | 💰 {mw_str} € | ⏳ {time_str}\n"
        msg += f"  ✅ <b>Einschätzung:</b> {esc(eval_result['label'])} (Gebot mind./max.: {max_bid_str} €)\n"
        msg += f"  📊 <b>Punkte:</b> {eval_result['total_points']} (Ø {eval_result['avg_points']})\n"

        if eval_result["predicted_starter"] is True:
            msg += "  🏟️ <b>Startelf-Tipp:</b> Wahrscheinlich (LigaInsider)\n"
        elif eval_result["predicted_starter"] is False:
            msg += "  🪑 <b>Startelf-Tipp:</b> Fraglich/Bank (LigaInsider)\n"

        if eval_result["fixture_difficulty"] == "leicht":
            msg += "  📅 <b>Restprogramm:</b> Leicht (Form-Pick)\n"
        elif eval_result["fixture_difficulty"] == "schwer":
            msg += "  📅 <b>Restprogramm:</b> Schwer\n"

        if eval_result["reason"]:
            msg += f"  ℹ️ <b>Begründung:</b> {esc(eval_result['reason'])}\n"
        if eval_result["is_injured"]:
            msg += "  🩹 <b>Status: Verletzt / Reha / Gesperrt</b>\n"
        if eval_result["budget_exceeded"]:
            msg += "  🚫 <b>Achtung: Gebot uebersteigt dein Budget</b>\n"
        if p.get("tid") in blocked_teams:
            msg += f"  🚫 <b>Sperre (Gegner):</b> {esc(', '.join(blocked_teams[p.get('tid')]))}\n"
        for h in eval_result["matched_news"]:
            msg += f"  📰 <b>News:</b> {esc(h)}\n"
        msg += "\n"

        scored_players.append({
            "name": nachname,
            "marktwert": p.get("mv", 0),
            "trend": "steigend" if p.get("mvt") == 1 else "fallend",
            "gesamtpunkte": eval_result["total_points"],
            "durchschnittspunkte": eval_result["avg_points"],
            "startelf_tipp": eval_result["predicted_starter"],
            "restprogramm": eval_result["fixture_difficulty"],
            "einschaetzung": eval_result["label"],
            "risiken": eval_result["reason"],
        })

    llm_text = get_llm_analysis(scored_players, mw_cycles)
    msg += "🤖 <b>KI-Manager-Einschätzung:</b>\n" + esc(llm_text)

    send_telegram_messages(msg)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"Fehler: {e}")
        traceback.print_exc()
        sys.exit(1)
