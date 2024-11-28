# flask_app.py
import os
import requests
from dotenv import load_dotenv
from flask import Flask, redirect, request, session
import threading
import discord
from discord.ext import commands



# Umgebungsvariablen laden
load_dotenv()

app = Flask(__name__)
app.secret_key = os.urandom(24)

# Konfiguration
CLIENT_ID = os.getenv('DISCORD_ID')
CLIENT_SECRET = os.getenv('DISCORD_CLIENT_SECRET')
DISCORD_TOKEN = os.getenv('DISCORD_TOKEN')
REDIRECT_URI = 'http://localhost:12000/callback'

SCOPE = 'identify email guilds guilds.members.read bot'

@app.route('/')
def index():
    return '<a href="/login">Mit Discord einloggen</a>'

@app.route('/keep_alive')
def keep_alive():
    return "Ich bin online!", 200

@app.route('/login')
def login():
    return redirect(
        f'https://discord.com/api/oauth2/authorize?client_id={CLIENT_ID}&redirect_uri={REDIRECT_URI}&response_type=code&scope={SCOPE}&prompt=consent'
    )

@app.route('/callback')
def callback():
    if 'error' in request.args:
        return f"Fehler: {request.args['error']}, Beschreibung: {request.args['error_description']}"

    code = request.args.get('code')

    token_response = requests.post('https://discord.com/api/oauth2/token', data={
        'client_id': CLIENT_ID,
        'client_secret': CLIENT_SECRET,
        'grant_type': 'authorization_code',
        'code': code,
        'redirect_uri': REDIRECT_URI,
        'scope': SCOPE,
    })

    if token_response.status_code != 200:
        return f'Fehler beim Abrufen des Access Tokens: {token_response.json()}'

    access_token = token_response.json().get('access_token')
    session['token'] = access_token

    user_response = requests.get('https://discord.com/api/v10/users/@me', headers={
        'Authorization': f'Bearer {access_token}'
    })

    user_data = user_response.json()
    if 'username' in user_data:
        return f'Benutzername: {user_data["username"]}, ID: {user_data["id"]}'
    else:
        return f'Fehler beim Abrufen der Benutzerdaten: {user_data}'

app = Flask(__name__)

@app.route('/')
def home():
    return "Bot ist online!"

def run_flask():
    port = int(os.environ.get("Port", 12000))
    app.run(host="0.0.0.0", port=port)

# Discord Bot Konfiguration
intents = discord.Intents.default()
intents.presences = True

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=12000)