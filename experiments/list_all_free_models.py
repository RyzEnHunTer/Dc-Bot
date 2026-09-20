import os
import urllib.request
import json

api_key = os.getenv("OPENROUTER_API_KEY", "")

req = urllib.request.Request(
    "https://openrouter.ai/api/v1/models",
    headers={"Authorization": f"Bearer {api_key}"}
)

with urllib.request.urlopen(req, timeout=10) as resp:
    data = json.loads(resp.read().decode("utf-8"))
    free_models = [m["id"] for m in data.get("data", []) if ":free" in m.get("id", "")]

print(f"Total free models currently registered on OpenRouter: {len(free_models)}")
for fm in free_models:
    print(" -", fm)
