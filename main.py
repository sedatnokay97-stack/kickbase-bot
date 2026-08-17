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
    # Liga-Regeln / Zeitlogik
    "MAX_PER_TEAM": 2,
    "FIRST_MATCHDAY": date(2026, 8, 28),
    "MW_UPDATE_HOUR_UTC": 20,
    "PLAYERS_TO_SHOW": 15,

    # Technische Einstellungen
    "REQUEST_TIMEOUT": 10,
    "TELEGRAM_MAX_LEN": 3900,

    # KI-Modell (aus deiner Liste)
    "GEMINI_MODEL": "gemini-3.7-flash",
}

# Session für alle HTTP-Requests
session = requests.Session()
session.headers.update({
    "User-Agent": "Kickbase/6.8.0 (iPhone; iOS 16.5; Scale/3.00)",
    "Content-Type": "application/json",
})


def esc(value) -> str:
    """HTML-escaped, sicher fuer Telegram parse_mode=HTML."""
    return html.escape(str(value), quote=False)


# ---------------------------------------------------------------------------
# Datenquellen
# ---------------------------------------------------------------------------

def get_news():
    """LigaInsider-News via RSS. Bei 404 nur Warnung, kein Abbruch."""
    news = []
    try:
        res = requests.get(
            "https://www.ligainsider.de/rss/news.xml",
            timeout=CONFIG["REQUEST_TIMEOUT"],
        )
        res.raise_for_status()
        root = ET.fromstring(res.text)
        for item in root.findall(".//item"):
            title = item.find("title")
            if title is not None and title.text:
                news.append(title.text)
    except Exception as e:
        print(f"Warnung: LigaInsider-News konnten nicht geladen werden: {e}")
    return news


def count_by_team(squad_items):
    """Zählt Spieler pro Team-ID im Kader."""
    counts = {}
    for sp in squad_items:
        tid = sp.get("tid")
        if not tid:
            continue
        counts[tid] = counts.get(tid, 0) + 1
    return counts


def build_blocked_teams(liga_id, ranking_res, my_user_id):
    """
    Gegner-Radar: Für jeden Gegner prüfen, welche Vereine bereits
    mit >= MAX_PER_TEAM Spielern besetzt sind.
    """
    blocked_teams = {}
    for user in ranking_res.get("users", []):
        if user.get("id") == my_user_id:
            continue
        try:
            res = session.get(
                f"https://api.kickbase.com/v4/leagues/{liga_id}/users/{user.get('id')}/squad",
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


def get_my_team_counts(liga_id, my_user_id):
    """Eigenen Kader laden und Spieleranzahl pro Verein zählen."""
    if not my_user_id:
        print("Warnung: my_user_id ist None – eigener Kader kann nicht geladen werden.")
        return {}

    try:
        res = session.get(
            f"https://api.kickbase.com/v4/leagues/{liga_id}/users/{my_user_id}/squad",
            timeout=CONFIG["REQUEST_TIMEOUT"],
        )
        res.raise_for_status()
        my_squad = res.json().get("it", [])
    except Exception as e:
        print(f"Warnung: Eigener Kader konnte nicht geladen werden: {e}")
        my_squad = []
    return count_by_team(my_squad)


def market_updates_until_first_matchday():
    """
    Schätzt, wie viele tägliche Marktwert-Updates (einmal pro Tag um MW_UPDATE_HOUR_UTC)
    bis zum ersten Spieltag noch kommen.
    """
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
    """Findet News-Headlines, die den Spielernamen enthalten."""
    matched = []
    for headline in news_headlines:
        if re.search(r'\b' + re.escape(lastname) + r'\b', headline, re.IGNORECASE):
            matched.append(headline)
    return matched


# ---------------------------------------------------------------------------
# Bewertung
# ---------------------------------------------------------------------------

def evaluate_player(p, p_detail, news_headlines, blocked_teams, my_team_counts, mw_cycles):
    """
    Bewertet einen Marktspieler nach Marktwert, Trend, News, Verletzung und Ligaregeln.
    Gibt ein Dict mit Label, Begründung, max_bid, News und Verletzungsflag zurück.
    """
    lastname = p.get("n", "Unbekannt")
    mv = p.get("mv", 0)
    trend_flag = p.get("mvt")
    tid = p.get("tid")

    # Status-Flags aus Markt- und Detaildaten
    st_market = str(p.get("st", "0"))
    st_detail = str(p_detail.get("status", p_detail.get("st", "0")) if p_detail else "0")
    is_injured = st_market in ["1", "2", "4", "8"] or st_detail in ["1", "2", "4", "8"]

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

    # Score berechnen (80/20-Heuristik)
    score = 0

    # Marktwert ~ grobe Qualitätsskala
    if mv < 2_000_000:
        score += 1
    elif mv < 6_000_000:
        score += 2
    elif mv < 12_000_000:
        score += 3
    else:
        score += 4

    # Trend
    if trend_flag == 1:
        score += 2
    elif trend_flag == 2:
        score -= 1

    # Zeit bis Spieltag
    if mw_cycles >= 15:
        score += 2
    elif mw_cycles >= 7:
        score += 1

    # Risiko
    if is_injured:
        score -= 3
    if has_negative_news:
        score -= 2
    if violates_my_limit:
        score -= 4
    if opponents_blocked:
        score -= 1

    # Label / max_bid
    if score >= 6 and not violates_my_limit and not is_injured and not has_negative_news:
        label = "TOP-KAUF"
        max_bid = int(mv * 1.15)
    elif score >= 3:
        label = "SOLIDE"
        max_bid = int(mv * 1.05)
    else:
        label = "RISKANT"
        max_bid = int(mv * 1.00)

    # Nie unter Marktwert bieten
    max_bid = max(max_bid, mv)

    # Begründungstext
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
        "matched_news": matched_news[:3],
        "is_injured": is_injured,
    }


