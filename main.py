import os
import sys
import re
import json
import time
import requests
from google import genai
from google.genai import types

# ==========================================
# CONFIG & SECRETS
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
# 1. KICKBASE API LIVE-DATA ENGINE
# ==========================================
def kickbase_login():
    """ Authentifiziert den Bot bei Kickbase v4 und gibt Token sowie Liga-ID zurück. """
    if not KB_EMAIL or not KB_PASSWORD:
        raise ValueError("KB_EMAIL oder KB_PASSWORD fehlt in den Environment Variables.")
    
    url = "https://api.kickbase.com/v4/user/login"
    payload = {"email": KB_EMAIL, "password": KB_PASSWORD}
    
    resp = requests.post(url, json=payload, timeout=CONFIG["REQUEST_TIMEOUT"])
    resp.raise_for_status()
    data = resp.json()
    
    token = data.get("token")
    leagues = data.get("leagues", [])
    
    if not token or not leagues:
        raise ValueError("Login erfolgreich, aber kein Token oder keine Liga gefunden.")
    
    # Nutzt die erste aktive Liga
    league_id = leagues[0].get("id")
    print(f"✅ Kickbase Login erfolgreich. Liga-ID: {league_id}")
    return token, league_id

def get_market_players(token, league_id):
    """ Ruft alle aktuellen Spieler auf dem Transfermarkt ab. """
    url = f"https://api.kickbase.com/v4/leagues/{league_id}/market"
    headers = {"Authorization": f"Bearer {token}"}
    
    resp = requests.get(url, headers=headers, timeout=CONFIG["REQUEST_TIMEOUT"])
    resp.raise_for_status()
    data = resp.json()
    
    return data.get("players", [])

# ==========================================
# 2. HISTORY & REAL TREND CALCULATION
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
        print(f"⚠️ Warnung beim Speichern der History: {e}")

def calculate_real_daily_trend(player_id, current_mv, history):
    """ Berechnet den echten Euro-Trend anhand der Historie statt der statischen mvt-Status-Code ID. """
    player_history = history.get(str(player_id), {})
    previous_mv = player_history.get("mv")
    
    if previous_mv is not None and previous_mv > 0:
        daily_trend = current_mv - previous_mv
    else:
        # Fallback falls neu auf dem Markt: Schätzwert basierend auf mvt
        daily_trend = 150000 if player_history.get("mvt") == 1 else 0

    # History aktualisieren
    history[str(player_id)] = {"mv": current_mv, "mvt": player_history.get("mvt", 1)}
    return daily_trend

# ==========================================
# 3. PROGNOSE & QUOTEN ENGINE
# ==========================================
def get_matchup_factor(win_odds=None):
    if not win_odds or win_odds <= 1.0:
        return 1.0
    return round(0.8 + ((1.0 / win_odds) * 0.6), 2)

def predict_mv_and_overpay(current_mv, daily_trend, days_until_matchday=11, risk_factor=0.60):
    if daily_trend <= 0 or days_until_matchday <= 0:
        return current_mv, int(current_mv * 1.03), 0

    damping_factor = 0.96
    projected_growth = 0
    current_daily = daily_trend

    for _ in range(days_until_matchday):
        projected_growth += current_daily
        current_daily *= damping_factor

    projected_mv = int(current_mv + projected_growth)
    max_overpay = int(projected_growth * risk_factor)
    max_bid = current_mv + max_overpay

    return projected_mv, max_bid, int(projected_growth)

def evaluate_player(player, history):
    player_id = player.get("id")
    name = player.get("n", player.get("lastName", "Unbekannt"))
    mv = int(player.get("mv", 0))
    win_odds = player.get("winOdds", None)
    
    daily_trend = calculate_real_daily_trend(player_id, mv, history)
    matchup_mult = get_matchup_factor(win_odds)

    proj_mv, max_bid, raw_profit = predict_mv_and_overpay(
        current_mv=mv,
        daily_trend=daily_trend,
        days_until_matchday=CONFIG["DAYS_UNTIL_MATCHDAY"],
        risk_factor=CONFIG["OVERPAY_RISK_FACTOR"]
    )
    
    # Quoten-bereinigter Profit entscheidet über die Empfehlung
    adjusted_profit = int(raw_profit * matchup_mult)

    recommendation = "ABWARTEN"
    if adjusted_profit > 1_500_000:
        recommendation = "KAUF JETZT (High Profit)"
    elif daily_trend > 0:
        recommendation = "BEOBACHTEN"

    return {
        "id": player_id,
        "name": name,
        "mv": mv,
        "proj_mv": proj_mv,
        "max_bid": max_bid,
        "exp_profit": raw_profit,
        "adjusted_profit": adjusted_profit,
        "matchup_mult": matchup_mult,
        "recommendation": recommendation
    }

