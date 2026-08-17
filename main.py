import os
import sys
import re
import json
import time
import requests
from bs4 import BeautifulSoup
from google import genai
from google.genai import types

# ==========================================
# CONFIG & ENVIRONMENT SECRETS
# ==========================================
KB_EMAIL = os.environ.get("KB_EMAIL")
KB_PASSWORD = os.environ.get("KB_PASSWORD")
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

CONFIG = {
    "DRY_RUN": False,
    "TELEGRAM_MAX_LEN": 4000,
    "REQUEST_TIMEOUT": 15,
    "DAYS_UNTIL_MATCHDAY": 11,
    "OVERPAY_RISK_FACTOR": 0.60,
    "HISTORY_FILE": "history.json"
}

# ==========================================
# KNOTEN 1: KICKBASE LIVE API (v4)
# ==========================================
def kickbase_login():
    """ Loggt den Bot bei Kickbase v4 ein und liefert Token + Liga-ID. """
    if not KB_EMAIL or not KB_PASSWORD:
        raise ValueError("❌ KB_EMAIL oder KB_PASSWORD fehlt in den Repository Secrets!")
    
    url = "https://api.kickbase.com/v4/user/login"
    payload = {"email": KB_EMAIL.strip(), "password": KB_PASSWORD.strip()}
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }
    
    resp = requests.post(url, json=payload, headers=headers, timeout=CONFIG["REQUEST_TIMEOUT"])
    if resp.status_code == 401:
        raise ValueError("❌ Login fehlgeschlagen (401 Unauthorized): Zugangsdaten prüfen!")
    resp.raise_for_status()
    
    data = resp.json()
    token = data.get("token")
    leagues = data.get("leagues", [])
    
    if not token or not leagues:
        raise ValueError("❌ Login-Antwort unvollständig (kein Token/keine Liga).")
    
    league_id = leagues[0].get("id")
    print(f"✅ Kickbase Login erfolgreich. Liga-ID: {league_id}")
    return token, league_id

def get_market_players(token, league_id):
    """ Holt alle aktuellen Transfermarkt-Spieler der Liga. """
    url = f"https://api.kickbase.com/v4/leagues/{league_id}/market"
    headers = {"Authorization": f"Bearer {token}"}
    
    resp = requests.get(url, headers=headers, timeout=CONFIG["REQUEST_TIMEOUT"])
    resp.raise_for_status()
    data = resp.json()
    return data.get("players", [])

# ==========================================
# KNOTEN 2: LIGAINSIDER SCRAPING
# ==========================================
def get_ligainsider_lineups():
    """ Scrapt voraussichtliche Startelf-Spieler von LigaInsider. """
    url = "https://www.ligainsider.de/bundesliga/aufstellung/"
    starters = set()
    try:
        resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=CONFIG["REQUEST_TIMEOUT"])
        if resp.status_code == 200:
            soup = BeautifulSoup(resp.text, "html.parser")
            for link in soup.find_all("a", href=True):
                if "/spieler/" in link["href"]:
                    name = link.text.strip()
                    if name:
                        starters.add(name.lower())
    except Exception as e:
        print(f"⚠️ LigaInsider Scraping-Hinweis: {e}")
    return starters

def get_ligainsider_injuries():
    """ Scrapt die Verletztenliste von LigaInsider. """
    url = "https://www.ligainsider.de/verletzungen-sperren/"
    injured = set()
    try:
        resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=CONFIG["REQUEST_TIMEOUT"])
        if resp.status_code == 200:
            soup = BeautifulSoup(resp.text, "html.parser")
            for tag in soup.find_all(["span", "td", "a"]):
                text = tag.text.strip()
                if len(text) > 3 and len(text) < 30:
                    injured.add(text.lower())
    except Exception as e:
        print(f"⚠️ Verletzungs-Scraping-Hinweis: {e}")
    return injured

# ==========================================
# KNOTEN 3: DATA CONSOLIDATION & PROGNOSE ENGINE
# ==========================================
def load_history():
    if os.path.exists(CONFIG["HISTORY_FILE"]):
        try:
            with open(CONFIG["HISTORY_FILE"], "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def save_history(history):
    try:
        with open(CONFIG["HISTORY_FILE"], "w", encoding="utf-8") as f:
            json.dump(history, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"⚠️ History Speicherung fehlgeschlagen: {e}")

def calculate_real_daily_trend(player_id, current_mv, history):
    """ Berechnet den echten Euro-Trend anhand des Vortags-MW. """
    player_id_str = str(player_id)
    player_hist = history.get(player_id_str, {})
    previous_mv = player_hist.get("mv")
    
    if previous_mv is not None and previous_mv > 0:
        daily_trend = current_mv - previous_mv
    else:
        daily_trend = 150000 if current_mv > 5000000 else 50000

    history[player_id_str] = {"mv": current_mv}
    return daily_trend

def predict_mv_and_overpay(current_mv, daily_trend):
    """ S-Kurven-Dämpfung für den Marktwertgewinn bis Spieltag 1. """
    if daily_trend <= 0:
        return current_mv, int(current_mv * 1.02), 0

    damping = 0.96
    projected_growth = 0
    current_daily = daily_trend

    for _ in range(CONFIG["DAYS_UNTIL_MATCHDAY"]):
        projected_growth += current_daily
        current_daily *= damping

    projected_mv = int(current_mv + projected_growth)
    max_bid = current_mv + int(projected_growth * CONFIG["OVERPAY_RISK_FACTOR"])
    return projected_mv, max_bid, int(projected_growth)

def evaluate_player(player, history, starters, injured_list):
    player_id = player.get("id")
    name = player.get("n", player.get("lastName", "Unbekannt"))
    mv = int(player.get("mv", 0))
    
    daily_trend = calculate_real_daily_trend(player_id, mv, history)
    proj_mv, max_bid, raw_profit = predict_mv_and_overpay(mv, daily_trend)
    
    name_lower = name.lower()
    is_starter = any(s in name_lower or name_lower in s for s in starters)
    is_injured = any(i in name_lower or name_lower in i for i in injured_list)
    
    recommendation = "ABWARTEN"
    if raw_profit > 1_500_000 and not is_injured:
        recommendation = "KAUF JETZT (High Profit)"
    elif daily_trend > 0:
        recommendation = "BEOBACHTEN"
    if is_injured:
        recommendation = "RISIKO (Verletzt/Angeschlagen)"

    return {
        "id": player_id,
        "name": name,
        "mv": mv,
        "proj_mv": proj_mv,
        "max_bid": max_bid,
        "exp_profit": raw_profit,
        "is_starter": is_starter,
        "is_injured": is_injured,
        "recommendation": recommendation
    }

# ==========================================
# KNOTEN 4: GEMINI LLM ANALYSIS
# ==========================================
def call_gemini(prompt):
    if not GEMINI_API_KEY:
        return "KI-Analyse übersprungen: Kein Gemini API-Key."
    try:
        client = genai.Client(api_key=GEMINI_API_KEY)
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=0.3,
                max_output_tokens=300
            )
        )
        return response.text.strip() if response.text else "Keine KI-Antwort erhalten."
    except Exception as exc:
        print(f"⚠️ Gemini API-Hinweis: {exc}")
        return "KI-Analyse derzeit nicht verfügbar."

