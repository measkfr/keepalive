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

# Global vars for Flask route
VOICE_ENABLED_ONE = False
VOICE_ENABLED_TWO = False

@app.route("/")
def home():
    return jsonify({
        "status": "online",
        "service": "dual-discord-keepalive-rotational",
        "timestamp": datetime.now().isoformat(),
        "websocket": WEBSOCKET_AVAILABLE,
        "accounts": {
            "account_one": {"voice_enabled": VOICE_ENABLED_ONE, "status_type": "fixed"},
            "account_two": {"voice_enabled": VOICE_ENABLED_TWO, "status_type": "rotational"}
        }
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
# VOICE CONNECTION HANDLER
# ============================
class VoiceConnection:
    def __init__(self, token, channel_id, account_name):
        self.token = token
        self.channel_id = channel_id
        self.account_name = account_name
        self.ws = None
        self.voice_ws = None
        self.session_id = None
        self.user_id = None
        self.guild_id = None
        self.endpoint = None
        self.ready = False
        self.running = True
        self.reconnect_attempts = 0
        
    def connect(self):
        """Connect to Discord and join voice channel"""
        self.ws = websocket.WebSocketApp(
            "wss://gateway.discord.gg/?v=9&encoding=json",
            on_open=self.on_gateway_open,
            on_message=self.on_gateway_message,
            on_error=self.on_gateway_error,
            on_close=self.on_gateway_close
        )
        self.ws.run_forever()
    
    def on_gateway_open(self, ws):
        logger.info(f"🎙️ [{self.account_name}] Gateway connected for voice")
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
                    "activities": [{"name": "Fucking RICH 💸💸", "type": 0}]
                }
            }
        }
        ws.send(json.dumps(identify))
    
    def on_gateway_message(self, ws, message):
        try:
            data = json.loads(message)
            op = data.get('op')
            t = data.get('t')
            d = data.get('d', {})
            
            if op == 10:
                def heartbeat():
                    while self.running:
                        time.sleep(d['heartbeat_interval'] / 1000)
                        if ws.sock and ws.sock.connected:
                            ws.send(json.dumps({"op": 1, "d": None}))
                threading.Thread(target=heartbeat, daemon=True).start()
                
            elif t == 'READY':
                self.user_id = d['user']['id']
                logger.info(f"🎙️ [{self.account_name}] Ready! User ID: {self.user_id}")
                self.join_voice()
                
            elif t == 'VOICE_STATE_UPDATE':
                if d.get('user_id') == self.user_id:
                    session_id = d.get('session_id')
                    if session_id:
                        self.session_id = session_id
                        logger.info(f"🎙️ [{self.account_name}] Got session ID: {self.session_id}")
                        
            elif t == 'VOICE_SERVER_UPDATE':
                self.endpoint = d.get('endpoint')
                token = d.get('token')
                self.guild_id = d.get('guild_id')
                
                if self.endpoint and token and self.session_id:
                    logger.info(f"🎙️ [{self.account_name}] Voice server ready: {self.endpoint}")
                    self.connect_to_voice_udp(token)
                    
        except Exception as e:
            logger.error(f"🎙️ [{self.account_name}] Gateway message error: {e}")
    
    def join_voice(self):
        """Send voice state update to join voice channel"""
        payload = {
            "op": 4,
            "d": {
                "guild_id": self.guild_id if self.guild_id else "0",
                "channel_id": self.channel_id,
                "self_mute": False,
                "self_deaf": True
            }
        }
        if self.ws and self.ws.sock:
            self.ws.send(json.dumps(payload))
            logger.info(f"🎙️ [{self.account_name}] Attempting to join voice channel: {self.channel_id}")
    
    def connect_to_voice_udp(self, token):
        """Connect to voice WebSocket"""
        if not self.endpoint:
            return
            
        voice_ws_url = f"wss://{self.endpoint}?v=4"
        
        self.voice_ws = websocket.WebSocketApp(
            voice_ws_url,
            on_open=lambda ws: self.on_voice_open(ws, token),
            on_message=self.on_voice_message,
            on_error=self.on_voice_error,
            on_close=self.on_voice_close
        )
        
        threading.Thread(target=self.voice_ws.run_forever, daemon=True).start()
    
    def on_voice_open(self, ws, token):
        logger.info(f"🎙️ [{self.account_name}] Voice WebSocket opened")
        identify = {
            "op": 0,
            "d": {
                "server_id": self.guild_id,
                "user_id": self.user_id,
                "session_id": self.session_id,
                "token": token
            }
        }
        ws.send(json.dumps(identify))
        self.ready = True
        logger.info(f"✅ [{self.account_name}] PERMANENTLY JOINED VOICE CHANNEL - WILL NEVER LEAVE")
    
    def on_voice_message(self, ws, message):
        try:
            data = json.loads(message)
            op = data.get('op')
            if op == 2:
                logger.info(f"🎙️ [{self.account_name}] Voice connection ready - permanently in channel")
            elif op == 4:
                ws.send(json.dumps({"op": 3, "d": int(time.time() * 1000)}))
        except:
            pass
    
    def on_voice_error(self, ws, error):
        logger.error(f"🎙️ [{self.account_name}] Voice WebSocket error: {error}")
        self.reconnect_voice()
    
    def on_voice_close(self, ws, close_status, close_msg):
        logger.warning(f"🎙️ [{self.account_name}] Voice connection closed: {close_status} - {close_msg}")
        self.reconnect_voice()
    
    def on_gateway_error(self, ws, error):
        logger.error(f"🎙️ [{self.account_name}] Gateway error: {error}")
    
    def on_gateway_close(self, ws, close_status, close_msg):
        logger.warning(f"🎙️ [{self.account_name}] Gateway closed")
        if self.running:
            self.reconnect()
    
    def reconnect_voice(self):
        if self.running:
            logger.info(f"🔄 [{self.account_name}] Rejoining voice channel...")
            time.sleep(5)
            self.join_voice()
    
    def reconnect(self):
        if self.running:
            logger.info(f"🔄 [{self.account_name}] Full reconnection...")
            time.sleep(5)
            self.connect()
    
    def start(self):
        voice_thread = threading.Thread(target=self.connect, daemon=True)
        voice_thread.start()
    
    def stop(self):
        self.running = False

