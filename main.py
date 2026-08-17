import os
import requests
import xml.etree.ElementTree as ET
import re
import json
import html
from datetime import datetime, date

KB_EMAIL = os.environ.get("KB_EMAIL")
KB_PASSWORD = os.environ.get("KB_PASSWORD")
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

# "flash-latest" ist ein Alias, der immer automatisch auf das aktuell
# gültige, stabile Flash-Modell zeigt -> kein manuelles Update mehr nötig.
GEMINI_MODEL = "gemini-flash-latest"

CONFIG = {
    "MAX_PER_TEAM": 2,
    "FIRST_MATCHDAY": date(2026, 8, 28),
    "MW_UPDATE_HOUR_UTC": 20,
    "PLAYERS_TO_SHOW": 15,
    "REQUEST_TIMEOUT": 10
}


def get_news():
    news = []
    try:
        res = requests.get(
            "https://www.ligainsider.de/rss/news.xml",
            timeout=CONFIG["REQUEST_TIMEOUT"]
        )
        if res.status_code == 200:
            root = ET.fromstring(res.text)
            for item in root.findall(".//item"):
                title = item.find("title")
                if title is not None and title.text:
                    news.append(title.text)
    except Exception as e:
        print(f"News-Fehler: {e}")
    return news


def count_by_team(squad_items):
    counts = {}
    for sp in squad_items:
        tid = sp.get("tid")
        if not tid:
            continue
        counts[tid] = counts.get(tid, 0) + 1
    return counts


def build_blocked_teams(liga_id, ranking_res, my_user_id, headers):
    blocked_teams = {}
    for user in ranking_res.get("users", []):
        if user.get("id") == my_user_id:
            continue
        try:
            squad = requests.get(
                f"https://api.kickbase.com/v4/leagues/{liga_id}/users/{user.get('id')}/squad",
                headers=headers,
                timeout=CONFIG["REQUEST_TIMEOUT"]
            ).json().get("it", [])
        except Exception as e:
            print(f"Gegner-Kader-Fehler: {e}")
            squad = []
        counts = count_by_team(squad)
        for tid, count in counts.items():
            if count >= CONFIG["MAX_PER_TEAM"]:
                blocked_teams.setdefault(tid, []).append(user.get("name"))
    return blocked_teams


def get_my_team_counts(liga_id, my_user_id, headers):
    try:
        my_squad = requests.get(
            f"https://api.kickbase.com/v4/leagues/{liga_id}/users/{my_user_id}/squad",
            headers=headers,
            timeout=CONFIG["REQUEST_TIMEOUT"]
        ).json().get("it", [])
    except Exception as e:
        print(f"Eigener-Kader-Fehler: {e}")
        my_squad = []
    return count_by_team(my_squad)


def market_updates_until_first_matchday():
    now_utc = datetime.utcnow()
    matchday_start = datetime.combine(CONFIG["FIRST_MATCHDAY"], datetime.min.time())
    if now_utc >= matchday_start:
        return 0
    today_update = now_utc.replace(
        hour=CONFIG["MW_UPDATE_HOUR_UTC"], minute=0, second=0, microsecond=0
    )
    remaining_days = (CONFIG["FIRST_MATCHDAY"] - now_utc.date()).days
    return remaining_days if now_utc < today_update else max(0, remaining_days - 1)


def match_news_for_player(lastname, news_headlines):
    matched = []
    for headline in news_headlines:
        if re.search(r'\b' + re.escape(lastname) + r'\b', headline, re.IGNORECASE):
            matched.append(headline)
    return matched


