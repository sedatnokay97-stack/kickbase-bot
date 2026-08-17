import os
import requests
import xml.etree.ElementTree as ET
import re

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
                title = item.find("title")
                if title is not None and title.text:
                    news.append(title.text)
    except Exception:
        pass
    return news

def main():
    headers = {
        "User-Agent": "Kickbase/6.8.0 (iPhone; iOS 16.5; Scale/3.00)",
        "Content-Type": "application/json"
    }
    payload = {"em": KB_EMAIL, "pass": KB_PASSWORD, "loy": False, "rep": {}}
    
    try:
        # Login
        login_res = requests.post("https://api.kickbase.com/v4/user/login", json=payload, headers=headers).json()
        token = login_res.get("tkn")
        liga_id = login_res.get("srvl", [])[0].get("id")
        headers["Authorization"] = f"Bearer {token}"
        
        # Daten laden
        news_headlines = get_news()
        ranking_res = requests.get(f"https://api.kickbase.com/v4/leagues/{liga_id}/ranking", headers=headers).json()
        market_res = requests.get(f"https://api.kickbase.com/v4/leagues/{liga_id}/market", headers=headers).json()
        players = sorted(market_res.get("it", []), key=lambda x: x.get("exs", 999999))
        
        # Gegner-Radar (2-Spieler-Regel)
        blocked_teams = {}
        for user in ranking_res.get("users", []):
            if user.get("id") == login_res.get("u", {}).get("i"): continue
            squad = requests.get(f"https://api.kickbase.com/v4/leagues/{liga_id}/users/{user.get('id')}/squad", headers=headers).json().get("it", [])
            counts = {}
            for sp in squad:
                tid = sp.get("tid")
                counts[tid] = counts.get(tid, 0) + 1
            for tid, count in counts.items():
                if count >= 2: 
                    blocked_teams.setdefault(tid, []).append(user.get("name"))

        # Nachricht zusammenbauen
        msg = "⚽ *Markt-Update: Kickbase Elite*\n\n"
        for p in players[:7]:
            nachname = p.get("n", "Unbekannt")
            mw = f"{p.get('mv', 0):,}".replace(",", ".")
            trend = "📈" if p.get("mvt") == 1 else "📉" if p.get("mvt") == 2 else "➖"
            exs = p.get("exs", 0)
            time_str = f"{exs//3600}h {(exs%3600)//60}m" if exs > 0 else "Abgelaufen"
            
            # Basis-Zeile für den Spieler
            msg += f"• *{nachname}* {trend} | 💰 {mw} € | ⏳ {time_str}\n"
            
            # 1. Offizieller Kickbase-Status (fängt bestehende Verletzungen wie Michel ab)
            if p.get("status") == 2:
                msg += "  🩹 *Status: Verletzt/Gesperrt*\n"
            
            # 2. Gegner-Sperre (Radar)
            if p.get("tid") in blocked_teams:
                msg += f"  🚫 *Sperre:* {', '.join(blocked_teams[p.get('tid')])}\n"
                
            # 3. Präziser News-Check (mit exakter Wortgrenze \b gegen Fehlalarme)
            matched_news = []
            for headline in news_headlines:
                # \b sorgt dafür, dass nur exakte Worttreffer (z.B. "Michel") zählen
                if re.search(r'\b' + re.escape(nachname) + r'\b', headline, re.IGNORECASE):
                    if headline not in matched_news:
                        matched_news.append(headline)
            
            for h in matched_news[:2]:
                msg += f"  📰 *News:* {h}\n"
                
            msg += "\n"
            
        # An Telegram senden
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage", 
            json={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "Markdown"}
        )
        
    except Exception as e:
        print(f"Fehler: {e}")

if __name__ == "__main__":
    main()
