import os
import sys
import re
import json
import time
import base64
import requests
from bs4 import BeautifulSoup

# ==========================================
# CONFIG & ENVIRONMENT SECRETS
# ==========================================
KB_EMAIL = os.environ.get("KB_EMAIL")
KB_PASSWORD = os.environ.get("KB_PASSWORD")
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN")
GITHUB_REPOSITORY = os.environ.get("GITHUB_REPOSITORY")

CONFIG = {
    "DRY_RUN": False,
    "TELEGRAM_MAX_LEN": 4000,
    "REQUEST_TIMEOUT": 15,
    "DAYS_UNTIL_MATCHDAY": 11,
    "OVERPAY_RISK_FACTOR": 0.60,
    "HISTORY_FILE": "history.json",
    "MV_HISTORY_LENGTH": 5
}

# ==========================================
# KNOTEN 1: KICKBASE LIVE API
# ==========================================
def kickbase_login():
    print("🚀 Starte Kickbase Login mit Android-Header...")
    headers = {
        "User-Agent": "Kickbase/8.10.0 (Android)",
        "Content-Type": "application/json"
    }
    body = {
        "em": KB_EMAIL,
        "pass": KB_PASSWORD,
        "loy": False,
        "rep": {}
    }
    resp = requests.post("https://api.kickbase.com/v4/user/login", json=body, headers=headers)
    if resp.status_code != 200:
        raise ValueError(f"❌ Kickbase API Login-Fehler (HTTP {resp.status_code}): {resp.text}")

    data = resp.json()
    token = data.get("tkn")
    
    league_id = None
    if "lins" in data and len(data["lins"]) > 0:
        league_id = data["lins"][0]["i"]

    if not token or not league_id:
         raise ValueError("❌ Konnte Token oder Liga-ID nicht auslesen.")

    return token, str(league_id)

def get_market_players(token, league_id):
    """ Holt alle Transfermarkt-Spieler und umgeht die neuen Kickbase-Namen. """
    url = f"https://api.kickbase.com/v4/leagues/{league_id}/market"
    headers = {
        "Authorization": f"Bearer {token}",
        "User-Agent": "Kickbase/8.10.0 (Android)",
        "Accept": "application/json"
    }
    
    resp = requests.get(url, headers=headers, timeout=CONFIG["REQUEST_TIMEOUT"])
    resp.raise_for_status()
    data = resp.json()
    
    # 1. Klassischer Weg
    if "players" in data and data["players"]:
        return data["players"]
        
    # 2. Smart-Scan: Suche automatisch nach der Liste, die die Spieler enthält
    for key, value in data.items():
        if isinstance(value, list) and len(value) > 0 and isinstance(value[0], dict):
            if any(k in value[0] for k in ["id", "i", "n", "lastName", "mv"]):
                print(f"🔍 Kickbase API Update umgangen: Spieler im neuen Ordner '{key}' gefunden!")
                return value
                
    # 3. Fallback
    print(f"⚠️ DEBUG RAW MARKTDATEN: {json.dumps(data)[:800]}")
    return []

# ==========================================
# KNOTEN 2: LIGAINSIDER SCRAPING
# ==========================================
def get_ligainsider_lineups():
    url = "https://www.ligainsider.de/bundesliga/aufstellung/"
    starters = set()
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    try:
        resp = requests.get(url, headers=headers, timeout=CONFIG["REQUEST_TIMEOUT"])
        if resp.status_code == 200:
            soup = BeautifulSoup(resp.text, "html.parser")
            for link in soup.find_all("a", href=True):
                if "/spieler/" in link["href"]:
                    name = link.text.strip()
                    if name:
                        starters.add(name.lower())
    except Exception as e:
        print(f"⚠️ LigaInsider Scraping-Hinweis: {e}")
    return starters

def get_ligainsider_injuries():
    url = "https://www.ligainsider.de/verletzungen-sperren/"
    injured = set()
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    try:
        resp = requests.get(url, headers=headers, timeout=CONFIG["REQUEST_TIMEOUT"])
        if resp.status_code == 200:
            soup = BeautifulSoup(resp.text, "html.parser")
            for tag in soup.find_all(["span", "td", "a"]):
                text = tag.text.strip()
                if 3 < len(text) < 30:
                    injured.add(text.lower())
    except Exception as e:
        print(f"⚠️ Verletzungs-Scraping-Hinweis: {e}")
    return injured