def evaluate_player(p, p_detail, news_headlines, blocked_teams, my_team_counts, mw_cycles):
    lastname = p.get("n", "Unbekannt")
    mv = p.get("mv", 0)
    trend_flag = p.get("mvt")
    tid = p.get("tid")

    is_injured = False
    st_market = str(p.get("st", "0"))
    st_detail = str(p_detail.get("status", p_detail.get("st", "0")) if p_detail else "0")
    if st_market in ["1", "2", "4", "8"] or st_detail in ["1", "2", "4", "8"]:
        is_injured = True

    matched_news = match_news_for_player(lastname, news_headlines)
    has_negative_news = any(
        re.search(r"(verletzt|muskul|ausfall|gesperrt|rot|kreuzband|bruch|reha|trainingsrückstand)", h, re.IGNORECASE)
        for h in matched_news
    )

    my_count_for_team = my_team_counts.get(tid, 0)
    violates_my_limit = my_count_for_team >= CONFIG["MAX_PER_TEAM"]
    opponents_blocked = tid in blocked_teams

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
        max_bid = int(mv * 1.15)
    elif score >= 3:
        label = "SOLIDE"
        max_bid = int(mv * 1.05)
    else:
        label = "RISKANT"
        max_bid = int(mv * 1.00)

    max_bid = max(max_bid, mv)

    reasons = []
    if trend_flag == 1:
        reasons.append("positiver Marktwert-Trend")
    elif trend_flag == 2:
        reasons.append("fallender Marktwert-Trend")
    if is_injured:
        reasons.append("Verletzungs-/Sperrrisiko (Kickbase)")
    if has_negative_news:
        reasons.append("kritische News bei LigaInsider")
    if mw_cycles > 0:
        reasons.append(f"{mw_cycles} Marktwert-Updates bis 1. Spieltag")
    if violates_my_limit:
        reasons.append("überschreitet 2-Spieler-Regel")
    if opponents_blocked:
        reasons.append("Gegner haben den Verein blockiert")

    return {
        "label": label,
        "reason": ", ".join(reasons) if reasons else "keine besonderen Risiken",
        "max_bid": max_bid,
        "matched_news": matched_news[:3]
    }


def get_llm_analysis(scored_players, cycles_info):
    """
    Ruft Gemini über den 'flash-latest'-Alias auf.
    Gibt None zurück bei Fehlern, damit main() den Rest der Nachricht
    trotzdem verschicken kann.
    """
    if not GEMINI_API_KEY:
        print("Kein GEMINI_API_KEY gesetzt - KI-Analyse wird übersprungen.")
        return None

    players_json = json.dumps(scored_players, ensure_ascii=False, indent=2)
    prompt = f"""Du bist ein Kickbase-Experte. Ziel: Herbstmeister werden.
Noch {cycles_info} Marktwert-Updates bis zum ersten Spieltag.
Regeln: Maximal 2 Spieler pro Verein, nie unter dem aktuellen Marktwert bieten.

Hier sind die aktuellen Marktspieler mit Analyse-Daten:
{players_json}

Gib für die 3-5 spannendsten Spieler eine kurze Empfehlung ab (Kauf oder Risiko).
Antworte DIREKT in klarem Fließtext auf Deutsch.
Verwende KEINE Markdown-Zeichen wie Sternchen, Unterstriche oder Rauten."""

    url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"

    try:
        response = requests.post(
            url,
            params={"key": GEMINI_API_KEY},
            headers={"Content-Type": "application/json"},
            json={"contents": [{"parts": [{"text": prompt}]}]},
            timeout=25
        )

        if response.status_code != 200:
            print(f"Gemini HTTP-Fehler {response.status_code}: {response.text[:300]}")
            return None

        result = response.json()
        candidates = result.get("candidates", [])
        if not candidates:
            print("Gemini-Antwort ohne 'candidates' (evtl. Safety-Block).")
            return None

        return candidates[0]["content"]["parts"][0]["text"]

    except Exception as e:
        print(f"Gemini-Fehler: {e}")
        return None


def send_telegram_message(text):
    """
    Sendet die Nachricht per HTML-Parse-Mode (nur < > & müssen escaped sein,
    das ist deutlich robuster als Markdown/MarkdownV2).
    """
    tg_url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"

    response = requests.post(
        tg_url,
        json={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML"},
        timeout=CONFIG["REQUEST_TIMEOUT"]
    )

    if response.status_code != 200:
        print(f"Telegram HTML-Fehler: {response.text[:300]} - Fallback ohne Formatierung.")
