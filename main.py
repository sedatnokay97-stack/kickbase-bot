import os
import sys
import re
import json
import html
import time
import base64
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
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN")
GITHUB_REPOSITORY = os.environ.get("GITHUB_REPOSITORY")

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
    "GEMINI_MODEL": "gemini-3.6-flash",
    "GEMINI_FALLBACK_MODEL": "gemini-2.0-flash",
    "GEMINI_MAX_RETRIES": 1,
    "GEMINI_RETRY_DELAY": 5,
    "DEBUG_PLAYER_DETAIL": True,
    "STARTELF_HEURISTIC_ENABLED": True,
    "FIXTURE_DIFFICULTY_ENABLED": True,
    "FIXTURE_LOOKAHEAD": 3,
    "SEASON_YEAR": 2026,
    "COMPETITION_ID": "1",
    "NEWS_FILTER_WITH_GEMINI": True,
    "NEWS_AGING_ENABLED": True,
    "OPPONENT_BUDGET_TRACKING_ENABLED": False,
    "HISTORY_FILE_PATH": "history.json",
    "MV_HISTORY_MAX_ENTRIES": 6,
    "KB_RETRY_ATTEMPTS": 3,
    "DRY_RUN": os.environ.get("DRY_RUN", "false").lower() == "true",
    "OVERPAY_TIERS": [(110, 0.30), (60, 0.15), (0, 0.08)],
    "OVERPAY_CAP_RISKANT": 0.05,
}

NEWS_FEEDS = [
    "https://newsfeed.kicker.de/news/bundesliga",
    "https://feeds.t-online.de/rss/transfermarkt",
    "https://www.sportschau.de/fussball/bundesliga/index~rss2.xml",
]
LIGAINSIDER_INJURY_URL = "https://www.ligainsider.de/bundesliga/verletzte-und-gesperrte-spieler/"

TEAM_LINEUP_SLUGS = {
    "bayern": "fc-bayern-muenchen/1", "werder": "sv-werder-bremen/2",
    "frankfurt": "eintracht-frankfurt/3", "leverkusen": "bayer-04-leverkusen/4",
    "gladbach": "borussia-moenchengladbach/5", "hamburg": "hamburger-sv/9",
    "hoffenheim": "tsg-hoffenheim/10", "stuttgart": "vfb-stuttgart/12",
    "schalke": "fc-schalke-04/13", "dortmund": "borussia-dortmund/14",
    "koeln": "1-fc-koeln/15", "mainz": "1-fsv-mainz-05/17",
    "freiburg": "sc-freiburg/18", "augsburg": "fc-augsburg/21",
    "union berlin": "1-fc-union-berlin/1246", "paderborn": "sc-paderborn-07/1249",
    "leipzig": "rb-leipzig/1311", "elversberg": "sv-07-elversberg/1331",
}
BUNDESLIGA_TEAM_KEYS = list(TEAM_LINEUP_SLUGS.keys())
TEAM_DISPLAY_NAMES = {
    "bayern": "Bayern", "werder": "Werder Bremen", "frankfurt": "Frankfurt",
    "leverkusen": "Leverkusen", "gladbach": "Gladbach", "hamburg": "Hamburger SV",
    "hoffenheim": "Hoffenheim", "stuttgart": "Stuttgart", "schalke": "Schalke 04",
    "dortmund": "Dortmund", "koeln": "Koeln", "mainz": "Mainz 05",
    "freiburg": "Freiburg", "augsburg": "Augsburg", "union berlin": "Union Berlin",
    "paderborn": "Paderborn", "leipzig": "Leipzig", "elversberg": "Elversberg",
}
KICKBASE_TID_TO_TEAM = {
    "2": "bayern", "4": "frankfurt", "5": "freiburg", "7": "leverkusen",
    "9": "stuttgart", "10": "werder", "14": "hoffenheim", "15": "gladbach",
    "18": "mainz", "28": "koeln", "40": "union berlin", "43": "leipzig",
}
TEAM_STRENGTH_LAST_SEASON = {
    "bayern": 89, "dortmund": 73, "leipzig": 65, "stuttgart": 62,
    "hoffenheim": 61, "leverkusen": 59, "freiburg": 47, "frankfurt": 44,
    "augsburg": 43, "mainz": 40, "union berlin": 39, "gladbach": 38,
    "hamburg": 38, "koeln": 32, "werder": 32, "paderborn": 22,
    "schalke": 20, "elversberg": 20,
}
TEAM_STRENGTH_DEFAULT = 45
LINEUP_SKIP_PHRASES = {
    "auf dieser seite", "wir aktualisieren", "voraussichtliche aufstellung",
    "spieler stand in der startelf", "spieler wurde eingewechselt",
    "spieler sass auf der bank", "spieler war nicht im", "zur detailansicht",
    "kader", "heimspiel gegen", "auswartsspiel gegen", "torhueter",
    "abwehrspieler", "mittelfeldspieler", "stuermer", "legende",
}
NEWS_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"}