# ==========================================
# KNOTEN 3: HISTORY & PROGNOSE ENGINE
# ==========================================
def load_history():
    """
    Laedt den Verlauf primaer aus dem GitHub-Repo (ueberlebt GitHub-Actions-Runs),
    mit Fallback auf eine lokale Datei fuer Tests ausserhalb von Actions.
    Gibt (history_dict, sha_oder_None) zurueck - sha wird beim Speichern gebraucht,
    um die richtige Datei-Version im Repo zu ueberschreiben.
    """
    if GITHUB_TOKEN and GITHUB_REPOSITORY:
        url = f"https://api.github.com/repos/{GITHUB_REPOSITORY}/contents/{CONFIG['HISTORY_FILE']}"
        headers = {"Authorization": f"token {GITHUB_TOKEN}", "Accept": "application/vnd.github+json"}
        try:
            resp = requests.get(url, headers=headers, timeout=CONFIG["REQUEST_TIMEOUT"])
            if resp.status_code == 404:
                print("ℹ️ Noch kein Verlauf im Repo vorhanden - wird nach diesem Lauf angelegt.")
                return {}, None
            resp.raise_for_status()
            data = resp.json()
            content = base64.b64decode(data["content"]).decode("utf-8")
            history = json.loads(content)
            print(f"✅ Verlauf aus GitHub geladen ({len(history)} Spieler bekannt).")
            return history, data.get("sha")
        except Exception as e:
            print(f"⚠️ Verlauf (GitHub) konnte nicht geladen werden: {e}")
            return {}, None

    print("ℹ️ Kein GITHUB_TOKEN/GITHUB_REPOSITORY gesetzt - nutze lokale Datei (nur fuer lokale Tests sinnvoll).")
    if os.path.exists(CONFIG["HISTORY_FILE"]):
        try:
            with open(CONFIG["HISTORY_FILE"], "r", encoding="utf-8") as f:
                return json.load(f), None
        except Exception:
            return {}, None
    return {}, None


def save_history(history, sha=None):
    """Speichert den Verlauf dorthin zurueck, wo er geladen wurde (GitHub oder lokale Datei)."""
    content_str = json.dumps(history, indent=2, ensure_ascii=False)

    if GITHUB_TOKEN and GITHUB_REPOSITORY:
        url = f"https://api.github.com/repos/{GITHUB_REPOSITORY}/contents/{CONFIG['HISTORY_FILE']}"
        headers = {"Authorization": f"token {GITHUB_TOKEN}", "Accept": "application/vnd.github+json"}
        payload = {
            "message": f"Update Kickbase-Verlauf {int(time.time())}",
            "content": base64.b64encode(content_str.encode("utf-8")).decode("utf-8"),
        }
        if sha:
            payload["sha"] = sha
        try:
            resp = requests.put(url, headers=headers, json=payload, timeout=CONFIG["REQUEST_TIMEOUT"])
            resp.raise_for_status()
            print("✅ Verlauf erfolgreich ins GitHub-Repo geschrieben.")
            return
        except Exception as e:
            print(f"⚠️ Verlauf (GitHub) konnte nicht gespeichert werden: {e}")
            # Kein Rueckfall auf lokale Datei hier, da die eh beim naechsten Run weg waere.
            return

    try:
        with open(CONFIG["HISTORY_FILE"], "w", encoding="utf-8") as f:
            f.write(content_str)
        print("✅ Verlauf lokal gespeichert.")
    except Exception as e:
        print(f"⚠️ History Speicherung fehlgeschlagen: {e}")

