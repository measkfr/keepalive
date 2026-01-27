import os
import sys
import time
import json
import random
import threading
import logging
from datetime import datetime, timedelta
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
# DISCORD KEEP-ALIVE (WEBSOCKET) - IMPROVED
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
        self.reconnect_attempts = 0
        self.max_reconnect_attempts = 10
        self.connected_at = None
        
        # FIXED STATUS - Only "Fucking RICH 💸💸"
        self.status = {"name": "Fucking RICH 💸💸", "type": 0}
        
    def connect(self):
        """Connect to Discord Gateway with reconnection logic"""
        if not WEBSOCKET_AVAILABLE:
            logger.error("❌ websocket-client is not installed!")
            return
            
        while self.running and self.reconnect_attempts < self.max_reconnect_attempts:
            try:
                logger.info(f"🌐 Connecting to Discord Gateway... (Attempt {self.reconnect_attempts + 1}/{self.max_reconnect_attempts})")
                
                self.ws = websocket.WebSocketApp(
                    "wss://gateway.discord.gg/?v=9&encoding=json",
                    on_open=self.on_open,
                    on_message=self.on_message,
                    on_error=self.on_error,
                    on_close=self.on_close
                )
                
                # Run WebSocket with keepalive options
                self.ws.run_forever(
                    ping_interval=30,  # Send ping every 30 seconds
                    ping_timeout=10,   # Wait 10 seconds for pong
                    reconnect=5        # Auto-reconnect after 5 seconds
                )
                
                # If we get here, connection closed
                if self.running:
                    self.reconnect_attempts += 1
                    wait_time = min(2 ** self.reconnect_attempts, 30)  # Exponential backoff
                    logger.warning(f"🔌 Connection closed, reconnecting in {wait_time} seconds...")
                    time.sleep(wait_time)
                else:
                    break
                    
            except Exception as e:
                logger.error(f"💥 Connection error: {e}")
                self.reconnect_attempts += 1
                wait_time = min(2 ** self.reconnect_attempts, 30)
                time.sleep(wait_time)
    
    def on_open(self, ws):
        """Called when WebSocket connection opens"""
        self.reconnect_attempts = 0  # Reset reconnect attempts on successful connection
        self.connected_at = time.time()
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
                
                # Start heartbeat with more robust timing
                if not hasattr(self, 'heartbeat_thread') or not self.heartbeat_thread.is_alive():
                    self.heartbeat_thread = threading.Thread(target=self.heartbeat_loop, daemon=True)
                    self.heartbeat_thread.start()
                
            elif op == 11:  # Heartbeat ACK
                self.last_heartbeat_ack = time.time()
                logger.debug("❤️ Heartbeat acknowledged")
                
            elif op == 0:  # Dispatch
                if t == 'READY':
                    self.session_id = d.get('session_id')
                    user = d.get('user', {})
                    logger.info(f"🎉 Ready! User: {user.get('username', 'Unknown')}#{user.get('discriminator', '0000')}")
                    logger.info(f"📱 Session ID: {self.session_id}")
                    
                    # Start status updater thread
                    if not hasattr(self, 'status_updater_thread') or not self.status_updater_thread.is_alive():
                        self.status_updater_thread = threading.Thread(target=self.status_updater_loop, daemon=True)
                        self.status_updater_thread.start()
                    
                elif t == 'RESUMED':
                    logger.info("🔄 Session resumed successfully")
                    
            elif op == 9:  # Invalid session
                logger.warning("⚠️ Invalid session, reconnecting...")
                time.sleep(2)
                self.resume_or_reconnect()
                
            elif op == 7:  # Reconnect
                logger.info("🔁 Reconnect requested")
                time.sleep(1)
                self.resume_or_reconnect()
                
        except Exception as e:
            logger.error(f"❌ Error processing message: {e}")
            import traceback
            logger.error(traceback.format_exc())
    
    def on_error(self, ws, error):
        logger.error(f"💥 WebSocket error: {error}")
    
    def on_close(self, ws, close_status_code, close_msg):
        if close_status_code or close_msg:
            logger.warning(f"🔌 Connection closed: {close_status_code} - {close_msg}")
        
        if self.running:
            logger.info("🔄 Will attempt to reconnect...")
            time.sleep(2)
    
    def send_json(self, data):
        """Send JSON data through WebSocket with retry logic"""
        max_retries = 3
        for attempt in range(max_retries):
            try:
                if self.ws and self.ws.sock and self.ws.sock.connected:
                    self.ws.send(json.dumps(data))
                    return True
                else:
                    raise Exception("WebSocket not connected")
            except Exception as e:
                if attempt < max_retries - 1:
                    logger.warning(f"⚠️ Send failed (attempt {attempt + 1}), retrying...")
                    time.sleep(1)
                else:
                    logger.error(f"❌ Failed to send data after {max_retries} attempts: {e}")
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
                        "name": "Fucking RICH 💸💸",
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
    
    def resume(self):
        """Resume existing session"""
        if self.session_id and self.sequence:
            resume_payload = {
                "op": 6,
                "d": {
                    "token": self.token,
                    "session_id": self.session_id,
                    "seq": self.sequence
                }
            }
            
            if self.send_json(resume_payload):
                logger.info("🔄 Attempting to resume session...")
                return True
        return False
    
    def resume_or_reconnect(self):
        """Try to resume, if fails then reconnect"""
        if not self.resume():
            logger.info("🔄 Resuming failed, starting fresh connection...")
            time.sleep(2)
            self.reconnect()
    
    def heartbeat_loop(self):
        """Improved heartbeat loop with better error handling"""
        logger.info("💓 Starting heartbeat loop...")
        
        missed_heartbeats = 0
        max_missed_heartbeats = 3
        
        while self.running:
            try:
                # Calculate sleep time with small buffer
                sleep_time = (self.heartbeat_interval / 1000) * 0.8  # 80% of interval
                time.sleep(sleep_time)
                
                # Check if we got ACKs
                time_since_ack = time.time() - self.last_heartbeat_ack
                if time_since_ack > (self.heartbeat_interval / 1000) * 2:
                    missed_heartbeats += 1
                    logger.warning(f"⚠️ No heartbeat ACK for {time_since_ack:.1f}s (Missed: {missed_heartbeats}/{max_missed_heartbeats})")
                    
                    if missed_heartbeats >= max_missed_heartbeats:
                        logger.error("💥 Too many missed heartbeats, reconnecting...")
                        self.reconnect()
                        break
                else:
                    missed_heartbeats = 0  # Reset on successful ACK
                
                # Send heartbeat
                heartbeat = {
                    "op": 1,
                    "d": self.sequence if self.sequence else None
                }
                
                if self.send_json(heartbeat):
                    logger.debug(f"💓 Sent heartbeat (seq: {self.sequence})")
                else:
                    logger.error("❌ Failed to send heartbeat")
                    missed_heartbeats += 1
                    
            except Exception as e:
                logger.error(f"💥 Heartbeat error: {e}")
                missed_heartbeats += 1
                time.sleep(1)
    
    def status_updater_loop(self):
        """Periodically update status to ensure it stays visible"""
        logger.info("🔄 Starting status updater loop...")
        
        while self.running:
            try:
                # Update every 30 minutes to keep status fresh
                time.sleep(1800)  # 30 minutes
                
                if self.ws and self.ws.sock and self.ws.sock.connected:
                    self.update_status()
                else:
                    logger.warning("⚠️ Cannot update status - not connected")
                    
            except Exception as e:
                logger.error(f"❌ Status updater error: {e}")
    
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
                        "created_at": int(time.time() * 1000),
                        "timestamps": {
                            "start": int(time.time() * 1000)
                        }
                    }],
                    "status": "online",
                    "afk": False
                }
            }
            
            if self.send_json(update_payload):
                uptime = timedelta(seconds=int(time.time() - self.connected_at)) if self.connected_at else "N/A"
                current_time = datetime.now().strftime("%H:%M:%S")
                logger.info(f"💰 [{current_time}] Status: Fucking RICH 💸💸 | Uptime: {uptime}")
            else:
                logger.warning("⚠️ Failed to send status update")
                
        except Exception as e:
            logger.error(f"❌ Error updating status: {e}")
    
    def reconnect(self):
        """Reconnect to Discord"""
        logger.info("🔄 Reconnecting to Discord...")
        
        if self.ws:
            try:
                self.ws.close()
            except:
                pass
        
        time.sleep(2)
        
        # Reset some state but keep session info for resuming
        self.sequence = None
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
        """Stop the Discord connection gracefully"""
        logger.info("🛑 Stopping Discord keep-alive...")
        self.running = False
        if self.ws:
            try:
                # Send close frame
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
    logger.info(f"📡 Pinging {base_url}/ping every 3 minutes")
    
    failed_pings = 0
    max_failed_pings = 5
    
    while True:
        try:
            response = requests.get(f"{base_url}/ping", timeout=10)
            if response.status_code == 200:
                logger.debug(f"✅ Keep-alive ping successful")
                failed_pings = 0  # Reset counter
            else:
                logger.warning(f"⚠️ Ping returned status {response.status_code}")
                failed_pings += 1
                
        except requests.exceptions.ConnectionError:
            logger.warning("🌐 Flask server not responding, retrying...")
            failed_pings += 1
        except Exception as e:
            logger.error(f"❌ Ping error: {e}")
            failed_pings += 1
        
        # Check if we should restart
        if failed_pings >= max_failed_pings:
            logger.error(f"💥 Too many failed pings ({failed_pings}), system may need restart")
            # In a production environment, you might want to restart here
            # os._exit(1)  # This will cause Render to restart the service
        
        # Ping every 3 minutes (Render sleeps after 5 minutes of inactivity)
        time.sleep(180)

