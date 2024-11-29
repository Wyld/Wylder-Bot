# Datei: main.py
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
import asyncpg
from flask_app import run_flask
import logging
import random
from discord.ui import Button, View, Modal, TextInput

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger(__name__)

logger.info("Bot Wylder Bot#8351 ist online.")



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

DATABASE_CONFIG = {
    "user": os.getenv("DB_USER"),
    "password": os.getenv("DB_PASSWORD"),
    "database": os.getenv("DB_NAME"),
    "host": os.getenv("DB_HOST"),
    "port": int(os.getenv("DB_PORT", 6543)),  # Verwende DB_PORT hier
    "ssl": "require",
}



pool = None  # Connection-Pool

async def init_db_pool():
    global pool
    try:
        print("Initialisiere den Datenbank-Pool...")
        pool = await asyncpg.create_pool(
            user=DATABASE_CONFIG["user"],
            password=DATABASE_CONFIG["password"],
            database=DATABASE_CONFIG["database"],
            host=DATABASE_CONFIG["host"],
            port=DATABASE_CONFIG["port"],
            ssl="require",  # SSL hinzufügen
            max_size=10,
            statement_cache_size=0,
        )
        print("Datenbank-Pool erfolgreich initialisiert.")
    except Exception as e:
        print(f"Fehler beim Initialisieren des Datenbank-Pools: {e}")
        raise  # Weitergeben des Fehlers für Debugging




async def get_db_connection():
    global pool
    if not pool:
        print("Datenbank-Pool ist nicht initialisiert. Versuche, ihn zu initialisieren...")
        await init_db_pool()
    if not pool:
        raise RuntimeError("Datenbank-Pool konnte nicht initialisiert werden.")
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

async def test_db_connection():
    conn = await get_db_connection()
    try:
        # Test, ob Policies korrekt greifen
        test_query = await conn.fetchrow("SELECT * FROM users LIMIT 1")
        print(f"Testabfrage erfolgreich: {test_query}")
    except Exception as e:
        print(f"Fehler bei der Testabfrage: {e}")
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

# /ping Command (für alle Benutzer)
@bot.tree.command(name="ping")
async def ping(interaction: discord.Interaction):
    latency = round(bot.latency * 1000)  # Reaktionszeit in ms
    await interaction.response.send_message(f"Ping: {latency} ms", ephemeral=True)


# Roulette-Daten
ROULETTE_COLORS = {
    0: "Grün", 1: "Rot", 2: "Schwarz", 3: "Rot", 4: "Schwarz", 5: "Rot", 6: "Schwarz", 7: "Rot",
    8: "Schwarz", 9: "Rot", 10: "Schwarz", 11: "Schwarz", 12: "Rot", 13: "Schwarz", 14: "Rot",
    15: "Schwarz", 16: "Rot", 17: "Schwarz", 18: "Rot", 19: "Rot", 20: "Schwarz", 21: "Rot",
    22: "Schwarz", 23: "Rot", 24: "Schwarz", 25: "Rot", 26: "Schwarz", 27: "Rot", 28: "Schwarz",
    29: "Schwarz", 30: "Rot", 31: "Schwarz", 32: "Rot", 33: "Schwarz", 34: "Rot", 35: "Schwarz", 36: "Rot",
}

class CustomBetModal(Modal):
    def __init__(self, title: str, placeholders: dict):
        super().__init__(title=title)
        self.fields = {}
        for name, placeholder in placeholders.items():
            field = TextInput(
                label=name,
                style=discord.TextStyle.short,
                placeholder=placeholder,
                required=True
            )
            self.fields[name] = field
            self.add_item(field)

    async def on_submit(self, interaction: discord.Interaction):
        # Überprüfung der Eingaben abhängig vom Feldnamen
        for name, field in self.fields.items():
            value = field.value.strip()

            if name == "Einsatz":
                if not value.isdigit():
                    await interaction.response.send_message(
                        f"Ungültige Eingabe für {name}: Bitte gib eine gültige Zahl ein.", ephemeral=True
                    )
                    self.stop()  # Modal schließen bei Fehler
                    return

            elif name == "Farbe (Rot/Schwarz)":
                if value.lower() not in ["rot", "schwarz"]:
                    await interaction.response.send_message(
                        f"Ungültige Eingabe für {name}: Bitte gib 'Rot' oder 'Schwarz' ein.", ephemeral=True
                    )
                    self.stop()  # Modal schließen bei Fehler
                    return

            elif name == "Typ (Gerade/Ungerade)":
                if value.lower() not in ["gerade", "ungerade"]:
                    await interaction.response.send_message(
                        f"Ungültige Eingabe für {name}: Bitte gib 'Gerade' oder 'Ungerade' ein.", ephemeral=True
                    )
                    self.stop()  # Modal schließen bei Fehler
                    return

            elif name == "Zahl (0-36)":
                if not value.isdigit() or not (0 <= int(value) <= 36):
                    await interaction.response.send_message(
                        f"Ungültige Eingabe für {name}: Bitte gib eine Zahl zwischen 0 und 36 ein.", ephemeral=True
                    )
                    self.stop()  # Modal schließen bei Fehler
                    return

        # Erfolgreich validiert
        await interaction.response.send_message("Wette erfolgreich platziert!", ephemeral=True)
        self.stop()  # Modal korrekt schließen



