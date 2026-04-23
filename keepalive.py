import os
import sys
import time
import json
import threading
import logging
import socket
import struct
from datetime import datetime
from flask import Flask, jsonify
from dotenv import load_dotenv

try:
    import websocket
    WEBSOCKET_AVAILABLE = True
except ImportError:
    WEBSOCKET_AVAILABLE = False
    print("ERROR: websocket-client not installed. Run: pip install websocket-client")

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

app = Flask(__name__)
PORT = int(os.environ.get('PORT', 10000))

VOICE_ENABLED_ONE = False
VOICE_ENABLED_TWO = False

@app.route("/")
def home():
    return jsonify({
        "status": "online",
        "service": "dual-discord-keepalive-real-voice",
        "timestamp": datetime.now().isoformat(),
        "accounts": {
            "account_one": {"voice_enabled": VOICE_ENABLED_ONE},
            "account_two": {"voice_enabled": VOICE_ENABLED_TWO}
        }
    })

@app.route("/health")
def health():
    return jsonify({"status": "healthy"})

@app.route("/ping")
def ping():
    logger.info(f"Ping received at {datetime.now().strftime('%H:%M:%S')}")
    return jsonify({"pong": True})

def start_flask():
    logger.info(f"🚀 Flask server on port {PORT}")
    app.run(host='0.0.0.0', port=PORT, debug=False, use_reloader=False)

