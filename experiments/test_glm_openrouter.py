import os
import urllib.request
import urllib.error
import json
import time

api_key = os.getenv("OPENROUTER_API_KEY", "")

def test_model(model_name):
    print(f"Testing {model_name}...")
    body = {
        "model": model_name,
        "messages": [
            {"role": "user", "content": "You are a quant trader. Reply in exactly 1 sentence confirming you are online."}
        ],
        "max_tokens": 100
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
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=12) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                content = data["choices"][0]["message"]["content"]
                print(f"  [SUCCESS] {model_name}: {content.strip()}")
                return True
        except urllib.error.HTTPError as e:
            err = e.read().decode("utf-8", errors="ignore")
            print(f"  [Attempt {attempt+1}] HTTP {e.code}: {err[:150]}...")
            time.sleep(3)
        except Exception as e:
            print(f"  [Attempt {attempt+1}] Error: {e}")
            time.sleep(3)
    return False

# Test Z.ai models and free fallbacks
models_to_test = [
    "z-ai/glm-5.2:free",
    "meta-llama/llama-3.3-70b-instruct:free",
    "meta-llama/llama-3.2-3b-instruct:free",
    "google/gemini-2.0-flash-lite-preview-02-05:free",
    "deepseek/deepseek-r1:free"
]

for m in models_to_test:
    if test_model(m):
        break
