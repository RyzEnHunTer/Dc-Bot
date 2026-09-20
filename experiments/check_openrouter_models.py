import os
import urllib.request
import json

api_key = os.getenv("OPENROUTER_API_KEY", "")

req = urllib.request.Request(
    "https://openrouter.ai/api/v1/models",
    headers={"Authorization": f"Bearer {api_key}"}
)

try:
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read().decode("utf-8"))
        models = data.get("data", [])
        print(f"Total models available on OpenRouter: {len(models)}")
        
        matches = []
        for m in models:
            m_id = m.get("id", "").lower()
            m_name = m.get("name", "").lower()
            if "glm" in m_id or "glm" in m_name or "z-ai" in m_id or "z.ai" in m_name:
                matches.append(m)
        
        print(f"\nFound {len(matches)} matching model(s):")
        for m in matches:
            print(f"  • ID: {m.get('id')} | Name: {m.get('name')}")
            
except Exception as e:
    print("Error querying OpenRouter:", e)
