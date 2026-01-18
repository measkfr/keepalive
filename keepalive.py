import os
import sys
import time
import json
import random
import threading
import logging
from datetime import datetime
from flask import Flask, jsonify
from dotenv import load_dotenv

# Try to import websocket
try:
    import websocket
    WEBSOCKET_AVAILABLE = True
except ImportError:
    WEBSOCKET_AVAILABLE = False
    print("Warning: websocket-client not installed")

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
# FLASK SERVER
# ============================
app = Flask(__name__)
PORT = int(os.environ.get('PORT', 10000))

@app.route("/")
def home():
    return jsonify({
        "status": "online",
        "service": "discord-keepalive",
        "timestamp": datetime.now().isoformat(),
        "websocket": WEBSOCKET_AVAILABLE
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
# DISCORD KEEP-ALIVE (WEBSOCKET)
# ============================
class DiscordKeepAlive:
    def __init__(self):
        self.token = os.environ.get('DISCORD_TOKEN')
        if not self.token:
            logger.error("❌ DISCORD_TOKEN not found in environment variables!")
            logger.info("💡 Set DISCORD_TOKEN in Render environment variables")
            raise ValueError("DISCORD_TOKEN is required")
        
        logger.info(f"✅ Token loaded (starts with: {self.token[:10]}...)")
        
        self.ws = None
        self.heartbeat_interval = 41250  # Default in ms
        self.sequence = None
        self.session_id = None
        self.running = True
        self.last_heartbeat_ack = time.time()
        
        # FIXED STATUS - Only "Fucking RICH 💸💸"
        self.status = {"name": "Fucking RICH 💸💸", "type": 0}
        
    def connect(self):
        """Connect to Discord Gateway"""
        if not WEBSOCKET_AVAILABLE:
            logger.error("❌ websocket-client is not installed!")
            return
            
        logger.info("🌐 Connecting to Discord Gateway...")
        
        self.ws = websocket.WebSocketApp(
            "wss://gateway.discord.gg/?v=9&encoding=json",
            on_open=self.on_open,
            on_message=self.on_message,
            on_error=self.on_error,
            on_close=self.on_close
        )
        
        # Run WebSocket in a separate thread
        self.ws.run_forever()
    
    def on_open(self, ws):
        """Called when WebSocket connection opens"""
        logger.info("✅ Connected to Discord Gateway")
        
    def on_message(self, ws, message):
        """Handle incoming messages"""
        try:
            data = json.loads(message)
            op = data.get('op')
            t = data.get('t')
            d = data.get('d', {})
            
            # Update sequence
            if data.get('s'):
                self.sequence = data['s']
            
            # Handle op codes
            if op == 10:  # Hello
                self.heartbeat_interval = d['heartbeat_interval']
                logger.info(f"📊 Heartbeat interval: {self.heartbeat_interval}ms")
                
                # Send identify
                self.identify()
                
                # Start heartbeat
                threading.Thread(target=self.heartbeat_loop, daemon=True).start()
                
            elif op == 11:  # Heartbeat ACK
                self.last_heartbeat_ack = time.time()
                logger.debug("❤️ Heartbeat acknowledged")
                
            elif op == 0:  # Dispatch
                if t == 'READY':
                    self.session_id = d.get('session_id')
                    user = d.get('user', {})
                    logger.info(f"🎉 Ready! User: {user.get('username', 'Unknown')}#{user.get('discriminator', '0000')}")
                    logger.info(f"📱 Session ID: {self.session_id}")
                    
                    # Set initial status
                    self.update_status()
                    
            elif op == 9:  # Invalid session
                logger.warning("⚠️ Invalid session, reconnecting...")
                time.sleep(5)
                self.reconnect()
                
            elif op == 7:  # Reconnect
                logger.info("🔁 Reconnect requested")
                self.reconnect()
                
        except Exception as e:
            logger.error(f"❌ Error processing message: {e}")
    
    def on_error(self, ws, error):
        logger.error(f"💥 WebSocket error: {error}")
    
    def on_close(self, ws, close_status_code, close_msg):
        logger.warning(f"🔌 Connection closed: {close_status_code} - {close_msg}")
        if self.running:
            logger.info("🔄 Reconnecting in 10 seconds...")
            time.sleep(10)
            self.reconnect()
    
    def send_json(self, data):
        """Send JSON data through WebSocket"""
        try:
            if self.ws and self.ws.sock and self.ws.sock.connected:
                self.ws.send(json.dumps(data))
                return True
        except Exception as e:
            logger.error(f"❌ Failed to send data: {e}")
        return False
    
    def identify(self):
        """Send identify payload to Discord"""
        identify_payload = {
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
                        "name": "Fucking RICH 💸💸",  # Fixed status
                        "type": 0,  # Playing
                        "created_at": int(time.time() * 1000)
                    }],
                    "afk": False
                },
                "compress": False,
                "large_threshold": 250
            }
        }
        
        if self.send_json(identify_payload):
            logger.info("📨 Sent identify payload with status: Fucking RICH 💸💸")
        else:
            logger.error("❌ Failed to send identify")
    
    def heartbeat_loop(self):
        """Send heartbeats periodically"""
        logger.info("💓 Starting heartbeat loop...")
        
        while self.running:
            try:
                # Calculate sleep time
                sleep_time = self.heartbeat_interval / 1000
                time.sleep(sleep_time)
                
                # Send heartbeat
                heartbeat = {
                    "op": 1,
                    "d": self.sequence
                }
                
                if self.send_json(heartbeat):
                    logger.debug(f"💓 Sent heartbeat (seq: {self.sequence})")
                    
                    # Check if we got an ACK
                    if time.time() - self.last_heartbeat_ack > sleep_time * 2:
                        logger.warning("⚠️ No heartbeat ACK received, connection may be dead")
                        
            except Exception as e:
                logger.error(f"💥 Heartbeat error: {e}")
                break
    
    def update_status(self):
        """Update Discord status - Always shows 'Fucking RICH 💸💸'"""
        try:
            update_payload = {
                "op": 3,
                "d": {
                    "since": 0,
                    "activities": [{
                        "name": "Fucking RICH 💸💸",
                        "type": 0,  # Playing
                        "created_at": int(time.time() * 1000)
                    }],
                    "status": "online",
                    "afk": False
                }
            }
            
            if self.send_json(update_payload):
                current_time = datetime.now().strftime("%H:%M:%S")
                logger.info(f"💰 [{current_time}] Status: Fucking RICH 💸💸")
            else:
                logger.warning("⚠️ Failed to send status update")
                
        except Exception as e:
            logger.error(f"❌ Error updating status: {e}")
    
    def reconnect(self):
        """Reconnect to Discord"""
        if self.ws:
            try:
                self.ws.close()
            except:
                pass
        
        time.sleep(2)
        self.connect()
    
    def start(self):
        """Start the Discord connection"""
        if not WEBSOCKET_AVAILABLE:
            logger.error("❌ Cannot start: websocket-client is not available")
            return
        
        logger.info("🚀 Starting Discord keep-alive...")
        logger.info("💰 Status will be: Fucking RICH 💸💸")
        self.connection_thread = threading.Thread(target=self.connect, daemon=True)
        self.connection_thread.start()
    
    def stop(self):
        """Stop the Discord connection"""
        logger.info("🛑 Stopping Discord keep-alive...")
        self.running = False
        if self.ws:
            try:
                self.ws.close()
            except:
                pass