session = requests.Session()
session.headers.update({
    "User-Agent": "Kickbase/6.8.0 (iPhone; iOS 16.5; Scale/3.00)",
    "Content-Type": "application/json",
})


def esc(value):
    return html.escape(str(value), quote=False)


def kb_get(url, **kwargs):
    """GET mit Retry/Backoff; permanente 4xx-Fehler werden nicht wiederholt."""
    timeout = kwargs.pop("timeout", CONFIG["REQUEST_TIMEOUT"])

    for attempt in range(CONFIG["KB_RETRY_ATTEMPTS"]):
        try:
            response = session.get(url, timeout=timeout, **kwargs)
            response.raise_for_status()
            return response

        except requests.HTTPError as exc:
            status_code = exc.response.status_code if exc.response is not None else None

            if status_code is not None and 400 <= status_code < 500 and status_code != 429:
                print(f"Info: Request nicht verfügbar ({status_code}) {url}; kein Retry.")
                return None

            print(
                f"Warnung: Request fehlgeschlagen "
                f"({attempt + 1}/{CONFIG['KB_RETRY_ATTEMPTS']}) {url}: {exc}"
            )

        except requests.RequestException as exc:
            print(
                f"Warnung: Request fehlgeschlagen "
                f"({attempt + 1}/{CONFIG['KB_RETRY_ATTEMPTS']}) {url}: {exc}"
            )

        if attempt < CONFIG["KB_RETRY_ATTEMPTS"] - 1:
            time.sleep(2 ** attempt)

    return None


def load_history():
    if not GITHUB_TOKEN or not GITHUB_REPOSITORY:
        return {}, None
    url = f"https://api.github.com/repos/{GITHUB_REPOSITORY}/contents/{CONFIG['HISTORY_FILE_PATH']}"
    headers = {"Authorization": f"Bearer {GITHUB_TOKEN}", "Accept": "application/vnd.github+json"}
    try:
        response = requests.get(url, headers=headers, timeout=CONFIG["REQUEST_TIMEOUT"])
        if response.status_code == 404:
            return {}, None
        response.raise_for_status()
        payload = response.json()
        return json.loads(base64.b64decode(payload["content"]).decode("utf-8")), payload["sha"]
    except Exception as exc:
        print(f"Warnung: history.json konnte nicht geladen werden: {exc}")
        return {}, None


def save_history(history, sha):
    if CONFIG["DRY_RUN"] or not GITHUB_TOKEN or not GITHUB_REPOSITORY:
        return
    url = f"https://api.github.com/repos/{GITHUB_REPOSITORY}/contents/{CONFIG['HISTORY_FILE_PATH']}"
    headers = {"Authorization": f"Bearer {GITHUB_TOKEN}", "Accept": "application/vnd.github+json"}
    payload = {
        "message": "Update history.json (automatisiert)",
        "content": base64.b64encode(json.dumps(history, ensure_ascii=False, indent=2).encode("utf-8")).decode("utf-8"),
        "branch": "main",
    }
    if sha:
        payload["sha"] = sha
    try:
        requests.put(url, headers=headers, json=payload, timeout=CONFIG["REQUEST_TIMEOUT"]).raise_for_status()
    except Exception as exc:
        print(f"Warnung: history.json konnte nicht gespeichert werden: {exc}")


