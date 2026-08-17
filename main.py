import os
import sys
import re
import json
import html
import traceback
from datetime import datetime, timezone, date
import xml.etree.ElementTree as ET

import requests
from google import genai
from google.genai import types
from bs4 import BeautifulSoup

# ---------------------------------------------------------------------------
# Konfiguration
# ---------------------------------------------------------------------------

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
    "MW_UPDATE_HOUR_UTC": 20,
    "PLAYERS_TO_SHOW": 15,
    "REQUEST_TIMEOUT": 10,
    "TELEGRAM_MAX_LEN": 3900,
    "GEMINI_MODEL": "gemini-3.7-flash",
    "DEBUG_PLAYER_DETAIL": True,  # einmalig Rohdaten des ersten Spielers loggen
}

NEWS_FEEDS = [
    "https://newsfeed.kicker.de/news/bundesliga",
    "https://feeds.t-online.de/rss/transfermarkt",
    "https://www.sportschau.de/fussball/bundesliga/index~rss2.xml",
]

LIGAINSIDER_INJURY_URL = "https://www.ligainsider.de/bundesliga/verletzte-und-gesperrte-spieler/"

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


# ---------------------------------------------------------------------------
# News-Datenquellen
# ---------------------------------------------------------------------------

def get_ligainsider_injury_news():
    headlines = []
    skip_labels = {"spieler", "grund", "letzte news zu diesem status", "fehlt seit:", "fehlt seit"}

    try:
        res = requests.get(
            LIGAINSIDER_INJURY_URL,
            headers=NEWS_HEADERS,
            timeout=CONFIG["REQUEST_TIMEOUT"],
        )
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


# ---------------------------------------------------------------------------
# Kickbase-Kader / Liga
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Zeitlogik & News-Matching
# ---------------------------------------------------------------------------

def market_updates_until_first_matchday():
    now_utc = datetime.now(timezone.utc)
    matchday_start = datetime.combine(
        CONFIG["FIRST_MATCHDAY"], datetime.min.time(), tzinfo=timezone.utc
    )
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


# ---------------------------------------------------------------------------
# Bewertung
# ---------------------------------------------------------------------------

def extract_points(p_detail):
    """
    Liest Gesamtpunkte/Ø-Punkte robust aus, da der Feldname je nach
    Kickbase-API-Version variieren kann (p/ap, tp/avp, points/averagePoints...).
    """
    if not p_detail:
        return 0, 0

    total_candidates = ["p", "tp", "points", "totalPoints", "sp"]
    avg_candidates = ["ap", "avp", "avgPoints", "averagePoints", "sap"]

    total_points = next((p_detail[k] for k in total_candidates if k in p_detail and p_detail[k] is not None), 0)
    avg_points = next((p_detail[k] for k in avg_candidates if k in p_detail and p_detail[k] is not None), 0)

    return total_points, avg_points


def evaluate_player(p, p_detail, news_headlines, blocked_teams, my_team_counts, mw_cycles):
    lastname = p.get("n", "Unbekannt")
    mv = p.get("mv", 0)
    trend_flag = p.get("mvt")
    tid = p.get("tid")

    st_market = str(p.get("st", "0"))
    st_detail = str(p_detail.get("status", p_detail.get("st", "0")) if p_detail else "0")
    is_injured = st_market in ["1", "2", "4", "8"] or st_detail in ["1", "2", "4", "8"]

    total_points, avg_points = extract_points(p_detail)

    matched_news = match_news_for_player(lastname, news_headlines)
    has_negative_news = any(
        re.search(
            r"(verletzt|muskul|ausfall|gesperrt|rot|kreuzband|bruch|reha|trainingsrückstand)",
            h,
            re.IGNORECASE,
        )
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

    if avg_points >= 100:
        score += 3
    elif avg_points >= 60:
        score += 2
    elif avg_points >= 30:
        score += 1
    elif 0 < avg_points < 15:
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
    if avg_points >= 60:
        reasons.append(f"starke Punkteform (Ø {avg_points})")
    elif 0 < avg_points < 15:
        reasons.append(f"schwache Punkteform (Ø {avg_points})")
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

    return {
        "label": label,
        "reason": ", ".join(reasons) if reasons else "keine besonderen Risiken",
        "max_bid": max_bid,
        "matched_news": matched_news[:3],
        "is_injured": is_injured,
        "total_points": total_points,
        "avg_points": avg_points,
    }


# ---------------------------------------------------------------------------
# Gemini / LLM
# ---------------------------------------------------------------------------

def get_llm_analysis(scored_players, cycles_info):
    if not GEMINI_API_KEY:
        return "FEHLER: Kein GEMINI_API_KEY gefunden!"

    players_json = json.dumps(scored_players, ensure_ascii=False, indent=2)
    prompt = f"""Du bist ein Kickbase-Experte. Ziel: Herbstmeister werden.
Noch {cycles_info} Marktwert-Updates. Max 2 Spieler per Verein, nie unter Marktwert bieten.

Hier sind die aktuellen Marktspieler mit Analyse-Daten (inkl. Gesamtpunkte und Ø-Punkte):
{players_json}

Gib fuer die 3-5 spannendsten Spieler eine kurze Empfehlung ab (Kauf oder Risiko).
Beruecksichtige dabei sowohl Marktwert-Potenzial als auch die Punkteform.
Antworte in klarem Fliesstext ohne Markdown-Formatierung (keine Sternchen, keine Unterstriche, keine Rauten)."""

    try:
        client = genai.Client(api_key=GEMINI_API_KEY)

        response = client.models.generate_content(
            model=CONFIG["GEMINI_MODEL"],
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=0.4,
                max_output_tokens=700,
                automatic_function_calling=types.AutomaticFunctionCallingConfig(
                    disable=True
                ),
            ),
        )

        text = (response.text or "").strip()
        return text if text else "KI hat keine Analyse geliefert."
    except Exception as e:
        print(f"Warnung: Gemini-Aufruf fehlgeschlagen: {e}")
        return f"KI-Analyse aktuell nicht verfuegbar ({type(e).__name__})."