# ============================
# ROTATIONAL STATUS MANAGER
# ============================
class RotationalStatusManager:
    def __init__(self, token, account_name, status_list, interval_minutes=30):
        self.token = token
        self.account_name = account_name
        self.status_list = status_list
        self.interval_seconds = interval_minutes * 60
        self.current_index = 0
        self.ws = None
        self.sequence = None
        self.heartbeat_interval = 41250
        self.session_id = None
        self.running = True
        self.last_heartbeat_ack = time.time()
        self.reconnect_attempts = 0
        self.voice_connection = None
        self.voice_enabled = False
        self.voice_channel_id = None
        
    def set_voice(self, enabled, channel_id):
        self.voice_enabled = enabled
        self.voice_channel_id = channel_id
        
    def connect(self):
        while self.running and self.reconnect_attempts < 10:
            try:
                logger.info(f"🔄 [{self.account_name}] Connecting to Discord Gateway...")
                
                self.ws = websocket.WebSocketApp(
                    "wss://gateway.discord.gg/?v=9&encoding=json",
                    on_open=self.on_open,
                    on_message=self.on_message,
                    on_error=self.on_error,
                    on_close=self.on_close
                )
                
                self.ws.run_forever(ping_interval=30, ping_timeout=10, reconnect=5)
                
                if self.running:
                    self.reconnect_attempts += 1
                    time.sleep(min(2 ** self.reconnect_attempts, 30))
                    
            except Exception as e:
                logger.error(f"💥 [{self.account_name}] Connection error: {e}")
                self.reconnect_attempts += 1
                time.sleep(min(2 ** self.reconnect_attempts, 30))
    
    def on_open(self, ws):
        self.reconnect_attempts = 0
        logger.info(f"✅ [{self.account_name}] Connected to Discord Gateway")
    
    def on_message(self, ws, message):
        try:
            data = json.loads(message)
            op = data.get('op')
            t = data.get('t')
            d = data.get('d', {})
            
            if data.get('s'):
                self.sequence = data['s']
            
            if op == 10:
                self.heartbeat_interval = d['heartbeat_interval']
                self.identify()
                
                if not hasattr(self, 'heartbeat_thread') or not self.heartbeat_thread.is_alive():
                    self.heartbeat_thread = threading.Thread(target=self.heartbeat_loop, daemon=True)
                    self.heartbeat_thread.start()
                    
                # Start rotation loop
                if not hasattr(self, 'rotation_thread') or not self.rotation_thread.is_alive():
                    self.rotation_thread = threading.Thread(target=self.rotation_loop, daemon=True)
                    self.rotation_thread.start()
                
            elif op == 11:
                self.last_heartbeat_ack = time.time()
                
            elif op == 0:
                if t == 'READY':
                    self.session_id = d.get('session_id')
                    user = d.get('user', {})
                    logger.info(f"🎉 [{self.account_name}] Ready! User: {user.get('username')}")
                    logger.info(f"🔄 [{self.account_name}] Rotational status active: {self.status_list}")
                    
                    # Start voice if enabled
                    if self.voice_enabled and self.voice_channel_id:
                        logger.info(f"🎙️ [{self.account_name}] Voice mode ENABLED")
                        self.start_voice()
                    
                elif t == 'RESUMED':
                    logger.info(f"🔄 [{self.account_name}] Session resumed")
                    
            elif op == 9 or op == 7:
                logger.warning(f"⚠️ [{self.account_name}] Invalid session/reconnect requested")
                time.sleep(2)
                self.resume_or_reconnect()
                
        except Exception as e:
            logger.error(f"❌ [{self.account_name}] Message error: {e}")
    
    def start_voice(self):
        if self.voice_channel_id:
            self.voice_connection = VoiceConnection(
                self.token, 
                self.voice_channel_id, 
                self.account_name
            )
            self.voice_connection.start()
    
    def update_status(self, status_name):
        """Update Discord status to the given status"""
        try:
            update_payload = {
                "op": 3,
                "d": {
                    "since": 0,
                    "activities": [{
                        "name": status_name,
                        "type": 0,
                        "created_at": int(time.time() * 1000)
                    }],
                    "status": "online",
                    "afk": False
                }
            }
            
            if self.send_json(update_payload):
                current_time = datetime.now().strftime("%H:%M:%S")
                logger.info(f"🔄 [{self.account_name}] [{current_time}] Status rotated to: {status_name}")
                return True
            return False
        except Exception as e:
            logger.error(f"❌ [{self.account_name}] Status update error: {e}")
            return False
    
    def rotation_loop(self):
        """Loop through statuses at specified interval"""
        # Wait for initial connection
        time.sleep(10)
        
        # Set first status
        if self.status_list:
            self.update_status(self.status_list[0])
        
        while self.running:
            time.sleep(self.interval_seconds)
            
            # Rotate to next status
            self.current_index = (self.current_index + 1) % len(self.status_list)
            next_status = self.status_list[self.current_index]
            self.update_status(next_status)
    
    def send_json(self, data):
        try:
            if self.ws and self.ws.sock and self.ws.sock.connected:
                self.ws.send(json.dumps(data))
                return True
        except:
            pass
        return False
    
    def identify(self):
        identify_payload = {
            "op": 2,
            "d": {
                "token": self.token,
                "properties": {"$os": "linux", "$browser": "chrome", "$device": "chrome"},
                "presence": {
                    "status": "online",
                    "activities": [{"name": self.status_list[0] if self.status_list else "Online", "type": 0}],
                    "afk": False
                }
            }
        }
        self.send_json(identify_payload)
        logger.info(f"📨 [{self.account_name}] Identified with rotational status system")
    
    def resume(self):
        if self.session_id and self.sequence:
            resume_payload = {"op": 6, "d": {"token": self.token, "session_id": self.session_id, "seq": self.sequence}}
            return self.send_json(resume_payload)
        return False
    
    def resume_or_reconnect(self):
        if not self.resume():
            time.sleep(2)
            self.reconnect()
    
    def heartbeat_loop(self):
        while self.running:
            time.sleep((self.heartbeat_interval / 1000) * 0.8)
            heartbeat = {"op": 1, "d": self.sequence}
            self.send_json(heartbeat)
    
    def reconnect(self):
        logger.info(f"🔄 [{self.account_name}] Reconnecting...")
        if self.ws:
            try:
                self.ws.close()
            except:
                pass
        time.sleep(2)
        self.sequence = None
        self.connect()
    
    def start(self):
        logger.info(f"🚀 [{self.account_name}] Starting with rotational status...")
        self.connection_thread = threading.Thread(target=self.connect, daemon=True)
        self.connection_thread.start()
    
    def stop(self):
        self.running = False
        if self.voice_connection:
            self.voice_connection.stop()
        if self.ws:
            try:
                self.ws.close()
            except:
                pass