def get_ligainsider_injury_news():
    headlines = []
    try:
        response = requests.get(LIGAINSIDER_INJURY_URL, headers=NEWS_HEADERS, timeout=CONFIG["REQUEST_TIMEOUT"])
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        for tag in soup.find_all(["b", "strong"]):
            text = tag.get_text(strip=True)
            if len(text.split()) < 2:
                continue
            parent = tag.find_parent(["div", "li", "tr", "p"]) or tag.parent
            full_text = parent.get_text(" ", strip=True)
            if full_text:
                headlines.append(full_text)
    except Exception as exc:
        print(f"Warnung: LigaInsider-Verletztenseite nicht ladbar: {exc}")
    return headlines


def get_news():
    headlines = []
    for url in NEWS_FEEDS:
        try:
            response = requests.get(url, headers=NEWS_HEADERS, timeout=CONFIG["REQUEST_TIMEOUT"])
            response.raise_for_status()
            root = ET.fromstring(response.text)
            for item in root.findall(".//item"):
                title = item.find("title")
                if title is not None and title.text:
                    headlines.append(title.text)
        except Exception as exc:
            print(f"Warnung: News-Feed nicht ladbar ({url}): {exc}")
    headlines.extend(get_ligainsider_injury_news())
    return list(dict.fromkeys(headlines))


def count_by_team(squad_items):
    counts = {}
    for player in squad_items:
        tid = player.get("tid")
        if tid:
            counts[tid] = counts.get(tid, 0) + 1
    return counts


def build_blocked_teams(liga_id, ranking, my_manager_id):
    blocked = {}
    for user in ranking.get("users", []):
        manager_id = user.get("mid") or user.get("id")
        if not manager_id or manager_id == my_manager_id:
            continue
        response = kb_get(f"https://api.kickbase.com/v4/leagues/{liga_id}/managers/{manager_id}/squad")
        squad = response.json().get("it", []) if response else []
        for tid, count in count_by_team(squad).items():
            if count >= CONFIG["MAX_PER_TEAM"]:
                blocked.setdefault(tid, []).append(user.get("name", "Unbekannt"))
    return blocked


def get_my_team_counts(liga_id, my_manager_id):
    if not my_manager_id:
        return {}
    response = kb_get(f"https://api.kickbase.com/v4/leagues/{liga_id}/managers/{my_manager_id}/squad")
    return count_by_team(response.json().get("it", [])) if response else {}


def get_my_budget(liga_id):
    for endpoint in ("me/budget", "me"):
        response = kb_get(f"https://api.kickbase.com/v4/leagues/{liga_id}/{endpoint}")
        if not response:
            continue
        payload = response.json()
        for key in ("budget", "b", "amount"):
            if payload.get(key) is not None:
                return int(payload[key])
    return None


def get_team_names():
    """
    Verwendet immer die statische, bekannte tid-Zuordnung.
    Der undokumentierte Teams-Endpunkt dient nur als optionales Enrichment.
    """
    names = dict(KICKBASE_TID_TO_TEAM)

    url = f"https://api.kickbase.com/v4/competitions/{CONFIG['COMPETITION_ID']}/teams"
    response = kb_get(url)

    if not response:
        print("Info: Team-Endpunkt nicht verfügbar; nutze statische Team-Zuordnung.")
        return names

    try:
        data = response.json()
        items = data.get("it", data) if isinstance(data, dict) else data

        if isinstance(items, list):
            for item in items:
                tid = item.get("id") or item.get("tid")
                name = item.get("name") or item.get("nm") or item.get("n")

                if tid and name:
                    names[str(tid)] = name

    except (ValueError, TypeError) as exc:
        print(
            "Info: Team-Endpunkt konnte nicht ausgewertet werden; "
            f"nutze statische Zuordnung: {exc}"
        )

    return names


