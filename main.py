import os
import requests

def check_available_models():
    # Zieht den Key sicher aus deinen GitHub Secrets
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("Fehler: Kein API-Key gefunden.")
        return

    url = f"https://generativelanguage.googleapis.com/v1beta/models?key={api_key}"
    response = requests.get(url)
    
    if response.status_code == 200:
        models = response.json().get("models", [])
        print("✅ Folgende Modelle sind für deinen Key aktuell freigeschaltet:")
        for m in models:
            methods = m.get('supportedGenerationMethods', [])
            if "generateContent" in methods:
                print(f"  -> {m['name']}")
    else:
        print(f"❌ Abfrage gescheitert: HTTP {response.status_code}")
        print(response.text)

if __name__ == "__main__":
    check_available_models()