class SetBetModal(Modal):
    def __init__(self, current_score: int, callback):
        super().__init__(title="Einsatz festlegen")
        self.current_score = current_score
        self.callback = callback
        self.amount = TextInput(
            label="Einsatz",
            style=discord.TextStyle.short,
            placeholder="Gib deinen Einsatz ein.",
            required=True
        )
        self.add_item(self.amount)

    async def on_submit(self, interaction: discord.Interaction):
        if not self.amount.value.isdigit():
            await interaction.response.send_message("Bitte gib eine gültige Zahl ein.", ephemeral=True)
            self.stop()
            return

        bet_amount = int(self.amount.value)
        if bet_amount > self.current_score:
            await interaction.response.send_message(
                f"Du kannst nicht mehr setzen, als du hast! Verfügbar: {self.current_score} Punkte.",
                ephemeral=True
            )
            self.stop()
        elif bet_amount <= 0:
            await interaction.response.send_message("Der Einsatz muss größer als 0 sein!", ephemeral=True)
            self.stop()
        else:
            await self.callback(interaction, bet_amount)  # Callback aufrufen
            self.stop()



class BetPhaseView(View):
    def __init__(self, user_id: int, current_score: int):
        super().__init__(timeout=300)
        self.user_id = user_id
        self.current_score = current_score
        self.bet_amount = 0

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Das ist nicht deine Runde!", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Einsatz festlegen", style=discord.ButtonStyle.primary)
    async def set_bet(self, interaction: discord.Interaction, button: Button):
        modal = SetBetModal(current_score=self.current_score, callback=self.set_bet_callback)
        await interaction.response.send_modal(modal)

    async def set_bet_callback(self, interaction: discord.Interaction, bet_amount: int):
        # Verarbeite den gesetzten Einsatz
        self.bet_amount = bet_amount
        await interaction.response.edit_message(
            content=f"Einsatz geändert: {self.bet_amount} Punkte.\nDrücke '🍀 Weiter', um zu spielen!",
            view=self
        )

        # Logge den neuen Einsatz
        logger.info(f"{interaction.user} hat einen Einsatz von {self.bet_amount} Punkten festgelegt.")
        await log_points_activity(f"🎡 {interaction.user} hat einen Einsatz von {self.bet_amount} Punkten festgelegt.")

        # Überprüfe, ob bet_amount eine gültige Zahl ist
        if bet_amount <= 0:
            await interaction.followup.send(
                content="Der Einsatz muss größer als 0 sein!",
                ephemeral=True
            )
            return

        if bet_amount > self.current_score:
            await interaction.followup.send(
                content=f"Du kannst nicht mehr setzen, als du hast! Verfügbar: {self.current_score} Punkte.",
                ephemeral=True
            )
            return

        await interaction.edit_original_response(
            content=f"Dein aktueller Einsatz: {self.bet_amount} Punkte\n"
                    f"Dein Kontostand: {self.current_score - self.bet_amount} Punkte\n"
                    f"Drücke auf 'Weiter', wenn du fertig bist!",
            view=self
        )

    @discord.ui.button(label="Weiter", style=discord.ButtonStyle.success)
    async def continue_to_bets(self, interaction: discord.Interaction, button: Button):
        if self.bet_amount <= 0:
            await interaction.response.send_message(
                "Setze zuerst deinen Einsatz, bevor du fortfährst!", ephemeral=True
            )
            return

        await interaction.response.edit_message(
            content=f"Einsatz festgelegt: {self.bet_amount} Punkte. Wähle deine Wetten:",
            view=WagerPhaseView(user_id=self.user_id, current_score=self.current_score, bet_amount=self.bet_amount),
        )