# ---------------------------------------------------------------------------
# Telegram
# ---------------------------------------------------------------------------

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
                requests.post(
                    tg_url,
                    json={"chat_id": TELEGRAM_CHAT_ID, "text": clean},
                    timeout=CONFIG["REQUEST_TIMEOUT"],
                )
        except Exception as e:
            print(f"Fehler beim Telegram-Versand: {e}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    missing = [k for k, v in REQUIRED_SECRETS.items() if not v]
    if missing:
        print(f"Fehler: Folgende Secrets fehlen: {', '.join(missing)}")
        sys.exit(1)

    payload = {"em": KB_EMAIL, "pass": KB_PASSWORD, "loy": False, "rep": {}}
    login_res_raw = session.post(
        "https://api.kickbase.com/v4/user/login",
        json=payload,
        timeout=CONFIG["REQUEST_TIMEOUT"],
    )
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

    ranking_res = session.get(
        f"https://api.kickbase.com/v4/leagues/{liga_id}/ranking",
        timeout=CONFIG["REQUEST_TIMEOUT"],
    ).json()

    market_res = session.get(
        f"https://api.kickbase.com/v4/leagues/{liga_id}/market",
        timeout=CONFIG["REQUEST_TIMEOUT"],
    ).json()

    players = sorted(market_res.get("it", []), key=lambda x: x.get("exs", 999999))

    blocked_teams = build_blocked_teams(liga_id, ranking_res, my_manager_id)
    my_team_counts = get_my_team_counts(liga_id, my_manager_id)
    mw_cycles = market_updates_until_first_matchday()

    msg = "⚽ <b>Markt-Update: Kickbase Elite</b>\n\n"
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
                res = session.get(
                    f"https://api.kickbase.com/v4/leagues/{liga_id}/players/{player_id}",
                    timeout=CONFIG["REQUEST_TIMEOUT"],
                )
                res.raise_for_status()
                p_detail = res.json()

                if CONFIG["DEBUG_PLAYER_DETAIL"] and not debug_done:
                    print("DEBUG P_DETAIL (erster Spieler):",
                          json.dumps(p_detail, indent=2, ensure_ascii=False))
                    debug_done = True

            except Exception as e:
                print(f"Warnung: Detaildaten fuer {nachname} nicht geladen: {e}")

        eval_result = evaluate_player(
            p, p_detail, news_headlines, blocked_teams, my_team_counts, mw_cycles
        )
        max_bid_str = f"{eval_result['max_bid']:,}".replace(",", ".")

        msg += f"• <b>{esc(nachname)}</b> {trend} | 💰 {mw_str} € | ⏳ {time_str}\n"
        msg += f"  ✅ <b>Einschätzung:</b> {esc(eval_result['label'])} (Gebot mind./max.: {max_bid_str} €)\n"
        msg += f"  📊 <b>Punkte:</b> {eval_result['total_points']} (Ø {eval_result['avg_points']})\n"

        if eval_result["reason"]:
            msg += f"  ℹ️ <b>Begründung:</b> {esc(eval_result['reason'])}\n"

        if eval_result["is_injured"]:
            msg += "  🩹 <b>Status: Verletzt / Reha / Gesperrt</b>\n"

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
