import discord
import asyncio
import random
import threading
import time
import logging
import sys
import os
from datetime import datetime
from flask import Flask
from flask_cors import CORS
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
# FLASK SERVER (MINIMAL)
# ============================
app = Flask(__name__)
CORS(app)

@app.route("/")
def home():
    """Simple health check"""
    return {
        "status": "online",
        "service": "discord-keepalive",
        "timestamp": datetime.now().isoformat(),
        "discord_running": discord_running,
        "port": FLASK_PORT
    }

@app.route("/health")
def health():
    """Health endpoint for Render"""
    return {"status": "healthy", "timestamp": datetime.now().isoformat()}

@app.route("/ping")
def ping():
    """Ping endpoint to keep Render alive"""
    logger.info(f"Ping received at {datetime.now().strftime('%H:%M:%S')}")
    return {"pong": True, "timestamp": datetime.now().isoformat()}

def start_flask():
    """Start Flask server in background"""
    logger.info(f"Starting Flask server on port {FLASK_PORT}")
    app.run(host="0.0.0.0", port=FLASK_PORT, debug=False, use_reloader=False)

# ============================
# DISCORD KEEP-ALIVE
# ============================
class DiscordKeepAlive:
    def __init__(self):
        self.token = os.environ.get('DISCORD_TOKEN')
        if not self.token:
            logger.error("❌ DISCORD_TOKEN not found in environment variables!")
            raise ValueError("DISCORD_TOKEN is required")
        
        # Status configurations
        self.statuses = [
            "Always Online 🟢",
            "Listening to Music 🎵",
            "Coding with Python 🐍",
            "Watching Tutorials 📺",
            "24/7 Active ⚡",
            "Chilling 🍃",
            "Learning New Things 📚",
            "AFK but Here 👻",
            "Invisible Mode 👁️",
            "Busy Working 💼"
        ]
        
        # Activity types
        self.activity_types = [
            discord.ActivityType.playing,
            discord.ActivityType.listening,
            discord.ActivityType.watching,
            discord.ActivityType.streaming
        ]
        
        # Setup client
        intents = discord.Intents.default()
        intents.typing = False
        intents.presences = False
        
        self.client = discord.Client(intents=intents)
        self.setup_events()
        self.connection_time = None
        
    def setup_events(self):
        @self.client.event
        async def on_ready():
            global discord_running
            logger.info(f"✅ Discord logged in as {self.client.user}")
            logger.info(f"🆔 User ID: {self.client.user.id}")
            logger.info("🚀 Starting keep-alive activities...")
            
            self.connection_time = datetime.now()
            discord_running = True
            
            # Start background tasks
            self.client.loop.create_task(self.status_updater())
            self.client.loop.create_task(self.activity_simulator())
            
            # Set initial status
            await self.update_status()
            
            logger.info("🎮 Discord client is now running and active")
        
        @self.client.event
        async def on_disconnect():
            global discord_running
            logger.warning("⚠️ Disconnected from Discord")
            discord_running = False
        
        @self.client.event
        async def on_connect():
            logger.info("🔗 Connected to Discord")
    
    async def update_status(self):
        """Update Discord status with random activity"""
        try:
            # Random status and activity
            status_text = random.choice(self.statuses)
            activity_type = random.choice(self.activity_types)
            
            # Create activity
            activity = discord.Activity(
                name=status_text,
                type=activity_type
            )
            
            # Update presence
            await self.client.change_presence(
                activity=activity,
                status=discord.Status.online
            )
            
            timestamp = datetime.now().strftime("%H:%M:%S")
            logger.info(f"[{timestamp}] Status: {status_text}")
            
            return True
            
        except Exception as e:
            logger.error(f"Failed to update status: {e}")
            return False
    
    async def simulate_activity(self):
        """Simulate user activity"""
        try:
            # Get DM channel with self
            user = self.client.user
            dm_channel = user.dm_channel
            if dm_channel is None:
                dm_channel = await user.create_dm()
            
            # Simulate typing for 3-10 seconds
            typing_time = random.randint(3, 10)
            
            async with dm_channel.typing():
                await asyncio.sleep(typing_time)
            
            timestamp = datetime.now().strftime("%H:%M:%S")
            logger.info(f"[{timestamp}] Simulated typing for {typing_time}s")
            
            return True
            
        except Exception as e:
            logger.debug(f"Typing simulation failed: {e}")
            return False
    
    async def status_updater(self):
        """Periodically update status"""
        logger.info("🔄 Starting status updater...")
        while True:
            # Random interval: 3-10 minutes
            wait_time = random.randint(180, 600)
            await asyncio.sleep(wait_time)
            
            await self.update_status()
    
    async def activity_simulator(self):
        """Simulate various user activities"""
        logger.info("🎭 Starting activity simulator...")
        while True:
            # Random interval: 5-15 minutes
            wait_time = random.randint(300, 900)
            await asyncio.sleep(wait_time)
            
            # Random activity type
            activity_type = random.choice(['typing', 'status_change'])
            
            if activity_type == 'typing':
                await self.simulate_activity()
            else:
                await self.update_status()
    
    def run(self):
        """Run the Discord client"""
        logger.info("🟡 Starting Discord keep-alive client...")
        
        try:
            # Run the client
            self.client.run(self.token, bot=False)
            
        except discord.LoginFailure:
            logger.error("❌ Invalid Discord token! Check your DISCORD_TOKEN")
        except KeyboardInterrupt:
            logger.info("👋 Shutting down gracefully...")
        except Exception as e:
            logger.error(f"💥 Unexpected error: {e}")
        finally:
            global discord_running
            discord_running = False
    
    def stop(self):
        """Stop the Discord client"""
        logger.info("🛑 Stopping Discord client...")
        asyncio.create_task(self.client.close())

