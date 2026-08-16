import os
import requests
from datetime import datetime, timezone

KB_EMAIL = os.environ.get("KB_EMAIL")
KB_PASSWORD = os.environ.get("KB_PASSWORD")
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

def main():
    print("▶️ Starte smarten Abruf und Gegner-Radar...")
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
        
        # 3. Spieler sortieren (Wer läuft als Nächstes ab?)
        def get_expiry(p):
            dt_str = p.get("dt", "")
            try:
                return datetime.strptime(dt_str, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
            except:
                return datetime.max.replace(tzinfo=timezone.utc)
                
        players.sort(key=get_expiry)
        now = datetime.now(timezone.utc)
        
        # 4. NEUE Telegram-Nachricht (Schluss mit der Probe-Nachricht!)
        msg = f"⚽ *Markt-Update: {liga_name}*\n"
        msg += f"📊 Spieler auf dem Markt: {len(players)}\n\n"
        msg += "🔥 *Laufen als Nächstes ab:*\n\n"
        
        for p in players[:7]:
            vorname = p.get("fn", "")
            nachname = p.get("n", "Unbekannt")
            marktwert = p.get("mv", 0)
            mw_formatiert = f"{marktwert:,}".replace(",", ".")
            
            # Trend: 1 = Steigt, 2 = Sinkt
            mvt = p.get("mvt", 0)
            trend = "📈" if mvt == 1 else "📉" if mvt == 2 else "➖"
            
            # Zeit berechnen
            dt_str = p.get("dt", "")
            if dt_str:
                dt_obj = datetime.strptime(dt_str, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
                diff = dt_obj - now
                hours = int(diff.total_seconds() // 3600)
                minutes = int((diff.total_seconds() % 3600) // 60)
                
                if diff.total_seconds() <= 0:
                    time_str = "Abgelaufen"
                else:
                    time_str = f"{hours}h {minutes}m"
            else:
                time_str = "??"
                
            msg += f"• *{vorname} {nachname}* {trend}\n"
            msg += f"  💰 {mw_formatiert} € | ⏳ {time_str}\n\n"
            
        send_telegram(msg)
        print("✅ Smarte Markt-Nachricht versendet!")

        # 5. SPIONAGE-RADAR (Sucht die anderen Manager für die 2-Spieler-Regel)
        print("\n🔍 --- STARTE GEGNER-RADAR ---")
        ranking_url = f"https://api.kickbase.com/v4/leagues/{liga_id}/ranking"
        ranking_res = requests.get(ranking_url, headers=headers)
        
        if ranking_res.status_code == 200:
            print("✅ Manager-Liste erfolgreich geknackt! Rohdaten:")
            print(str(ranking_res.json())[:500])
        else:
            print(f"⚠️ Konnte Gegner nicht finden, Status-Code: {ranking_res.status_code}")
        
    except Exception as e:
        print(f"❌ Fehler: {str(e)}")

def send_telegram(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "Markdown"})

if __name__ == "__main__":
    main()
