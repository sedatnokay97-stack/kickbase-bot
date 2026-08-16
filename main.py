import os
import requests

# Zugangsdaten
KB_EMAIL = os.environ.get("KB_EMAIL")
KB_PASSWORD = os.environ.get("KB_PASSWORD")

def main():
    print("▶️ Starte Direkttest an Kickbase...")
    
    # Wir tarnen den Bot als normales iPhone
    headers = {
        "User-Agent": "Kickbase/6.8.0 (iPhone; iOS 16.5; Scale/3.00)",
        "Content-Type": "application/json"
    }
    
    payload = {
        "email": KB_EMAIL,
        "password": KB_PASSWORD,
        "ext": False
    }
    
    print("⏳ Klopfe an der Kickbase-Tür...")
    try:
        url = "https://api.kickbase.com/user/login"
        response = requests.post(url, json=payload, headers=headers)
        
        print(f"📊 Status Code: {response.status_code}")
        print(f"🗣️ Kickbase Server-Antwort: {response.text}")
        
        if response.status_code == 200:
            print("✅ Login hat manuell geklappt! Das Kickbase-Paket ist kaputt, wir müssen es selbst schreiben.")
        elif response.status_code == 403:
            print("❌ 403 Forbidden: Kickbase blockiert die GitHub-Server (Türsteher-Effekt).")
        elif response.status_code == 401:
            print("❌ 401 Unauthorized: Irgendwas stimmt mit der E-Mail/Passwort-Kombination nicht.")
            
    except Exception as e:
        print(f"❌ System-Fehler: {str(e)}")

if __name__ == "__main__":
    main()
