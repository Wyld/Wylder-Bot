# test_db_connection.py
import os
import asyncpg
from dotenv import load_dotenv

# Umgebungsvariablen laden
load_dotenv()

async def test_connection():
    try:
        # Datenbankverbindung herstellen
        conn = await asyncpg.connect(
            user=os.getenv("DB_USER"),
            password=os.getenv("DB_PASSWORD"),
            database=os.getenv("DB_NAME"),
            host=os.getenv("DB_HOST"),
            port=int(os.getenv("DB_PORT", 6543)),
            ssl="require"
        )
        print("Verbindung zur Datenbank erfolgreich!")

        # Eine einfache Abfrage ausführen
        result = await conn.fetch("SELECT NOW()")
        print(f"Aktuelle Zeit in der Datenbank: {result}")

    except Exception as e:
        print(f"Fehler bei der Verbindung zur Datenbank: {e}")

    finally:
        if conn:
            await conn.close()

# Hauptfunktion zum Ausführen des Tests
if __name__ == "__main__":
    import asyncio
    asyncio.run(test_connection())