def get_team_key(team_name):
    if not team_name:
        return None
    team_name = team_name.lower()
    if "koln" in team_name or "koeln" in team_name:
        return "koeln"
    for key in BUNDESLIGA_TEAM_KEYS:
        if key in team_name:
            return key
    return None


def get_predicted_lineup(slug_id):
    names = []
    try:
        response = requests.get(f"https://www.ligainsider.de/bundesliga/team/{slug_id}/", headers=NEWS_HEADERS, timeout=CONFIG["REQUEST_TIMEOUT"])
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        for tag in soup.find_all(["b", "strong"]):
            text = tag.get_text(strip=True)
            low = text.lower()
            if not text or len(text.split()) > 3 or any(phrase in low for phrase in LINEUP_SKIP_PHRASES) or any(c.isdigit() for c in text):
                continue
            if text not in names:
                names.append(text)
            if len(names) >= 11:
                break
    except Exception as exc:
        print(f"Warnung: Lineup-Vorschau fuer {slug_id} nicht ladbar: {exc}")
    return names


def get_season_fixtures():
    try:
        response = requests.get(f"https://api.openligadb.de/getmatchdata/bl1/{CONFIG['SEASON_YEAR']}", timeout=CONFIG["REQUEST_TIMEOUT"])
        response.raise_for_status()
        return response.json()
    except Exception as exc:
        print(f"Warnung: OpenLigaDB-Spielplan nicht ladbar: {exc}")
        return []


def derive_first_matchday(fixtures, fallback):
    dates = []
    for fixture in fixtures:
        raw_date = fixture.get("matchDateTimeUTC") or fixture.get("matchDateTime")
        if raw_date:
            try:
                dates.append(datetime.fromisoformat(raw_date.replace("Z", "+00:00")).date())
            except ValueError:
                pass
    return min(dates) if dates else fallback


def fixture_difficulty(fixtures, team_key):
    if not fixtures or not team_key:
        return None
    upcoming = []
    for fixture in fixtures:
        if fixture.get("matchIsFinished"):
            continue
        home = get_team_key((fixture.get("team1") or {}).get("teamName"))
        away = get_team_key((fixture.get("team2") or {}).get("teamName"))
        if home == team_key and away:
            upcoming.append((fixture.get("matchDateTimeUTC", ""), away))
        elif away == team_key and home:
            upcoming.append((fixture.get("matchDateTimeUTC", ""), home))
    opponents = [key for _, key in sorted(upcoming)[:CONFIG["FIXTURE_LOOKAHEAD"]]]
    if not opponents:
        return None
    average = sum(TEAM_STRENGTH_LAST_SEASON.get(key, TEAM_STRENGTH_DEFAULT) for key in opponents) / len(opponents)
    label = "leicht" if average <= 35 else "mittel" if average <= 55 else "schwer"
    return {"label": label, "opponents": [TEAM_DISPLAY_NAMES.get(key, key.title()) for key in opponents]}


def market_updates_until_first_matchday(first_matchday):
    now = datetime.now(timezone.utc)
    if now.date() >= first_matchday:
        return 0
    remaining = (first_matchday - now.date()).days
    today_update = now.replace(hour=CONFIG["MW_UPDATE_HOUR_UTC"], minute=0, second=0, microsecond=0)
    return remaining if now < today_update else max(0, remaining - 1)


def call_gemini(prompt, max_tokens, temperature):
    client = genai.Client(api_key=GEMINI_API_KEY)
    last_error = None
    for model in (CONFIG["GEMINI_MODEL"], CONFIG["GEMINI_FALLBACK_MODEL"]):
        for attempt in range(CONFIG["GEMINI_MAX_RETRIES"] + 1):
            try:
                response = client.models.generate_content(
                    model=model,
                    contents=prompt,
                    config=types.GenerateContentConfig(temperature=temperature, max_output_tokens=max_tokens),
                )
                if response.text and response.text.strip():
                    return response.text.strip()
            except Exception as exc:
                last_error = exc
                print(f"Warnung: Gemini-Aufruf mit {model} fehlgeschlagen: {exc}")
                if attempt < CONFIG["GEMINI_MAX_RETRIES"]:
                    time.sleep(CONFIG["GEMINI_RETRY_DELAY"])
    raise last_error or RuntimeError("Gemini lieferte keine Antwort")


