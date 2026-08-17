def kickbase_login():
    """ Authentifiziert den Bot bei Kickbase v4 und gibt Token sowie Liga-ID zurück. """
    if not KB_EMAIL or not KB_PASSWORD:
        raise ValueError("❌ KB_EMAIL oder KB_PASSWORD fehlt in den GitHub Repository Secrets!")
    
    url = "https://api.kickbase.com/v4/user/login"
    payload = {
        "email": KB_EMAIL.strip(),
        "password": KB_PASSWORD.strip()
    }
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }
    
    resp = requests.post(url, json=payload, headers=headers, timeout=CONFIG["REQUEST_TIMEOUT"])
    
    if resp.status_code == 401:
        raise ValueError("❌ Login fehlgeschlagen (401 Unauthorized): Falsche E-Mail oder Passwort in den GitHub Secrets hinterlegt!")
    
    resp.raise_for_status()
    data = resp.json()
    
    token = data.get("token")
    leagues = data.get("leagues", [])
    
    if not token or not leagues:
        raise ValueError("Login-Antwort erhalten, aber kein Token oder keine aktive Liga gefunden.")
    
    league_id = leagues[0].get("id")
    print(f"✅ Kickbase Login erfolgreich. Liga-ID: {league_id}")
    return token, league_id
