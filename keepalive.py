import os
import sys
import time
import threading
import logging
from datetime import datetime, timedelta
from flask import Flask, jsonify
from dotenv import load_dotenv

# Discord.py for reliable voice + presence
import discord
from discord.ext import tasks

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

# ============================
# FLASK SERVER (unchanged)
# ============================
app = Flask(__name__)
PORT = int(os.environ.get('PORT', 10000))

@app.route("/")
def home():
    return jsonify({
        "status": "online",
        "service": "discord-keepalive",
        "timestamp": datetime.now().isoformat(),
        "websocket": False  # we are using discord.py now
    })

@app.route("/health")
def health():
    return jsonify({"status": "healthy"})

@app.route("/ping")
def ping():
    current_time = datetime.now().strftime("%H:%M:%S")
    logger.info(f"Ping received at {current_time}")
    return jsonify({"pong": True, "time": current_time})

def start_flask():
    """Start Flask server in background"""
    logger.info(f"🚀 Starting Flask server on port {PORT}")
    app.run(host='0.0.0.0', port=PORT, debug=False, use_reloader=False)

# ============================
# RENDER KEEP-ALIVE PINGER (unchanged)
# ============================
def render_pinger():
    """Ping Flask server to keep Render alive"""
    import requests
    
    time.sleep(5)
    base_url = f"http://localhost:{PORT}"
    logger.info("🔄 Starting Render keep-alive pinger...")
    logger.info(f"📡 Pinging {base_url}/ping every 3 minutes")
    
    failed_pings = 0
    max_failed_pings = 5
    
    while True:
        try:
            response = requests.get(f"{base_url}/ping", timeout=10)
            if response.status_code == 200:
                logger.debug(f"✅ Keep-alive ping successful")
                failed_pings = 0
            else:
                logger.warning(f"⚠️ Ping returned status {response.status_code}")
                failed_pings += 1
        except Exception as e:
            logger.error(f"❌ Ping error: {e}")
            failed_pings += 1
        
        if failed_pings >= max_failed_pings:
            logger.error(f"💥 Too many failed pings ({failed_pings})")
        time.sleep(180)

# ============================
# HEALTH MONITOR (unchanged)
# ============================
def health_monitor():
    """Monitor system health and log status periodically"""
    time.sleep(10)
    while True:
        try:
            current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            logger.info(f"📊 System Status Check - {current_time}")
            logger.info(f"📈 Memory usage: {sys.getsizeof({})} bytes (sample)")
            logger.info("=" * 50)
        except:
            pass
        time.sleep(3600)

# ============================
# DISCORD BOT WITH VOICE + ROTATIONAL STATUS
# ============================
class DiscordBot:
    def __init__(self, token, voice_channel_id, account_name, status_list, rotate_interval=30):
        self.token = token
        self.voice_channel_id = int(voice_channel_id) if voice_channel_id and str(voice_channel_id).strip() else None
        self.account_name = account_name
        self.status_list = status_list
        self.rotate_interval = rotate_interval
        self.current_index = 0
        self.bot = None
        self.running = True
        self.ready = False

    async def update_presence(self):
        if not self.bot or not self.bot.is_ready():
            return
        status_text = self.status_list[self.current_index]
        activity = discord.Activity(name=status_text, type=discord.ActivityType.playing)
        await self.bot.change_presence(activity=activity, status=discord.Status.online)
        logger.info(f"🎭 [{self.account_name}] Status → {status_text}")

    async def rotate_status(self):
        if len(self.status_list) <= 1:
            return
        while self.running and self.ready:
            await asyncio.sleep(self.rotate_interval)
            self.current_index = (self.current_index + 1) % len(self.status_list)
            await self.update_presence()

    async def join_voice(self):
        if not self.voice_channel_id:
            return
        try:
            channel = self.bot.get_channel(self.voice_channel_id)
            if not channel:
                channel = await self.bot.fetch_channel(self.voice_channel_id)
            if channel and isinstance(channel, discord.VoiceChannel):
                if self.bot.voice_clients:
                    vc = self.bot.voice_clients[0]
                    if vc.channel.id != channel.id:
                        await vc.move_to(channel)
                        logger.info(f"🎤 [{self.account_name}] Moved to {channel.name}")
                else:
                    await channel.connect()
                    logger.info(f"🎤 [{self.account_name}] Joined {channel.name}")
            else:
                logger.error(f"❌ [{self.account_name}] Invalid voice channel ID")
        except Exception as e:
            logger.error(f"❌ [{self.account_name}] Voice join error: {e}")

    @tasks.loop(seconds=30)
    async def stay_in_voice(self):
        if not self.voice_channel_id or not self.bot.is_ready():
            return
        if not self.bot.voice_clients:
            logger.warning(f"⚠️ [{self.account_name}] Not in voice – rejoining")
            await self.join_voice()
        else:
            vc = self.bot.voice_clients[0]
            if vc.channel.id != self.voice_channel_id or not vc.is_connected():
                logger.warning(f"⚠️ [{self.account_name}] Voice issue – fixing")
                await self.join_voice()

    async def on_ready(self):
        self.ready = True
        logger.info(f"✅ [{self.account_name}] Logged in as {self.bot.user}")
        await self.update_presence()
        if len(self.status_list) > 1:
            self.bot.loop.create_task(self.rotate_status())
        if self.voice_channel_id:
            await self.join_voice()
            self.stay_in_voice.start()

    def run(self):
        intents = discord.Intents.default()
        intents.presences = True
        intents.voice_states = True

        self.bot = discord.Client(intents=intents)

        @self.bot.event
        async def on_ready():
            await self.on_ready()
        @self.bot.event
        async def on_disconnect():
            logger.warning(f"🔌 [{self.account_name}] Disconnected – auto-reconnect")
        @self.bot.event
        async def on_resumed():
            logger.info(f"🔄 [{self.account_name}] Session resumed")

        try:
            self.bot.run(self.token, reconnect=True)
        except Exception as e:
            logger.error(f"💥 [{self.account_name}] Bot crashed: {e}")
            time.sleep(5)
            if self.running:
                self.run()

    def stop(self):
        self.running = False
        if self.bot and not self.bot.is_closed():
            try:
                asyncio.run_coroutine_threadsafe(self.bot.close(), self.bot.loop)
            except:
                pass

