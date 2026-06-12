import os
import re
import json
import time
import base64
import socketio
import requests

from collections import deque
from dotenv import load_dotenv
from google import genai

load_dotenv()

# --------------------------
# CONFIG
# --------------------------

USERNAME = os.getenv("MIG66_USERNAME")
PASSWORD = os.getenv("MIG66_PASSWORD")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
API_BASE = os.getenv("API_BASE")

ROOM_ID = int(os.getenv("ROOM_ID", "50"))

TRIGGER = os.getenv(
    "TRIGGER_KEYWORD",
    f"@{USERNAME}"
).lower()

PARENT_USERS = set(
    x.strip().lower()
    for x in os.getenv("PARENT_USERS", "faysal").split(",")
)

client = genai.Client(api_key=GEMINI_API_KEY)

sio = socketio.Client()

token = None
my_user_id = None

joined_rooms = set()

processed = deque(maxlen=500)

voucher_enabled = True

# --------------------------
# LOGIN
# --------------------------

def login():
    global token
    global my_user_id

    print("[*] Logging in...")

    resp = requests.post(
        f"{API_BASE}/api/auth/login",
        json={
            "username": USERNAME,
            "password": PASSWORD,
            "remember_me": True,
            "login_offline": False,
            "device_info": "Flutter Web"
        }
    )

    data = resp.json()

    token = data.get("token") or data.get("data", {}).get("token")

    if not token:
        raise Exception("Login failed")

    payload = token.split(".")[1]
    payload += "=" * (-len(payload) % 4)

    decoded = json.loads(
        base64.b64decode(payload)
    )

    my_user_id = str(decoded.get("id"))

    print(f"[✓] Logged in as {decoded.get('username')}")

# --------------------------
# AI
# --------------------------

def ask_ai(question):

    try:
        response = client.models.generate_content(
            model="gemini-2.0-flash",
            contents=question
        )

        return response.text.strip()

    except Exception as e:
        print(e)
        return "Sorry, I couldn't answer right now."

# --------------------------
# HELPERS
# --------------------------

def send_room(room_id, text):

    sio.emit(
        "send_message",
        {
            "room_id": room_id,
            "content": text,
            "msg_type": "text"
        }
    )

def send_private(username, text):

    sio.emit(
        "private_message",
        {
            "to_username": username,
            "content": text
        }
    )

def join_room(room_id):

    room_id = int(room_id)

    sio.emit(
        "join_room",
        {
            "room_id": room_id,
            "is_manual": True
        }
    )

    joined_rooms.add(room_id)

def leave_room(room_id):

    room_id = int(room_id)

    sio.emit(
        "leave_room",
        {
            "room_id": room_id
        }
    )

    joined_rooms.discard(room_id)

# --------------------------
# PARENT COMMANDS
# --------------------------

def handle_parent_command(sender, text):

    global voucher_enabled
    global auto_reply_enabled
    if text.lower() == "|ar on":
    auto_reply_enabled = True
    send_private(sender, "Auto Reply ON")
    return

    if text.lower() == "|ar off":
    auto_reply_enabled = False
    send_private(sender, "Auto Reply OFF")
    return
    
    if text.startswith("|help"):

        send_private(
            sender,
            """
|join_room 50
|leave_room 50
|leave_room all
|text_room 50 hello
|voucher on
|voucher off
|ar on
|ar off
|status
"""
        )

        return

    m = re.match(r"\|join_room\s+(\d+)", text)

    if m:
        room = int(m.group(1))
        join_room(room)
        send_private(sender, f"Joined room {room}")
        return

    if text.lower() == "|leave_room all":

        for r in list(joined_rooms):
            leave_room(r)

        send_private(sender, "All rooms left")
        return

    m = re.match(r"\|leave_room\s+(\d+)", text)

    if m:
        room = int(m.group(1))
        leave_room(room)
        send_private(sender, f"Left room {room}")
        return

    m = re.match(r"\|text_room\s+(\d+)\s+(.+)", text)

    if m:
        room = int(m.group(1))
        msg = m.group(2)

        send_room(room, msg)

        send_private(
            sender,
            f"Message sent to room {room}"
        )

        return

    if text.lower() == "|voucher on":

        voucher_enabled = True
        auto_reply_enabled = True
        send_private(sender, "Voucher ON")
        return

    if text.lower() == "|voucher off":

        voucher_enabled = False
        send_private(sender, "Voucher OFF")
        return

    if text.lower() == "|status":

        send_private(
            sender,
            f"""
Rooms: {list(joined_rooms)}
Voucher: {voucher_enabled}
AutoReply: {auto_reply_enabled}
Parents: {list(PARENT_USERS)}
"""
        )

# --------------------------
# SOCKET
# --------------------------

@sio.event
def connect():

    print("[✓] Connected")

    join_room(ROOM_ID)

@sio.event
def disconnect():

    print("[!] Disconnected")

@sio.on("*")
def catch_all(event, data):

    print(f"\nEVENT: {event}")
    print(json.dumps(data, indent=2)[:500])

# --------------------------
# PRIVATE MESSAGE
# --------------------------

@sio.on("private_message")
def private_message(data):

    sender = str(
        data.get("sender_name", "")
    )

    sender_id = str(
        data.get("sender_id", "")
    )

    text = str(
        data.get("content", "")
    ).strip()

    if not text:
        return

    if sender_id == my_user_id:
        return

    if sender.lower() in PARENT_USERS and text.startswith("|"):
        handle_parent_command(sender, text)
        return

    if not auto_reply_enabled:
    return

    answer = ask_ai(text)
    send_private(sender, answer)

# --------------------------
# ROOM MESSAGE
# --------------------------

@sio.on("new_message")
def room_message(data):

    sender = str(
        data.get("username", "")
    )

    sender_id = str(
        data.get("sender_id", "")
    )

    text = str(
        data.get("content", "")
    )

    room_id = data.get("room_id")

    if sender_id == my_user_id:
        return

    if voucher_enabled:

        match = re.search(
            r"\[code\]\s+(\d+)",
            text,
            re.I
        )

        if match:

            code = match.group(1)

            send_room(
                room_id,
                f"/pick {code}"
            )

            return

    if TRIGGER not in text.lower():
        return

    question = text.replace(
        TRIGGER,
        ""
    ).strip()

    key = f"{sender}:{question}"

    if key in processed:
        return

    processed.append(key)

    if not auto_reply_enabled:
    return

     answer = ask_ai(question)

    send_room(
        room_id,
        f"@{sender} {answer}"
    )

# --------------------------
# MAIN
# --------------------------

def main():

    login()

    sio.connect(
        API_BASE,
        auth={
            "token": token
        },
        transports=[
            "websocket",
            "polling"
        ]
    )

    sio.wait()

import threading
import os
from flask import Flask

app = Flask(__name__)

@app.route("/")
def home():
    return "Bot is running!"

def start_bot():
    main()   # your existing bot function

if __name__ == "__main__":
    # Run bot in background
    threading.Thread(target=start_bot).start()

    # Start web server (THIS is what Render needs)
    port = int(os.environ.get("PORT", 10000))
    print(f"Server running on port {port}")

    app.run(host="0.0.0.0", port=port)
