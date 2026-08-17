import os
import requests
import xml.etree.ElementTree as ET
import re
import json
from datetime import datetime, date

# Umgebungsvariablen (GitHub Secrets)
KB_EMAIL = os.environ.get("KB_EMAIL")
KB_PASSWORD = os.environ.get("KB_PASSWORD")
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
GEMINI_MODEL = "gemini-1.5-flash"

# Konfiguration deiner Liga-Regeln und Zeitlogik
CONFIG = {
    "MAX_PER_TEAM": 2,                    # eure 2-Spieler-pro-Verein-Regel
    "FIRST_MATCHDAY": date(2026, 8, 28),  # echter 1. Spieltag Bundesliga 2026/27
    "MW_UPDATE_HOUR_UTC": 20,             # Marktwert-Update ca. 22:00 MESZ = 20:00 UTC
    "PLAYERS_TO_SHOW": 15                 # Anzahl Spieler in der Nachricht
}


def get_news():
    """LigaInsider-Headlines aus dem RSS-Feed holen."""
    news = []
    try:
        res = requests.get("https://www.ligainsider.de/rss/news.xml", timeout=5)
        if res.status_code == 200:
            root = ET.fromstring(res.text)
            for item in root.findall(".//item"):
                title = item.find("title")
                if title is not None and title.text:
                    news.append(title.text)
    except:
        pass
    return news


def count_by_team(squad_items):
    """Zählt pro Team-ID, wie viele Spieler im Kader sind."""
    counts = {}
    for sp in squad_items:
        tid = sp.get("tid")
        if not tid:
            continue
        counts[tid] = counts.get(tid, 0) + 1
    return counts


def build_blocked_teams(liga_id, ranking_res, my_user_id, headers):
    """Gegner-Radar: welche Teams sind bei Gegnern schon mit >= MAX_PER_TEAM besetzt."""
    blocked_teams = {}
    for user in ranking_res.get("users", []):
        if user.get("id") == my_user_id:
            continue
        try:
            squad = requests.get(
                f"https://api.kickbase.com/v4/leagues/{liga_id}/users/{user.get('id')}/squad",
                headers=headers
            ).json().get("it", [])
        except:
            squad = []
        counts = count_by_team(squad)
        for tid, count in counts.items():
            if count >= CONFIG["MAX_PER_TEAM"]:
                blocked_teams.setdefault(tid, []).append(user.get("name"))
    return blocked_teams


def get_my_team_counts(liga_id, my_user_id, headers):
    """Deinen eigenen Kader laden und Spieleranzahl pro Verein zählen."""
    try:
        my_squad = requests.get(
            f"https://api.kickbase.com/v4/leagues/{liga_id}/users/{my_user_id}/squad",
            headers=headers
        ).json().get("it", [])
    except:
        my_squad = []
    return count_by_team(my_squad)


def market_updates_until_first_matchday():
    """Zählt reale Marktwert-Update-Zyklen bis zum 1. Spieltag."""
    now_utc = datetime.utcnow()
    matchday_start = datetime.combine(CONFIG["FIRST_MATCHDAY"], datetime.min.time())

    if now_utc >= matchday_start:
        return 0

    today_update = now_utc.replace(
        hour=CONFIG["MW_UPDATE_HOUR_UTC"], minute=0, second=0, microsecond=0
    )
    remaining_days = (CONFIG["FIRST_MATCHDAY"] - now_utc.date()).days

    if now_utc < today_update:
        return remaining_days
    else:
        return max(0, remaining_days - 1)


def match_news_for_player(lastname, news_headlines):
    """Zu einem Spieler passende LigaInsider-News suchen."""
    matched = []
    for headline in news_headlines:
        if re.search(r'\b' + re.escape(lastname) + r'\b', headline, re.IGNORECASE):
            matched.append(headline)
    return matched