# ============================
# HEALTH MONITOR
# ============================
def health_monitor():
    """Monitor system health and log status periodically"""
    time.sleep(10)  # Wait for system to initialize
    
    while True:
        try:
            current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            logger.info(f"📊 System Status Check - {current_time}")
            logger.info(f"📈 Memory usage: {sys.getsizeof({})} bytes (sample)")
            logger.info("=" * 50)
        except:
            pass
        
        # Log status every hour
        time.sleep(3600)

# ============================
# MAIN FUNCTION
# ============================
def main():
    """Start all services"""
    print("=" * 60)
    print("Discord Keep-Alive System for Render")
    print("💰 Status: Fucking RICH 💸💸")
    print("💰 Goal: 24/7 Online")
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
        print("2. Press F12 → Application → Local Storage")
        print("3. Look for 'token' key")
        print("=" * 60)
        return
    
    logger.info("📦 Starting all services...")
    
    # 1. Start Flask server
    flask_thread = threading.Thread(target=start_flask, daemon=True)
    flask_thread.start()
    
    # 2. Start Render pinger
    pinger_thread = threading.Thread(target=render_pinger, daemon=True)
    pinger_thread.start()
    
    # 3. Start health monitor
    health_thread = threading.Thread(target=health_monitor, daemon=True)
    health_thread.start()
    
    # Wait for Flask to initialize
    time.sleep(3)
    
    # 4. Start Discord keep-alive
    try:
        discord_client = DiscordKeepAlive()
        discord_client.start()
        
        logger.info("✅ All services started successfully!")
        logger.info(f"🌐 Flask server: http://localhost:{PORT}")
        logger.info("💰 Discord status: Fucking RICH 💸💸")
        logger.info("🔄 Render pinger: Active (every 3 minutes)")
        logger.info("📊 Health monitor: Active")
        logger.info("")
        logger.info("📈 System is now running 24/7")
        logger.info("💪 Your Discord account will stay online continuously")
        logger.info("")
        logger.info("To stop: Press Ctrl+C or stop the service in Render dashboard")
        
        # Keep main thread alive and monitor
        try:
            while True:
                time.sleep(60)  # Check every minute
                # You can add additional monitoring here
                
        except KeyboardInterrupt:
            logger.info("👋 Shutting down gracefully...")
            discord_client.stop()
            time.sleep(2)
            logger.info("✅ Shutdown complete")
            
    except Exception as e:
        logger.error(f"💥 Failed to start: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()
