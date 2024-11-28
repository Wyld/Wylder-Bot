import discord

async def update_presence(bot: discord.Client):
    try:
        activity = discord.Streaming(
            name="mit den anderen Bots",
            url="https://www.youtube.com/watch?v=dQw4w9WgXcQ&pp=ygUJcmljayByb2xs"
        )
        print("Aktualisiere Präsenz auf 'Streaming'...")
        await bot.change_presence(activity=activity)
        print("Präsenz erfolgreich aktualisiert.")
    except Exception as e:
        print(f"Fehler beim Aktualisieren der Präsenz: {e}")