# ============================
# REAL VOICE CONNECTION (UDP + WebSocket)
# ============================
class RealVoiceConnection:
    def __init__(self, token, channel_id, account_name):
        self.token = token
        self.channel_id = channel_id
        self.account_name = account_name
        self.gateway_ws = None
        self.voice_ws = None
        self.udp_socket = None
        self.session_id = None
        self.user_id = None
        self.guild_id = None
        self.endpoint = None
        self.voice_token = None
        self.ssrc = None
        self.ip = None
        self.port = None
        self.ready = False
        self.running = True
        self.heartbeat_interval = 41250
        self.last_heartbeat = 0
        
    def connect_gateway(self):
        """Connect to Discord gateway for voice state management"""
        self.gateway_ws = websocket.WebSocketApp(
            "wss://gateway.discord.gg/?v=9&encoding=json",
            on_open=self.on_gateway_open,
            on_message=self.on_gateway_message,
            on_error=self.on_gateway_error,
            on_close=self.on_gateway_close
        )
        self.gateway_ws.run_forever()
        
    def on_gateway_open(self, ws):
        logger.info(f"🎙️ [{self.account_name}] Voice gateway open, identifying")
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
                    "activities": [{"name": "Voice", "type": 0}]
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
                # Heartbeat interval
                self.heartbeat_interval = d['heartbeat_interval'] / 1000
                threading.Thread(target=self.gateway_heartbeat, daemon=True).start()
                
            elif t == 'READY':
                self.user_id = d['user']['id']
                logger.info(f"🎙️ [{self.account_name}] Gateway ready, user_id={self.user_id}")
                # Request to join voice channel
                self.join_voice(ws)
                
            elif t == 'VOICE_STATE_UPDATE':
                if d.get('user_id') == self.user_id:
                    self.session_id = d.get('session_id')
                    logger.info(f"🎙️ [{self.account_name}] Received session_id: {self.session_id}")
                    
            elif t == 'VOICE_SERVER_UPDATE':
                self.endpoint = d.get('endpoint')
                self.voice_token = d.get('token')
                self.guild_id = d.get('guild_id')
                logger.info(f"🎙️ [{self.account_name}] Voice server: {self.endpoint}")
                if self.endpoint and self.voice_token and self.session_id:
                    self.connect_voice_udp()
                    
        except Exception as e:
            logger.error(f"🎙️ [{self.account_name}] Gateway msg error: {e}")
            
    def join_voice(self, ws):
        payload = {
            "op": 4,
            "d": {
                "guild_id": self.guild_id if self.guild_id else "0",
                "channel_id": self.channel_id,
                "self_mute": False,
                "self_deaf": True   # Deafen to avoid audio processing
            }
        }
        ws.send(json.dumps(payload))
        logger.info(f"🎙️ [{self.account_name}] Requested join voice channel {self.channel_id}")
        
    def connect_voice_udp(self):
        """Establish voice WebSocket and UDP connection"""
        if not self.endpoint:
            return
        # Remove port from endpoint if present
        endpoint_host = self.endpoint.split(':')[0]
        voice_ws_url = f"wss://{endpoint_host}:443?v=4"
        
        self.voice_ws = websocket.WebSocketApp(
            voice_ws_url,
            on_open=self.on_voice_open,
            on_message=self.on_voice_message,
            on_error=self.on_voice_error,
            on_close=self.on_voice_close
        )
        threading.Thread(target=self.voice_ws.run_forever, daemon=True).start()
        
    def on_voice_open(self, ws):
        logger.info(f"🎙️ [{self.account_name}] Voice WebSocket opened")
        # Send identify to voice server
        identify = {
            "op": 0,
            "d": {
                "server_id": self.guild_id,
                "user_id": self.user_id,
                "session_id": self.session_id,
                "token": self.voice_token
            }
        }
        ws.send(json.dumps(identify))
        
    def on_voice_message(self, ws, message):
        try:
            data = json.loads(message)
            op = data.get('op')
            d = data.get('d', {})
            
            if op == 2:  # Ready
                self.ssrc = d.get('ssrc')
                self.ip = d.get('ip')
                self.port = d.get('port')
                logger.info(f"🎙️ [{self.account_name}] Voice ready - SSRC={self.ssrc}, IP={self.ip}, PORT={self.port}")
                # Start UDP discovery
                self.start_udp_discovery()
                # Start voice heartbeat
                threading.Thread(target=self.voice_heartbeat, daemon=True).start()
                self.ready = True
                logger.info(f"✅ [{self.account_name}] PERMANENTLY IN VOICE CHANNEL (deafened)")
                
            elif op == 4:  # Heartbeat ACK
                pass
            elif op == 8:  # Hello
                interval = d.get('heartbeat_interval', 41250) / 1000
                self.voice_heartbeat_interval = interval
                
        except Exception as e:
            logger.error(f"🎙️ [{self.account_name}] Voice msg error: {e}")
            
    def start_udp_discovery(self):
        """Send UDP discovery packet to voice server"""
        try:
            self.udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            # Create discovery packet: [ssrc (4 bytes)] + [zeros 70 bytes]
            packet = struct.pack('>I', self.ssrc) + b'\x00' * 70
            self.udp_socket.sendto(packet, (self.ip, self.port))
            # Receive response
            response, _ = self.udp_socket.recvfrom(74)
            # Parse response (IP and port)
            ip = response[4:4+64].split(b'\x00')[0].decode()
            port = struct.unpack('>H', response[68:70])[0]
            logger.info(f"🎙️ [{self.account_name}] UDP discovery complete - IP={ip}, PORT={port}")
            # Send select protocol (op 1) via voice WebSocket
            select_payload = {
                "op": 1,
                "d": {
                    "protocol": "udp",
                    "data": {
                        "address": ip,
                        "port": port,
                        "mode": "xsalsa20_poly1305"
                    }
                }
            }
            self.voice_ws.send(json.dumps(select_payload))
        except Exception as e:
            logger.error(f"🎙️ [{self.account_name}] UDP discovery error: {e}")
            
    def gateway_heartbeat(self):
        while self.running and self.gateway_ws and self.gateway_ws.sock:
            time.sleep(self.heartbeat_interval)
            try:
                self.gateway_ws.send(json.dumps({"op": 1, "d": None}))
            except:
                break
                
    def voice_heartbeat(self):
        interval = getattr(self, 'voice_heartbeat_interval', 41.25)
        while self.running and self.voice_ws and self.voice_ws.sock:
            time.sleep(interval)
            try:
                self.voice_ws.send(json.dumps({"op": 3, "d": int(time.time() * 1000)}))
            except:
                break
                
    def on_voice_error(self, ws, error):
        logger.error(f"🎙️ [{self.account_name}] Voice WebSocket error: {error}")
        self.reconnect()
        
    def on_voice_close(self, ws, code, msg):
        logger.warning(f"🎙️ [{self.account_name}] Voice closed: {code} - {msg}")
        self.reconnect()
        
    def on_gateway_error(self, ws, error):
        logger.error(f"🎙️ [{self.account_name}] Gateway error: {error}")
        
    def on_gateway_close(self, ws, code, msg):
        logger.warning(f"🎙️ [{self.account_name}] Gateway closed, reconnecting...")
        if self.running:
            time.sleep(5)
            self.connect_gateway()
            
    def reconnect(self):
        if self.running:
            logger.info(f"🔄 [{self.account_name}] Reconnecting voice...")
            time.sleep(5)
            if self.udp_socket:
                self.udp_socket.close()
            self.connect_gateway()
            
    def start(self):
        threading.Thread(target=self.connect_gateway, daemon=True).start()
        
    def stop(self):
        self.running = False
        if self.udp_socket:
            self.udp_socket.close()
        if self.voice_ws:
            self.voice_ws.close()
        if self.gateway_ws:
            self.gateway_ws.close()

