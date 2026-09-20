import os
import urllib.request
import urllib.error
import json

api_key = os.getenv("OPENROUTER_API_KEY", "")
if not api_key:
    cfg_file = os.path.join(os.path.dirname(__file__), "..", "bot_accounts_config.json")
    if os.path.exists(cfg_file):
        try:
            with open(cfg_file) as f:
                api_key = json.load(f).get("openrouter", {}).get("api_key", "")
        except Exception:
            pass

candidates = [
    "z-ai/glm-5.2:free",
    "google/gemma-4-31b-it:free",
    "google/gemma-4-26b-a4b-it:free",
    "inclusionai/ling-3.0-flash-fin:free",
    "nvidia/nemotron-3.5-lightning:free",
    "liquid/lfm-2.5-2.6b:free"
]

for m in candidates:
    print(f"Testing {m}...")
    body = {
        "model": m,
        "messages": [{"role": "user", "content": "You are a quantitative trader. Reply with 'ONLINE: Institutional trading desk ready.'"}]
    }
    req = urllib.request.Request(
        "https://openrouter.ai/api/v1/chat/completions",
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/RyzEnHunTer/Dc-Bot",
            "X-Title": "DCC Trade Forensics"
        },
        method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=12) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            print(f"  >>> SUCCESS with {m}! Output:")
            print("     ", data["choices"][0]["message"]["content"].strip())
            break
    except urllib.error.HTTPError as e:
        err = e.read().decode("utf-8", errors="ignore")
        print(f"  HTTP {e.code}: {err[:120]}...")
    except Exception as e:
        print(f"  Error: {e}")
