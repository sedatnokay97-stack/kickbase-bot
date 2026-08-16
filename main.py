import os
import requests
import traceback
from kickbase_api.kickbase import Kickbase

# Zugangsdaten laden
KB_EMAIL = os.environ.get("KB_EMAIL")
KB_PASSWORD = os.environ.get("KB_PASSWORD")
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

def main():
    print("▶️ Starte das Skript...")
    try:
        print(f"⏳ Versuche Kickbase Login für: {KB_EMAIL}")
        kb = Kickbase()
        kb.login(KB_EMAIL, KB_PASSWORD)
        print("✅ Kickbase Login erfolgreich!")
        
        leagues = kb.leagues()
        if not leagues:
            print("⚠️ Login hat geklappt, aber du bist in keiner Liga!")
            return
            
        league = leagues[0]
        print(f"✅ Liga gefunden: {league.name}")
        
        print("⏳ Lade Transfermarkt...")
        market = kb.market(league)
        
        msg = f"⚽ *Kickbase Update - {league.name}*\n\n"
        msg += f"📊 *Spieler auf dem Transfermarkt:* {len(market.players)}\n"
        
        print("⏳ Sende Nachricht an Telegram...")
        send_telegram(msg)
        print("✅ Skript komplett durchgelaufen!")
        
    except Exception as e:
        print("❌ FEHLER-DETAILS (Bitte kopieren/fotografieren):")
        print(traceback.format_exc())

def send_telegram(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    response = requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "Markdown"})
    print(f"Telegram Status: Code {response.status_code} | Antwort: {response.text}")

if __name__ == "__main__":
    main()
