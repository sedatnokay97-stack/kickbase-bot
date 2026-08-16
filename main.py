import os
import requests
from kickbase_api.kickbase import Kickbase

# 1. Zugangsdaten aus den sicheren Secrets laden
KB_EMAIL = os.environ.get("KB_EMAIL")
KB_PASSWORD = os.environ.get("KB_PASSWORD")
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

def main():
    try:
        # Kickbase Login
        kb = Kickbase()
        kb.login(KB_EMAIL, KB_PASSWORD)
        
        leagues = kb.leagues()
        if not leagues:
            send_telegram("⚠️ Login erfolgreich, aber keine Liga gefunden!")
            return
            
        league = leagues[0]
        market = kb.market(league)
        
        # Einfache Übersicht für den Start
        msg = f"⚽ *Kickbase Update - {league.name}*\n\n"
        msg += f"📊 *Spieler auf dem Transfermarkt:* {len(market.players)}\n\n"
        
        msg += "*Top 5 Marktwert-Steigerungen:*\n"
        # Sortiere nach Marktwerttrend
        sorted_players = sorted(market.players, key=lambda p: p.market_value_trend, reverse=True)[:5]
        for p in sorted_players:
            msg += f"• {p.first_name} {p.last_name}: +{p.market_value_trend:,} €\n"
            
        send_telegram(msg)
        
    except Exception as e:
        send_telegram(f"❌ Fehler beim Abrufen: {str(e)}")

def send_telegram(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "Markdown"})

if __name__ == "__main__":
    main()
