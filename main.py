import os
import requests
import xml.etree.ElementTree as ET

KB_EMAIL = os.environ.get("KB_EMAIL")
KB_PASSWORD = os.environ.get("KB_PASSWORD")
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

def get_ligainsider_news():
    news_items = []
    try:
        url = "https://www.ligainsider.de/rss/news.xml"
        headers = {"User-Agent": "Mozilla/5.0"}
        res = requests.get(url, headers=headers, timeout=5)
        if res.status_code == 200:
            root = ET.fromstring(res.text)
            for item in root.findall(".//item"):
                title = item.find("title").text if item.find("title") is not None else ""
                link = item.find("link").text if item.find("link") is not None else ""
                news_items.append({"title": title, "link": link})
    except: pass
    return news_items

def main():
    headers = {
        "User-Agent": "Kickbase/6.8.0 (iPhone; iOS 16.5; Scale/3.00)",
        "Content-Type": "application/json",
        "Accept": "application/json"
    }
    payload = {"em": KB_EMAIL, "pass": KB_PASSWORD, "loy": False, "rep": {}}
    
    try:
        login_res = requests.post("https://api.kickbase.com/v4/user/login", json=payload, headers=headers).json()
        token = login_res.get("tkn")
        liga_id = login_res.get("srvl", [])[0].get("id")
        headers["Authorization"] = f"Bearer {token}"
        
        news_items = get_ligainsider_news()
        
        # Radar & Markt
        ranking_res = requests.get(f"https://api.kickbase.com/v4/leagues/{liga_id}/ranking", headers=headers).json()
        market_res = requests.get(f"https://api.kickbase.com/v4/leagues/{liga_id}/market", headers=headers).json()
        players = sorted(market_res.get("it", []), key=lambda x: x.get("exs", 999999))
        
        # Hier die Sperren-Logik (wie gehabt)
        blocked_teams = {}
        for user in ranking_res.get("users", []):
            if user.get("id") == login_res.get("u", {}).get("i"): continue
            squad = requests.get(f"https://api.kickbase.com/v4/leagues/{liga_id}/users/{user.get('id')}/squad", headers=headers).json().get("it", [])
            counts = {}
            for sp in squad:
                tid = sp.get("tid")
                counts[tid] = counts.get(tid, 0) + 1
            for tid, count in counts.items():
                if count >= 2: blocked_teams.setdefault(tid, []).append(user.get("name"))

        # Nachricht
        msg = "⚽ *Markt-Update: Kickbase Elite*\n\n"
        for p in players[:7]:
            nachname = p.get("n", "Unbekannt")
            mw = f"{p.get('mv', 0):,}".replace(",", ".")
            trend = "📈" if p.get("mvt") == 1 else "📉" if p.get("mvt") == 2 else "➖"
            exs = p.get("exs", 0)
            time_str = f"{exs//3600}h {(exs%3600)//60}m" if exs > 0 else "Abgelaufen"
            
            msg += f"• *{nachname}* {trend} | 💰 {mw} € | ⏳ {time_str}\n"
            if p.get("tid") in blocked_teams:
                msg += f"  🚫 *Sperre:* {', '.join(blocked_teams[p.get('tid')])}\n"
            
            # News nur wenn Treffer
            for n in news_items:
                if nachname.lower() in n["title"].lower() and len(nachname) > 3:
                    msg += f"  📰 [{n['title']}]({n['link']})\n"
                    break
            msg += "\n"
            
        send_telegram(msg)
    except: pass

def send_telegram(text):
    requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage", json={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "Markdown", "disable_web_page_preview": True})

if __name__ == "__main__":
    main()