def filter_news_with_gemini(headlines, names):
    """
    Best-effort-News-Zuordnung via Gemini.
    Bei ungultigem Modell-JSON wird automatisch der Regex-Fallback verwendet.
    """
    if not GEMINI_API_KEY or not headlines or not names:
        return {}

    prompt = f'''Ordne nur eindeutig passende Fussball-Schlagzeilen den folgenden
Kickbase-Spieler-Nachnamen zu und bewerte jeden Treffer als positiv, negativ oder neutral.

Antworte ausschließlich mit valide parsebarem JSON:
{{"Nachname": [{{"headline": "...", "sentiment": "negativ"}}]}}

Wichtig:
- Kein Markdown.
- Kein erklärender Text vor oder nach dem JSON.
- Keine Zeilenumbruche innerhalb eines JSON-Strings.
- Spieler ohne Treffer weglassen.

Spieler: {", ".join(names)}

Schlagzeilen:
{json.dumps(headlines, ensure_ascii=False)}'''

    try:
        raw = call_gemini(prompt, 1000, 0.1).strip()
        raw = re.sub(
            r"^```(?:json)?\s*|\s*```$",
            "",
            raw,
            flags=re.IGNORECASE,
        ).strip()

        try:
            result = json.loads(raw)
        except json.JSONDecodeError:
            start = raw.find("{")
            end = raw.rfind("}")

            if start < 0 or end <= start:
                print("Info: Gemini-News-Filter liefert kein JSON; Regex-Fallback aktiv.")
                return {}

            result = json.loads(raw[start:end + 1])

        return result if isinstance(result, dict) else {}

    except (json.JSONDecodeError, ValueError, TypeError) as exc:
        print(f"Info: Gemini-News-Filter liefert kein verwertbares JSON; Regex-Fallback aktiv: {exc}")
        return {}

    except Exception as exc:
        print(f"Info: Gemini-News-Filter nicht verfügbar; Regex-Fallback aktiv: {exc}")
        return {}


def match_news_for_player(lastname, headlines, gemini_map):
    if gemini_map and lastname in gemini_map:
        return gemini_map[lastname]
    return [{"headline": headline, "sentiment": None} for headline in headlines if re.search(r"\b" + re.escape(lastname) + r"\b", headline, re.IGNORECASE)]


def classify_negative_news(matched, known):
    fresh, aged = [], []
    for item in matched:
        headline = item.get("headline", "")
        negative = item.get("sentiment") == "negativ" or (item.get("sentiment") is None and re.search(r"verletzt|muskul|ausfall|gesperrt|rot|kreuzband|bruch|reha|trainingsrueckstand", headline, re.IGNORECASE))
        if negative:
            (aged if headline in known else fresh).append(headline)
    return fresh, aged


def extract_points(player, detail):
    total = next((source[key] for source in (player or {}, detail or {}) for key in ("totalPoints", "p", "tp", "points", "sp") if source.get(key) is not None), 0)
    average = next((source[key] for source in (player or {}, detail or {}) for key in ("averagePoints", "ap", "avp", "avgPoints", "sap") if source.get(key) is not None), 0)
    return total, average


def extract_form_points(detail):
    for key in ("lastMatchdaysPoints", "recentPoints", "form", "last5", "matchdaysPoints"):
        value = (detail or {}).get(key)
        if isinstance(value, list):
            values = [v for v in value if isinstance(v, (int, float))]
            if values:
                return sum(values) / len(values)
        if isinstance(value, (int, float)):
            return value
    return None


