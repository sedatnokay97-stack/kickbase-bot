import os
import requests

KB_EMAIL = os.environ.get("KB_EMAIL")
KB_PASSWORD = os.environ.get("KB_PASSWORD")
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

def main():
    print("▶️ Starte korrigierten Markt-Abruf...")
    headers = {
        "User-Agent": "Kickbase/6.8.0 (iPhone; iOS 16.5; Scale/3.00)",
        "Content-Type": "application/json",
        "Accept": "application/json"
    }
    payload = {"em": KB_EMAIL, "pass": KB_PASSWORD, "loy": False, "rep": {}}
    
    try:
        # 1. Login
        login_res = requests.post("https://api.kickbase.com/v4/user/login", json=payload, headers=headers).json()
        token = login_res.get("tkn")
        liga_id = login_res.get("srvl", [])[0].get("id")
        liga_name = login_res.get("srvl", [])[0].get("name", "Unbekannt")
        
        # 2. Transfermarkt abrufen
        headers["Authorization"] = f"Bearer {token}"
        market_res = requests.get(f"https://api.kickbase.com/v4/leagues/{liga_id}/market", headers=headers).json()
        players = market_res.get("it", [])
        
        # 3. Spieler nach verbleibenden Sekunden (exs) sortieren
        players.sort(key=lambda x: x.get("exs", 999999))
        
        # 4. Telegram-Nachricht bauen
        msg = f"⚽ *Markt-Update: {liga_name}*\n"
        msg += f"📊 Spieler auf dem Markt: {len(players)}\n\n"
        msg += "🔥 *Laufen als Nächstes ab:*\n\n"
        
        # Hier kannst du die 7 anpassen, wenn du mehr/weniger Spieler sehen willst
        for p in players[:7]:
            vorname = p.get("fn", "")
            nachname = p.get("n", "Unbekannt")
            marktwert = p.get("mv", 0)
            mw_formatiert = f"{marktwert:,}".replace(",", ".")
            
            # Trend: 1 = Steigt, 2 = Sinkt
            mvt = p.get("mvt", 0)
            trend = "📈" if mvt == 1 else "📉" if mvt == 2 else "➖"
            
            # Echtes Zeitfenster aus den Verbleibenden Sekunden (exs) berechnen
            exs = p.get("exs", 0)
            if exs > 0:
                hours = exs // 3600
                minutes = (exs % 3600) // 60
                time_str = f"{hours}h {minutes}m"
            else:
                time_str = "Abgelaufen"
                
            msg += f"• *{vorname} {nachname}* {trend}\n"
            msg += f"  💰 {mw_formatiert} € | ⏳ {time_str}\n\n"
            
        send_telegram(msg)
        print("✅ Korrigierte Nachricht versendet!")

        # 5. SPIONAGE-RADAR
        ranking_url = f"https://api.kickbase.com/v4/leagues/{liga_id}/ranking"
        ranking_res = requests.get(ranking_url, headers=headers)
        if ranking_res.status_code == 200:
            print("\n🔍 RADAR RESULTAT:")
            print(str(ranking_res.json())[:500])
        
    except Exception as e:
        print(f"❌ Fehler: {str(e)}")

def send_telegram(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "Markdown"})

if __name__ == "__main__":
    main()