# Roulette-View für Wett-Phase
class WagerPhaseView(View):
    def __init__(self, user_id: int, current_score: int, bet_amount: int):
        super().__init__(timeout=900)  # Timeout auf 15 Minuten erhöhen
        self.user_id = user_id
        self.current_score = current_score
        self.bet_amount = bet_amount
        self.remaining_amount = bet_amount
        self.placed_bets = {}  # Gespeicherte Wetten (Key: Button, Value: Einsatz)

        # Clear Button hinzufügen
        self.clear_button = Button(label="Einsätze zurücksetzen", style=discord.ButtonStyle.danger)
        self.clear_button.callback = self.clear_bets
        self.add_item(self.clear_button)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Das ist nicht deine Runde!", ephemeral=True)
            return False
        return True

    async def handle_bet(self, interaction: discord.Interaction, label: str, placeholders: dict, multiplier: int):
        if label in self.placed_bets:
            await interaction.response.send_message("Dieser Button wurde bereits verwendet!", ephemeral=True)
            return

        # Überprüfen, ob bereits eine der speziellen Wetten (1st 12, 2nd 12, 3rd 12, 1-18, 19-36) gesetzt wurde
        special_bets = ["1st 12", "2nd 12", "3rd 12", "1-18", "19-36"]
        if any(bet in self.placed_bets for bet in special_bets):
            if label in special_bets:
                await interaction.response.send_message(
                    "Du kannst nur eine der folgenden Wetten gleichzeitig setzen: 1st 12, 2nd 12, 1-18 oder 19-36.",
                    ephemeral=True
                )
                return

        modal = CustomBetModal(title=f"{label} setzen", placeholders=placeholders)
        await interaction.response.send_modal(modal)
        await modal.wait()

        if modal.is_finished():  # Prüfen, ob das Modal korrekt geschlossen wurde
            bet_value = modal.fields["Einsatz"].value
            if not bet_value.isdigit():
                await interaction.followup.send(
                    content="Ungültiger Einsatz! Bitte gib eine gültige Zahl ein.",
                    ephemeral=True
                )
                return  # Kein Abzug von Punkten, wenn die Eingabe ungültig ist

            bet_amount = int(bet_value)
            if bet_amount > self.remaining_amount:
                await interaction.followup.send(
                    content=f"Ungültiger Einsatz: Du hast nur noch {self.remaining_amount} Punkte übrig!",
                    ephemeral=True
                )
                return  # Kein Abzug, wenn der Einsatz größer als der verbleibende Betrag ist

            # Nur gültige Einsätze speichern und abziehen
            self.remaining_amount -= bet_amount

            # Validierung für 'Farbe' oder 'Gerade/Ungerade' (diese Felder werden als Wörter validiert)
            if label == "Farbe":
                value = modal.fields["Farbe (Rot/Schwarz)"].value.strip().lower()
                if value not in ["rot", "schwarz"]:
                    await interaction.followup.send(
                        "Ungültige Farbe! Bitte gib 'Rot' oder 'Schwarz' ein.",
                        ephemeral=True
                    )
                    return  # Keine Wette setzen, wenn ungültige Eingabe

            elif label == "Gerade/Ungerade":
                value = modal.fields["Typ (Gerade/Ungerade)"].value.strip().lower()
                if value not in ["gerade", "ungerade"]:
                    await interaction.followup.send(
                        "Ungültiger Typ! Bitte gib 'Gerade' oder 'Ungerade' ein.",
                        ephemeral=True
                    )
                    return  # Keine Wette setzen, wenn ungültige Eingabe

            elif label in special_bets:
                # Wette für 1st 12, 2nd 12, 3rd 12, 1-18, 19-36
                value = label  # Hier wird der Label-Wert als Wert gespeichert
            else:
                # **Werteingabe für Zahl (0-36) validieren** (nur hier erfolgt die Zahlüberprüfung)
                value = modal.fields["Zahl (0-36)"].value.strip()

                # Überprüfen, ob die Zahl im Bereich 0-36 liegt
                if not value.isdigit() or not (0 <= int(value) <= 36):
                    await interaction.followup.send(
                        "Ungültige Zahl! Bitte gib eine Zahl zwischen 0 und 36 ein.",
                        ephemeral=True
                    )
                    return  # Keine Wette setzen, wenn ungültige Zahl eingegeben wurde

            # Wette setzen und Whisper Nachricht nur bei erfolgreicher Eingabe senden
            self.placed_bets[label] = {
                "amount": bet_amount,
                "multiplier": multiplier,
                "value": value
            }

            # Nur erfolgreich gesetzte Wetten anzeigen (mit korrektem Betrag und Status)
            await interaction.edit_original_response(
                content=f"{label} wurde mit {bet_amount} Punkten gesetzt. Verbleibend: {self.remaining_amount} Punkte",
                view=self
            )

    @discord.ui.button(label="Zahl", style=discord.ButtonStyle.success)
    async def bet_on_number(self, interaction: discord.Interaction, button: Button):
        await self.handle_bet(
            interaction,
            "Zahl",
            {"Zahl (0-36)": "Gib die Zahl ein", "Einsatz": "Gib deinen Einsatz ein"},
            multiplier=35
        )

    @discord.ui.button(label="Farbe", style=discord.ButtonStyle.primary)
    async def bet_on_color(self, interaction: discord.Interaction, button: Button):
        await self.handle_bet(
            interaction,
            "Farbe",
            {"Farbe (Rot/Schwarz)": "Gib die Farbe ein", "Einsatz": "Gib deinen Einsatz ein"},
            multiplier=2
        )

    @discord.ui.button(label="Gerade/Ungerade", style=discord.ButtonStyle.danger)
    async def bet_on_parity(self, interaction: discord.Interaction, button: Button):
        await self.handle_bet(
            interaction,
            "Gerade/Ungerade",
            {"Typ (Gerade/Ungerade)": "Gib deinen Typ ein", "Einsatz": "Gib deinen Einsatz ein"},
            multiplier=2
        )

    @discord.ui.button(label="1st 12", style=discord.ButtonStyle.secondary)
    async def bet_on_first_12(self, interaction: discord.Interaction, button: Button):
        await self.handle_bet(interaction, "1st 12", {"Einsatz": "Gib deinen Einsatz ein"}, multiplier=3)

    @discord.ui.button(label="2nd 12", style=discord.ButtonStyle.secondary)
    async def bet_on_second_12(self, interaction: discord.Interaction, button: Button):
        await self.handle_bet(interaction, "2nd 12", {"Einsatz": "Gib deinen Einsatz ein"}, multiplier=3)

    @discord.ui.button(label="3rd 12", style=discord.ButtonStyle.secondary)
    async def bet_on_third_12(self, interaction: discord.Interaction, button: Button):
        await self.handle_bet(interaction, "3rd 12", {"Einsatz": "Gib deinen Einsatz ein"}, multiplier=3)

    @discord.ui.button(label="1-18", style=discord.ButtonStyle.secondary)
    async def bet_on_1_to_18(self, interaction: discord.Interaction, button: Button):
        await self.handle_bet(interaction, "1-18", {"Einsatz": "Gib deinen Einsatz ein"}, multiplier=2)

    @discord.ui.button(label="19-36", style=discord.ButtonStyle.secondary)
    async def bet_on_19_to_36(self, interaction: discord.Interaction, button: Button):
        await self.handle_bet(interaction, "19-36", {"Einsatz": "Gib deinen Einsatz ein"}, multiplier=2)

    @discord.ui.button(label="Spiel starten", style=discord.ButtonStyle.success)
    async def play_game(self, interaction: discord.Interaction, button: Button):
        if self.remaining_amount > 0:
            await interaction.response.send_message(
                f"Setze zuerst den gesamten Betrag! Verbleibend: {self.remaining_amount} Punkte.", ephemeral=True
            )
            return

        # Roulette-Ergebnis
        result_number = random.randint(0, 36)
        result_color = ROULETTE_COLORS[result_number]
        result_parity = "Gerade" if result_number % 2 == 0 else "Ungerade"

        winnings = 0
        results_summary = []

        # Gewinne/Verluste berechnen
        for bet, details in self.placed_bets.items():
            won = False
            if bet == "Zahl" and details["value"] == str(result_number):
                won = True
                winnings += details["amount"] * details["multiplier"]
            elif bet == "Farbe" and details["value"].casefold() == result_color.casefold():
                won = True
                winnings += details["amount"] * details["multiplier"]
            elif bet == "Gerade/Ungerade" and details["value"].casefold() == result_parity.casefold():
                won = True
                winnings += details["amount"] * details["multiplier"]
            elif bet == "1st 12" and 1 <= result_number <= 12:
                won = True
                winnings += details["amount"] * 3
            elif bet == "2nd 12" and 13 <= result_number <= 24:
                won = True
                winnings += details["amount"] * 3
            elif bet == "3rd 12" and 25 <= result_number <= 36:
                won = True
                winnings += details["amount"] * 3
            elif bet == "1-18" and 1 <= result_number <= 18:
                won = True
                winnings += details["amount"] * 2
            elif bet == "19-36" and 19 <= result_number <= 36:
                won = True
                winnings += details["amount"] * 2

            results_summary.append(
                f"{bet}: {'Gewonnen' if won else 'Verloren'} - Einsatz: {details['amount']} Punkte"
            )

        # Gewinne/Verluste loggen
        logger.info(
            f"Roulette-Ergebnis für {interaction.user}: {result_number} ({result_color}). Gewinne: {winnings} Punkte."
        )
        await log_points_activity(
            f"🎡 Ergebnis für {interaction.user}: {result_number} ({result_color}). "
            f"Gewinne: {winnings} Punkte. Einsätze: {', '.join(results_summary)}"
        )

        # Punktestand aktualisieren
        conn = await get_db_connection()
        try:
            user = await conn.fetchrow("SELECT score FROM users WHERE discord_id = $1", interaction.user.id)
            if user:
                new_score = user["score"] + winnings - sum([bet["amount"] for bet in self.placed_bets.values()])
                await conn.execute(
                    "UPDATE users SET score = $1 WHERE discord_id = $2",
                    new_score, interaction.user.id
                )
                game_result = "Gewonnen 🎉" if winnings > 0 else "Verloren 😢"
                final_message = (
                        f"🎡 Ergebnis: {result_number} ({result_color})\n"
                        f"Gewinn: {winnings} Punkte\n"
                        f"Verbleibender Kontostand: {new_score}\n"
                        f"Ergebnis: {game_result}\n\n"
                        f"Zusammenfassung:\n" + "\n".join(results_summary)
                )
                await log_points_activity(
                    f"📊 {interaction.user} hat jetzt {new_score} Punkte. Spielresultat: {game_result}."
                )

                if interaction.response.is_done():
                    await interaction.followup.send(content=final_message, ephemeral=True)
                else:
                    await interaction.response.edit_message(content=final_message, view=None)

        finally:
            await release_db_connection(conn)

    # Funktion zum Zurücksetzen der Einsätze
    async def clear_bets(self, interaction: discord.Interaction):
        self.placed_bets.clear()
        self.remaining_amount = self.bet_amount  # Zurücksetzen auf den ursprünglichen Betrag

        # Nachricht aktualisieren, um den Status der Wetten zu zeigen
        await interaction.response.edit_message(
            content=f"Alle Einsätze wurden zurückgesetzt. Dein ursprünglicher Einsatzbetrag beträgt jetzt {self.remaining_amount} Punkte.",
            view=self  # Behalte die View bei, damit die Buttons weiterhin sichtbar sind
        )


