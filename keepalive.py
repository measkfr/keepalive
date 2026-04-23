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
    print("ERROR: websocket-client not installed")
    sys.exit(1)

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

app = Flask(__name__)
PORT = int(os.environ.get('PORT', 10000))

@app.route('/')
def home():
    return jsonify({
        "status": "online",
        "service": "permanent-discord-voice",
        "timestamp": datetime.now().isoformat()
    })

@app.route('/health')
def health():
    return jsonify({"status": "healthy"})

@app.route('/ping')
def ping():
    logger.info(f"Ping at {datetime.now().strftime('%H:%M:%S')}")
    return jsonify({"pong": True})

def start_flask():
    app.run(host='0.0.0.0', port=PORT, debug=False, use_reloader=False)

# ============================
# COMPLETE VOICE CONNECTION (REAL JOIN + PERMANENT KEEPALIVE)
# ============================
class PermanentVoiceConnection:
    def __init__(self, token, guild_id, channel_id, account_name):
        self.token = token
        self.guild_id = guild_id
        self.channel_id = channel_id
        self.account_name = account_name
        
        self.gateway_ws = None
        self.voice_ws = None
        self.udp_socket = None
        
        self.session_id = None
        self.user_id = None
        self.endpoint = None
        self.voice_token = None
        self.ssrc = None
        self.voice_ip = None
        self.voice_port = None
        self.heartbeat_interval = 41250
        self.running = True
        self.connected_voice = False
        self.last_voice_check = 0
        
        # Lock to prevent concurrent rejoin attempts
        self.rejoin_lock = threading.Lock()
        
    def start(self):
        self.connect_gateway()
        # Start 30-second monitor
        threading.Thread(target=self.voice_keepalive_monitor, daemon=True).start()
        
    def connect_gateway(self):
        self.gateway_ws = websocket.WebSocketApp(
            "wss://gateway.discord.gg/?v=9&encoding=json",
            on_open=self.on_gateway_open,
            on_message=self.on_gateway_message,
            on_error=self.on_gateway_error,
            on_close=self.on_gateway_close
        )
        self.gateway_ws.run_forever()
        
    def on_gateway_open(self, ws):
        logger.info(f"🎙️ [{self.account_name}] Voice gateway open")
        identify = {
            "op": 2,
            "d": {
                "token": self.token,
                "properties": {
                    "$os": "linux",
                    "$browser": "DiscordKeepAlive",
                    "$device": "DiscordKeepAlive"
                },
                "presence": {
                    "status": "online",
                    "activities": [{"name": "In VC", "type": 0}],
                    "afk": False
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
                self.heartbeat_interval = d['heartbeat_interval'] / 1000
                threading.Thread(target=self.gateway_heartbeat, daemon=True).start()
                
            elif t == 'READY':
                self.user_id = d['user']['id']
                logger.info(f"🎙️ [{self.account_name}] Voice ready, user_id={self.user_id}")
                self.join_voice(ws)
                
            elif t == 'VOICE_STATE_UPDATE':
                if d.get('user_id') == self.user_id:
                    self.session_id = d.get('session_id')
                    logger.info(f"🎙️ [{self.account_name}] Voice session_id={self.session_id}")
                    
            elif t == 'VOICE_SERVER_UPDATE':
                self.endpoint = d.get('endpoint')
                self.voice_token = d.get('token')
                logger.info(f"🎙️ [{self.account_name}] Voice endpoint={self.endpoint}")
                if self.endpoint and self.voice_token and self.session_id:
                    self.connect_voice()
                    
        except Exception as e:
            logger.error(f"🎙️ [{self.account_name}] Gateway msg error: {e}")
            
    def join_voice(self, ws):
        payload = {
            "op": 4,
            "d": {
                "guild_id": self.guild_id,
                "channel_id": self.channel_id,
                "self_mute": False,
                "self_deaf": True
            }
        }
        ws.send(json.dumps(payload))
        logger.info(f"🎙️ [{self.account_name}] Joining channel {self.channel_id} (guild {self.guild_id})")
        
    # Public method to force rejoin (used by monitor)
    def force_rejoin(self):
        with self.rejoin_lock:
            if self.gateway_ws and self.gateway_ws.sock and self.gateway_ws.sock.connected:
                logger.warning(f"⚠️ [{self.account_name}] Force rejoin triggered")
                self.join_voice(self.gateway_ws)
            else:
                logger.warning(f"⚠️ [{self.account_name}] Cannot rejoin - gateway not connected, will reconnect")
                self.reconnect()
        
    def connect_voice(self):
        endpoint_host = self.endpoint.split(':')[0]
        voice_url = f"wss://{endpoint_host}:443?v=4"
        self.voice_ws = websocket.WebSocketApp(
            voice_url,
            on_open=self.on_voice_open,
            on_message=self.on_voice_message,
            on_error=self.on_voice_error,
            on_close=self.on_voice_close
        )
        threading.Thread(target=self.voice_ws.run_forever, daemon=True).start()
        
    def on_voice_open(self, ws):
        logger.info(f"🎙️ [{self.account_name}] Voice WebSocket open")
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
            
            if op == 2:
                self.ssrc = d.get('ssrc')
                self.voice_ip = d.get('ip')
                self.voice_port = d.get('port')
                logger.info(f"🎙️ [{self.account_name}] Voice ready, SSRC={self.ssrc}")
                self.udp_discovery()
                self.connected_voice = True
            elif op == 4:
                pass
            elif op == 8:
                interval = d.get('heartbeat_interval', 41250) / 1000
                threading.Thread(target=self.voice_heartbeat, args=(ws, interval), daemon=True).start()
        except Exception as e:
            logger.error(f"🎙️ [{self.account_name}] Voice msg error: {e}")
            
    def udp_discovery(self):
        try:
            self.udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            packet = struct.pack('>I', self.ssrc) + b'\x00' * 70
            self.udp_socket.sendto(packet, (self.voice_ip, self.voice_port))
            response, _ = self.udp_socket.recvfrom(74)
            ip = response[4:68].split(b'\x00')[0].decode()
            port = struct.unpack('>H', response[68:70])[0]
            logger.info(f"🎙️ [{self.account_name}] UDP discovery: IP={ip}, Port={port}")
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
            logger.info(f"✅ [{self.account_name}] PERMANENTLY IN VOICE CHANNEL (deafened)")
        except Exception as e:
            logger.error(f"🎙️ [{self.account_name}] UDP error: {e}")
            self.connected_voice = False
            
    def gateway_heartbeat(self):
        while self.running and self.gateway_ws and self.gateway_ws.sock:
            time.sleep(self.heartbeat_interval)
            try:
                self.gateway_ws.send(json.dumps({"op": 1, "d": None}))
            except:
                break
                
    def voice_heartbeat(self, ws, interval):
        while self.running and ws and ws.sock:
            time.sleep(interval)
            try:
                ws.send(json.dumps({"op": 3, "d": int(time.time() * 1000)}))
            except:
                break
                
    def voice_keepalive_monitor(self):
        """Check every 30 seconds if still connected to voice; if not, rejoin."""
        while self.running:
            time.sleep(30)  # Check every 30 seconds
            if not self.connected_voice:
                logger.warning(f"⚠️ [{self.account_name}] Voice not connected! Rejoining...")
                self.force_rejoin()
            else:
                logger.debug(f"✅ [{self.account_name}] Voice still active")
                
    def on_voice_error(self, ws, error):
        logger.error(f"🎙️ [{self.account_name}] Voice WS error: {error}")
        self.connected_voice = False
    def on_voice_close(self, ws, code, msg):
        logger.warning(f"🎙️ [{self.account_name}] Voice closed: {code} - {msg}")
        self.connected_voice = False
    def on_gateway_error(self, ws, error):
        logger.error(f"🎙️ [{self.account_name}] Gateway error: {error}")
    def on_gateway_close(self, ws, code, msg):
        logger.warning(f"🎙️ [{self.account_name}] Gateway closed: {code} - {msg}")
        self.connected_voice = False
        if self.running:
            self.reconnect()
    def reconnect(self):
        logger.info(f"🔄 [{self.account_name}] Reconnecting voice...")
        time.sleep(5)
        if self.udp_socket:
            self.udp_socket.close()
        self.connect_gateway()
    def stop(self):
        self.running = False
        if self.udp_socket:
            self.udp_socket.close()
        if self.voice_ws:
            self.voice_ws.close()
        if self.gateway_ws:
            self.gateway_ws.close()

# ============================
# STANDALONE DISCORD CLIENT (WITH PERMANENT VOICE MONITOR)
# ============================
class StandaloneDiscordClient:
    def __init__(self, token, account_name, fixed_status=None, rotating_statuses=None, interval_minutes=30):
        self.token = token
        self.account_name = account_name
        self.fixed_status = fixed_status
        self.rotating_statuses = rotating_statuses
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
        self.voice_guild_id = None
        self.voice_channel_id = None
        
    def set_voice(self, enabled, guild_id, channel_id):
        self.voice_enabled = enabled
        self.voice_guild_id = guild_id
        self.voice_channel_id = channel_id
        
    def connect(self):
        while self.running and self.reconnect_attempts < 10:
            try:
                logger.info(f"{'💰' if self.fixed_status else '🔄'} [{self.account_name}] Connecting to Discord...")
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
                threading.Thread(target=self.heartbeat_loop, daemon=True).start()
                if self.rotating_statuses:
                    threading.Thread(target=self.rotation_loop, daemon=True).start()
                if self.fixed_status:
                    threading.Thread(target=self.status_refresh, daemon=True).start()
            elif op == 11:
                pass
            elif op == 0:
                if t == 'READY':
                    self.session_id = d.get('session_id')
                    user = d.get('user', {})
                    logger.info(f"🎉 [{self.account_name}] Logged in as {user.get('username')} (ID: {user.get('id')})")
                    if self.fixed_status:
                        self.update_status(self.fixed_status)
                    elif self.rotating_statuses:
                        self.update_status(self.rotating_statuses[0])
                    if self.voice_enabled and self.voice_channel_id:
                        self.start_voice()
                elif t == 'RESUMED':
                    logger.info(f"🔄 [{self.account_name}] Session resumed")
                    if self.fixed_status:
                        self.update_status(self.fixed_status)
                    elif self.rotating_statuses:
                        self.update_status(self.rotating_statuses[self.current_index])
            elif op == 9 or op == 7:
                logger.warning(f"⚠️ [{self.account_name}] Invalid session, reconnecting")
                time.sleep(2)
                self.resume_or_reconnect()
        except Exception as e:
            logger.error(f"❌ [{self.account_name}] Message error: {e}")
            
    def on_error(self, ws, error):
        logger.error(f"💥 [{self.account_name}] WebSocket error: {error}")
        
    def on_close(self, ws, code, msg):
        logger.warning(f"🔌 [{self.account_name}] Connection closed: {code} - {msg}")
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
                logger.info(f"{'💰' if self.fixed_status else '🔄'} [{self.account_name}] Status set to: '{status_text}'")
                return True
        except Exception as e:
            logger.error(f"Status update error: {e}")
        return False
        
    def status_refresh(self):
        time.sleep(60)
        while self.running:
            time.sleep(1800)
            if self.ws and self.ws.sock and self.ws.sock.connected:
                self.update_status(self.fixed_status)
                
    def rotation_loop(self):
        time.sleep(10)
        while self.running:
            time.sleep(self.interval_seconds)
            self.current_index = (self.current_index + 1) % len(self.rotating_statuses)
            self.update_status(self.rotating_statuses[self.current_index])
            
    def start_voice(self):
        self.voice_conn = PermanentVoiceConnection(
            self.token,
            self.voice_guild_id,
            self.voice_channel_id,
            self.account_name
        )
        threading.Thread(target=self.voice_conn.start, daemon=True).start()
        
    def send_json(self, data):
        try:
            if self.ws and self.ws.sock and self.ws.sock.connected:
                self.ws.send(json.dumps(data))
                return True
        except:
            pass
        return False
        
    def identify(self):
        first_status = ""
        if self.fixed_status:
            first_status = self.fixed_status
        elif self.rotating_statuses:
            first_status = self.rotating_statuses[0]
        else:
            first_status = "Online"
        payload = {
            "op": 2,
            "d": {
                "token": self.token,
                "properties": {"$os": "linux", "$browser": "DiscordKeepAlive", "$device": "DiscordKeepAlive"},
                "presence": {
                    "status": "online",
                    "activities": [{"name": first_status, "type": 0}],
                    "afk": False
                }
            }
        }
        self.send_json(payload)
        logger.info(f"📨 [{self.account_name}] Identify sent with initial status: '{first_status}'")
        
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
        threading.Thread(target=self.connect, daemon=True).start()
        
    def stop(self):
        self.running = False
        if self.voice_conn:
            self.voice_conn.stop()
        if self.ws:
            self.ws.close()

# ============================
# KEEP-ALIVE PINGER
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
        logger.info("📊 System running - Permanent voice enabled (30s monitor)")
        time.sleep(3600)

# ============================
# MAIN
# ============================
def main():
    print("=" * 60)
    print("PERMANENT DISCORD VOICE KEEP-ALIVE")
    print("💰 Account 1: Fixed 'Fucking RICH 💸💸'")
    print("🔄 Account 2: Rotating: Working On New Video🎥 | NYT💤 | YT- NOTE YOUR TYPE")
    print("🎙️ Voice: PERMANENT - Never leaves, auto-rejoin every 30 seconds if disconnected")
    print("=" * 60)

    token_one = os.environ.get('DISCORD_TOKEN_ONE', '').strip()
    token_two = os.environ.get('DISCORD_TOKEN_TWO', '').strip()
    
    if token_one:
        logger.info(f"Token One loaded: {token_one[:15]}...")
    else:
        logger.error("DISCORD_TOKEN_ONE is missing!")
    if token_two:
        logger.info(f"Token Two loaded: {token_two[:15]}...")
    else:
        logger.error("DISCORD_TOKEN_TWO is missing!")

    DEFAULT_GUILD_ID = "893842188037943346"
    DEFAULT_CHANNEL_ID = "896743673587437568"
    
    voice_one_enabled = os.environ.get('VOICE_JOIN_ONE', 'false').lower() == 'true'
    voice_one_guild = os.environ.get('VOICE_GUILD_ID_ONE', DEFAULT_GUILD_ID)
    voice_one_channel = os.environ.get('VOICE_CHANNEL_ID_ONE', DEFAULT_CHANNEL_ID)
    
    voice_two_enabled = os.environ.get('VOICE_JOIN_TWO', 'false').lower() == 'true'
    voice_two_guild = os.environ.get('VOICE_GUILD_ID_TWO', DEFAULT_GUILD_ID)
    voice_two_channel = os.environ.get('VOICE_CHANNEL_ID_TWO', DEFAULT_CHANNEL_ID)
    
    rotation_interval = int(os.environ.get('ROTATION_INTERVAL_MINUTES', '30'))
    rotational_statuses = ["Working On New Video🎥", "NYT💤", "YT- NOTE YOUR TYPE"]
    
    threading.Thread(target=start_flask, daemon=True).start()
    threading.Thread(target=render_pinger, daemon=True).start()
    threading.Thread(target=health_monitor, daemon=True).start()
    time.sleep(3)
    
    clients = []
    
    if token_one:
        client1 = StandaloneDiscordClient(token_one, "ACCOUNT_ONE", fixed_status="Fucking RICH 💸💸")
        if voice_one_enabled:
            client1.set_voice(True, voice_one_guild, voice_one_channel)
            logger.info(f"Account One voice: ENABLED -> guild {voice_one_guild}, channel {voice_one_channel}")
        else:
            logger.info("Account One voice: DISABLED")
        client1.start()
        clients.append(client1)
    
    if token_two:
        client2 = StandaloneDiscordClient(token_two, "ACCOUNT_TWO", rotating_statuses=rotational_statuses, interval_minutes=rotation_interval)
        if voice_two_enabled:
            client2.set_voice(True, voice_two_guild, voice_two_channel)
            logger.info(f"Account Two voice: ENABLED -> guild {voice_two_guild}, channel {voice_two_channel}")
        else:
            logger.info("Account Two voice: DISABLED")
        client2.start()
        clients.append(client2)
    
    logger.info("=" * 60)
    logger.info("✅ ALL CLIENTS STARTED WITH PERMANENT VOICE MONITORING")
    logger.info("🎙️ Accounts will be checked every 30 seconds and auto-join if disconnected")
    logger.info("=" * 60)
    
    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        logger.info("Shutting down...")
        for c in clients:
            c.stop()
        time.sleep(2)

if __name__ == "__main__":
    main()