# ============================
# RENDER KEEP-ALIVE PINGER
# ============================
def render_pinger():
    """Ping Flask server to keep Render alive"""
    import requests
    
    # Wait for Flask to start
    time.sleep(5)
    
    base_url = f"http://localhost:{PORT}"
    
    logger.info("🔄 Starting Render keep-alive pinger...")
    logger.info(f"📡 Pinging {base_url}/ping every 4 minutes")
    
    while True:
        try:
            response = requests.get(f"{base_url}/ping", timeout=10)
            if response.status_code == 200:
                logger.debug(f"✅ Keep-alive ping successful")
            else:
                logger.warning(f"⚠️ Ping returned status {response.status_code}")
                
        except requests.exceptions.ConnectionError:
            logger.warning("🌐 Flask server not ready yet, retrying...")
        except Exception as e:
            logger.error(f"❌ Ping error: {e}")
        
        # Ping every 4 minutes (Render sleeps after 5 minutes of inactivity)
        time.sleep(240)

# ============================
# MAIN FUNCTION
# ============================
def main():
    """Start all services"""
    print("=" * 60)
    print("Discord Keep-Alive System for Render")
    print("💰 Status: Fucking RICH 💸💸")
    print("=" * 60)
    print()
    
    # Check for token
    token = os.environ.get('DISCORD_TOKEN')
    if not token:
        print("❌ ERROR: DISCORD_TOKEN environment variable is not set!")
        print()
        print("To set it on Render:")
        print("1. Go to your Render dashboard")
        print("2. Select your web service")
        print("3. Click 'Environment'")
        print("4. Add DISCORD_TOKEN with your Discord token")
        print()
        print("To get your Discord token:")
        print("1. Open Discord in browser")
        print("2. Press F12 → Console")
        print("3. Paste: window.localStorage.getItem('token')")
        print("4. Or check all localStorage items")
        print("=" * 60)
        return
    
    logger.info("📦 Starting services...")
    
    # 1. Start Flask server
    flask_thread = threading.Thread(target=start_flask, daemon=True)
    flask_thread.start()
    
    # 2. Start Render pinger
    pinger_thread = threading.Thread(target=render_pinger, daemon=True)
    pinger_thread.start()
    
    # Wait for Flask to initialize
    time.sleep(3)
    
    # 3. Start Discord keep-alive
    try:
        discord_client = DiscordKeepAlive()
        discord_client.start()
        
        logger.info("✅ All services started successfully!")
        logger.info(f"🌐 Flask server: http://localhost:{PORT}")
        logger.info("💰 Discord status: Fucking RICH 💸💸")
        logger.info("🔄 Render pinger: Active")
        logger.info("")
        logger.info("📊 System is now running. Your status will always show 'Fucking RICH 💸💸'")
        logger.info("")
        logger.info("To stop: Press Ctrl+C or stop the service in Render dashboard")
        
        # Keep main thread alive
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            logger.info("👋 Shutting down gracefully...")
            discord_client.stop()
            
    except Exception as e:
        logger.error(f"💥 Failed to start: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()
