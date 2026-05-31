import httpx, json, re
r = httpx.get('http://127.0.0.1:4096/provider', timeout=5)
data = r.json()
text = json.dumps(data)
# Find big-pickle provider context
idx = text.find('big-pickle')
if idx > -1:
    print("bigPickle context:", text[max(0,idx-200):idx+100])