# ============================
# FIXED STATUS MANAGER (ACCOUNT ONE)
# ============================
class FixedStatusManager:
    def __init__(self, token, account_name, fixed_status):
        self.token = token
        self.account_name = account_name
        self.fixed_status = fixed_status
        self.ws = None
        self.sequence = None
        self.heartbeat_interval = 41250
        self.session_id = None
        self.running = True
        self.last_heartbeat_ack = time.time()
        self.reconnect_attempts = 0
        self.voice_connection = None
        self.voice_enabled = False
        self.voice_channel_id = None
        
    def set_voice(self, enabled, channel_id):
        self.voice_enabled = enabled
        self.voice_channel_id = channel_id
        
    def connect(self):
        while self.running and self.reconnect_attempts < 10:
            try:
                logger.info(f"🌐 [{self.account_name}] Connecting to Discord Gateway...")
                
                self.ws = websocket.WebSocketApp(
                    "wss://gateway.discord.gg/?v=9&encoding=json",
                    on_open=self.on_open,
                    on_message=self.on_message,
                    on_error=self.on_error,
                    on_close=self.on_close
                )
                
                self.ws.run_forever(ping_interval=30, ping_timeout=10, reconnect=5)
                
                if self.running:
                    self.reconnect_attempts += 1
                    time.sleep(min(2 ** self.reconnect_attempts, 30))
                    
            except Exception as e:
                logger.error(f"💥 [{self.account_name}] Connection error: {e}")
                self.reconnect_attempts += 1
                time.sleep(min(2 ** self.reconnect_attempts, 30))
    
    def on_open(self, ws):
        self.reconnect_attempts = 0
        logger.info(f"✅ [{self.account_name}] Connected to Discord Gateway")
    
    def on_message(self, ws, message):
        try:
            data = json.loads(message)
            op = data.get('op')
            t = data.get('t')
            d = data.get('d', {})
            
            if data.get('s'):
                self.sequence = data['s']
            
            if op == 10:
                self.heartbeat_interval = d['heartbeat_interval']
                self.identify()
                
                if not hasattr(self, 'heartbeat_thread') or not self.heartbeat_thread.is_alive():
                    self.heartbeat_thread = threading.Thread(target=self.heartbeat_loop, daemon=True)
                    self.heartbeat_thread.start()
                
            elif op == 11:
                self.last_heartbeat_ack = time.time()
                
            elif op == 0:
                if t == 'READY':
                    self.session_id = d.get('session_id')
                    user = d.get('user', {})
                    logger.info(f"🎉 [{self.account_name}] Ready! User: {user.get('username')}")
                    logger.info(f"💰 [{self.account_name}] Fixed status: {self.fixed_status}")
                    
                    if self.voice_enabled and self.voice_channel_id:
                        logger.info(f"🎙️ [{self.account_name}] Voice mode ENABLED")
                        self.start_voice()
                    
                elif t == 'RESUMED':
                    logger.info(f"🔄 [{self.account_name}] Session resumed")
                    
            elif op == 9 or op == 7:
                logger.warning(f"⚠️ [{self.account_name}] Invalid session/reconnect requested")
                time.sleep(2)
                self.resume_or_reconnect()
                
        except Exception as e:
            logger.error(f"❌ [{self.account_name}] Message error: {e}")
    
    def start_voice(self):
        if self.voice_channel_id:
            self.voice_connection = VoiceConnection(
                self.token, 
                self.voice_channel_id, 
                self.account_name
            )
            self.voice_connection.start()
    
    def send_json(self, data):
        try:
            if self.ws and self.ws.sock and self.ws.sock.connected:
                self.ws.send(json.dumps(data))
                return True
        except:
            pass
        return False
    
    def identify(self):
        identify_payload = {
            "op": 2,
            "d": {
                "token": self.token,
                "properties": {"$os": "linux", "$browser": "chrome", "$device": "chrome"},
                "presence": {
                    "status": "online",
                    "activities": [{"name": self.fixed_status, "type": 0}],
                    "afk": False
                }
            }
        }
        self.send_json(identify_payload)
        logger.info(f"📨 [{self.account_name}] Identified with fixed status: {self.fixed_status}")
    
    def resume(self):
        if self.session_id and self.sequence:
            resume_payload = {"op": 6, "d": {"token": self.token, "session_id": self.session_id, "seq": self.sequence}}
            return self.send_json(resume_payload)
        return False
    
    def resume_or_reconnect(self):
        if not self.resume():
            time.sleep(2)
            self.reconnect()
    
    def heartbeat_loop(self):
        while self.running:
            time.sleep((self.heartbeat_interval / 1000) * 0.8)
            heartbeat = {"op": 1, "d": self.sequence}
            self.send_json(heartbeat)
    
    def reconnect(self):
        logger.info(f"🔄 [{self.account_name}] Reconnecting...")
        if self.ws:
            try:
                self.ws.close()
            except:
                pass
        time.sleep(2)
        self.sequence = None
        self.connect()
    
    def start(self):
        logger.info(f"🚀 [{self.account_name}] Starting with fixed status...")
        self.connection_thread = threading.Thread(target=self.connect, daemon=True)
        self.connection_thread.start()
    
    def stop(self):
        self.running = False
        if self.voice_connection:
            self.voice_connection.stop()
        if self.ws:
            try:
                self.ws.close()
            except:
                pass

