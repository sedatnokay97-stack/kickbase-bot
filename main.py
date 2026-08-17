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
"MV_HISTORY_LENGTH": 5,
"MAX_PER_TEAM": 2,
"OPPONENT_FETCH_SLEEP": 0.3,
"SEASON_YEAR": 2026, # naechstes Jahr manuell auf 2027 aendern
"FIXTURE_LOOKAHEAD": 3
}

# ==========================================
# VEREINS-ZUORDNUNG (fuer Restprogramm/Gegnerstaerke)
# ==========================================
# Kickbase-interne tid -> Team-Key. tid kommt als STRING aus der API.
# Manuell verifiziert: 12 von 18 Vereinen. Offen: dortmund, schalke, hamburg,
# augsburg, paderborn, elversberg - fuer deren Spieler gibt es bis auf Weiteres
# kein Restprogramm (kein Absturz, das Feld bleibt einfach leer).
KICKBASE_TID_TO_TEAM = {
"2": "bayern",
"4": "frankfurt",
"5": "freiburg",
"7": "leverkusen",
"9": "stuttgart",
"10": "werder",
"14": "hoffenheim",
"15": "gladbach",
"18": "mainz",
"28": "koeln",
"40": "union berlin",
"43": "leipzig",
}

TEAM_STRENGTH_LAST_SEASON = {
"bayern": 89,
"dortmund": 73,
"leipzig": 65,
"stuttgart": 62,
"hoffenheim": 61,
"leverkusen": 59,
"freiburg": 47,
"frankfurt": 44,
"augsburg": 43,
"mainz": 40,
"union berlin": 39,
"gladbach": 38,
"hamburg": 38,
"koeln": 32,
"werder": 32,
"paderborn": 22,
"schalke": 20,
"elversberg": 20,
}
TEAM_STRENGTH_DEFAULT = 45
BUNDESLIGA_TEAM_KEYS = list(TEAM_STRENGTH_LAST_SEASON.keys())

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

user_obj = data.get("u", {}) or {}
my_user_id = user_obj.get("id") or user_obj.get("i")
my_manager_id = user_obj.get("mid") or my_user_id
if not my_manager_id:
print("⚠️ Konnte eigene Manager-ID nicht auslesen - Budget-Check und 2-Spieler-Regel werden uebersprungen.")

return token, str(league_id), my_manager_id

def _kb_headers(token):
return {
"Authorization": f"Bearer {token}",
"User-Agent": "Kickbase/8.10.0 (Android)",
"Accept": "application/json",
}

def get_market_players(token, league_id):
""" Holt alle Transfermarkt-Spieler und umgeht die neuen Kickbase-Namen. """
url = f"https://api.kickbase.com/v4/leagues/{league_id}/market"
headers = _kb_headers(token)

resp = requests.get(url, headers=headers, timeout=CONFIG["REQUEST_TIMEOUT"])
resp.raise_for_status()
data = resp.json()

if "players" in data and data["players"]:
return data["players"]

for key, value in data.items():
if isinstance(value, list) and len(value) > 0 and isinstance(value[0], dict):
if any(k in value[0] for k in ["id", "i", "n", "lastName", "mv"]):
print(f"🔍 Kickbase API Update umgangen: Spieler im neuen Ordner '{key}' gefunden!")
return value

print(f"⚠️ DEBUG RAW MARKTDATEN: {json.dumps(data)[:800]}")
return []

def get_my_budget(token, league_id):
"""Liest den verfuegbaren Kontostand aus, mit Fallback von /me/budget auf /me."""
for path in ["me/budget", "me"]:
try:
resp = requests.get(
f"https://api.kickbase.com/v4/leagues/{league_id}/{path}",
headers=_kb_headers(token),
timeout=CONFIG["REQUEST_TIMEOUT"],
)
resp.raise_for_status()
data = resp.json()
for key in ["budget", "b", "amount"]:
if key in data and data[key] is not None:
return int(data[key])
except Exception as e:
print(f"⚠️ Budget ueber /{path} nicht ladbar: {e}")
return None

def get_ranking(token, league_id):
try:
resp = requests.get(
f"https://api.kickbase.com/v4/leagues/{league_id}/ranking",
headers=_kb_headers(token),
timeout=CONFIG["REQUEST_TIMEOUT"],
)
resp.raise_for_status()
return resp.json()
except Exception as e:
print(f"⚠️ Liga-Rangliste nicht ladbar, Gegner-Analyse wird uebersprungen: {e}")
return {}

def get_squad(token, league_id, manager_id):
try:
resp = requests.get(
f"https://api.kickbase.com/v4/leagues/{league_id}/managers/{manager_id}/squad",
headers=_kb_headers(token),
timeout=CONFIG["REQUEST_TIMEOUT"],
)
resp.raise_for_status()
return resp.json().get("it", [])
except Exception as e:
print(f"⚠️ Kader von Manager {manager_id} nicht ladbar: {e}")
return []

def count_players_by_team(squad_items):
counts = {}
for sp in squad_items:
tid = sp.get("tid")
if not tid:
continue
counts[tid] = counts.get(tid, 0) + 1
return counts