# ---------------------------------------------------------------------------
# Gemini / LLM
# ---------------------------------------------------------------------------

def get_llm_analysis(scored_players, cycles_info):
    """
    Ruft Gemini über das google-genai SDK auf und gibt einen Text zurück.
    Bei Fehlern kommt ein klarer Hinweis statt eines Absturzes.
    """
    if not GEMINI_API_KEY:
        return "FEHLER: Kein GEMINI_API_KEY gefunden!"

    players_json = json.dumps(scored_players, ensure_ascii=False, indent=2)
    prompt = f"""Du bist ein Kickbase-Experte. Ziel: Herbstmeister werden.
Noch {cycles_info} Marktwert-Updates. Max 2 Spieler per Verein, nie unter Marktwert bieten.

Hier sind die aktuellen Marktspieler mit Analyse-Daten:
{players_json}

Gib fuer die 3-5 spannendsten Spieler eine kurze Empfehlung ab (Kauf oder Risiko).
Antworte in klarem Fliesstext ohne Markdown-Formatierung (keine Sternchen, keine Unterstriche, keine Rauten)."""

    try:
        client = genai.Client(api_key=GEMINI_API_KEY)

        response = client.models.generate_content(
            model=CONFIG["GEMINI_MODEL"],
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=0.4,
                max_output_tokens=700,
            ),
        )

        text = (response.text or "").strip()
        return text if text else "KI hat keine Analyse geliefert."
    except Exception as e:
        # Hier landen auch 503-Fehler bei hoher Auslastung; Script bleibt stabil.[file:69][web:88]
        print(f"Warnung: Gemini-Aufruf fehlgeschlagen: {e}")
        return f"KI-Analyse aktuell nicht verfuegbar ({type(e).__name__})."


# ---------------------------------------------------------------------------
# Telegram
# ---------------------------------------------------------------------------

def send_telegram_messages(full_text):
    """
    Schickt den Text in Blöcken an Telegram, falls er länger als TELEGRAM_MAX_LEN ist.
    """
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
                # Fallback: HTML-Tags entfernen
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
    # Secrets prüfen
    missing = [k for k, v in REQUIRED_SECRETS.items() if not v]
    if missing:
        print(f"Fehler: Folgende Secrets fehlen: {', '.join(missing)}")
        sys.exit(1)

    # Login bei Kickbase
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

    # League-ID und eigene User-ID robust auslesen.[web:40][web:42]
    liga_id = login_res.get("srvl", [])[0].get("id")

    user_obj = login_res.get("u", {}) or {}
    my_user_id = user_obj.get("id") or user_obj.get("i")
    if not my_user_id:
        raise ValueError(f"Keine User-ID im Login-Response gefunden: {user_obj}")

    session.headers["Authorization"] = f"Bearer {token}"

    # Daten holen
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

    blocked_teams = build_blocked_teams(liga_id, ranking_res, my_user_id)
    my_team_counts = get_my_team_counts(liga_id, my_user_id)
    mw_cycles = market_updates_until_first_matchday()

    msg = "⚽ <b>Markt-Update: Kickbase Elite</b>\n\n"
    scored_players = []

    # Markt-Spieler durchgehen
    for p in players[:CONFIG["PLAYERS_TO_SHOW"]]:
        nachname = p.get("n", "Unbekannt")
        mw_str = f"{p.get('mv', 0):,}".replace(",", ".")
        trend = "📈" if p.get("mvt") == 1 else "📉" if p.get("mvt") == 2 else "➖"
        exs = p.get("exs", 0)
        time_str = f"{exs//3600}h {(exs%3600)//60}m" if exs > 0 else "Abgelaufen"

        # Detaildaten / Status
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
            except Exception as e:
                print(f"Warnung: Detaildaten fuer {nachname} nicht geladen: {e}")

        eval_result = evaluate_player(
            p, p_detail, news_headlines, blocked_teams, my_team_counts, mw_cycles
        )
        max_bid_str = f"{eval_result['max_bid']:,}".replace(",", ".")

        msg += f"• <b>{esc(nachname)}</b> {trend} | 💰 {mw_str} € | ⏳ {time_str}\n"
        msg += f"  ✅ <b>Einschätzung:</b> {esc(eval_result['label'])} (Gebot mind./max.: {max_bid_str} €)\n"

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
            "einschaetzung": eval_result["label"],
            "risiken": eval_result["reason"],
        })

    # KI-Analyse anhängen (oder Fehlertext)
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
