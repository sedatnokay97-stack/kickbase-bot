import os
import requests
import xml.etree.ElementTree as ET
import re
from datetime import datetime, date, timedelta

# Umgebungsvariablen wie bisher in GitHub Actions setzen
KB_EMAIL = os.environ.get("KB_EMAIL")
KB_PASSWORD = os.environ.get("KB_PASSWORD")
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

# Konfiguration deiner Liga-Regeln und Zeitlogik
CONFIG = {
    "MAX_PER_TEAM": 2,                  # eure 2-Spieler-pro-Verein-Regel
    "FIRST_MATCHDAY": date(2026, 8, 23),  # hier Datum 1. Spieltag anpassen
    "MARKET_CYCLE_HOURS": 8,           # Intervall der Marktzyklen = 8 Stunden
    "WINTER_RESET": True               # für spätere Logik (Reset im Winter)
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
    """
    Gegner-Radar: für jeden Gegner schauen, welche Teams bereits mit
    >= MAX_PER_TEAM Spielern besetzt sind.
    """
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

def cycles_until_first_matchday():
    """Berechnet, wie viele Marktzyklen (8h) bis zum ersten Spieltag übrig sind."""
    now = datetime.utcnow().date()
    if CONFIG["FIRST_MATCHDAY"] <= now:
        return 0
    delta_days = (CONFIG["FIRST_MATCHDAY"] - now).days
    total_hours = delta_days * 24
    return max(0, total_hours // CONFIG["MARKET_CYCLE_HOURS"])

def match_news_for_player(lastname, news_headlines):
    """Zu einem Spieler passende LigaInsider-News suchen."""
    matched = []
    for headline in news_headlines:
        if re.search(r'\b' + re.escape(lastname) + r'\b', headline, re.IGNORECASE):
            matched.append(headline)
    return matched

def evaluate_player(p, p_detail, news_headlines, blocked_teams, my_team_counts):
    """
    Bewertet einen Marktspieler und gibt:
    - label: 'TOP-KAUF' / 'SOLIDE' / 'RISKANT'
    - reason: kurze Text-Begründung
    - max_bid: empfohlene Gebotsobergrenze (int)
    - matched_news: Liste relevanter News-Headlines
    """
    lastname = p.get("n", "Unbekannt")
    mv = p.get("mv", 0)               # aktueller Marktwert
    trend_flag = p.get("mvt")         # 1 = hoch, 2 = runter, sonst neutral
    exs = p.get("exs", 0)             # Restlaufzeit auf dem Markt in Sekunden
    tid = p.get("tid")
    player_id = p.get("id")

    # Verletzungs-/Sperrstatus aus dem Detail-Endpoint
    is_injured = False
    if p_detail:
        if p_detail.get("status") == 2 or p_detail.get("st") == 2:
            is_injured = True

    # News-Matching & negative Begriffe
    matched_news = match_news_for_player(lastname, news_headlines)
    has_negative_news = any(
        re.search(r"(verletzt|muskul|ausfall|gesperrt|rot|kreuzband|bruch)", h, re.IGNORECASE)
        for h in matched_news
    )

    # Team-Limits (dein eigener Kader)
    my_count_for_team = my_team_counts.get(tid, 0)
    violates_my_limit = my_count_for_team >= CONFIG["MAX_PER_TEAM"]

    # Gegner-Blockaden
    opponents_blocked = tid in blocked_teams

    # Zeitlicher Hebel bis zum ersten Spieltag
    cycles = cycles_until_first_matchday()

    # Grober Score (80/20-Ansatz)
    score = 0

    # Marktwert: rudimentäre Qualitätsskala
    if mv < 2_000_000:
        score += 1
    elif mv < 6_000_000:
        score += 2
    elif mv < 12_000_000:
        score += 3
    else:
        score += 4

    # Marktwert-Trend
    if trend_flag == 1:
        score += 2   # steigend
    elif trend_flag == 2:
        score -= 1   # fallend

    # Zeit bis Spieltag (mehr Zyklen = mehr MW-Flip-Potenzial)
    if cycles >= 8:
        score += 2
    elif cycles >= 4:
        score += 1

    # Risiko-Abzüge
    if is_injured:
        score -= 3
    if has_negative_news:
        score -= 2

    # Team-Limit & Gegner-Blockaden
    if violates_my_limit:
        score -= 4   # praktisch nicht kaufbar nach Liga-Regel
    if opponents_blocked:
        score -= 1   # Gegner haben Club schon „voll“

    # Label & max_bid bestimmen
    if score >= 6 and not violates_my_limit and not is_injured and not has_negative_news:
        label = "TOP-KAUF"
        max_bid = int(mv * 1.15)  # leicht über MW gehen
    elif score >= 3:
        label = "SOLIDE"
        max_bid = int(mv * 1.05)
    else:
        label = "RISKANT"
        max_bid = int(mv * 0.95)

    # Begründungstext bauen
    reasons = []
    if trend_flag == 1:
        reasons.append("positiver Marktwert-Trend")
    elif trend_flag == 2:
        reasons.append("fallender Marktwert-Trend")

    if is_injured:
        reasons.append("Verletzungs-/Sperrrisiko")
    if has_negative_news:
        reasons.append("kritische News bei LigaInsider")

    if cycles > 0:
        reasons.append(f"{cycles} Marktzyklen bis zum 1. Spieltag")

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

def main():
    # Login bei Kickbase
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

        # News & Liga-Daten holen
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

        # Gegner-Radar & eigener Kader
        blocked_teams = build_blocked_teams(liga_id, ranking_res, my_user_id, headers)
        my_team_counts = get_my_team_counts(liga_id, my_user_id, headers)

        # Nachricht bauen
        msg = "⚽ *Markt-Update: Kickbase Elite*\n\n"

        for p in players[:7]:
            nachname = p.get("n", "Unbekannt")
            mw_str = f"{p.get('mv', 0):,}".replace(",", ".")
            trend = "📈" if p.get("mvt") == 1 else "📉" if p.get("mvt") == 2 else "➖"
            exs = p.get("exs", 0)
            time_str = f"{exs//3600}h {(exs%3600)//60}m" if exs > 0 else "Abgelaufen"

            # Detail-Endpoint für Status/Fitness
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
                p, p_detail, news_headlines, blocked_teams, my_team_counts
            )

            msg += f"• *{nachname}* {trend} | 💰 {mw_str} € | ⏳ {time_str}\n"

            max_bid_str = f"{eval_result['max_bid']:,}".replace(",", ".")
            msg += f"  ✅ *Einschätzung:* {eval_result['label']} (max. Gebot: {max_bid_str} €)\n"

            if eval_result["reason"]:
                msg += f"  ℹ️ *Begründung:* {eval_result['reason']}\n"

            # Verletzungsstatus explizit hervorheben
            if p_detail.get("status") == 2 or p_detail.get("st") == 2:
                msg += "  🩹 *Status: Verletzt/Gesperrt*\n"

            # Gegner-Blockaden
            if p.get("tid") in blocked_teams:
                msg += f"  🚫 *Sperre (Gegner):* {', '.join(blocked_teams[p.get('tid')])}\n"

            # News zum Spieler
            for h in eval_result["matched_news"]:
                msg += f"  📰 *News:* {h}\n"

            msg += "\n"

        # Nachricht an Telegram schicken
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "Markdown"}
        )

    except Exception as e:
        print(f"Fehler: {e}")

if __name__ == "__main__":
    main()
