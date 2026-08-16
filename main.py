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
        # 1. Login
        print("⏳ Logge ein...")
        login_res = requests.post("https://api.kickbase.com/v4/user/login", json=payload, headers=headers).json()
        token = login_res.get("tkn")
        
        # Liga ID & Name auslesen
        ligen = login_res.get("srvl", [])
        if not ligen:
            print("❌ Keine Ligen gefunden!")
            return
            
        liga_id = ligen[0].get("i")
        liga_name = ligen[0].get("n", "Unbekannt")
        print(f"✅ Eingeloggt! Lade Markt für Liga: {liga_name}")
        
        # 2. Transfermarkt abrufen (Jetzt OHNE das /v4/ in der URL!)
        headers["Authorization"] = f"Bearer {token}"
        market_url = f"https://api.kickbase.com/leagues/{liga_id}/market"
        
        print("⏳ Lade Transfermarkt herunter...")
        market_res = requests.get(market_url, headers=headers).json()
        
        # Prüfen, ob Kickbase wieder meckert
        if "err" in market_res:
            print(f"❌ Kickbase meldet einen Fehler: {market_res}")
            return
            
        players = market_res.get("players", [])
        print(f"✅ {len(players)} Spieler gefunden!")
        
        # 3. Telegram-Nachricht bauen
        msg = f"📈 *Transfermarkt: {liga_name}*\n\n"
        msg += f"Aktuell sind *{len(players)} Spieler* auf dem Markt.\n\n"
        msg += "Hier sind 5 zufällige Spieler zur Probe:\n"
        
        for p in players[:5]:
            vorname = p.get("fn", "")
            nachname = p.get("n", "Unbekannt")
            marktwert = p.get("mv", 0)
            mw_formatiert = f"{marktwert:,}".replace(",", ".")
            msg += f"• {vorname} {nachname} ({mw_formatiert} €)\n"
            
        send_telegram(msg)
        print("✅ Alles erledigt!")
        
    except Exception as e:
        print(f"❌ Fehler: {str(e)}")

def send_telegram(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "Markdown"})

if __name__ == "__main__":
    main()
