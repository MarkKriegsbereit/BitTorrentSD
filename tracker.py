# tracker.py
import os
import socket
import threading
import json
import time
from datetime import datetime
from common import *

TIMEOUT_LIMIT = 20  # Damos más tiempo antes de borrar peers

# Estructura: swarm_db[hash][peer_id] = { "percent": 50, "role": "Leecher", "last_seen": timestamp }
swarm_db = {}
# Estructura: file_names[hash] = "video.mp4"
file_names = {} 

lock = threading.Lock()

def get_timestamp():
    return datetime.now().strftime("%H:%M:%S")

def prune_dead_peers():
    while True:
        time.sleep(5)
        with lock:
            now = time.time()
            for f_hash in list(swarm_db.keys()):
                peers = swarm_db[f_hash]
                to_remove = []
                for pid, info in peers.items():
                    if now - info['last_seen'] > TIMEOUT_LIMIT:
                        to_remove.append(pid)
                for pid in to_remove:
                    # Opcional: imprimir log de desconexión
                    # print(f"[{get_timestamp()}] Peer {pid} desconectado (Timeout).")
                    del swarm_db[f_hash][pid]

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
                if f_hash not in swarm_db:
                    swarm_db[f_hash] = {}
                # Guardamos el nombre del archivo si no lo teníamos
                if f_hash not in file_names or file_names[f_hash] == 'Unknown':
                    file_names[f_hash] = filename
                
                role = "Seeder" if percent == 100 else "Leecher"
                swarm_db[f_hash][peer_id] = {
                    "percent": percent, 
                    "role": role,
                    "last_seen": time.time()
                }

            # Responder con peers DE ESE ARCHIVO
            peer_list = []
            peers_in_swarm = swarm_db.get(f_hash, {})
            for pid, info in peers_in_swarm.items():
                peer_list.append({"id": pid, "percent": info["percent"]})
            
            response = {"status": "ok", "peers": peer_list}

        elif cmd == CMD_LIST_FILES:
            catalog = []
            with lock:
                for h, name in file_names.items():
                    count = len(swarm_db.get(h, {}))
                    # Solo mostrar si hay alguien conectado
                    if count > 0:
                        catalog.append({"hash": h, "filename": name, "peers_count": count})
            response = {"status": "ok", "files": catalog}

        conn.send(json.dumps(response).encode('utf-8'))
    except: pass
    finally: conn.close()

def monitor_display():
    """Imprime la tabla de estado cada 3 segundos."""
    while True:
        time.sleep(3)
        os.system('cls' if os.name == 'nt' else 'clear')
        print(f"┌──────────────────────────────────────────────────────────────┐")
        print(f"│  TRACKER MONITOR - {get_timestamp():<34}│")
        print(f"├──────────────────────────────────────────────────────────────┤")
        print(f"│ {'ARCHIVO':<20} | {'PEER ID':<21} | {'%':<5} | {'ROL':<7} │")
        print(f"├──────────────────────────────────────────────────────────────┤")
        
        with lock:
            if not swarm_db:
                print(f"│  {'--- Sin actividad ---':<58}│")
            
            for f_hash, peers in swarm_db.items():
                fname = file_names.get(f_hash, "Unknown")[:20]
                if not peers:
                    continue
                
                first = True
                for pid, info in peers.items():
                    # Solo imprimimos el nombre del archivo en la primera fila del grupo
                    display_name = fname if first else ""
                    print(f"│ {display_name:<20} | {pid:<21} | {info['percent']:<5} | {info['role']:<7} │")
                    first = False
                print(f"│ {'-'*60} │")
        
        print(f"└──────────────────────────────────────────────────────────────┘")

def start_tracker():
    import os
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(('0.0.0.0', TRACKER_PORT))
    server.listen(10)
    
    t_prune = threading.Thread(target=prune_dead_peers, daemon=True)
    t_prune.start()
    
    t_monitor = threading.Thread(target=monitor_display, daemon=True)
    t_monitor.start()

    while True:
        conn, addr = server.accept()
        threading.Thread(target=handle_peer, args=(conn, addr)).start()

if __name__ == "__main__":
    start_tracker()