# ==========================================
# KNOTEN 5: TELEGRAM DISPATCHER & SPLITTER
# ==========================================
def split_telegram_message(message, max_len):
    chunks = []
    current = ""
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
    return [chunk for chunk in chunks if chunk.strip()]

def send_telegram_messages(message):
    if CONFIG["DRY_RUN"]:
        print("DRY_RUN aktiv – Telegram-Versand übersprungen.")
        print(message)
        return

    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        raise ValueError("❌ TELEGRAM_TOKEN oder TELEGRAM_CHAT_ID fehlt in den Secrets!")

    chunks = split_telegram_message(message, CONFIG["TELEGRAM_MAX_LEN"])
    print(f"DEBUG Telegram: Sende {len(chunks)} Teil(e)...")

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    for index, chunk in enumerate(chunks, start=1):
        response = requests.post(
            url,
            json={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": chunk,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
            timeout=CONFIG["REQUEST_TIMEOUT"],
        )
        print(f"DEBUG Telegram HTML {index}/{len(chunks)} ({len(chunk)} Zeichen): HTTP {response.status_code}")

        if response.ok:
            time.sleep(0.25)
            continue

        plain = re.sub(r"<[^>]+>", "", chunk)
        fallback = requests.post(
            url,
            json={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": plain,
                "disable_web_page_preview": True,
            },
            timeout=CONFIG["REQUEST_TIMEOUT"],
        )
        print(f"DEBUG Telegram Plaintext Fallback {index}/{len(chunks)}: HTTP {fallback.status_code}")
        fallback.raise_for_status()
        time.sleep(0.25)

# ==========================================
# HAUPTABLAUF (PIPELINE EXECUTION)
# ==========================================
def main():
    print("🚀 Starte Kickbase Bot Engine...")
    
    token, league_id = kickbase_login()
    raw_players = get_market_players(token, league_id)
    print(f"📊 {len(raw_players)} Spieler auf dem Transfermarkt gefunden.")
    
    starters = get_ligainsider_lineups()
    injured = get_ligainsider_injuries()
    history = load_history()
    
    evaluated_list = []
    report_lines = [f"⚽ <b>Kickbase Markt-Report ({CONFIG['DAYS_UNTIL_MATCHDAY']} Tage bis Spieltag 1)</b>\n"]
    
    for p in raw_players:
        res = evaluate_player(p, history, starters, injured)
        evaluated_list.append(res)
        
        profit_formatted = f"+{res['exp_profit']:,}".replace(",", ".")
        mv_formatted = f"{res['mv']:,}".replace(",", ".")
        proj_formatted = f"{res['proj_mv']:,}".replace(",", ".")
        bid_formatted = f"{res['max_bid']:,}".replace(",", ".")
        
        status_icons = "✅ S11" if res["is_starter"] else "❓ Rotation"
        if res["is_injured"]:
            status_icons += " | 🚑 Verletzt"
            
        report_lines.append(
            f"• <b>{res['name']}</b> | MW: {mv_formatted} €\n"
            f"  📈 Proj. MW (MD1): {proj_formatted} € ({profit_formatted} €)\n"
            f"  🎯 Max. Gebot: <b>{bid_formatted} €</b> ({status_icons})\n"
            f"  💡 Empfehlung: <b>{res['recommendation']}</b>\n"
        )
    
    save_history(history)
    
    top_targets = [p for p in evaluated_list if p["exp_profit"] > 0][:5]
    prompt = (
        f"Du bist ein Kickbase-Experte. Gib ein knallhartes Fazit (max 3 deutsche Sätze) "
        f"zu diesen Markt-Chancen:\n{json.dumps(top_targets)}"
    )
    llm_summary = call_gemini(prompt)
    
    report_lines.append(f"\n🤖 <b>KI-Manager Fazit:</b>\n{llm_summary}")
    full_report = "\n".join(report_lines)
    
    send_telegram_messages(full_report)
    print("✅ Pipeline-Durchlauf vollkommen erfolgreich beendet!")

if __name__ == "__main__":
    main()
