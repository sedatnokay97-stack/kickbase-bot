import os
import requests
import xml.etree.ElementTree as ET

KB_EMAIL = os.environ.get("KB_EMAIL")
KB_PASSWORD = os.environ.get("KB_PASSWORD")
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

def get_ligainsider_news():
    """ Holt die neuesten Nachrichten von LigaInsider via RSS-Feed """
    print("⏳ Hole LigaInsider RSS-News...")
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
            print(f"✅ {len(news_items)} LigaInsider-Artikel abgerufen.")
    except Exception as e:
        print(f"⚠️ LigaInsider Feed konnte nicht geladen werden: {e}")
    return news_items

def main():
    print("▶️ Starte Markt-Abruf inkl. Gegner-Sperren & LigaInsider-News...")
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
        my_user_id = login_res.get("u", {}).get("i")
        
        headers["Authorization"] = f"Bearer {token}"
        
        # 2. LigaInsider News holen
        news_items = get_ligainsider_news()
        
        # 3. Gegner-Kader analysieren (2-Spieler-Regel)
        print("⏳ Scanne Konkurrenz-Kader...")
        ranking_res = requests.get(f"https://api.kickbase.com/v4/leagues/{liga_id}/ranking", headers=headers).json()
        users = ranking_res.get("users", ranking_res.get("u", []))
        
        blocked_teams = {}
        for user in users:
            user_id = user.get("id", user.get("i"))
            user_name = user.get("name", user.get("n", "Manager"))
            
            if user_id == my_user_id:
                continue
                
            squad_res = requests.get(f"https://api.kickbase.com/v4/leagues/{liga_id}/users/{user_id}/squad", headers=headers).json()
            squad_players = squad_res.get("it", squad_res.get("players", []))
            
            team_counts = {}
            for sp in squad_players:
                tid = sp.get("tid")
                if tid:
                    team_counts[tid] = team_counts.get(tid, 0) + 1
                    
            for tid, count in team_counts.items():
                if count >= 2:
                    if tid not in blocked_teams:
                        blocked_teams[tid] = []
                    blocked_teams[tid].append(user_name)

        # 4. Transfermarkt abrufen
        print("⏳ Lade Transfermarkt...")
        market_res = requests.get(f"https://api.kickbase.com/v4/leagues/{liga_id}/market", headers=headers).json()
        players = market_res.get("it", [])
        
        players.sort(key=lambda x: x.get("exs", 999999))
        
        # 5. Telegram-Nachricht bauen
        msg = f"⚽ *Markt-Update: {liga_name}*\n"
        msg += f"📊 Spieler auf dem Markt: {len(players)}\n\n"
        msg += "🔥 *Laufen als Nächstes ab:*\n\n"
        
        for p in players[:7]:
            vorname = p.get("fn", "")
            nachname = p.get("n", "Unbekannt")
            marktwert = p.get("mv", 0)
            mw_formatiert = f"{marktwert:,}".replace(",", ".")
            team_id = p.get("tid")
            
            # Trend: 1 = Steigt, 2 = Sinkt
            mvt = p.get("mvt", 0)
            trend = "📈" if mvt == 1 else "📉" if mvt == 2 else "➖"
            
            # Zeit
            exs = p.get("exs", 0)
            if exs > 0:
                hours = exs // 3600
                minutes = (exs % 3600) // 60
                time_str = f"{hours}h {minutes}m"
            else:
                time_str = "Abgelaufen"
                
            msg += f"• *{vorname} {nachname}* {trend}\n"
            msg += f"  💰 {mw_formatiert} € | ⏳ {time_str}\n"
            
            # Prüfen, ob Konkurrenten für diesen Spieler gesperrt sind
            if team_id in blocked_teams:
                blocked_names = ", ".join(blocked_teams[team_id])
                msg += f"  🚫 *Gesperrt für:* {blocked_names}\n"
                
            # LigaInsider News-Check für diesen Spieler
            matching_news = [n for n in news_items if nachname.lower() in n["title"].lower() and len(nachname) > 3]
            if matching_news:
                latest_article = matching_news[0]
                msg += f"  📰 *News:* [{latest_article['title']}]({latest_article['link']})\n"
                
            msg += "\n"
            
        send_telegram(msg)
        print("✅ Markt-Update inkl. News & Sperren versendet!")

    except Exception as e:
        print(f"❌ Fehler: {str(e)}")

def send_telegram(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "Markdown", "disable_web_page_preview": True})

if __name__ == "__main__":
    main()