# ==========================================
# 4. GEMINI ABSICHERUNG
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
                max_output_tokens=300,
            ),
        )
        return response.text.strip() if response.text else "KI-Analyse lieferte keine Antwort."
    except Exception as exc:
        print(f"Warnung: Gemini nicht verfügbar: {exc}")
        return "KI-Analyse derzeit nicht verfügbar; Empfehlungen basieren rein auf der MW-Prognose."

# ==========================================
# 5. KRITISCHER TELEGRAM-FIX (SPLITTING)
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
        print("DRY_RUN aktiv – Nachricht wird nur im Terminal gedruckt:")
        print(message)
        return

    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        raise ValueError("TELEGRAM_TOKEN oder TELEGRAM_CHAT_ID fehlt.")

    chunks = split_telegram_message(message, CONFIG["TELEGRAM_MAX_LEN"])
    print(f"DEBUG Telegram: Sende {len(chunks)} Teil(e), max. {CONFIG['TELEGRAM_MAX_LEN']} Zeichen pro Teil.")

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
        print(f"DEBUG Telegram Plaintext {index}/{len(chunks)}: HTTP {fallback.status_code}")
        fallback.raise_for_status()
        time.sleep(0.25)

# ==========================================
# 6. HAUPTABLAUF
# ==========================================
def main():
    print("🚀 Starte Kickbase Bot Engine...")
    
    # Live Kickbase-Daten abrufen
    token, league_id = kickbase_login()
    raw_players = get_market_players(token, league_id)
    print(f"📊 {len(raw_players)} Spieler auf dem Transfermarkt gefunden.")
    
    history = load_history()
    evaluated_list = []
    report_lines = [f"⚽ <b>Kickbase Markt-Update ({CONFIG['DAYS_UNTIL_MATCHDAY']} Tage bis Spieltag 1)</b>\n"]
    
    for p in raw_players:
        res = evaluate_player(p, history)
        evaluated_list.append(res)
        
        profit_formatted = f"+{res['exp_profit']:,}".replace(",", ".")
        mv_formatted = f"{res['mv']:,}".replace(",", ".")
        proj_formatted = f"{res['proj_mv']:,}".replace(",", ".")
        bid_formatted = f"{res['max_bid']:,}".replace(",", ".")
        
        report_lines.append(
            f"• <b>{res['name']}</b> | MW: {mv_formatted} €\n"
            f"  📈 Proj. MW (MD1): {proj_formatted} € ({profit_formatted} €)\n"
            f"  🎯 Max. Gebot: <b>{bid_formatted} €</b> (Quoten-Fak.: {res['matchup_mult']})\n"
            f"  💡 Empfehlung: <b>{res['recommendation']}</b>\n"
        )
    
    # History nach der Auswertung speichern
    save_history(history)
    
    # KI-Zusammenfassung anfordern
    top_targets = [p for p in evaluated_list if p["adjusted_profit"] > 0]
    prompt = f"Analysiere diese besten Kickbase-Transfers in max 3 deutschen Sätzen:\n{json.dumps(top_targets[:5])}"
    llm_summary = call_gemini(prompt)
    
    report_lines.append(f"🤖 <b>KI-Manager Fazit:</b>\n{llm_summary}")
    full_report = "\n".join(report_lines)
    
    # Telegram-Versand ohne umhüllendes try/except ausführen, damit Fehler weitergereicht werden
    send_telegram_messages(full_report)
    print("✅ Prozess erfolgreich abgeschlossen.")

if __name__ == "__main__":
    main()