# ============================
# RENDER KEEP-ALIVE PINGER
# ============================
def render_pinger():
    import requests
    time.sleep(5)
    base_url = f"http://localhost:{PORT}"
    
    while True:
        try:
            requests.get(f"{base_url}/ping", timeout=10)
            logger.debug("✅ Keep-alive ping sent")
        except:
            pass
        time.sleep(180)

# ============================
# HEALTH MONITOR
# ============================
def health_monitor():
    time.sleep(10)
    while True:
        logger.info("📊 System Status - Dual Account (Fixed + Rotational)")
        time.sleep(3600)

# ============================
# MAIN FUNCTION
# ============================
def main():
    print("=" * 60)
    print("DUAL DISCORD KEEP-ALIVE SYSTEM")
    print("💰 Account 1: Fucking RICH 💸💸 (FIXED)")
    print("🔄 Account 2: Rotational Status")
    print("   1. Working On New Video🎥")
    print("   2. NYT💤")
    print("   3. YT- NOTE YOUR TYPE")
    print("=" * 60)
    print()
    
    # Load tokens
    token_one = os.environ.get('DISCORD_TOKEN_ONE')
    token_two = os.environ.get('DISCORD_TOKEN_TWO')
    
    # Voice settings for account one
    voice_enabled_one = os.environ.get('VOICE_JOIN_ONE', 'false').lower() == 'true'
    voice_channel_one = os.environ.get('VOICE_CHANNEL_ID_ONE')
    
    # Voice settings for account two
    voice_enabled_two = os.environ.get('VOICE_JOIN_TWO', 'false').lower() == 'true'
    voice_channel_two = os.environ.get('VOICE_CHANNEL_ID_TWO')
    
    # Rotation interval (default 30 minutes)
    rotation_interval = int(os.environ.get('ROTATION_INTERVAL_MINUTES', '30'))
    
    # Rotational statuses for account two
    rotational_statuses = [
        "Working On New Video🎥",
        "NYT💤",
        "YT- NOTE YOUR TYPE"
    ]
    
    # Validate tokens
    if not token_one and not token_two:
        print("❌ ERROR: At least one DISCORD_TOKEN is required!")
        print()
        print("=" * 60)
        return
    
    # Set global vars for Flask
    global VOICE_ENABLED_ONE, VOICE_ENABLED_TWO
    VOICE_ENABLED_ONE = voice_enabled_one
    VOICE_ENABLED_TWO = voice_enabled_two
    
    logger.info("📦 Starting all services...")
    
    # Start Flask server
    flask_thread = threading.Thread(target=start_flask, daemon=True)
    flask_thread.start()
    
    # Start Render pinger
    pinger_thread = threading.Thread(target=render_pinger, daemon=True)
    pinger_thread.start()
    
    # Start health monitor
    health_thread = threading.Thread(target=health_monitor, daemon=True)
    health_thread.start()
    
    time.sleep(3)
    
    # Start Discord clients
    clients = []
    
    if token_one:
        client_one = FixedStatusManager(token_one, "ACCOUNT_ONE", "Fucking RICH 💸💸")
        client_one.set_voice(voice_enabled_one, voice_channel_one)
        client_one.start()
        clients.append(client_one)
        logger.info(f"✅ Account One started - Status: Fucking RICH 💸💸 (FIXED)")
        logger.info(f"   Voice: {'ENABLED' if voice_enabled_one else 'DISABLED'}")
        if voice_enabled_one:
            logger.info(f"   Voice Channel ID: {voice_channel_one}")
    
    if token_two:
        client_two = RotationalStatusManager(token_two, "ACCOUNT_TWO", rotational_statuses, rotation_interval)
        client_two.set_voice(voice_enabled_two, voice_channel_two)
        client_two.start()
        clients.append(client_two)
        logger.info(f"✅ Account Two started - Status: ROTATIONAL (30 min interval)")
        logger.info(f"   Statuses: {', '.join(rotational_statuses)}")
        logger.info(f"   Voice: {'ENABLED' if voice_enabled_two else 'DISABLED'}")
        if voice_enabled_two:
            logger.info(f"   Voice Channel ID: {voice_channel_two}")
    
    logger.info("")
    logger.info("=" * 60)
    logger.info("✅ ALL SERVICES STARTED SUCCESSFULLY!")
    logger.info("💰 Account One: Fucking RICH 💸💸 (Fixed)")
    logger.info("🔄 Account Two: Rotating every 30 minutes")
    logger.info("   1. Working On New Video🎥")
    logger.info("   2. NYT💤")
    logger.info("   3. YT- NOTE YOUR TYPE")
    if voice_enabled_one or voice_enabled_two:
        logger.info("🎙️ Voice accounts will NEVER LEAVE the channel (auto-rejoin)")
    logger.info("=" * 60)
    
    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        logger.info("👋 Shutting down...")
        for client in clients:
            client.stop()
        time.sleep(2)
        logger.info("✅ Shutdown complete")

if __name__ == "__main__":
    main()