# ============================
# KEEP-ALIVE PINGER FOR RENDER
# ============================
def render_keepalive_pinger():
    """Ping the Flask server periodically to keep Render alive"""
    import requests
    
    # Wait for Flask to start
    time.sleep(5)
    
    base_url = f"http://localhost:{FLASK_PORT}"
    
    logger.info("🔄 Starting Render keep-alive pinger...")
    
    while True:
        try:
            response = requests.get(f"{base_url}/ping", timeout=10)
            if response.status_code == 200:
                logger.debug(f"✅ Keep-alive ping successful: {datetime.now().strftime('%H:%M:%S')}")
            else:
                logger.warning(f"⚠️ Ping returned status {response.status_code}")
                
        except requests.exceptions.ConnectionError:
            logger.warning("🌐 Flask server not reachable yet, retrying...")
        except Exception as e:
            logger.error(f"❌ Keep-alive ping failed: {e}")
        
        # Ping every 4 minutes (Render free tier sleeps after 5 minutes)
        time.sleep(240)

# ============================
# MAIN EXECUTION
# ============================
# Global variables
FLASK_PORT = int(os.environ.get('PORT', 10000))
discord_running = False

def main():
    """Main function to start everything"""
    print("=" * 60)
    print("🚨 WARNING: Using self-bots violates Discord's Terms of Service!")
    print("🚨 Your account can be permanently banned!")
    print("=" * 60)
    print("\n📦 Starting Discord Keep-Alive System...")
    
    # 1. Start Flask server in background thread
    flask_thread = threading.Thread(target=start_flask, daemon=True)
    flask_thread.start()
    
    # 2. Start Render keep-alive pinger
    pinger_thread = threading.Thread(target=render_keepalive_pinger, daemon=True)
    pinger_thread.start()
    
    # 3. Wait a moment for Flask to initialize
    time.sleep(3)
    
    # 4. Start Discord client
    try:
        discord_client = DiscordKeepAlive()
        discord_client.run()
        
    except ValueError as e:
        logger.error(f"Configuration error: {e}")
        logger.info("Set your Discord token as DISCORD_TOKEN environment variable")
    except Exception as e:
        logger.error(f"Failed to start Discord: {e}")

if __name__ == "__main__":
    main()
