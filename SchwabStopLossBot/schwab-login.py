import base64, requests, webbrowser
APP_KEY = "VFtjTWvOCHWBl1QA7y1SC4DkD8eMzpyW9ctEV8A3uD4rxAlG"
APP_SECRET = "oGseH8WrKxoraW732k1ThIPdlrDawxy6lUj4pJaylyatJjaYExsPfI3BGpR0QEk5"
REDIRECT_URI = "https://127.0.0.1"
auth_url = f"https://api.schwabapi.com/v1/oauth/authorize?client_id={APP_KEY}&redirect_uri={REDIRECT_URI}"
webbrowser.open(auth_url)
returned_url = input("Paste Returned URL: ")
code = returned_url.split("code=")[1].split("%40")[0] + "@"
headers = {
   "Authorization": "Basic " + base64.b64encode(f"{APP_KEY}:{APP_SECRET}".encode()).decode(),
   "Content-Type": "application/x-www-form-urlencoded"
}
payload = {"grant_type": "authorization_code", "code": code, "redirect_uri": REDIRECT_URI}
tokens = requests.post("https://api.schwabapi.com/v1/oauth/token", headers=headers, data=payload).json()
print(tokens)