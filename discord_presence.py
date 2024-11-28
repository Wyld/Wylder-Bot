import discord

async def update_presence(bot: discord.Client):
    activity = discord.Streaming(
        name="mit den anderen Bots",  # Name des Streams
        url="https://www.youtube.com/watch?v=dQw4w9WgXcQ&pp=ygUJcmljayByb2xs"  # Ersetze durch eine echte URL, wenn gewünscht
    )
    print("Aktualisiere Präsenz auf 'Streaming'...")
    await bot.change_presence(activity=activity)
    print("Präsenz auf 'Streaming' aktualisiert.")
