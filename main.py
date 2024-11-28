# Datei: bot.py
import discord
from discord.ext import commands, tasks
from discord import app_commands
import asyncio
import asyncpg
import os
from dotenv import load_dotenv
from datetime import datetime, timedelta
from flask_app import keep_alive
from discord_presence import update_presence
from flask import Flask
import threading


from asyncpg.pool import create_pool

async def get_connection():
    pool = await create_pool(dsn='your_database_dsn', statement_cache_size=0)
    return pool


# Umgebungsvariablen laden
load_dotenv()

# Discord-Bot konfigurieren
intents = discord.Intents.default()
intents.messages = True
intents.message_content = True
intents.guilds = True
intents.members = True
intents.presences = True
intents.voice_states = True

bot = commands.Bot(command_prefix="!", intents=intents)

# Globale Datenbankkonfiguration und Verbindungspool
DATABASE_CONFIG = {
    "user": os.getenv("DB_USER", "your_user"),
    "password": os.getenv("DB_PASSWORD", "your_password"),
    "database": os.getenv("DB_NAME", "your_database"),
    "host": os.getenv("DB_HOST", "127.0.0.1"),
    "port": os.getenv("DB_PORT", "5432"),
}

pool = None  # Connection-Pool

async def init_db_pool():
    global pool
    try:
        pool = await asyncpg.create_pool(
            **DATABASE_CONFIG,
            max_size=10,  # Maximale Verbindungen
            statement_cache_size=0  # Deaktiviert vorbereitete Statements
        )
        print("Datenbank-Pool erfolgreich initialisiert.")
    except Exception as e:
        print(f"Fehler beim Initialisieren des Datenbank-Pools: {e}")


async def get_db_connection():
    global pool
    if not pool:
        await init_db_pool()
    return await pool.acquire()

async def release_db_connection(conn):
    global pool
    if pool and conn:
        await pool.release(conn)