def evaluate_player(p, p_detail, news_headlines, blocked_teams, my_team_counts, mw_cycles):
    """Bewertet einen Marktspieler inkl. Verletzungslogik."""
    lastname = p.get("n", "Unbekannt")
    mv = p.get("mv", 0)
    trend_flag = p.get("mvt")
    tid = p.get("tid")

    is_injured = False
    if p_detail:
        # Checkt Status auf "1" (Verletzt) oder "2" (Gesperrt)
        st = str(p_detail.get("status", p_detail.get("st", "")))
        if st in ["1", "2"]:
            is_injured = True

    matched_news = match_news_for_player(lastname, news_headlines)
    has_negative_news = any(
        re.search(r"(verletzt|muskul|ausfall|gesperrt|rot|kreuzband|bruch)", h, re.IGNORECASE)
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
        reasons.append("Verletzungs-/Sperrrisiko")
    if has_negative_news:
        reasons.append("kritische News bei LigaInsider")

    if mw_cycles > 0:
        reasons.append(f"{mw_cycles} Marktwert-Updates bis zum 1. Spieltag")

    if violates_my_limit:
        reasons.append("überschreitet deine 2-Spieler-pro-Verein-Regel")
    if opponents_blocked:
        reasons.append("Gegner haben den Verein bereits blockiert")

    reason_text = ", ".join(reasons) if reasons else "keine besonderen Risiken"

    return {
        "label": label,
        "reason": reason_text,
        "max_bid": max_bid,
        "matched_news": matched_news[:3]
    }


def build_llm_prompt(scored_players, cycles_info):
    """Baut einen kompakten Prompt aus den bereits berechneten Scores."""
    players_json = json.dumps(scored_players, ensure_ascii=False, indent=2)

    prompt = f"""
Du bist ein erfahrener Kickbase-Top-Manager und Analyst.
Ziel des Nutzers: Herbstmeister und Saisonsieger seiner Liga werden.

Liga-Regeln:
- Maximal 2 Spieler pro Verein.
- Im Winter werden alle Spieler außer einem verkauft (Reset-Regel).
- Es dürfen NIEMALS Gebote unter dem aktuellen Marktwert abgegeben werden.

Zeitlicher Kontext: Noch {cycles_info} Marktwert-Updates bis zum ersten Spieltag.

Hier sind die aktuell interessantesten Marktspieler mit ihren Kennzahlen
(bereits vorberechnete Scores, Trends, Risiken, News):

{players_json}

Aufgabe:
Gib für die 3-5 wichtigsten Spieler eine kurze, konkrete Einschätzung
(max. 2 Sätze je Spieler) ab, wer JETZT ein Kauf ist, wer beobachtet werden
sollte und wer trotz gutem Score riskant ist. Schreibe direkt, ohne
Einleitung, auf Deutsch, im Telegram-Markdown-Stil mit Bulletpoints.
"""
    return prompt.strip()


def get_llm_analysis(scored_players, cycles_info):
    """Ruft Gemini auf und fängt Fehler sauber ab, damit der Bot nicht abstürzt."""
    if not GEMINI_API_KEY:
        return "❌ FEHLER: Kein API-Key gefunden! Bitte GitHub Secrets und run.yml prüfen."

    prompt = build_llm_prompt(scored_players, cycles_info)
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"

    try:
        res = requests.post(
            url,
            params={"key": GEMINI_API_KEY},
            json={"contents": [{"parts": [{"text": prompt}]}]},
            timeout=20
        )
        data = res.json()
        
        # Schauen, ob Google gemeckert hat und den exakten Fehler ausgeben
        if "error" in data:
            return f"❌ Google API Fehler: {data['error'].get('message', 'Unbekannter Fehler')}"
            
        return data["candidates"][0]["content"]["parts"][0]["text"]
        
    except Exception as e:
        print(f"LLM-Fehler: {e}")
        return f"❌ Fehler beim Verarbeiten der KI-Daten: {e}"


def main():
    headers = {
        "User-Agent": "Kickbase/6.8.0 (iPhone; iOS 16.5; Scale/3.00)",
        "Content-Type": "application/json"
    }
    payload = {"em": KB_EMAIL, "pass": KB_PASSWORD, "loy": False, "rep": {}}

    try:
        login_res = requests.post(
            "https://api.kickbase.com/v4/user/login",
            json=payload,
            headers=headers
        ).json()

        token = login_res.get("tkn")
        liga_id = login_res.get("srvl", [])[0].get("id")
        my_user_id = login_res.get("u", {}).get("i")

        headers["Authorization"] = f"Bearer {token}"

        news_headlines = get_news()

        ranking_res = requests.get(
            f"https://api.kickbase.com/v4/leagues/{liga_id}/ranking",
            headers=headers
        ).json()

        market_res = requests.get(
            f"https://api.kickbase.com/v4/leagues/{liga_id}/market",
            headers=headers
        ).json()

        players = sorted(market_res.get("it", []), key=lambda x: x.get("exs", 999999))

        blocked_teams = build_blocked_teams(liga_id, ranking_res, my_user_id, headers)
        my_team_counts = get_my_team_counts(liga_id, my_user_id, headers)
        mw_cycles = market_updates_until_first_matchday()

        msg = "⚽ *Markt-Update: Kickbase Elite*\n\n"
        scored_players = []

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
                    p_detail = requests.get(
                        f"https://api.kickbase.com/v4/leagues/{liga_id}/players/{player_id}",
                        headers=headers
                    ).json()
                except:
                    p_detail = {}

            eval_result = evaluate_player(
                p, p_detail, news_headlines, blocked_teams, my_team_counts, mw_cycles
            )

            msg += f"• *{nachname}* {trend} | 💰 {mw_str} € | ⏳ {time_str}\n"

            max_bid_str = f"{eval_result['max_bid']:,}".replace(",", ".")
            msg += f"  ✅ *Einschätzung:* {eval_result['label']} (Gebot mind./max.: {max_bid_str} €)\n"

            if eval_result["reason"]:
                msg += f"  ℹ️ *Begründung:* {eval_result['reason']}\n"

            st = str(p_detail.get("status", p_detail.get("st", "")))
            if st in ["1", "2"]:
                msg += "  🩹 *Status: Verletzt/Gesperrt*\n"

            if p.get("tid") in blocked_teams:
                msg += f"  🚫 *Sperre (Gegner):* {', '.join(blocked_teams[p.get('tid')])}\n"

            for h in eval_result["matched_news"]:
                msg += f"  📰 *News:* {h}\n"

            msg += "\n"

            scored_players.append({
                "name": nachname,
                "marktwert": p.get("mv", 0),
                "trend": "steigend" if p.get("mvt") == 1 else "fallend" if p.get("mvt") == 2 else "neutral",
                "einschaetzung": eval_result["label"],
                "max_gebot": eval_result["max_bid"],
                "begruendung": eval_result["reason"],
                "news": eval_result["matched_news"]
            })

        # KI-Aufruf mit Error-Handling
        llm_text = get_llm_analysis(scored_players, mw_cycles)
        if llm_text:
            msg += "\n🤖 *KI-Manager-Einschätzung:*\n" + llm_text

        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "Markdown"}
        )

    except Exception as e:
        print(f"Fehler: {e}")


if __name__ == "__main__":
    main()
