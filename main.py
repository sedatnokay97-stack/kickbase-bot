import os
import requests
import json

# Zugangsdaten aus den GitHub Secrets
KB_EMAIL = os.environ.get("KB_EMAIL")
KB_PASSWORD = os.environ.get("KB_PASSWORD")
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

def send_telegram(text):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("⚠️ Telegram Variablen fehlen!")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    response = requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "Markdown"})
    print(f"Telegram Status: {response.status_code}")

def main():
    print("▶️ Starte Direkt-Verbindung zur Kickbase V4 API...")
    
    # Wir geben uns als normales Smartphone aus
    headers = {
        "User-Agent": "Kickbase/6.8.0 (iPhone; iOS 16.5; Scale/3.00)",
        "Content-Type": "application/json",
        "Accept": "application/json"
    }
    
    # Die neue Art, wie Kickbase die Daten haben will (V4 Format)
    payload = {
        "em": KB_EMAIL,
        "pass": KB_PASSWORD,
        "loy": False,
        "rep": {}
    }
    
    try:
        url = "https://api.kickbase.com/v4/user/login"
        print(f"⏳ Logge ein bei Kickbase v4 mit {KB_EMAIL}...")
        
        # Anfrage abschicken
        response = requests.post(url, json=payload, headers=headers)
        
        if response.status_code == 200:
            print("✅ LOGIN ERFOLGREICH!")
            data = response.json()
            
            # Token und Ligen auslesen
            token = data.get("tkn")
            ligen = data.get("srvl", [])
            
            msg = "✅ *Kickbase Bot erfolgreich verbunden!*\n\n"
            msg += "Wir sind drin! Die neue V4 API Tür wurde geknackt. 🔓\n\n"
            msg += f"Gefundene Ligen: {len(ligen)}\n"
            
            for liga in ligen:
                # n = Name der Liga, i = ID der Liga
                liga_name = liga.get('n', 'Unbekannte Liga')
                msg += f"⚽ {liga_name}\n"
                
            send_telegram(msg)
            print("✅ Erfolgs-Nachricht an Telegram gesendet!")
            
        else:
            print(f"❌ Fehler {response.status_code}: {response.text}")
            
    except Exception as e:
        print(f"❌ System-Fehler: {str(e)}")

if __name__ == "__main__":
    main()
