import os
import requests

KB_EMAIL = os.environ.get("KB_EMAIL")
KB_PASSWORD = os.environ.get("KB_PASSWORD")
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

def main():
    print("▶️ Starte Transfermarkt-Abruf...")
    
    headers = {
        "User-Agent": "Kickbase/6.8.0 (iPhone; iOS 16.5; Scale/3.00)",
        "Content-Type": "application/json"
    }
    payload = {"em": KB_EMAIL, "pass": KB_PASSWORD, "loy": False, "rep": {}}
    
    try:
        # 1. Login & Liga-ID holen
        print("⏳ Logge ein...")
        login_res = requests.post("https://api.kickbase.com/v4/user/login", json=payload, headers=headers).json()
        token = login_res.get("tkn")
        liga_id = login_res.get("srvl", [])[0].get("i") # Wir nehmen direkt deine Haupt-Liga
        
        # 2. Transfermarkt abrufen
        print("⏳ Lade Transfermarkt herunter...")
        headers["Authorization"] = f"Bearer {token}"
        market_url = f"https://api.kickbase.com/v4/leagues/{liga_id}/market"
        market_res = requests.get(market_url, headers=headers).json()
        
        players = market_res.get("players", [])
        
        # 3. Telegram-Nachricht bauen
        msg = f"📈 *Dein Transfermarkt ist live!*\n\n"
        msg += f"Aktuell sind *{len(players)} Spieler* auf dem Markt.\n\n"
        msg += "Hier sind 5 zufällige Spieler zur Probe:\n"
        
        # Wir zeigen testweise die ersten 5 Spieler aus der Liste
        for p in players[:5]:
            vorname = p.get("fn", "")
            nachname = p.get("n", "Unbekannt")
            marktwert = p.get("mv", 0)
            
            # Formatierung, damit es schöner aussieht (z.B. 12.500.000 €)
            mw_formatiert = f"{marktwert:,}".replace(",", ".")
            
            msg += f"• {vorname} {nachname} ({mw_formatiert} €)\n"
            
        print("⏳ Sende Daten an Telegram...")
        send_telegram(msg)
        print("✅ Alles erledigt!")
        
    except Exception as e:
        print(f"❌ Fehler: {str(e)}")
        send_telegram("❌ Fehler beim Marktabruf. Schau in die GitHub Logs!")

def send_telegram(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "Markdown"})

if __name__ == "__main__":
    main()