# Slash-Command für Roulette
@bot.tree.command(name="roulette", description="Spiele Roulette mit einem Einsatz.")
async def roulette(interaction: discord.Interaction):
    conn = await get_db_connection()
    try:
        user = await conn.fetchrow("SELECT score FROM users WHERE discord_id = $1", interaction.user.id)
        if not user or user["score"] <= 0:
            await interaction.response.send_message(
                "Du hast nicht genug Punkte, um Roulette zu spielen.", ephemeral=True
            )
            return

        await interaction.response.send_message(
            f"🎡 Willkommen beim Roulette! Dein aktueller Punktestand: {user['score']} Punkte",
            view=BetPhaseView(user_id=interaction.user.id, current_score=user["score"]),
        )
    finally:
        await release_db_connection(conn)




# Slot-Symbole und Gewinnkombinationen
SLOT_SYMBOLS = ["🍒", "🍋", "🍊", "🍉", "⭐", "💎"]
WINNING_COMBINATIONS = {
    "🍒🍒🍒": 5,    # Multiplikator für drei gleiche Kirschen
    "🍋🍋🍋": 10,   # Multiplikator für drei gleiche Zitronen
    "🍊🍊🍊": 15,   # Multiplikator für drei gleiche Orangen
    "🍉🍉🍉": 20,   # Multiplikator für drei gleiche Melonen
    "⭐⭐⭐": 50,     # Multiplikator für drei Sterne
    "💎💎💎": 100   # Multiplikator für drei Diamanten
}

