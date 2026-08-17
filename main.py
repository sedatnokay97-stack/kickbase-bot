# -----------------------------------------------------------------
    # TEMPORÄRER DEBUG-BLOCK: Startelf-Wahrscheinlichkeit erkunden
    # (Nutzt den eigenen Kader statt Markt, damit es unabhängig davon
    # funktioniert, ob gerade Spieler auf dem Markt sind.)
    # -----------------------------------------------------------------
    try:
        my_squad_res = session.get(
            f"https://api.kickbase.com/v4/leagues/{liga_id}/managers/{my_manager_id}/squad",
            timeout=CONFIG["REQUEST_TIMEOUT"],
        )
        my_squad_items = my_squad_res.json().get("it", [])
        debug_player_id = my_squad_items[0].get("id") if my_squad_items else None
        print("DEBUG: Anzahl eigener Kaderspieler:", len(my_squad_items))
    except Exception as e:
        debug_player_id = None
        print("Fehler beim Laden des eigenen Kaders fuer Debug:", e)

    if debug_player_id:
        try:
            r1 = session.get(
                f"https://api.kickbase.com/v4/leagues/{liga_id}/players/{debug_player_id}/performance",
                timeout=CONFIG["REQUEST_TIMEOUT"],
            )
            print("PERFORMANCE:", r1.status_code, r1.text[:1500])
        except Exception as e:
            print("Fehler performance:", e)

        try:
            r2 = session.get(
                f"https://api.kickbase.com/v4/leagues/{liga_id}/teamcenter/myeleven",
                timeout=CONFIG["REQUEST_TIMEOUT"],
            )
            print("MYELEVEN:", r2.status_code, r2.text[:1500])
        except Exception as e:
            print("Fehler myeleven:", e)
    else:
        print("DEBUG: Kein Spieler im eigenen Kader gefunden - Debug-Block übersprungen.")
    # -----------------------------------------------------------------
    # ENDE DEBUG-BLOCK
    # -----------------------------------------------------------------