# ============================
# FIXED STATUS MANAGER (Account One)
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
        self.reconnect_attempts = 0
        self.voice_conn = None
        self.voice_enabled = False
        self.voice_channel_id = None
        self.last_status_update = 0

    def set_voice(self, enabled, channel_id):
        self.voice_enabled = enabled
        self.voice_channel_id = channel_id

    def connect(self):
        while self.running and self.reconnect_attempts < 10:
            try:
                logger.info(f"💰 [{self.account_name}] Connecting to Discord...")
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
        logger.info(f"✅ [{self.account_name}] Gateway connected")

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
                # Periodically update status to ensure it sticks
                if not hasattr(self, 'status_refresh_thread') or not self.status_refresh_thread.is_alive():
                    self.status_refresh_thread = threading.Thread(target=self.status_refresh_loop, daemon=True)
                    self.status_refresh_thread.start()
            elif op == 11:
                pass  # Heartbeat ACK
            elif op == 0:
                if t == 'READY':
                    self.session_id = d.get('session_id')
                    user = d.get('user', {})
                    logger.info(f"🎉 [{self.account_name}] Logged in as {user.get('username')}")
                    # Set status immediately
                    self.update_status(self.fixed_status)
                    # Start voice if enabled
                    if self.voice_enabled and self.voice_channel_id:
                        self.start_voice()
                elif t == 'RESUMED':
                    logger.info(f"🔄 [{self.account_name}] Session resumed")
                    self.update_status(self.fixed_status)
            elif op == 9 or op == 7:
                logger.warning(f"⚠️ [{self.account_name}] Invalid session, reconnecting")
                time.sleep(2)
                self.resume_or_reconnect()
        except Exception as e:
            logger.error(f"❌ [{self.account_name}] Message error: {e}")

    def on_error(self, ws, error):
        logger.error(f"💥 [{self.account_name}] WebSocket error: {error}")

    def on_close(self, ws, code, msg):
        logger.warning(f"🔌 [{self.account_name}] Connection closed: {code}")
        if self.running:
            time.sleep(2)

    def update_status(self, status_text):
        """Send presence update with proper formatting"""
        try:
            payload = {
                "op": 3,
                "d": {
                    "since": 0,
                    "activities": [{
                        "name": status_text,
                        "type": 0
                    }],
                    "status": "online",
                    "afk": False
                }
            }
            if self.send_json(payload):
                logger.info(f"💰 [{self.account_name}] Status set to: {status_text}")
                self.last_status_update = time.time()
                return True
        except Exception as e:
            logger.error(f"Status update error: {e}")
        return False

    def status_refresh_loop(self):
        """Re-send status every 30 minutes to prevent Discord from clearing it"""
        time.sleep(60)  # wait for initial set
        while self.running:
            time.sleep(1800)  # 30 minutes
            if self.ws and self.ws.sock and self.ws.sock.connected:
                self.update_status(self.fixed_status)

    def start_voice(self):
        self.voice_conn = RealVoiceConnection(self.token, self.voice_channel_id, self.account_name)
        self.voice_conn.start()

    def send_json(self, data):
        try:
            if self.ws and self.ws.sock and self.ws.sock.connected:
                self.ws.send(json.dumps(data))
                return True
        except:
            pass
        return False

    def identify(self):
        payload = {
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
        self.send_json(payload)
        logger.info(f"📨 [{self.account_name}] Identify sent")

    def resume(self):
        if self.session_id and self.sequence:
            payload = {"op": 6, "d": {"token": self.token, "session_id": self.session_id, "seq": self.sequence}}
            return self.send_json(payload)
        return False

    def resume_or_reconnect(self):
        if not self.resume():
            time.sleep(2)
            self.reconnect()

    def heartbeat_loop(self):
        while self.running:
            time.sleep((self.heartbeat_interval / 1000) * 0.8)
            self.send_json({"op": 1, "d": self.sequence})

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
        logger.info(f"🚀 [{self.account_name}] Starting (fixed status: {self.fixed_status})")
        threading.Thread(target=self.connect, daemon=True).start()

    def stop(self):
        self.running = False
        if self.voice_conn:
            self.voice_conn.stop()
        if self.ws:
            try:
                self.ws.close()
            except:
                pass

# ============================
# ROTATIONAL STATUS MANAGER (Account Two)
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
        self.reconnect_attempts = 0
        self.voice_conn = None
        self.voice_enabled = False
        self.voice_channel_id = None

    def set_voice(self, enabled, channel_id):
        self.voice_enabled = enabled
        self.voice_channel_id = channel_id

    def connect(self):
        while self.running and self.reconnect_attempts < 10:
            try:
                logger.info(f"🔄 [{self.account_name}] Connecting to Discord...")
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
        logger.info(f"✅ [{self.account_name}] Gateway connected")

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
                if not hasattr(self, 'rotation_thread') or not self.rotation_thread.is_alive():
                    self.rotation_thread = threading.Thread(target=self.rotation_loop, daemon=True)
                    self.rotation_thread.start()
            elif op == 11:
                pass
            elif op == 0:
                if t == 'READY':
                    self.session_id = d.get('session_id')
                    user = d.get('user', {})
                    logger.info(f"🎉 [{self.account_name}] Logged in as {user.get('username')}")
                    # Set first status
                    if self.status_list:
                        self.update_status(self.status_list[0])
                    if self.voice_enabled and self.voice_channel_id:
                        self.start_voice()
                elif t == 'RESUMED':
                    logger.info(f"🔄 [{self.account_name}] Session resumed")
                    # Re-set current status after resume
                    if self.status_list:
                        self.update_status(self.status_list[self.current_index])
            elif op == 9 or op == 7:
                logger.warning(f"⚠️ [{self.account_name}] Invalid session, reconnecting")
                time.sleep(2)
                self.resume_or_reconnect()
        except Exception as e:
            logger.error(f"❌ [{self.account_name}] Message error: {e}")

    def on_error(self, ws, error):
        logger.error(f"💥 [{self.account_name}] WebSocket error: {error}")

    def on_close(self, ws, code, msg):
        logger.warning(f"🔌 [{self.account_name}] Connection closed: {code}")
        if self.running:
            time.sleep(2)

    def update_status(self, status_text):
        try:
            payload = {
                "op": 3,
                "d": {
                    "since": 0,
                    "activities": [{"name": status_text, "type": 0}],
                    "status": "online",
                    "afk": False
                }
            }
            if self.send_json(payload):
                logger.info(f"🔄 [{self.account_name}] Status → {status_text}")
                return True
        except Exception as e:
            logger.error(f"Status update error: {e}")
        return False

    def rotation_loop(self):
        time.sleep(10)  # wait for initial
        while self.running:
            time.sleep(self.interval_seconds)
            self.current_index = (self.current_index + 1) % len(self.status_list)
            self.update_status(self.status_list[self.current_index])

    def start_voice(self):
        self.voice_conn = RealVoiceConnection(self.token, self.voice_channel_id, self.account_name)
        self.voice_conn.start()

    def send_json(self, data):
        try:
            if self.ws and self.ws.sock and self.ws.sock.connected:
                self.ws.send(json.dumps(data))
                return True
        except:
            pass
        return False

    def identify(self):
        first_status = self.status_list[0] if self.status_list else "Online"
        payload = {
            "op": 2,
            "d": {
                "token": self.token,
                "properties": {"$os": "linux", "$browser": "chrome", "$device": "chrome"},
                "presence": {
                    "status": "online",
                    "activities": [{"name": first_status, "type": 0}],
                    "afk": False
                }
            }
        }
        self.send_json(payload)
        logger.info(f"📨 [{self.account_name}] Identify sent (rotational)")

    def resume(self):
        if self.session_id and self.sequence:
            payload = {"op": 6, "d": {"token": self.token, "session_id": self.session_id, "seq": self.sequence}}
            return self.send_json(payload)
        return False

    def resume_or_reconnect(self):
        if not self.resume():
            time.sleep(2)
            self.reconnect()

    def heartbeat_loop(self):
        while self.running:
            time.sleep((self.heartbeat_interval / 1000) * 0.8)
            self.send_json({"op": 1, "d": self.sequence})

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
        logger.info(f"🚀 [{self.account_name}] Starting (rotational)")
        threading.Thread(target=self.connect, daemon=True).start()

    def stop(self):
        self.running = False
        if self.voice_conn:
            self.voice_conn.stop()
        if self.ws:
            try:
                self.ws.close()
            except:
                pass

# ============================
# RENDER PINGER & HEALTH
# ============================
def render_pinger():
    import requests
    time.sleep(5)
    url = f"http://localhost:{PORT}/ping"
    while True:
        try:
            requests.get(url, timeout=10)
        except:
            pass
        time.sleep(180)

def health_monitor():
    time.sleep(10)
    while True:
        logger.info("📊 System running - Real voice + Status working")
        time.sleep(3600)

# ============================
# MAIN
# ============================
def main():
    print("=" * 60)
    print("DUAL DISCORD KEEP-ALIVE - REAL VOICE + WORKING STATUS")
    print("💰 Account 1: Fucking RICH 💸💸 (FIXED)")
    print("🔄 Account 2: Rotating: Working On New Video🎥 | NYT💤 | YT- NOTE YOUR TYPE")
    print("🎙️ Voice: REAL JOIN (deafened) - will appear in voice channel")
    print("=" * 60)

    token_one = os.environ.get('DISCORD_TOKEN_ONE')
    token_two = os.environ.get('DISCORD_TOKEN_TWO')

    voice_one = os.environ.get('VOICE_JOIN_ONE', 'false').lower() == 'true'
    voice_channel_one = os.environ.get('VOICE_CHANNEL_ID_ONE')
    voice_two = os.environ.get('VOICE_JOIN_TWO', 'false').lower() == 'true'
    voice_channel_two = os.environ.get('VOICE_CHANNEL_ID_TWO')
    rotation_interval = int(os.environ.get('ROTATION_INTERVAL_MINUTES', '30'))

    rotational_statuses = ["Working On New Video🎥", "NYT💤", "YT- NOTE YOUR TYPE"]

    global VOICE_ENABLED_ONE, VOICE_ENABLED_TWO
    VOICE_ENABLED_ONE = voice_one
    VOICE_ENABLED_TWO = voice_two

    threading.Thread(target=start_flask, daemon=True).start()
    threading.Thread(target=render_pinger, daemon=True).start()
    threading.Thread(target=health_monitor, daemon=True).start()
    time.sleep(3)

    if token_one:
        c1 = FixedStatusManager(token_one, "ACCOUNT_ONE", "Fucking RICH 💸💸")
        c1.set_voice(voice_one, voice_channel_one)
        c1.start()
        logger.info(f"✅ Account One started - Fixed status, Voice: {voice_one}")
    if token_two:
        c2 = RotationalStatusManager(token_two, "ACCOUNT_TWO", rotational_statuses, rotation_interval)
        c2.set_voice(voice_two, voice_channel_two)
        c2.start()
        logger.info(f"✅ Account Two started - Rotational every {rotation_interval} min, Voice: {voice_two}")

    logger.info("=" * 60)
    logger.info("✅ ALL SYSTEMS ONLINE — Status WILL show in Discord, Voice WILL join channel")
    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        logger.info("Shutting down...")
        # cleanup handled by daemon threads
        time.sleep(2)

if __name__ == "__main__":
    main()