class SetBetModal(Modal):
    def __init__(self, current_score: int, callback):
        super().__init__(title="Einsatz ändern")
        self.current_score = current_score
        self.callback = callback

        self.bet_input = TextInput(
            label="Einsatz",
            placeholder="Gib deinen Einsatz ein.",
            style=discord.TextStyle.short,
            required=True
        )
        self.add_item(self.bet_input)

    async def on_submit(self, interaction: discord.Interaction):
        if not self.bet_input.value.isdigit():
            await interaction.response.send_message("Bitte gib eine gültige Zahl ein.", ephemeral=True)
            return

        bet_amount = int(self.bet_input.value)
        if bet_amount > self.current_score or bet_amount <= 0:
            await interaction.response.send_message(
                f"Ungültiger Einsatz! Du hast {self.current_score} Punkte verfügbar.",
                ephemeral=True
            )
            return

        await self.callback(interaction, bet_amount)


class SlotMachineView(View):
    def __init__(self, user_id: int, current_score: int):
        super().__init__(timeout=300)  # Timeout auf 5 Minuten
        self.user_id = user_id
        self.current_score = current_score
        self.bet_amount = 10  # Standardwert für Einsatz
        self.message = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Das ist nicht dein Spiel!", ephemeral=True)
            return False
        return True

    async def update_points_in_db(self):
        """
        Aktualisiert den Punktestand des Nutzers in der Datenbank.
        """
        conn = await get_db_connection()
        try:
            await conn.execute(
                "UPDATE users SET score = $1 WHERE discord_id = $2",
                self.current_score, self.user_id
            )
            logger.info(f"Punktestand für {self.user_id} erfolgreich auf {self.current_score} aktualisiert.")
        except Exception as e:
            logger.error(f"Fehler beim Aktualisieren des Punktestands für {self.user_id}: {e}")
        finally:
            await conn.close()

    async def set_bet_callback(self, interaction: discord.Interaction, bet_amount: int):
        self.bet_amount = bet_amount
        logger.info(f"{interaction.user} hat den Einsatz auf {bet_amount} Punkte geändert.")
        await log_points_activity(f"🔧 {interaction.user} hat den Einsatz auf {bet_amount} Punkte geändert.")
        await interaction.response.edit_message(
            content=f"Einsatz geändert: {self.bet_amount} Punkte.\nDrücke '🎰 Spin', um zu spielen!",
            view=self
        )

    @discord.ui.button(label="Einsatz ändern", style=discord.ButtonStyle.secondary)
    async def set_bet(self, interaction: discord.Interaction, button: Button):
        # Modal anzeigen, um den Einsatz festzulegen
        modal = SetBetModal(self.current_score, self.set_bet_callback)
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="🎰 Spin", style=discord.ButtonStyle.primary)
    async def spin(self, interaction: discord.Interaction, button: Button):
        if self.current_score < self.bet_amount:
            await interaction.response.send_message(
                f"Du hast nicht genug Punkte für einen Einsatz von {self.bet_amount} Punkten!",
                ephemeral=True
            )
            return

        # Punkte abziehen
        self.current_score -= self.bet_amount
        logger.info(f"{interaction.user} hat {self.bet_amount} Punkte als Einsatz abgezogen.")
        await log_points_activity(
            f"📉 {interaction.user} hat {self.bet_amount} Punkte gesetzt. Neuer Punktestand: {self.current_score}."
        )

        # Slots drehen
        slots = [random.choice(SLOT_SYMBOLS) for _ in range(3)]
        result = "".join(slots)

        # Gewinn berechnen
        winnings = self.bet_amount * WINNING_COMBINATIONS.get(result, 0)
        self.current_score += winnings

        # Gewinn-Logging
        if winnings > 0:
            logger.info(f"{interaction.user} hat {winnings} Punkte gewonnen mit der Kombination {result}.")
            await log_points_activity(
                f"📈 {interaction.user} hat {winnings} Punkte gewonnen mit der Kombination {result}. Neuer Punktestand: {self.current_score}."
            )
        else:
            logger.info(f"{interaction.user} hat keinen Gewinn erzielt mit der Kombination {result}.")
            await log_points_activity(
                f"💔 {interaction.user} hat keinen Gewinn erzielt mit der Kombination {result}. Punktestand: {self.current_score}."
            )

        # Punkte in der Datenbank aktualisieren
        await self.update_points_in_db()

        # Ergebnisnachricht
        result_message = (
            f"🎰 | {' | '.join(slots)} | 🎰\n\n"
            f"{'✨ Gewonnen!' if winnings > 0 else '💔 Leider verloren!'}\n"
            f"Einsatz: {self.bet_amount} Punkte\n"
            f"{'Gewinn: ' + str(winnings) + ' Punkte' if winnings > 0 else 'Kein Gewinn'}\n"
            f"Aktueller Punktestand: {self.current_score} Punkte"
        )

        await interaction.response.edit_message(content=result_message, view=self)

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True
        if self.message:
            await self.message.delete()


