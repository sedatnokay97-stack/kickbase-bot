import os
import requests
import xml.etree.ElementTree as ET

KB_EMAIL = os.environ.get("KB_EMAIL")
KB_PASSWORD = os.environ.get("KB_PASSWORD")
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

def get_ligainsider_news():
    print("⏳ Hole LigaInsider RSS-News...")
    news_items = []
    try:
        url = "https://www.ligainsider.de/rss/news.xml"
        # Etwas "menschlicherer" User-Agent
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36"}
        res = requests.get(url, headers=headers, timeout=10)
        
        if res.status_code == 200:
            root = ET.fromstring(res.text)
            for item in root.findall(".//item"):
                title = item.find("title").text if item.find("title") is not None else ""
                link = item.find("link").text if item.find("link") is not None else ""
                news_items.append({"title": title, "link": link})
            print(f"✅ Erfolg: {len(news_items)} News-Artikel vom Feed geladen.")
        else:
            print(f"⚠️ Feed-Fehler: Status-Code {res.status_code}")
            
    except Exception as e:
        print(f"❌ Fehler beim News-Abruf: {e}")
    return news_items

def main():
    print("▶️ Starte Markt-Abruf inkl. Gegner-Sperren & LigaInsider-Debug...")
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
        
        headers["Authorization"] = f"Bearer {token}"
        
        # 2. News holen
        news_items = get_ligainsider_news()
        
        # 3. Markt holen
        print("⏳ Lade Transfermarkt...")
        market_res = requests.get(f"https://api.kickbase.com/v4/leagues/{liga_id}/market", headers=headers).json()
        players = market_res.get("it", [])
        
        # 4. Telegram-Nachricht bauen
        msg = "⚽ *Markt-Update: News-Check*\n\n"
        
        for p in players[:7]:
            nachname = p.get("n", "Unbekannt")
            msg += f"• *{nachname}*\n"
            
            # Debug: Suche nach diesem Nachnamen in den News
            found_news = []
            for n in news_items:
                if nachname.lower() in n["title"].lower():
                    found_news.append(n)
            
            if found_news:
                msg += f"  📰 *News gefunden:* {found_news[0]['title']}\n"
                print(f"✅ Match gefunden für {nachname}: {found_news[0]['title']}")
            else:
                msg += "  (Keine aktuellen News)\n"
                
        send_telegram(msg)
        print("✅ Nachricht gesendet!")
        
    except Exception as e:
        print(f"❌ Fehler: {str(e)}")

def send_telegram(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "Markdown", "disable_web_page_preview": True})

if __name__ == "__main__":
    main()
