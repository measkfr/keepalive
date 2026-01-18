import discord
import asyncio
import random
import threading
import time
import logging
import sys
import os
from datetime import datetime
from flask import Flask, jsonify
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler('discord_keepalive.log')
    ]
)
logger = logging.getLogger(__name__)

# ============================
# FLASK SERVER
# ============================
app = Flask(__name__)

@app.route("/")
def home():
    return jsonify({
        "status": "online",
        "service": "discord-keepalive",
        "timestamp": datetime.now().isoformat(),
        "discord_running": discord_running,
        "port": PORT
    })

@app.route("/health")
def health():
    return jsonify({"status": "healthy"})

@app.route("/ping")
def ping():
    logger.info(f"Ping at {datetime.now().strftime('%H:%M:%S')}")
    return jsonify({"pong": True})

def start_flask():
    logger.info(f"Starting Flask on port {PORT}")
    app.run(host="0.0.0.0", port=PORT, debug=False, use_reloader=False)

# ============================
# DISCORD CLIENT WITH DISCORD.PY-SELF
# ============================
class DiscordKeepAlive:
    def __init__(self):
        self.token = os.environ.get('DISCORD_TOKEN')
        if not self.token:
            logger.error("❌ DISCORD_TOKEN not found!")
            raise ValueError("DISCORD_TOKEN is required")
        
        logger.info(f"Token starts with: {self.token[:20]}...")
        
        # Setup client with discord.py-self
        self.client = discord.Client()
        self.setup_events()
        
        # Status configurations
        self.statuses = [
            "Always Online 🟢",
            "Listening to Music 🎵", 
            "Coding with Python 🐍",
            "Watching Videos 📺",
            "24/7 Active ⚡",
            "Chilling 🍃",
            "AFK but Here 👻",
            "Invisible Mode 👁️",
            "Busy Working 💼",
            "Taking a Break ☕"
        ]
        
    def setup_events(self):
        @self.client.event
        async def on_ready():
            global discord_running
            logger.info(f"✅ SUCCESS! Logged in as {self.client.user}")
            logger.info(f"🆔 User ID: {self.client.user.id}")
            logger.info(f"📧 Email: {self.client.user.email if hasattr(self.client.user, 'email') else 'N/A'}")
            
            discord_running = True
            
            # Start background tasks
            self.client.loop.create_task(self.status_loop())
            self.client.loop.create_task(self.activity_loop())
            
            # Set initial status
            await self.update_status()
            
        @self.client.event
        async def on_disconnect():
            global discord_running
            logger.warning("⚠️ Disconnected from Discord")
            discord_running = False
            
        @self.client.event
        async def on_connect():
            logger.info("🔗 Connected to Discord gateway")
            
        @self.client.event
        async def on_message(self, message):
            # Ignore messages to avoid detection
            pass
    
    async def update_status(self):
        """Update Discord status"""
        try:
            status_text = random.choice(self.statuses)
            
            # Random activity type
            activity_type = random.choice([
                discord.ActivityType.playing,
                discord.ActivityType.listening,
                discord.ActivityType.watching,
                discord.ActivityType.streaming
            ])
            
            activity = discord.Activity(
                name=status_text,
                type=activity_type
            )
            
            await self.client.change_presence(
                activity=activity,
                status=discord.Status.online
            )
            
            timestamp = datetime.now().strftime("%H:%M:%S")
            logger.info(f"[{timestamp}] Status: {status_text}")
            
        except Exception as e:
            logger.error(f"Status update failed: {e}")
    
    async def simulate_typing(self):
        """Simulate typing activity"""
        try:
            # Create DM with self
            user = self.client.user
            dm_channel = user.dm_channel
            if dm_channel is None:
                dm_channel = await user.create_dm()
            
            # Type for 3-8 seconds
            typing_time = random.randint(3, 8)
            
            async with dm_channel.typing():
                await asyncio.sleep(typing_time)
            
            logger.info(f"Typed for {typing_time}s")
            
        except Exception as e:
            logger.debug(f"Typing failed: {e}")
    
    async def status_loop(self):
        """Loop to update status"""
        logger.info("Starting status loop...")
        while True:
            await asyncio.sleep(random.randint(300, 600))  # 5-10 minutes
            await self.update_status()
    
    async def activity_loop(self):
        """Loop to simulate activity"""
        logger.info("Starting activity loop...")
        while True:
            await asyncio.sleep(random.randint(600, 1200))  # 10-20 minutes
            
            if random.choice([True, False]):
                await self.simulate_typing()
            else:
                await self.update_status()
    
    def run(self):
        """Run the Discord client"""
        logger.info("Starting Discord client...")
        
        try:
            # This is the KEY LINE - using bot=False for user accounts
            self.client.run(self.token, bot=False)
            
        except discord.LoginFailure as e:
            logger.error(f"❌ LOGIN FAILED: {e}")
            logger.error("Possible reasons:")
            logger.error("1. Invalid token")
            logger.error("2. Token expired")
            logger.error("3. Discord is blocking self-bots")
            
        except Exception as e:
            logger.error(f"💥 Unexpected error: {e}")
            import traceback
            traceback.print_exc()

# ============================
# RENDER PINGER
# ============================
def render_pinger():
    """Ping Flask to keep Render alive"""
    import requests
    
    time.sleep(5)
    
    base_url = f"http://localhost:{PORT}"
    
    logger.info("Starting Render pinger...")
    
    while True:
        try:
            response = requests.get(f"{base_url}/ping", timeout=10)
            if response.status_code == 200:
                logger.debug(f"Ping OK: {datetime.now().strftime('%H:%M:%S')}")
        except:
            pass
        
        time.sleep(240)  # Every 4 minutes

# ============================
# GLOBALS & MAIN
# ============================
PORT = int(os.environ.get('PORT', 10000))
discord_running = False

def main():
    print("=" * 60)
    print("🚨 WARNING: Discord may ban accounts using self-bots!")
    print("🚨 Use at your own risk for educational purposes only!")
    print("=" * 60)
    
    # 1. Start Flask
    flask_thread = threading.Thread(target=start_flask, daemon=True)
    flask_thread.start()
    
    # 2. Start Render pinger
    pinger_thread = threading.Thread(target=render_pinger, daemon=True)
    pinger_thread.start()
    
    time.sleep(3)
    
    # 3. Start Discord
    try:
        discord_client = DiscordKeepAlive()
        discord_client.run()
        
    except ValueError as e:
        logger.error(f"Config error: {e}")
    except Exception as e:
        logger.error(f"Startup failed: {e}")

if __name__ == "__main__":
    main()