def build_blocked_teams(token, league_id, ranking_res, my_manager_id):
blocked = {}
users = ranking_res.get("users", []) if ranking_res else []
for user in users:
opponent_id = user.get("mid") or user.get("id")
if not opponent_id or opponent_id == my_manager_id:
continue
squad = get_squad(token, league_id, opponent_id)
counts = count_players_by_team(squad)
for tid, count in counts.items():
if count >= CONFIG["MAX_PER_TEAM"]:
blocked.setdefault(tid, []).append(user.get("name", "?"))
time.sleep(CONFIG["OPPONENT_FETCH_SLEEP"])
return blocked

def get_my_team_counts(token, league_id, my_manager_id):
if not my_manager_id:
return {}
squad = get_squad(token, league_id, my_manager_id)
return count_players_by_team(squad)

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
# KNOTEN 3: RESTPROGRAMM / GEGNERSTAERKE (OpenLigaDB)
# ==========================================
def get_season_fixtures():
"""Laedt den kompletten Saison-Spielplan ueber die freie OpenLigaDB-API."""
url = f"https://api.openligadb.de/getmatchdata/bl1/{CONFIG['SEASON_YEAR']}"
try:
resp = requests.get(url, timeout=CONFIG["REQUEST_TIMEOUT"])
resp.raise_for_status()
data = resp.json()
return data if isinstance(data, list) else []
except Exception as e:
print(f"⚠️ OpenLigaDB-Spielplan konnte nicht geladen werden: {e}")
return []

def get_team_key(team_name):
"""Matched einen OpenLigaDB-Teamnamen auf einen der 18 festen Team-Keys."""
if not team_name:
return None
name_lower = team_name.lower()
if "köln" in name_lower or "koeln" in name_lower:
return "koeln"
for key in BUNDESLIGA_TEAM_KEYS:
if key in name_lower:
return key
return None

def get_upcoming_opponents(fixtures, team_key, count):
upcoming = []
for m in fixtures:
if m.get("matchIsFinished"):
continue
t1 = (m.get("team1") or {}).get("teamName", "")
t2 = (m.get("team2") or {}).get("teamName", "")
k1 = get_team_key(t1)
k2 = get_team_key(t2)
if k1 == team_key and k2:
upcoming.append((m.get("matchDateTimeUTC", ""), k2, t2))
elif k2 == team_key and k1:
upcoming.append((m.get("matchDateTimeUTC", ""), k1, t1))
upcoming.sort(key=lambda x: x[0])
return upcoming[:count]

def fixture_difficulty(fixtures, team_key):
"""Bewertet das Restprogramm anhand der Vorsaison-Staerke der naechsten Gegner."""
if not team_key or not fixtures:
return None
opponents = get_upcoming_opponents(fixtures, team_key, CONFIG["FIXTURE_LOOKAHEAD"])
if not opponents:
return None
strengths = [TEAM_STRENGTH_LAST_SEASON.get(k, TEAM_STRENGTH_DEFAULT) for _, k, _ in opponents]
avg_strength = sum(strengths) / len(strengths)
opponent_names = [name.split()[-1] for _, _, name in opponents if name]
if avg_strength <= 35:
label = "leicht"
elif avg_strength <= 55:
label = "mittel"
else:
label = "schwer"
return {"label": label, "opponents": opponent_names}

# ==========================================
# KNOTEN 4: HISTORY & PROGNOSE ENGINE
# ==========================================
def load_history():
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
return

try:
with open(CONFIG["HISTORY_FILE"], "w", encoding="utf-8") as f:
f.write(content_str)
print("✅ Verlauf lokal gespeichert.")
except Exception as e:
print(f"⚠️ History Speicherung fehlgeschlagen: {e}")

def calculate_real_daily_trend(player_id, current_mv, history):
player_id_str = str(player_id)
player_hist = history.get(player_id_str, {})

mv_history = player_hist.get("mv_history")
if mv_history is None:
old_mv = player_hist.get("mv")
mv_history = [old_mv] if old_mv is not None and old_mv > 0 else []
else:
mv_history = list(mv_history)