@bot.tree.command(name="slots", description="Spiele die Slot-Maschine und gewinne Punkte!")
async def slots(interaction: discord.Interaction):
    conn = await get_db_connection()  # Korrekt awaiten
    try:
        # Hole den Benutzer und prüfe, ob er genug Punkte hat
        user = await conn.fetchrow("SELECT score FROM users WHERE discord_id = $1", interaction.user.id)
        if not user or user["score"] <= 0:
            await interaction.response.send_message(
                "Du hast nicht genug Punkte, um Slots zu spielen.", ephemeral=True
            )
            return

        # Nachricht mit Slot-Maschine senden
        view = SlotMachineView(user_id=interaction.user.id, current_score=user["score"])
        message = await interaction.response.send_message(
            content=f"🎰 Willkommen bei der Slot-Maschine! Dein aktueller Punktestand: {user['score']} Punkte.",
            view=view
        )
        view.message = await interaction.original_response()

    finally:
        # Schließe die Verbindung, auch wenn ein Fehler auftritt
        await conn.close()


# Kartenstapel und Werte
SUITS = ['♠', '♣', '♦', '♥']
RANKS = ['2', '3', '4', '5', '6', '7', '8', '9', '10', 'J', 'Q', 'K', 'A']
VALUES = {
    '2': 2, '3': 3, '4': 4, '5': 5, '6': 6, '7': 7, '8': 8, '9': 9, '10': 10,
    'J': 10, 'Q': 10, 'K': 10, 'A': 11
}

# Blackjack-Spiel Klasse
class Blackjack:
    def __init__(self, bet_amount):
        self.deck = self.create_deck()
        self.player_hand = []
        self.dealer_hand = []
        self.bet_amount = bet_amount

    def create_deck(self):
        deck = [(rank, suit) for suit in SUITS for rank in RANKS]
        random.shuffle(deck)
        return deck

    def draw_card(self, hand):
        card = self.deck.pop()
        hand.append(card)
        return card

    def hand_value(self, hand):
        value = sum(VALUES[card[0]] for card in hand)
        aces = sum(1 for card in hand if card[0] == 'A')
        while value > 21 and aces:
            value -= 10
            aces -= 1
        return value

    def show_hand(self, hand):
        return " ".join([f"{card[0]}{card[1]}" for card in hand])

    def is_busted(self, hand):
        return self.hand_value(hand) > 21

    def dealer_turn(self):
        while self.hand_value(self.dealer_hand) < 17:
            self.draw_card(self.dealer_hand)

    def winner(self):
        player_value = self.hand_value(self.player_hand)
        dealer_value = self.hand_value(self.dealer_hand)

        if self.is_busted(self.player_hand):
            return "Du hast überkauft! Der Dealer gewinnt."
        elif self.is_busted(self.dealer_hand):
            return "Der Dealer hat überkauft! Du gewinnst."
        elif player_value > dealer_value:
            return "Du gewinnst!"
        elif player_value < dealer_value:
            return "Der Dealer gewinnt!"
        else:
            return "Unentschieden!"