# Tabelle sicherstellen
async def ensure_table_exists():
    conn = await get_db_connection()
    try:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id SERIAL PRIMARY KEY,
                discord_id BIGINT UNIQUE NOT NULL,
                username VARCHAR(100),
                score INT DEFAULT 0,
                last_daily TIMESTAMP
            );
        """)
        print("Tabelle 'users' ist vorhanden oder wurde erstellt.")
    except Exception as e:
        print(f"Fehler beim Erstellen der Tabelle: {e}")
    finally:
        await release_db_connection(conn)

# Benutzer-Synchronisierung
async def sync_members():
    conn = await get_db_connection()
    try:
        guild = bot.guilds[0]  # Zugriff auf die erste Gilde
        for member in guild.members:
            await conn.execute("""
                INSERT INTO users (discord_id, username)
                VALUES ($1, $2)
                ON CONFLICT (discord_id) DO NOTHING;
            """, member.id, member.name)
        print("Bestehende Mitglieder synchronisiert.")
    finally:
        await release_db_connection(conn)

@bot.event
async def on_member_join(member):
    conn = await get_db_connection()
    try:
        await conn.execute("""
            INSERT INTO users (discord_id, username)
            VALUES ($1, $2)
            ON CONFLICT (discord_id) DO NOTHING;
        """, member.id, member.name)
        print(f"Neues Mitglied {member.mention} hinzugefügt.")
    finally:
        await release_db_connection(conn)


# Logging-Funktion für Punkteaktivitäten
async def log_points_activity(message: str):
    channel_id = 1311449644571824208  # ID des Logging-Channels
    channel = bot.get_channel(channel_id)
    if channel:
        await channel.send(message)

# Slash-Command: Punkte anzeigen
@bot.tree.command(name="punkte", description="Zeigt deine aktuellen Punkte an.")
async def show_points(interaction: discord.Interaction):
    conn = await get_db_connection()
    try:
        result = await conn.fetchrow("SELECT score FROM users WHERE discord_id = $1", interaction.user.id)
        if result:
            await interaction.response.send_message(f"Du hast aktuell {result['score']} Punkte.")
        else:
            await interaction.response.send_message("Du hast noch kein Konto.")
    finally:
        await release_db_connection(conn)

# Slash-Command: Tägliche Punkte abholen
@bot.tree.command(name="daily", description="Hole deine täglichen Punkte ab.")
async def daily_points(interaction: discord.Interaction):
    conn = await get_db_connection()
    try:
        user = await conn.fetchrow("SELECT score, last_daily FROM users WHERE discord_id = $1", interaction.user.id)
        if user:
            last_daily = user['last_daily']
            now = datetime.utcnow()
            if last_daily is None or now - last_daily > timedelta(days=1):
                await conn.execute("""
                    UPDATE users
                    SET score = score + 1000, last_daily = $1
                    WHERE discord_id = $2
                """, now, interaction.user.id)
                await interaction.response.send_message("Du hast 1000 Punkte erhalten!")
                await log_points_activity(f"{interaction.user.mention} hat 1000 Punkte durch den Daily-Bonus erhalten.")
            else:
                next_claim = last_daily + timedelta(days=1)
                await interaction.response.send_message(
                    f"Du kannst deine Punkte erst wieder am {next_claim.strftime('%Y-%m-%d %H:%M:%S')} UTC abholen.",
                    ephemeral=True
                )
        else:
            await interaction.response.send_message("Du hast noch kein Konto.")
    finally:
        await release_db_connection(conn)

# Slash-Command: Punkte an andere Nutzer senden
@bot.tree.command(name="give", description="Sende Punkte an einen anderen Nutzer.")
async def give_points(interaction: discord.Interaction, member: discord.Member, points: int):
    if points <= 0:
        await interaction.response.send_message("Du kannst nur positive Punkte senden.", ephemeral=True)
        return

    conn = await get_db_connection()
    try:
        # Prüfen, ob der Nutzer genug Punkte hat
        sender = await conn.fetchrow("SELECT score FROM users WHERE discord_id = $1", interaction.user.id)
        if sender and sender['score'] >= points:
            # Punkte übertragen
            await conn.execute("""
                UPDATE users
                SET score = score - $1
                WHERE discord_id = $2
            """, points, interaction.user.id)

            await conn.execute("""
                UPDATE users
                SET score = score + $1
                WHERE discord_id = $2
            """, points, member.id)

            await interaction.response.send_message(
                f"Du hast erfolgreich {points} Punkte an {member.mention} gesendet!"
            )
            await log_points_activity(
                f"{interaction.user.mention} hat {points} Punkte an {member.mention} gesendet."
            )
        else:
            await interaction.response.send_message("Du hast nicht genug Punkte, um diese zu senden.", ephemeral=True)
    finally:
        await release_db_connection(conn)

# Punkte für Nachrichten vergeben
@bot.event
async def on_message(message):
    if message.author.bot:
        return
    conn = await get_db_connection()
    try:
        await conn.execute("""
            UPDATE users
            SET score = score + 10
            WHERE discord_id = $1
        """, message.author.id)
        await log_points_activity(f"{message.author.mention} hat 10 Punkte für eine Nachricht erhalten.")
    finally:
        await release_db_connection(conn)

# Punkte für Voice-Chat-Aktivität
@tasks.loop(minutes=5)
async def award_voice_points():
    guild = bot.guilds[0]
    conn = await get_db_connection()
    try:
        for member in guild.members:
            if member.voice and member.voice.channel:  # Überprüfen, ob der User im Voice-Chat ist
                await conn.execute("""
                    UPDATE users
                    SET score = score + 50
                    WHERE discord_id = $1
                """, member.id)
                await log_points_activity(f"{member.mention} hat 50 Punkte für Voice-Chat-Aktivität erhalten.")
    finally:
        await release_db_connection(conn)

# Slash-Command: Manuelles Synchronisieren der Slash-Commands
@bot.tree.command(name="sync", description="Synchronisiert alle Slash-Commands mit Discord.")
@app_commands.checks.has_permissions(administrator=True)
async def sync_commands(interaction: discord.Interaction):
    try:
        synced = await bot.tree.sync()
        await interaction.response.send_message(f"Erfolgreich {len(synced)} Slash-Commands synchronisiert!", ephemeral=True)
    except Exception as e:
        await interaction.response.send_message(f"Fehler beim Synchronisieren: {e}", ephemeral=True)

# Fehlerbehandlung für fehlende Berechtigungen
@sync_commands.error
async def sync_commands_error(interaction: discord.Interaction, error):
    if isinstance(error, app_commands.MissingPermissions):
        await interaction.response.send_message("Fehler: Du benötigst Administratorrechte, um diesen Befehl zu verwenden.", ephemeral=True)
    else:
        await interaction.response.send_message(f"Ein Fehler ist aufgetreten: {error}", ephemeral=True)

# Slash-Command: Punkte für einen Nutzer durch einen Admin ändern
@bot.tree.command(name="modify_points", description="Admins können Punkte zu einem Nutzer hinzufügen oder entfernen.")
@app_commands.checks.has_permissions(administrator=True)
async def modify_points(interaction: discord.Interaction, member: discord.Member, points: int):
    # Antwort sofort senden, um die Interaktion zu bestätigen
    await interaction.response.defer(ephemeral=True)  # Interaktion wird sofort als "im Gange" markiert

    conn = await get_db_connection()
    try:
        result = await conn.execute(
            "UPDATE users SET score = score + $1 WHERE discord_id = $2", points, member.id
        )
        if result == "UPDATE 0":
            await interaction.followup.send(f"Benutzer {member.mention} nicht gefunden.")
        else:
            if points > 0:
                await interaction.followup.send(f"{points} Punkte wurden zu {member.mention} hinzugefügt!")
                await log_points_activity(f"{interaction.user.mention} hat {points} Punkte zu {member.mention} hinzugefügt.")
            else:
                await interaction.followup.send(f"{abs(points)} Punkte wurden von {member.mention} entfernt!")
                await log_points_activity(f"{interaction.user.mention} hat {abs(points)} Punkte von {member.mention} entfernt.")
    finally:
        await release_db_connection(conn)


# Bot starten
@bot.event
async def on_ready():
    print(f"Bot {bot.user} ist online.")
    await ensure_table_exists()
    await sync_members()
    award_voice_points.start()
    await update_presence(bot)
    print("Bot ist bereit und alle Hintergrundaufgaben wurden gestartet.")

# Flask Setup
app = Flask(__name__)

@app.route('/')
def home():
    return "Bot ist online!"

def run_flask():
    app.run(port=12000)

# Bot und Flask in separaten Threads ausführen
if __name__ == '__main__':
    # Starte Flask in einem Thread
    flask_thread = threading.Thread(target=run_flask)
    flask_thread.daemon = True  # Sicherstellen, dass der Flask-Thread beendet wird, wenn das Hauptprogramm stoppt
    flask_thread.start()

    # Starte den Discord Bot
    bot.run(os.getenv("DISCORD_TOKEN"))