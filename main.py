"""
Kickbase Bot Engine - Mit Budget-Tracker Erweiterung
"""

import json
import os
import re
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
import requests
from bs4 import BeautifulSoup

# ============================================================================
# KONFIGURATION
# ============================================================================

# Login-Bonus-Staffelung laut Kickbase Help Center
LOGIN_BONUS_STAFFELUNG = {
    1: 10_000,
    2: 20_000,
    3: 30_000,
    4: 40_000,
    5: 50_000,
    6: 60_000,
    7: 70_000,
    8: 80_000,
    9: 90_000,
}
LOGIN_BONUS_AB_TAG_10 = 100_000  # Ab Tag 10 konstant

# Liga-Startdatum
LIGA_START_DATUM = datetime(2026, 8, 15)  # 15.08.2026

# Startbudget pro Team
STARTBUDGET = 50_000_000  # 50 Mio €

# ============================================================================
# HILFSFUNCTIONEN FUR BUDGET-TRACKING
# ============================================================================

def calculate_login_bonus(days_active: int) -> int:
    """Berechnet kumulativen Login-Bonus pro Spieler"""
    total = 0
    for day in range(1, days_active + 1):
        if day <= 9:
            total += LOGIN_BONUS_STAFFELUNG[day]
        else:
            total += LOGIN_BONUS_AB_TAG_10
    return total


def get_days_since_liga_start() -> int:
    """Berechnet Anzahl Tage seit Liga-Start"""
    today = datetime.now()
    delta = today - LIGA_START_DATUM
    return max(1, delta.days + 1)


def format_money(amount: float) -> str:
    """Formatiert Geldbetrage (z.B. 1234567 -> '1,23 Mio €')"""
    if amount >= 1_000_000:
        return f"{amount / 1_000_000:.2f} Mio €"
    elif amount >= 1_000:
        return f"{amount / 1_000:.1f}k €"
    else:
        return f"{int(amount)} €"


# ============================================================================
# HIER GEHT DEIN BESTEHENDER CODE WEITER
# (Alle deine bestehenden Funktionen wie gewohnt)
# ============================================================================

# ... (dein bestehender Code: get_market_players, parse_feed, etc.) ...

# Beispiel-Platzhalter (durch deine echten Funktionen ersetzen):
def get_market_players():
    """Deine bestehende Funktion"""
    return []  # Placeholder

def parse_feed():
    """Deine bestehende Funktion"""
    return []  # Placeholder

def get_opponent_squads():
    """Deine bestehende Funktion - gibt Squads der Gegner zuruck"""
    return {}  # Placeholder: {username: [player_ids]}

# ============================================================================
# ERWEITERTES KONKURRENZ-RADAR MIT BUDGET
# ============================================================================

def calculate_opponent_finances(transfer_summary: Dict, opponent_squads: Dict, days_active: int) -> Dict:
    """
    Berechnet Budget und Teamwert pro Gegner.
    
    Formel: budget = 50 Mio + (login_bonus * spieler) - kaufe + verkaufe
    """
    finances = {}
    login_bonus_pro_spieler = calculate_login_bonus(days_active)
    
    for username, squad in opponent_squads.items():
        transfer_data = transfer_summary.get(username, {'kauf_summe': 0, 'verkauf_summe': 0})
        
        budget = STARTBUDGET + (login_bonus_pro_spieler * len(squad)) - transfer_data.get('kauf_summe', 0) + transfer_data.get('verkauf_summe', 0)
        team_value = 100_000_000  # Placeholder - hier echte Marktwerte einsetzen
        
        finances[username] = {
            'budget': budget,
            'team_value': team_value,
            'spieler_count': len(squad),
        }
    
    return finances


# ============================================================================
# MAIN (Dein bestehender Code + Budget-Erweiterung)
# ============================================================================

def main():
    """Dein bestehender main-Code"""
    print("🚀 Starte Kickbase Bot Engine...")
    
    # Tage seit Liga-Start
    days_active = get_days_since_liga_start()
    print(f"📅 Liga-Tag: {days_active}")
    
    # Dein bestehender Code
    market_players = get_market_players()
    transfers = parse_feed()
    opponent_squads = get_opponent_squads()
    
    # Budget-Tracking (NEU)
    transfer_summary = {}  # Deine bestehende Logik
    opponent_finances = calculate_opponent_finances(transfer_summary, opponent_squads, days_active)
    
    # Konkurrenz-Radar mit Budget (NEU)
    print("\n🕵️ Konkurrenz-Radar")
    for username, data in sorted(opponent_finances.items(), key=lambda x: x[1]['budget'], reverse=True):
        print(f"• {username}: {data['spieler_count']} Spieler · Budget: {format_money(data['budget'])} · Teamwert: {format_money(data['team_value'])}")
    
    print("\n✅ Pipeline erfolgreich!")


if __name__ == "__main__":
    main()