# View mit Buttons
class BlackjackView(View):
    def __init__(self, user_id: int, current_score: int):
        super().__init__(timeout=300)
        self.user_id = user_id
        self.current_score = current_score
        self.game = None
        self.bet_amount = 0
        self.message = None

    # Button um den Einsatz festzulegen
    @discord.ui.button(label="Einsatz festlegen", style=discord.ButtonStyle.primary)
    async def set_bet(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = SetBetModal(self.current_score, self.set_bet_callback)
        await interaction.response.send_modal(modal)

    # Callback für den Einsatz
    async def set_bet_callback(self, interaction: discord.Interaction, bet_amount: int):
        self.bet_amount = bet_amount
        await interaction.response.edit_message(
            content=f"Einsatz festgelegt: {self.bet_amount} Punkte. Drücke 'Play', um zu spielen!",
            view=self
        )

    @discord.ui.button(label="Play", style=discord.ButtonStyle.primary)
    async def play_game(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.bet_amount == 0:
            await interaction.response.send_message("Du musst zuerst deinen Einsatz festlegen.", ephemeral=True)
            return

        # Starte das Spiel
        self.game = Blackjack(self.bet_amount)

        # Karten austeilen
        self.game.draw_card(self.game.player_hand)
        self.game.draw_card(self.game.player_hand)
        self.game.draw_card(self.game.dealer_hand)
        self.game.draw_card(self.game.dealer_hand)

        # Aktuelle Hand anzeigen
        content = (
            f"Du hast {self.bet_amount} Punkte gesetzt.\n\n"
            f"Deine Hand: {self.game.show_hand(self.game.player_hand)} (Wert: {self.game.hand_value(self.game.player_hand)})\n"
            f"Dealer zeigt: {self.game.dealer_hand[0][0]}{self.game.dealer_hand[0][1]}"
        )

        # Entferne alte Buttons und füge HIT und STAND hinzu
        self.clear_items()  # Entferne alle bestehenden Buttons
        self.add_item(HitButton(self.game))  # Füge HIT-Button hinzu
        self.add_item(StandButton(self.game))  # Füge STAND-Button hinzu

        # Nachricht aktualisieren
        await interaction.response.edit_message(content=content, view=self)

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True
        if self.message:
            await self.message.edit(view=self)


# Hit-Button für Spieleraktion
class HitButton(discord.ui.Button):
    def __init__(self, game: Blackjack):
        super().__init__(label="HIT", style=discord.ButtonStyle.primary)
        self.game = game

    async def callback(self, interaction: discord.Interaction):
        view = self.view  # Die aktuelle View abrufen

        # Spieler zieht eine Karte
        self.game.draw_card(self.game.player_hand)
        player_value = self.game.hand_value(self.game.player_hand)

        # Prüfe, ob Spieler überkauft hat
        if self.game.is_busted(self.game.player_hand):
            # Verbindung zur Datenbank herstellen
            conn = await get_db_connection()
            user = await conn.fetchrow("SELECT score FROM users WHERE discord_id = $1", interaction.user.id)

            # Verlust berechnen
            profit = -self.game.bet_amount
            new_score = user["score"] + profit
            log_message = f"📉 {interaction.user} hat {abs(profit)} Punkte verloren (überkauft). Neuer Punktestand: {new_score}."

            # Punkte in der Datenbank aktualisieren
            await conn.execute("UPDATE users SET score = $1 WHERE discord_id = $2", new_score, interaction.user.id)
            await conn.close()

            # Punkteaktivität loggen
            await log_points_activity(log_message)

            # Endnachricht bei Überkaufen
            content = (
                f"❌ Du hast überkauft! Dein Wert: {player_value}.\n\n"
                f"Dealer's Hand: {self.game.show_hand(self.game.dealer_hand)} "
                f"(Wert: {self.game.hand_value(self.game.dealer_hand)})\n"
                f"➡️ Ergebnis: Der Dealer gewinnt!\n"
                f"📊 Einsatz: {self.game.bet_amount} Punkte\n"
                f"❌ Verlust: {abs(profit)}\n"
                f"🔗 Neuer Punktestand: {new_score} Punkte."
            )

            # Buttons entfernen
            view.clear_items()

            # Nachricht aktualisieren
            await interaction.response.edit_message(content=content, view=view)
        else:
            # Spieler zieht weiter (keine Änderung bei Gewinn/Verlust)
            content = (
                f"Deine Hand: {self.game.show_hand(self.game.player_hand)} (Wert: {player_value})\n"
                f"Dealer zeigt: {self.game.dealer_hand[0][0]}{self.game.dealer_hand[0][1]}"
            )
            await interaction.response.edit_message(content=content, view=view)



# Stand-Button für Spieleraktion
class StandButton(discord.ui.Button):
    def __init__(self, game: Blackjack):
        super().__init__(label="STAND", style=discord.ButtonStyle.primary)
        self.game = game

    async def callback(self, interaction: discord.Interaction):
        view = self.view  # Die aktuelle View abrufen

        # Spieler bleibt stehen, Dealer zieht Karten
        self.game.dealer_turn()

        # Berechnung der Ergebnisse
        result = self.game.winner()
        player_value = self.game.hand_value(self.game.player_hand)
        dealer_value = self.game.hand_value(self.game.dealer_hand)

        # Verbindung zur Datenbank herstellen
        conn = await get_db_connection()
        user = await conn.fetchrow("SELECT score FROM users WHERE discord_id = $1", interaction.user.id)

        # Punkte aktualisieren
        if result == "Du gewinnst!":
            profit = self.game.bet_amount * 2  # Gewinn verdoppeln
            new_score = user["score"] + profit
            log_message = f"📈 {interaction.user} hat {profit} Punkte gewonnen (Einsatz verdoppelt). Neuer Punktestand: {new_score}."
        elif result == "Der Dealer gewinnt!":
            profit = -self.game.bet_amount  # Verlust bleibt Einsatz
            new_score = user["score"] + profit
            log_message = f"📉 {interaction.user} hat {abs(profit)} Punkte verloren. Neuer Punktestand: {new_score}."
        else:
            profit = 0  # Unentschieden, keine Änderung
            new_score = user["score"]
            log_message = f"🔄 {interaction.user} hat keine Punkte geändert. Punktestand bleibt: {new_score}."

        # Punkte in der Datenbank aktualisieren
        await conn.execute("UPDATE users SET score = $1 WHERE discord_id = $2", new_score, interaction.user.id)
        await conn.close()

        # Punkteaktivität loggen
        await log_points_activity(log_message)

        # Endnachricht mit Einsatz und Gewinn/Verlust
        content = (
            f"🎲 Spiel beendet! Einsatz: {self.game.bet_amount} Punkte.\n\n"
            f"Deine Hand: {self.game.show_hand(self.game.player_hand)} (Wert: {player_value})\n"
            f"Dealer's Hand: {self.game.show_hand(self.game.dealer_hand)} (Wert: {dealer_value})\n\n"
            f"➡️ Ergebnis: {result}\n"
            f"📊 Einsatz: {self.game.bet_amount} Punkte\n"
            f"{'💰 Gewinn: ' + str(profit) if profit > 0 else '❌ Verlust: ' + str(-profit) if profit < 0 else '🔄 Keine Veränderung'}\n"
            f"🔗 Neuer Punktestand: {new_score} Punkte."
        )

        # Buttons entfernen
        view.clear_items()

        # Nachricht aktualisieren
        await interaction.response.edit_message(content=content, view=view)


# End-Game-Button
class EndGameButton(discord.ui.Button):
    def __init__(self, game: Blackjack, view: BlackjackView):
        super().__init__(label="Spiel beenden", style=discord.ButtonStyle.danger)
        self.game = game
        self.view = view

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.edit_message(content="🎮 Danke fürs Spielen! Starte ein neues Spiel mit `/blackjack`.", view=None)

# Modal zum Einsatz festlegen
class SetBetModal(Modal):
    def __init__(self, current_score: int, callback):
        super().__init__(title="Einsatz festlegen")
        self.current_score = current_score
        self.callback = callback

        self.bet_input = TextInput(
            label="Einsatz",
            placeholder="Gib deinen Einsatz ein.",
            style=discord.TextStyle.short,
            required=True
        )
        self.add_item(self.bet_input)

    async def on_submit(self, interaction: discord.Interaction):
        if not self.bet_input.value.isdigit():
            await interaction.response.send_message("Bitte gib eine gültige Zahl ein.", ephemeral=True)
            return

        bet_amount = int(self.bet_input.value)
        if bet_amount > self.current_score or bet_amount <= 0:
            await interaction.response.send_message(
                f"Ungültiger Einsatz! Du hast {self.current_score} Punkte verfügbar.", ephemeral=True)
            return

        await self.callback(interaction, bet_amount)


# Deine blackjack-Funktion
@bot.tree.command(name="blackjack", description="Starte ein Spiel Blackjack!")
async def blackjack(interaction: discord.Interaction):
    # Manuelle Verwaltung der Datenbankverbindung
    conn = await get_db_connection()

    try:
        user = await conn.fetchrow("SELECT score FROM users WHERE discord_id = $1", interaction.user.id)
        if not user or user["score"] <= 0:
            await interaction.response.send_message("Du hast nicht genug Punkte, um zu spielen.", ephemeral=True)
            return

        # Blackjack-View anzeigen
        view = BlackjackView(user_id=interaction.user.id, current_score=user["score"])
        message = await interaction.response.send_message(
            content=f"🎲 Willkommen bei Blackjack! Dein aktueller Punktestand: {user['score']} Punkte.",
            view=view
        )
        view.message = await interaction.original_response()

    finally:
        # Schließe die Verbindung nach der Benutzung
        await conn.close()

# Bot starten
@bot.event
async def on_ready():
    print(f"Bot {bot.user} ist online.")
    try:
        await bot.tree.sync()  # Synchronisiert alle Slash-Commands
        print("Slash-Commands wurden erfolgreich synchronisiert.")
        await ensure_table_exists()
        await sync_members()
        award_voice_points.start()
        await update_presence(bot)
        print("Bot ist bereit und alle Hintergrundaufgaben wurden gestartet.")
    except Exception as e:
        print(f"Fehler in on_ready: {e}")



app = Flask(__name__)

@app.route('/')
def home():
    return "Bot ist online!"

# Flask starten
def run_flask():
    app.run(port=12000)

# Flask starten
if __name__ == '__main__':
    flask_thread = threading.Thread(target=run_flask)
    flask_thread.daemon = True
    flask_thread.start()
    print("Flask-Server läuft...")

bot.run(os.getenv("DISCORD_TOKEN"))