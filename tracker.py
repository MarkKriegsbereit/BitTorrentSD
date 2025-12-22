import socket
import threading
import json
import time
import os
import sys
from datetime import datetime
from common import *

TIMEOUT_LIMIT = 30  
swarm_db = {}   
file_names = {} 
lock = threading.Lock()

def get_timestamp(): return datetime.now().strftime("%H:%M:%S")

def prune_dead_peers():
    while True:
        time.sleep(5)
        with lock:
            now = time.time()
            for f_hash in list(swarm_db.keys()):
                peers = swarm_db[f_hash]
                to_remove = [pid for pid, info in peers.items() if now - info['last_seen'] > TIMEOUT_LIMIT]
                for pid in to_remove: del swarm_db[f_hash][pid]

def handle_peer(conn, addr):
    try:
        data = conn.recv(BUFFER_SIZE).decode('utf-8')
        if not data: return
        request = json.loads(data)
        
        cmd = request.get('command')
        f_hash = request.get('file_hash')
        peer_id = request.get('peer_id')
        response = {"status": "error"}

        if cmd == CMD_ANNOUNCE:
            filename = request.get('filename', 'Unknown')
            percent = request.get('percent', 0)
            with lock:
                if f_hash not in swarm_db: swarm_db[f_hash] = {}
                if f_hash not in file_names or file_names[f_hash] == 'Unknown': 
                    file_names[f_hash] = filename
                
                role = "Seeder" if percent == 100 else "Leecher"
                swarm_db[f_hash][peer_id] = {"percent": percent, "role": role, "last_seen": time.time()}
                
                peer_list = [{"id": pid, "percent": info["percent"]} for pid, info in swarm_db[f_hash].items()]
            response = {"status": "ok", "peers": peer_list}

        elif cmd == CMD_EXIT_SWARM:
            with lock:
                if f_hash in swarm_db and peer_id in swarm_db[f_hash]:
                    del swarm_db[f_hash][peer_id]
            response = {"status": "ok"}

        elif cmd == CMD_LIST_FILES:
            catalog = []
            with lock:
                for h, name in file_names.items():
                    count = len(swarm_db.get(h, {}))
                    if count > 0: catalog.append({"hash": h, "filename": name, "peers_count": count})
            response = {"status": "ok", "files": catalog}

        conn.send(json.dumps(response).encode('utf-8'))
    except: pass
    finally: conn.close()

def monitor_display():
    while True:
        time.sleep(3)
        os.system('cls' if os.name == 'nt' else 'clear')
        print(f"┌── TRACKER MONITOR {get_timestamp()} ────────────────────────┐")
        print(f"│ {'ARCHIVO':<15} | {'PEER ID':<20} | {'%':<5} | {'ROL':<7} │")
        print(f"├──────────────────────────────────────────────────────────┤")
        with lock:
            if not swarm_db: print(f"│  {'--- Sin actividad ---':<54}│")
            for f_hash, peers in swarm_db.items():
                fname = file_names.get(f_hash, "Unknown")[:15]
                for pid, info in peers.items():
                    print(f"│ {fname:<15} | {pid:<20} | {info['percent']:<5} | {info['role']:<7} │")
        print(f"└──────────────────────────────────────────────────────────┘")

def start_tracker(port):
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(('0.0.0.0', port))
    server.listen(10)
    print(f"[TRACKER] Iniciado en puerto {port}...")
    
    # Hilos daemon
    threading.Thread(target=prune_dead_peers, daemon=True).start()
    threading.Thread(target=monitor_display, daemon=True).start()
    
    while True:
        conn, addr = server.accept()
        threading.Thread(target=handle_peer, args=(conn, addr)).start()

if __name__ == "__main__":
    # Si le pasamos puerto por consola, lo usa. Si no, usa el 5000 por defecto.
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 5000
    start_tracker(port)
