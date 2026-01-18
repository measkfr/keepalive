import discord
from discord.ext import commands, tasks
import asyncio
import random
import threading
import time
import logging
from datetime import datetime
from flask import Flask, jsonify
import os
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Flask setup
app = Flask(__name__)
PORT = int(os.environ.get('PORT', 10000))
discord_running = False

@app.route("/")
def home():
    return jsonify({"status": "online", "discord_running": discord_running})

@app.route("/ping")
def ping():
    return jsonify({"pong": True})

def start_flask():
    app.run(host="0.0.0.0", port=PORT, debug=False, use_reloader=False)

# Discord Self-Bot
class DiscordSelfBot:
    def __init__(self):
        self.token = os.environ.get('DISCORD_TOKEN')
        if not self.token:
            raise ValueError("No token")
        
        # Use discord.py-selfbot-v13
        intents = discord.Intents.default()
        intents.typing = False
        intents.presences = False
        
        self.bot = commands.Bot(command_prefix="!", self_bot=True, intents=intents)
        self.setup_events()
        
    def setup_events(self):
        @self.bot.event
        async def on_ready():
            global discord_running
            logger.info(f"✅ Logged in as {self.bot.user}")
            discord_running = True
            self.status_task.start()
            
        @self.bot.event
        async def on_disconnect():
            global discord_running
            discord_running = False
            
    @tasks.loop(minutes=5.0)
    async def status_task(self):
        statuses = [
            ("Playing games", discord.ActivityType.playing),
            ("Listening to music", discord.ActivityType.listening),
            ("Watching YouTube", discord.ActivityType.watching),
            ("Always Online", discord.ActivityType.streaming)
        ]
        
        name, activity_type = random.choice(statuses)
        
        activity = discord.Activity(
            name=name,
            type=activity_type
        )
        
        await self.bot.change_presence(
            activity=activity,
            status=discord.Status.online
        )
        
        logger.info(f"Status updated: {name}")
    
    def run(self):
        self.bot.run(self.token, bot=False)

# Main
def main():
    print("Starting...")
    
    # Start Flask
    flask_thread = threading.Thread(target=start_flask, daemon=True)
    flask_thread.start()
    
    time.sleep(2)
    
    # Start Discord
    try:
        bot = DiscordSelfBot()
        bot.run()
    except Exception as e:
        logger.error(f"Failed: {e}")

if __name__ == "__main__":
    main()
