import os
import requests
import xml.etree.ElementTree as ET
import re  # Wir brauchen das für den exakten Wort-Abgleich

KB_EMAIL = os.environ.get("KB_EMAIL")
KB_PASSWORD = os.environ.get("KB_PASSWORD")
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

def get_news():
    news = []
    try:
        res = requests.get("https://www.ligainsider.de/rss/news.xml", timeout=5)
        if res.status_code == 200:
            root = ET.fromstring(res.text)
            for item in root.findall(".//item"):
                news.append(item.find("title").text)
    except: pass
    return news

def main():
    headers = {"User-Agent": "Kickbase/6.8.0 (iPhone; iOS 16.5; Scale/3.00)", "Content-Type": "application/json"}
    payload = {"em": KB_EMAIL, "pass": KB_PASSWORD, "loy": False, "rep": {}}
    
    try:
        # Login
        login_res = requests.post("https://api.kickbase.com/v4/user/login", json=payload, headers=headers).json()
        token = login_res.get("tkn")
        liga_id = login_res.get("srvl", [])[0].get("id")
        headers["Authorization"] = f"Bearer {token}"
        
        # Daten laden
        news_headlines = get_news()
        market_res = requests.get(f"https://api.kickbase.com/v4/leagues/{liga_id}/market", headers=headers).json()
        players = sorted(market_res.get("it", []), key=lambda x: x.get("exs", 999999))
        
        msg = "⚽ *Markt-Update: Elite-Check*\n\n"
        for p in players[:7]:
            nachname = p.get("n", "")
            mw = f"{p.get('mv', 0):,}".replace(",", ".")
            exs = p.get("exs", 0)
            time_str = f"{exs//3600}h {(exs%3600)//60}m" if exs > 0 else "Abgelaufen"
            
            msg += f"• *{nachname}* | 💰 {mw} € | ⏳ {time_str}\n"
            
            # 1. Offizieller Status
            if p.get("status") == 2:
                msg += "  🩹 *Status: Offiziell Verletzt*\n"
            
            # 2. News-Check mit exaktem Wort-Abgleich (Word Boundary \b)
            # \b sorgt dafür, dass "Jung" in "Junges" NICHT matched, aber "Jung" in "Jung verletzt" schon.
            for headline in news_headlines:
                if re.search(r'\b' + re.escape(nachname) + r'\b', headline, re.IGNORECASE):
                    msg += f"  📰 *News-Hinweis:* {headline}\n"
                    break 
            
            msg += "\n"
            
        requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage", 
                      json={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "Markdown"})
        
    except Exception as e:
        print(f"Fehler: {e}")

if __name__ == "__main__":
    main()