def compute_mv_acceleration(history, current_mv):
    if len(history) < 2:
        return None
    previous_change = history[-1]["mv"] - history[-2]["mv"]
    current_change = current_mv - history[-1]["mv"]
    if previous_change == 0:
        return None
    if current_change > previous_change and current_change > 0:
        return "beschleunigt"
    if current_change < previous_change and current_change < 0:
        return "verstaerkt fallend"
    return None


def get_overpay_pct(avg_points):
    for threshold, percentage in CONFIG["OVERPAY_TIERS"]:
        if avg_points >= threshold:
            return percentage
    return CONFIG["OVERPAY_TIERS"][-1][1]


def evaluate_player(player, detail, headlines, blocked_teams, my_team_counts, cycles, budget, lineup_names, fixture, gemini_map, known_headlines, acceleration, form_points):
    lastname = player.get("n", "Unbekannt")
    market_value = player.get("mv", 0)
    tid = player.get("tid")
    trend = player.get("mvt")
    total_points, avg_points = extract_points(player, detail)
    status = str(player.get("st", "0"))
    detail_status = str((detail or {}).get("status", (detail or {}).get("st", "0")))
    injured = status in {"1", "2", "4", "8"} or detail_status in {"1", "2", "4", "8"}
    matched = match_news_for_player(lastname, headlines, gemini_map)
    fresh_negative, aged_negative = classify_negative_news(matched, known_headlines)
    violates_limit = my_team_counts.get(tid, 0) >= CONFIG["MAX_PER_TEAM"]
    opponent_blocked = tid in blocked_teams
    starter = None if not lineup_names else any(re.search(r"\b" + re.escape(lastname) + r"\b", name, re.IGNORECASE) for name in lineup_names)

    score = 1 if market_value < 2_000_000 else 2 if market_value < 6_000_000 else 3 if market_value < 12_000_000 else 4
    score += 2 if trend == 1 else -1 if trend == 2 else 0
    score += 2 if cycles >= 15 else 1 if cycles >= 7 else 0
    score += 3 if avg_points >= 100 else 2 if avg_points >= 60 else 1 if avg_points >= 30 else -1 if 0 < avg_points < 15 else 0
    score += 1 if starter is True else -1 if starter is False else 0
    score += 1 if fixture and fixture["label"] == "leicht" else -1 if fixture and fixture["label"] == "schwer" else 0
    score += 1 if acceleration == "beschleunigt" else -1 if acceleration == "verstaerkt fallend" else 0
    score += 1 if form_points is not None and avg_points > 0 and form_points > avg_points * 1.1 else -1 if form_points is not None and avg_points > 0 and form_points < avg_points * 0.7 else 0
    score -= 3 if injured else 0
    score -= 2 if fresh_negative else 1 if aged_negative else 0
    score -= 4 if violates_limit else 0
    score -= 1 if opponent_blocked else 0

    label = "TOP-KAUF" if score >= 6 and not violates_limit and not injured and not fresh_negative else "SOLIDE" if score >= 3 else "RISKANT"
    overpay = min(get_overpay_pct(avg_points), CONFIG["OVERPAY_CAP_RISKANT"]) if label == "RISKANT" else get_overpay_pct(avg_points)
    max_bid = max(market_value, int(market_value * (1 + overpay)))
    budget_exceeded = budget is not None and max_bid > budget

    reasons = []
    if trend == 1: reasons.append("positiver Marktwert-Trend")
    if trend == 2: reasons.append("fallender Marktwert-Trend")
    if acceleration == "beschleunigt": reasons.append("Marktwert-Anstieg beschleunigt sich")
    if acceleration == "verstaerkt fallend": reasons.append("Marktwert faellt staerker als zuvor")
    if avg_points >= 60: reasons.append(f"starke Punkteform (Durchschnitt {avg_points})")
    if starter is True: reasons.append("voraussichtlich in Startelf")
    if starter is False: reasons.append("Startelf fraglich")
    if fixture: reasons.append(f"Restprogramm {fixture['label']}")
    if injured: reasons.append("Verletzungs- oder Sperrrisiko")
    if fresh_negative: reasons.append("kritische News neu")
    if aged_negative: reasons.append("News bereits bekannt und vermutlich teilweise eingepreist")
    if violates_limit: reasons.append("ueberschreitet 2-Spieler-Regel")
    if opponent_blocked: reasons.append("Gegner haben den Verein blockiert")
    if budget_exceeded: reasons.append("Gebot uebersteigt Budget")

    return {
        "label": label, "reason": ", ".join(reasons) or "keine besonderen Risiken",
        "max_bid": max_bid, "matched_news": [item.get("headline", "") for item in matched[:3]],
        "is_injured": injured, "total_points": total_points, "avg_points": avg_points,
        "budget_exceeded": budget_exceeded, "predicted_starter": starter,
        "fixture_difficulty": fixture["label"] if fixture else None,
        "all_negative_headlines": fresh_negative + aged_negative,
    }