if len(mv_history) >= 2:
deltas = sorted(mv_history[i] - mv_history[i - 1] for i in range(1, len(mv_history)))
daily_trend = deltas[len(deltas) // 2]
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

def evaluate_player(player, history, starters, injured_list, my_team_counts=None, blocked_teams=None, my_budget=None, fixture_info=None):
player_id = player.get("id", player.get("i"))
tid = player.get("tid")

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

my_team_counts = my_team_counts or {}
blocked_teams = blocked_teams or {}
violates_my_limit = tid is not None and my_team_counts.get(tid, 0) >= CONFIG["MAX_PER_TEAM"]
opponents_with_team_blocked = blocked_teams.get(tid, []) if tid is not None else []
budget_exceeded = my_budget is not None and max_bid > my_budget

recommendation = "ABWARTEN"
if raw_profit > 1_500_000 and not is_injured:
recommendation = "KAUF JETZT (High Profit)"
elif daily_trend > 0:
recommendation = "BEOBACHTEN"
if is_injured:
recommendation = "RISIKO (Verletzt/Angeschlagen)"
if violates_my_limit:
recommendation = "BLOCKIERT (2-Spieler-Regel)"
elif budget_exceeded:
recommendation = f"{recommendation} - Budget reicht nicht"

return {
"id": player_id,
"name": name,
"mv": mv,
"proj_mv": proj_mv,
"max_bid": max_bid,
"exp_profit": raw_profit,
"is_starter": is_starter,
"is_injured": is_injured,
"violates_my_limit": violates_my_limit,
"opponents_with_team_blocked": opponents_with_team_blocked,
"budget_exceeded": budget_exceeded,
"recommendation": recommendation,
"fixture_difficulty": fixture_info["label"] if fixture_info else None,
"fixture_opponents": fixture_info["opponents"] if fixture_info else []
}

# ==========================================
# KNOTEN 5: TELEGRAM DISPATCHER & SPLITTER
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

token, league_id, my_manager_id = kickbase_login()
raw_players = get_market_players(token, league_id)
print(f"📊 {len(raw_players)} Spieler auf dem Transfermarkt gefunden.")

starters = get_ligainsider_lineups()
injured = get_ligainsider_injuries()
history, history_sha = load_history()

my_budget = get_my_budget(token, league_id)
if my_budget is not None:
print(f"💳 Verfuegbares Budget: {my_budget:,}".replace(",", ".") + " €")
else:
print("⚠️ Budget konnte nicht ermittelt werden - Budget-Warnungen sind deaktiviert.")

ranking_res = get_ranking(token, league_id)
my_team_counts = get_my_team_counts(token, league_id, my_manager_id)
blocked_teams = build_blocked_teams(token, league_id, ranking_res, my_manager_id)

season_fixtures = get_season_fixtures()
fixture_cache = {}
unmapped_tids_seen = set()

evaluated_list = []
report_lines = [f"⚽ <b>Kickbase Markt-Report ({CONFIG['DAYS_UNTIL_MATCHDAY']} Tage bis Spieltag 1)</b>\n"]
if my_budget is not None:
budget_str = f"{my_budget:,}".replace(",", ".")
report_lines.append(f"💳 <b>Verfuegbares Budget:</b> {budget_str} €\n")

for p in raw_players:
tid_str = str(p.get("tid")) if p.get("tid") else None
team_key = KICKBASE_TID_TO_TEAM.get(tid_str) if tid_str else None
if tid_str and not team_key and tid_str not in unmapped_tids_seen:
print(f"ℹ️ Team-ID {tid_str} noch nicht zugeordnet (Spieler: {p.get('n')}) - kein Restprogramm fuer diesen Verein.")
unmapped_tids_seen.add(tid_str)

if team_key and team_key not in fixture_cache:
fixture_cache[team_key] = fixture_difficulty(season_fixtures, team_key)
fixture_info = fixture_cache.get(team_key) if team_key else None

res = evaluate_player(p, history, starters, injured, my_team_counts, blocked_teams, my_budget, fixture_info)
evaluated_list.append(res)

profit_formatted = f"+{res['exp_profit']:,}".replace(",", ".")
mv_formatted = f"{res['mv']:,}".replace(",", ".")
proj_formatted = f"{res['proj_mv']:,}".replace(",", ".")
bid_formatted = f"{res['max_bid']:,}".replace(",", ".")

status_icons = "✅ S11" if res["is_starter"] else "❓ Rotation"
if res["is_injured"]:
status_icons += " | 🚑 Verletzt"

extra_lines = ""
if res["fixture_difficulty"]:
opp_str = ", ".join(res["fixture_opponents"]) if res["fixture_opponents"] else "?"
extra_lines += f" 📅 Restprogramm: {res['fixture_difficulty'].capitalize()} (naechste Gegner: {opp_str})\n"
if res["violates_my_limit"]:
extra_lines += " 🚫 Ueberschreitet deine 2-Spieler-Regel\n"
if res["opponents_with_team_blocked"]:
names = ", ".join(res["opponents_with_team_blocked"])
extra_lines += f" ℹ️ Verein bei Gegnern schon voll: {names}\n"
if res["budget_exceeded"]:
extra_lines += " 🚫 Max. Gebot uebersteigt dein Budget\n"

report_lines.append(
f"• <b>{res['name']}</b> | MW: {mv_formatted} €\n"
f" 📈 Proj. MW (MD1): {proj_formatted} € ({profit_formatted} €)\n"
f" 🎯 Max. Gebot: <b>{bid_formatted} €</b> ({status_icons})\n"
f" 💡 Empfehlung: <b>{res['recommendation']}</b>\n"
f"{extra_lines}"
)

save_history(history, history_sha)

full_report = "\n".join(report_lines)

send_telegram_messages(full_report)
print("✅ Pipeline-Durchlauf vollkommen erfolgreich beendet!")

if __name__ == "__main__":
main()
