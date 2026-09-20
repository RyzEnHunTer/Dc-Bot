import urllib.request
import re
import json

vids = ['-Na8aAiaSBY', 'zGF0OUNrW4M']
for vid in vids:
    url = f'https://www.youtube.com/watch?v={vid}'
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'})
    try:
        with urllib.request.urlopen(req) as resp:
            html = resp.read().decode('utf-8', errors='ignore')
            m_title = re.search(r'<title>(.*?)</title>', html)
            title = m_title.group(1) if m_title else 'No Title'
            print(f'=== Video {vid} ===')
            print('Title:', title)
            m_desc = re.search(r'"shortDescription":"(.*?)"', html)
            if m_desc:
                print('Desc:', m_desc.group(1)[:300])
    except Exception as e:
        print(vid, 'Error:', e)