def get_llm_analysis(scored_players, cycles):
    if not GEMINI_API_KEY:
        return "KI-Analyse nicht verfuegbar: GEMINI_API_KEY fehlt."
    prompt = f'''Du bist Kickbase-Experte. Noch {cycles} Marktwert-Updates. Maximal zwei Spieler pro Verein und nie unter Marktwert bieten. Bewerte die interessantesten 3 bis 5 Spieler anhand von Marktwert, Punkten, Startelf und Restprogramm. Antworte kurz in Fliesstext ohne Markdown. Daten: {json.dumps(scored_players, ensure_ascii=False)}'''
    try:
        return call_gemini(prompt, 700, 0.4)
    except Exception as exc:
        return f"KI-Analyse aktuell nicht verfuegbar ({type(exc).__name__})."


def send_telegram_messages(message):
    if CONFIG["DRY_RUN"]:
        print("DRY_RUN aktiv – Telegram-Nachricht wird nicht gesendet.")
        print(message)
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    chunks, current = [], ""
    for line in message.split("\n"):
        if len(current) + len(line) + 1 > CONFIG["TELEGRAM_MAX_LEN"]:
            chunks.append(current)
            current = line
        else:
            current = f"{current}\n{line}" if current else line
    if current:
        chunks.append(current)
    for chunk in chunks:
        try:
            response = requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": chunk, "parse_mode": "HTML"}, timeout=CONFIG["REQUEST_TIMEOUT"])
            if response.status_code != 200:
                plain = re.sub(r"<[^>]+>", "", chunk)
                requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": plain}, timeout=CONFIG["REQUEST_TIMEOUT"])
        except Exception as exc:
            print(f"Fehler beim Telegram-Versand: {exc}")