def calculate_real_daily_trend(player_id, current_mv, history):
    """
    Ermittelt den taeglichen Marktwert-Trend robust ueber den MEDIAN mehrerer
    Deltas (bis zu MV_HISTORY_LENGTH Messpunkte) statt nur des letzten
    einzelnen Laufs - ein einzelner Ausreisser-Tag verzerrt die 11-Tage-Prognose
    so nicht mehr so stark.

    Migriert automatisch das alte Format {"mv": X} auf {"mv_history": [X, ...]}:
    ein bereits vorhandener alter "mv"-Wert wird zum ersten Listeneintrag, statt
    verloren zu gehen.
    """
    player_id_str = str(player_id)
    player_hist = history.get(player_id_str, {})

    mv_history = player_hist.get("mv_history")
    if mv_history is None:
        old_mv = player_hist.get("mv")
        mv_history = [old_mv] if old_mv is not None and old_mv > 0 else []
    else:
        mv_history = list(mv_history)  # Kopie, um das Original nicht versehentlich zu mutieren

    if len(mv_history) >= 2:
        deltas = sorted(mv_history[i] - mv_history[i - 1] for i in range(1, len(mv_history)))
        daily_trend = deltas[len(deltas) // 2]  # Median der letzten Deltas
    elif len(mv_history) == 1:
        daily_trend = current_mv - mv_history[-1]
    else:
        daily_trend = 150000 if current_mv > 5000000 else 50000

    mv_history.append(current_mv)
    mv_history = mv_history[-CONFIG["MV_HISTORY_LENGTH"]:]

    history[player_id_str] = {"mv_history": mv_history}
    return daily_trend

def predict_mv_and_overpay(current_mv, daily_trend):
    if daily_trend <= 0:
        return current_mv, int(current_mv * 1.02), 0

    damping = 0.96
    projected_growth = 0
    current_daily = daily_trend

    for _ in range(CONFIG["DAYS_UNTIL_MATCHDAY"]):
        projected_growth += current_daily
        current_daily *= damping

    projected_mv = int(current_mv + projected_growth)
    max_bid = current_mv + int(projected_growth * CONFIG["OVERPAY_RISK_FACTOR"])
    return projected_mv, max_bid, int(projected_growth)

def evaluate_player(player, history, starters, injured_list):
    player_id = player.get("id", player.get("i"))
    
    name = player.get("n", player.get("lastName", ""))
    if not name and "fn" in player and "ln" in player:
        name = f"{player['fn']} {player['ln']}"
    if not name:
        name = "Unbekannt"
        
    mv = int(player.get("mv", player.get("m", 0)))
    
    daily_trend = calculate_real_daily_trend(player_id, mv, history)
    proj_mv, max_bid, raw_profit = predict_mv_and_overpay(mv, daily_trend)
    
    name_lower = name.lower()
    is_starter = any(s in name_lower or name_lower in s for s in starters)
    is_injured = any(i in name_lower or name_lower in i for i in injured_list)
    
    recommendation = "ABWARTEN"
    if raw_profit > 1_500_000 and not is_injured:
        recommendation = "KAUF JETZT (High Profit)"
    elif daily_trend > 0:
        recommendation = "BEOBACHTEN"
    if is_injured:
        recommendation = "RISIKO (Verletzt/Angeschlagen)"

    return {
        "id": player_id,
        "name": name,
        "mv": mv,
        "proj_mv": proj_mv,
        "max_bid": max_bid,
        "exp_profit": raw_profit,
        "is_starter": is_starter,
        "is_injured": is_injured,
        "recommendation": recommendation
    }

# ==========================================
# KNOTEN 4: TELEGRAM DISPATCHER & SPLITTER
# ==========================================
def split_telegram_message(message, max_len):
    chunks = []
    current = ""
    for line in message.splitlines():
        line = line.rstrip()
        while len(line) > max_len:
            if current:
                chunks.append(current)
                current = ""
            split_at = line.rfind(" ", 0, max_len)
            if split_at <= 0:
                split_at = max_len
            chunks.append(line[:split_at])
            line = line[split_at:].lstrip()
        candidate = line if not current else f"{current}\n{line}"
        if len(candidate) > max_len:
            if current:
                chunks.append(current)
            current = line
        else:
            current = candidate
    if current:
        chunks.append(current)
    return [chunk for chunk in chunks if chunk.strip()]

def send_telegram_messages(message):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        raise ValueError("❌ TELEGRAM_TOKEN oder TELEGRAM_CHAT_ID fehlt in den Secrets!")

    chunks = split_telegram_message(message, CONFIG["TELEGRAM_MAX_LEN"])
    print(f"DEBUG Telegram: Sende {len(chunks)} Teil(e)...")

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    for index, chunk in enumerate(chunks, start=1):
        response = requests.post(
            url,
            json={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": chunk,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
            timeout=CONFIG["REQUEST_TIMEOUT"],
        )
        print(f"DEBUG Telegram HTML {index}/{len(chunks)} ({len(chunk)} Zeichen): HTTP {response.status_code}")

        if response.ok:
            time.sleep(0.25)
            continue

        plain = re.sub(r"<[^>]+>", "", chunk)
        fallback = requests.post(
            url,
            json={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": plain,
                "disable_web_page_preview": True,
            },
            timeout=CONFIG["REQUEST_TIMEOUT"],
        )
        print(f"DEBUG Telegram Plaintext Fallback {index}/{len(chunks)}: HTTP {fallback.status_code}")
        fallback.raise_for_status()
        time.sleep(0.25)

# ==========================================
# HAUPTABLAUF (PIPELINE EXECUTION)
# ==========================================
def main():
    print("🚀 Starte Kickbase Bot Engine...")
    
    token, league_id = kickbase_login()
    raw_players = get_market_players(token, league_id)
    print(f"📊 {len(raw_players)} Spieler auf dem Transfermarkt gefunden.")
    
    starters = get_ligainsider_lineups()
    injured = get_ligainsider_injuries()
    history, history_sha = load_history()
    
    evaluated_list = []
    report_lines = [f"⚽ <b>Kickbase Markt-Report ({CONFIG['DAYS_UNTIL_MATCHDAY']} Tage bis Spieltag 1)</b>\n"]
    
    for p in raw_players:
        res = evaluate_player(p, history, starters, injured)
        evaluated_list.append(res)
        
        profit_formatted = f"+{res['exp_profit']:,}".replace(",", ".")
        mv_formatted = f"{res['mv']:,}".replace(",", ".")
        proj_formatted = f"{res['proj_mv']:,}".replace(",", ".")
        bid_formatted = f"{res['max_bid']:,}".replace(",", ".")
        
        status_icons = "✅ S11" if res["is_starter"] else "❓ Rotation"
        if res["is_injured"]:
            status_icons += " | 🚑 Verletzt"
            
        report_lines.append(
            f"• <b>{res['name']}</b> | MW: {mv_formatted} €\n"
            f"  📈 Proj. MW (MD1): {proj_formatted} € ({profit_formatted} €)\n"
            f"  🎯 Max. Gebot: <b>{bid_formatted} €</b> ({status_icons})\n"
            f"  💡 Empfehlung: <b>{res['recommendation']}</b>\n"
        )
    
    save_history(history, history_sha)
    
    full_report = "\n".join(report_lines)
    
    send_telegram_messages(full_report)
    print("✅ Pipeline-Durchlauf vollkommen erfolgreich beendet!")

if __name__ == "__main__":
    main()
