import os
import requests

KB_EMAIL = os.environ.get("KB_EMAIL")
KB_PASSWORD = os.environ.get("KB_PASSWORD")

def main():
    print("▶️ Starte System-Analyse...")
    headers = {
        "User-Agent": "Kickbase/6.8.0 (iPhone; iOS 16.5; Scale/3.00)",
        "Content-Type": "application/json",
        "Accept": "application/json"
    }
    payload = {"em": KB_EMAIL, "pass": KB_PASSWORD, "loy": False, "rep": {}}
    
    try:
        print("⏳ Logge ein...")
        login_response = requests.post("https://api.kickbase.com/v4/user/login", json=payload, headers=headers)
        login_data = login_response.json()
        token = login_data.get("tkn")
        
        print("\n--- LIGA DATEN ANALYSE ---")
        ligen = login_data.get("srvl", [])
        if not ligen:
            print("❌ Keine Ligen gefunden. Komplette Login-Antwort:")
            print(login_data)
            return
            
        print(f"Gefundene Ligen-Daten (Roh): {ligen}")
        
        # Versuche ID zu finden (i oder id)
        liga = ligen[0]
        liga_id = liga.get("i", liga.get("id"))
        
        if not liga_id:
            print("❌ Konnte keine Liga-ID extrahieren!")
            return
            
        print(f"✅ Liga-ID erfolgreich isoliert: {liga_id}")
        
        # Markt Test V4 (Sicherer Abruf ohne Absturz)
        headers["Authorization"] = f"Bearer {token}"
        url_v4 = f"https://api.kickbase.com/v4/leagues/{liga_id}/market"
        print(f"\n⏳ Teste V4 Markt URL: {url_v4}")
        res_v4 = requests.get(url_v4, headers=headers)
        
        print(f"Status Code: {res_v4.status_code}")
        print(f"Server-Antwort (erste 300 Zeichen): {res_v4.text[:300]}")
        
    except Exception as e:
        print(f"❌ Fehler: {str(e)}")

if __name__ == "__main__":
    main()