def main():
    missing = [key for key, value in REQUIRED_SECRETS.items() if not value]
    if missing:
        raise ValueError(f"Fehlende Secrets: {', '.join(missing)}")

    login = session.post("https://api.kickbase.com/v4/user/login", json={"em": KB_EMAIL, "pass": KB_PASSWORD, "loy": False, "rep": {}}, timeout=CONFIG["REQUEST_TIMEOUT"])
    login.raise_for_status()
    login_data = login.json()
    token = login_data.get("tkn")
    if not token:
        raise ValueError("Kickbase-Login fehlgeschlagen: kein Token.")
    session.headers["Authorization"] = f"Bearer {token}"

    league_id = login_data.get("srvl", [])[0].get("id")
    user = login_data.get("u", {}) or {}
    manager_id = user.get("mid") or user.get("id") or user.get("i")
    if not league_id or not manager_id:
        raise ValueError("Liga- oder Manager-ID nicht gefunden.")

    history, history_sha = load_history()
    history.setdefault("players", {})
    news = get_news()
    ranking_response = kb_get(f"https://api.kickbase.com/v4/leagues/{league_id}/ranking")
    market_response = kb_get(f"https://api.kickbase.com/v4/leagues/{league_id}/market")
    if not ranking_response or not market_response:
        raise RuntimeError("Ranking oder Markt konnte nicht geladen werden.")

    ranking = ranking_response.json()
    market_players = sorted(market_response.json().get("it", []), key=lambda player: player.get("exs", 999999))[:CONFIG["PLAYERS_TO_SHOW"]]
    blocked = build_blocked_teams(league_id, ranking, manager_id)
    team_counts = get_my_team_counts(league_id, manager_id)
    budget = get_my_budget(league_id)
    fixtures = get_season_fixtures() if CONFIG["FIXTURE_DIFFICULTY_ENABLED"] else []
    first_matchday = derive_first_matchday(fixtures, CONFIG["FIRST_MATCHDAY"])
    cycles = market_updates_until_first_matchday(first_matchday)
    team_names = get_team_names()
    gemini_news = filter_news_with_gemini(news, [player.get("n") for player in market_players if player.get("n")])
    lineup_cache, fixture_cache = {}, {}

    message = "⚽ <b>Markt-Update: Kickbase Elite</b>\n\n"
    if budget is not None:
        message += f"💳 <b>Verfuegbares Budget:</b> {budget:,} EUR\n\n".replace(",", ".")
    if CONFIG["DRY_RUN"]:
        message += "🧪 <b>DRY_RUN aktiv</b> – Versand und History-Speicherung sind deaktiviert.\n\n"

    scored = []
    for player in market_players:
        player_id = str(player.get("id") or "")
        detail_response = kb_get(f"https://api.kickbase.com/v4/leagues/{league_id}/players/{player_id}") if player_id else None
        detail = detail_response.json() if detail_response else {}
        team_key = get_team_key(team_names.get(str(player.get("tid"))))
        lineup = None
        if team_key in TEAM_LINEUP_SLUGS:
            lineup = lineup_cache.setdefault(team_key, get_predicted_lineup(TEAM_LINEUP_SLUGS[team_key]))
        fixture = fixture_cache.setdefault(team_key, fixture_difficulty(fixtures, team_key)) if team_key else None
        previous = history["players"].get(player_id, {"mv_history": [], "known_headlines": []})
        result = evaluate_player(
            player, detail, news, blocked, team_counts, cycles, budget, lineup, fixture,
            gemini_news, previous.get("known_headlines", []),
            compute_mv_acceleration(previous.get("mv_history", []), player.get("mv", 0)),
            extract_form_points(detail),
        )
        mv_history = (previous.get("mv_history", []) + [{"date": datetime.now(timezone.utc).isoformat(), "mv": player.get("mv", 0)}])[-CONFIG["MV_HISTORY_MAX_ENTRIES"]:]
        known = list(dict.fromkeys(previous.get("known_headlines", []) + result["all_negative_headlines"]))[-20:]
        if player_id:
            history["players"][player_id] = {"mv_history": mv_history, "known_headlines": known}

        name, mv = player.get("n", "Unbekannt"), player.get("mv", 0)
        trend = "📈" if player.get("mvt") == 1 else "📉" if player.get("mvt") == 2 else "➖"
        message += f"• <b>{esc(name)}</b> {trend} | 💰 {mv:,} EUR\n".replace(",", ".")
        message += f"  ✅ <b>Einschaetzung:</b> {esc(result['label'])} (Max. Gebot: {result['max_bid']:,} EUR)\n".replace(",", ".")
        message += f"  📊 <b>Punkte:</b> {result['total_points']} (Durchschnitt {result['avg_points']})\n"
        message += f"  ℹ️ <b>Begruendung:</b> {esc(result['reason'])}\n"
        for headline in result["matched_news"]:
            message += f"  📰 <b>News:</b> {esc(headline)}\n"
        message += "\n"
        scored.append({"name": name, "marktwert": mv, "punkte": result["avg_points"], "startelf": result["predicted_starter"], "restprogramm": result["fixture_difficulty"], "einschaetzung": result["label"], "risiken": result["reason"]})

    message += "🤖 <b>KI-Manager-Einschaetzung:</b>\n" + esc(get_llm_analysis(scored, cycles))
    send_telegram_messages(message)
    save_history(history, history_sha)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"Fehler: {exc}")
        traceback.print_exc()
        sys.exit(1)
