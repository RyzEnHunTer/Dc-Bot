import json
import urllib.request
import os
import re

json_path = 'experiments/discord_trades_scraped.json'
out_dir = 'experiments/scraped_charts'
os.makedirs(out_dir, exist_ok=True)

with open(json_path, 'r', encoding='utf-8') as f:
    trades = json.load(f)

downloaded = 0
for t in trades:
    raw_user = t.get('user', 'unknown')
    user = re.sub(r'[^a-zA-Z0-9_-]', '_', raw_user).strip('_')
    trade_id = t.get('id', 'item')[-8:]
    media_list = t.get('media', [])
    
    for idx, url in enumerate(media_list):
        # We only need the original unresized images
        if 'attachments' in url:
            clean_url = url.split('?')[0]
            ext = '.png' if clean_url.endswith('.png') else ('.jpg' if clean_url.endswith('.jpg') else '.webp')
            # Avoid downloading duplicates (discord often includes both cdn and media URLs for same attachment)
            base_name = os.path.basename(clean_url)
            fname = f"{user}_{trade_id}_{base_name}"
            target_path = os.path.join(out_dir, fname)
            
            if not os.path.exists(target_path):
                try:
                    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'})
                    with urllib.request.urlopen(req, timeout=15) as resp:
                        content = resp.read()
                        with open(target_path, 'wb') as out_f:
                            out_f.write(content)
                    print(f"Downloaded: {fname} ({len(content)} bytes)")
                    downloaded += 1
                except Exception as e:
                    print(f"Failed {fname}: {e}")

print(f"\nDone! Successfully downloaded {downloaded} new chart images.")
