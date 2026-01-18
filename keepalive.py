import os
import sys
import time
import random
import threading
import logging
from datetime import datetime
from flask import Flask, jsonify
import asyncio
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

# ============================
# FLASK SERVER (MINIMAL)
# ============================
app = Flask(__name__)
PORT = int(os.environ.get('PORT', 10000))

@app.route("/")
def home():
    return jsonify({
        "status": "online",
        "service": "discord-keepalive",
        "timestamp": datetime.now().isoformat()
    })

@app.route("/health")
def health():
    return jsonify({"status": "healthy"})

@app.route("/ping")
def ping():
    logger.info(f"Ping at {datetime.now().strftime('%H:%M:%S')}")
    return jsonify({"pong": True})

def start_flask():
    logger.info(f"Starting Flask server on port {PORT}")
    app.run(host="0.0.0.0", port=PORT, debug=False, use_reloader=False)

# ============================
# DISCORD KEEP-ALIVE (WEBSOCKET METHOD)
# ============================
import websocket
import json

class DiscordKeepAlive:
    def __init__(self):
        self.token = os.environ.get('DISCORD_TOKEN')
        if not self.token:
            logger.error("❌ DISCORD_TOKEN not found!")
            raise ValueError("DISCORD_TOKEN is required")
        
        logger.info(f"Token: {self.token[:10]}...")
        
        self.ws = None
        self.heartbeat_interval = None
        self.running = True
        
    def on_message(self, ws, message):
        try:
            data = json.loads(message)
            
            # Handle different op codes
            if data['op'] == 10:  # Hello
                self.heartbeat_interval = data['d']['heartbeat_interval'] / 1000
                logger.info(f"Connected! Heartbeat interval: {self.heartbeat_interval}s")
                
                # Send identify payload
                identify = {
                    "op": 2,
                    "d": {
                        "token": self.token,
                        "properties": {
                            "$os": "linux",
                            "$browser": "chrome",
                            "$device": "chrome"
                        },
                        "presence": {
                            "status": "online",
                            "since": 0,
                            "activities": [{
                                "name": "24/7 Online",
                                "type": 0
                            }],
                            "afk": False
                        }
                    }
                }
                ws.send(json.dumps(identify))
                logger.info("✅ Sent identify payload")
                
                # Start heartbeat
                threading.Thread(target=self.heartbeat_thread, args=(ws,), daemon=True).start()
                
            elif data['op'] == 11:  # Heartbeat ACK
                logger.debug("❤️ Heartbeat ACK received")
                
            elif data['t'] == 'READY':
                logger.info("🎉 Discord connection ready!")
                
            elif data['op'] == 9:  # Invalid session
                logger.error("❌ Invalid session, reconnecting...")
                time.sleep(5)
                self.connect()
                
        except Exception as e:
            logger.error(f"Message error: {e}")
    
    def on_error(self, ws, error):
        logger.error(f"WebSocket error: {error}")
    
    def on_close(self, ws, close_status_code, close_msg):
        logger.warning(f"WebSocket closed: {close_status_code} - {close_msg}")
        if self.running:
            logger.info("Reconnecting in 5 seconds...")
            time.sleep(5)
            self.connect()
    
    def on_open(self, ws):
        logger.info("WebSocket connection opened")
    
    def heartbeat_thread(self, ws):
        """Send heartbeats at intervals"""
        while self.running:
            try:
                time.sleep(self.heartbeat_interval)
                heartbeat = {"op": 1, "d": None}
                ws.send(json.dumps(heartbeat))
                logger.debug("❤️ Sent heartbeat")
            except Exception as e:
                logger.error(f"Heartbeat error: {e}")
                break
    
    def connect(self):
        """Connect to Discord Gateway"""
        self.ws = websocket.WebSocketApp(
            "wss://gateway.discord.gg/?v=9&encoding=json",
            on_open=self.on_open,
            on_message=self.on_message,
            on_error=self.on_error,
            on_close=self.on_close
        )
        
        logger.info("Connecting to Discord Gateway...")
        self.ws.run_forever()
    
    def start(self):
        """Start the WebSocket connection"""
        self.connect_thread = threading.Thread(target=self.connect, daemon=True)
        self.connect_thread.start()
    
    def stop(self):
        """Stop the connection"""
        self.running = False
        if self.ws:
            self.ws.close()

# ============================
# RENDER PINGER
# ============================
def render_pinger():
    """Ping Flask to keep Render alive"""
    import requests
    
    time.sleep(5)  # Wait for Flask to start
    
    logger.info("Starting Render pinger...")
    
    while True:
        try:
            response = requests.get(f"http://localhost:{PORT}/ping", timeout=10)
            if response.status_code == 200:
                logger.debug(f"✅ Ping successful")
        except Exception as e:
            logger.warning(f"Ping failed: {e}")
        
        # Ping every 4 minutes (Render sleeps after 5)
        time.sleep(240)

# ============================
# MAIN FUNCTION
# ============================
def main():
    print("=" * 60)
    print("Discord Keep-Alive System")
    print("=" * 60)
    
    # Start Flask server
    flask_thread = threading.Thread(target=start_flask, daemon=True)
    flask_thread.start()
    
    # Start Render pinger
    pinger_thread = threading.Thread(target=render_pinger, daemon=True)
    pinger_thread.start()
    
    # Wait a moment
    time.sleep(3)
    
    # Start Discord keep-alive
    try:
        discord_client = DiscordKeepAlive()
        discord_client.start()
        
        logger.info("✅ All services started!")
        logger.info(f"🌐 Flask: http://localhost:{PORT}")
        logger.info("🎮 Discord: Connecting...")
        
        # Keep main thread alive
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            logger.info("Shutting down...")
            discord_client.stop()
            
    except Exception as e:
        logger.error(f"Failed: {e}")

if __name__ == "__main__":
    main()
