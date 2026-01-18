import os
import sys
import time
import random
import logging
import threading
import requests
from datetime import datetime
from dotenv import load_dotenv
from flask import Flask, jsonify

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
# FLASK SERVER (MINIMAL)
# ============================
app = Flask(__name__)

@app.route("/")
def home():
    return jsonify({
        "status": "online",
        "service": "discord-keepalive",
        "timestamp": datetime.now().isoformat(),
        "message": "Server is running"
    })

@app.route("/health")
def health():
    return jsonify({
        "status": "healthy",
        "timestamp": datetime.now().isoformat()
    })

@app.route("/ping")
def ping():
    current_time = datetime.now().strftime("%H:%M:%S")
    logger.info(f"Keep-alive ping at {current_time}")
    return jsonify({
        "pong": True,
        "timestamp": datetime.now().isoformat()
    })

def start_flask():
    port = int(os.environ.get('PORT', 10000))
    logger.info(f"Starting Flask server on port {port}")
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)

# ============================
# DISCORD KEEP-ALIVE (WEBHOOK METHOD)
# ============================
class DiscordKeepAlive:
    def __init__(self):
        self.token = os.environ.get('DISCORD_TOKEN')
        self.status_url = os.environ.get('STATUS_URL', '')
        self.session = requests.Session()
        
        if not self.token:
            logger.error("❌ DISCORD_TOKEN not found!")
            raise ValueError("DISCORD_TOKEN is required")
        
        # Status messages
        self.statuses = [
            "Always Online 🟢",
            "Listening to Music 🎵",
            "Coding with Python 🐍",
            "Watching Videos 📺",
            "24/7 Active ⚡",
            "AFK but Here 👻",
            "Busy Working 💼",
            "Taking a Break ☕",
            "Gaming 🎮",
            "Studying 📚"
        ]
        
        self.running = False
        
    def update_status_via_api(self, status_text):
        """Update status using Discord API (simplified)"""
        try:
            headers = {
                'Authorization': self.token,
                'Content-Type': 'application/json',
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            }
            
            payload = {
                'status': 'online',
                'activities': [{
                    'name': status_text,
                    'type': random.randint(0, 3)  # 0: playing, 1: streaming, 2: listening, 3: watching
                }],
                'since': int(time.time() * 1000),
                'afk': False
            }
            
            # Try to update via Discord API
            response = self.session.patch(
                'https://discord.com/api/v9/users/@me/settings',
                headers=headers,
                json=payload,
                timeout=10
            )
            
            if response.status_code in [200, 201, 204]:
                logger.info(f"✅ Status updated: {status_text}")
                return True
            else:
                logger.warning(f"⚠️ Status update failed: {response.status_code}")
                return False
                
        except Exception as e:
            logger.error(f"❌ API Error: {e}")
            return False
    
    def keep_alive_loop(self):
        """Main keep-alive loop"""
        logger.info("🟡 Starting Discord keep-alive...")
        self.running = True
        
        # Initial connection
        self.update_status_via_api("Starting up... 🔄")
        
        while self.running:
            try:
                # Random status update
                status = random.choice(self.statuses)
                self.update_status_via_api(status)
                
                # Simulate activity by updating status periodically
                sleep_time = random.randint(300, 600)  # 5-10 minutes
                logger.info(f"⏳ Next update in {sleep_time//60} minutes")
                
                # Countdown
                for i in range(sleep_time):
                    if not self.running:
                        break
                    time.sleep(1)
                    
            except KeyboardInterrupt:
                logger.info("👋 Shutdown requested")
                break
            except Exception as e:
                logger.error(f"💥 Error in keep-alive loop: {e}")
                time.sleep(60)  # Wait 1 minute before retry
        
        logger.info("🛑 Discord keep-alive stopped")
    
    def start(self):
        """Start the keep-alive in a separate thread"""
        self.thread = threading.Thread(target=self.keep_alive_loop, daemon=True)
        self.thread.start()
    
    def stop(self):
        """Stop the keep-alive"""
        self.running = False
        if self.thread:
            self.thread.join(timeout=5)

# ============================
# RENDER KEEP-ALIVE PINGER
# ============================
def render_pinger():
    """Ping the Flask app to keep Render alive"""
    time.sleep(5)  # Wait for Flask to start
    
    port = int(os.environ.get('PORT', 10000))
    base_url = f"http://localhost:{port}"
    
    logger.info("🔄 Starting Render keep-alive pinger...")
    
    while True:
        try:
            response = requests.get(f"{base_url}/ping", timeout=10)
            if response.status_code == 200:
                current_time = datetime.now().strftime("%H:%M:%S")
                logger.debug(f"✅ Ping successful at {current_time}")
            else:
                logger.warning(f"⚠️ Ping status: {response.status_code}")
                
        except Exception as e:
            logger.error(f"❌ Ping failed: {e}")
        
        # Ping every 4 minutes (Render sleeps after 5)
        time.sleep(240)

# ============================
# MAIN FUNCTION
# ============================
def main():
    """Start all services"""
    print("=" * 60)
    print("🚨 WARNING: This script may violate Discord's Terms of Service!")
    print("🚨 Use at your own risk!")
    print("=" * 60)
    
    logger.info("📦 Starting Discord Keep-Alive System...")
    
    # 1. Start Flask server
    flask_thread = threading.Thread(target=start_flask, daemon=True)
    flask_thread.start()
    
    # 2. Start Render pinger
    pinger_thread = threading.Thread(target=render_pinger, daemon=True)
    pinger_thread.start()
    
    # 3. Start Discord keep-alive
    try:
        discord_client = DiscordKeepAlive()
        discord_client.start()
        
        logger.info("✅ All services started successfully!")
        logger.info("🌐 Flask server: http://localhost:{}".format(os.environ.get('PORT', 10000)))
        logger.info("🎮 Discord keep-alive: Active")
        logger.info("🔄 Render pinger: Active")
        
        # Keep main thread alive
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            logger.info("👋 Shutting down...")
            discord_client.stop()
            
    except ValueError as e:
        logger.error(f"❌ Configuration error: {e}")
        logger.info("💡 Set DISCORD_TOKEN environment variable")
    except Exception as e:
        logger.error(f"💥 Failed to start: {e}")

if __name__ == "__main__":
    main()