# ============================
# MAIN FUNCTION (modified)
# ============================
def main():
    print("=" * 60)
    print("Dual Discord Account Keep-Alive System for Render")
    print("💰 Account1: Fucking RICH 💸💸 (fixed)")
    print("🔄 Account2: Rotational (Working On New Video🎥 / NYT💤 / YT- NOTE YOUR TYPE)")
    print("🎤 Voice: Auto-join & never leave (if channel ID provided)")
    print("=" * 60)
    print()

    token1 = os.environ.get('DISCORD_TOKEN_1')
    token2 = os.environ.get('DISCORD_TOKEN_2')
    vc1 = os.environ.get('VOICE_CHANNEL_ID_1', '')
    vc2 = os.environ.get('VOICE_CHANNEL_ID_2', '')

    if not token1 and not token2:
        logger.error("❌ No tokens provided! Set DISCORD_TOKEN_1 and/or DISCORD_TOKEN_2")
        return

    # Start Flask and helpers (same as before)
    flask_thread = threading.Thread(target=start_flask, daemon=True)
    flask_thread.start()
    pinger_thread = threading.Thread(target=render_pinger, daemon=True)
    pinger_thread.start()
    health_thread = threading.Thread(target=health_monitor, daemon=True)
    health_thread.start()

    time.sleep(3)  # let Flask settle

    # Account 1: fixed status
    if token1:
        status1 = ["Fucking RICH 💸💸"]
        bot1 = DiscordBot(token1, vc1, "Account1", status1)
        t1 = threading.Thread(target=bot1.run, daemon=True)
        t1.start()
        logger.info("🚀 Starting Account1 (fixed status)")

    # Account 2: rotational status
    if token2:
        status2 = ["Working On New Video🎥", "NYT💤", "YT- NOTE YOUR TYPE"]
        bot2 = DiscordBot(token2, vc2, "Account2", status2, rotate_interval=30)
        t2 = threading.Thread(target=bot2.run, daemon=True)
        t2.start()
        logger.info("🚀 Starting Account2 (rotational status)")

    logger.info("✅ All services started successfully!")
    logger.info(f"🌐 Flask server: http://localhost:{PORT}")
    logger.info("🔄 Render pinger: Active (every 3 minutes)")
    logger.info("📊 Health monitor: Active")
    logger.info("")
    logger.info("📈 System is now running 24/7")
    logger.info("💪 Your Discord accounts will stay online continuously")
    logger.info("")

    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        logger.info("👋 Shutting down gracefully...")
        # The bots are daemon threads, they will exit with main thread
        time.sleep(2)
        logger.info("✅ Shutdown complete")

if __name__ == "__main__":
    # Required for asyncio in threads (especially on Windows)
    import asyncio
    if os.name == 'nt':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    main()
