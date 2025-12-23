import socket
import threading
import time
import json
import sys
import os
import glob
import random
from common import *
from file_manager import FileManager

# --- CONFIGURACIÓN DE REDUNDANCIA ---
# Al iniciar el programa te pedirá la IP, pero aquí definimos los puertos
KNOWN_TRACKERS = [] # Se llenará dinámicamente en el main
TIME_DELAY = 0.05   # Reducido para que se note la velocidad del swarm

class PeerNode:
    def __init__(self, port, tracker_ip_hint):
        self.my_port = port
        self.my_ip = get_local_ip() # Usamos la función de common.py
        self.my_id = f"{self.my_ip}:{port}"
        
        self.folder = f"peer_{port}"
        if not os.path.exists(self.folder): os.makedirs(self.folder)

        self.managers = {} 
        self.managers_lock = threading.Lock()
        
        # --- CEREBRO DEL BALANCEADOR ---
        # Diccionario para guardar qué tan rápido es cada peer
        # { "IP:PORT": tiempo_promedio_respuesta }
        self.peer_performance = {} 
        
        self.running = True

        print(f"[*] MI IDENTIDAD: {self.my_id}")
        print(f"[*] Escuchando en: {self.my_ip}:{self.my_port}")

        # Configurar lista de trackers inicial
        global KNOWN_TRACKERS
        KNOWN_TRACKERS = [(tracker_ip_hint, 5000), (tracker_ip_hint, 5001)]

        self.scan_folder()

        threading.Thread(target=self.start_server, daemon=True).start()
        threading.Thread(target=self.heartbeat_loop, daemon=True).start()

    # --- 1. LÓGICA DE DESCARGA INTELIGENTE ---
    def _download_logic(self, file_hash):
        mgr = self.managers.get(file_hash)
        fm = mgr['fm']
        print(f"[*] Swarm iniciado para: {mgr['filename']}")
        
        while fm.get_missing_chunks() and mgr['downloading'] and self.running:
            # 1. Obtener lista de candidatos del Tracker
            peers = self.contact_tracker(CMD_ANNOUNCE, file_hash, mgr['trackers'])
            
            # 2. Filtrar: No soy yo y tienen ALGO del archivo (>0%)
            candidates = [p for p in peers if p['id'] != self.my_id and p['percent'] > 0]
            
            if not candidates: 
                time.sleep(2); continue
            
            # 3. ORDENAMIENTO INTELIGENTE (Smart Load Balancing)
            # Ordenamos los peers según su velocidad histórica (menor tiempo es mejor)
            # Si no los conocemos, asumimos un tiempo neutral (1.0s)
            candidates.sort(key=lambda p: self.peer_performance.get(p['id'], 1.0))

            # *Estrategia*: Si el mejor peer (ej. Seeder) está saturado (tiempo alto),
            # automáticamente quedará abajo en la lista y pediremos a los Leechers rápidos primero.

            # 4. Iterar piezas faltantes
            for idx in fm.get_missing_chunks():
                if not mgr['downloading'] or not self.running: break
                
                chunk_downloaded = False
                
                # Intentar con los candidatos en orden de velocidad
                for p in candidates:
                    start_time = time.time()
                    success = self.request_chunk(p['id'], idx, file_hash, fm)
                    elapsed = time.time() - start_time
                    
                    # 5. ACTUALIZAR RENDIMIENTO (Feedback Loop)
                    self.update_peer_score(p['id'], elapsed, success)
                    
                    if success:
                        chunk_downloaded = True
                        break # Ya tenemos la pieza, vamos a la siguiente
                
                # Si nadie tenía esta pieza específica (ej. Leechers vacíos), seguimos buscando
                if not chunk_downloaded:
                    time.sleep(0.1) 

            time.sleep(0.1)
        
        if not fm.get_missing_chunks():
            mgr['downloading'] = False
            self.contact_tracker(CMD_ANNOUNCE, file_hash, mgr['trackers'])
            print(f"\n[★] ¡DESCARGA COMPLETA!: {mgr['filename']}")

    def update_peer_score(self, peer_id, time_taken, success):
        """Ajusta el ranking del peer basado en su respuesta."""
        current_avg = self.peer_performance.get(peer_id, 0.5)
        
        if success:
            # Suavizado exponencial: El nuevo tiempo pesa un 30%
            new_score = (current_avg * 0.7) + (time_taken * 0.3)
        else:
            # CASTIGO: Si falla o da error, asumimos que está muy lento/saturado
            # Le sumamos 3 segundos artificiales para mandarlo al final de la cola
            new_score = current_avg + 3.0
            
        self.peer_performance[peer_id] = new_score

    # --- 2. GESTIÓN DE ARCHIVOS ---
    def add_manager(self, filename, file_hash, size, trackers):
        with self.managers_lock:
            if file_hash in self.managers: return self.managers[file_hash]
            real_path = os.path.join(self.folder, filename)
            # Lógica corregida: Seeder si existe y tiene el tamaño correcto
            is_seeder = os.path.exists(real_path) and os.path.getsize(real_path) == size
            fm = FileManager(self.folder, filename, size, is_seeder)
            self.managers[file_hash] = {"fm": fm, "filename": filename, "trackers": trackers, "downloading": False}
            return self.managers[file_hash]

    def scan_folder(self):
        json_files = glob.glob(os.path.join(self.folder, "*.json"))
        count = 0
        for json_path in json_files:
            try:
                with open(json_path, 'r') as f: meta = json.load(f)
                with self.managers_lock:
                    if meta['filehash'] in self.managers: continue
                
                # Intentamos recuperar
                real_path = os.path.join(self.folder, meta['filename'])
                if os.path.exists(real_path) or os.path.exists(real_path + ".progress"):
                    # Forzamos uso de KNOWN_TRACKERS para recuperar conectividad
                    self.add_manager(meta['filename'], meta['filehash'], meta['filesize'], KNOWN_TRACKERS)
                    self.contact_tracker(CMD_ANNOUNCE, meta['filehash'], KNOWN_TRACKERS)
                    count += 1
            except: pass
        if count > 0: print(f"[*] Sistema restaurado: {count} archivos activos.")

    # --- 3. SERVIDOR (Permite que Leechers suban lo que tienen) ---
    def start_server(self):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            # Bind a 0.0.0.0 para aceptar conexiones externas (Linux/Windows)
            s.bind(('0.0.0.0', self.my_port))
            s.listen(10)
            while self.running:
                conn, addr = s.accept()
                threading.Thread(target=self.handle_upload, args=(conn,)).start()
        except Exception as e:
            print(f"[!] Error servidor: {e}")

    def handle_upload(self, conn):
        try:
            raw = conn.recv(BUFFER_SIZE).decode()
            if not raw: return
            req = json.loads(raw)
            cmd = req.get('command')
            
            if cmd == CMD_REQUEST_CHUNK:
                mgr = self.managers.get(req.get('file_hash'))
                if mgr:
                    # NOTA: read_chunk lee del disco. 
                    # Si somos Leecher pero ya bajamos esa pieza, read_chunk devolverá datos.
                    # ¡Esto permite Leecher-to-Leecher!
                    data = mgr['fm'].read_chunk(req.get('chunk_index'))
                    if data:
                        conn.send(json.dumps({"status": "ok", "size": len(data)}).encode() + b'\n' + data)
                        return

            elif cmd == CMD_GET_METADATA:
                mgr = self.managers.get(req.get('file_hash'))
                if mgr:
                    meta = {"status": "ok", "filename": mgr['filename'], "filesize": mgr['fm'].total_size, "trackers": mgr['trackers']}
                    conn.send(json.dumps(meta).encode())
                    return
            
            conn.send(json.dumps({"status": "error"}).encode() + b'\n')
        except: pass
        finally: conn.close()

    # --- 4. CLIENTE DE RED ---
    def request_chunk(self, peer_addr, idx, file_hash, fm):
        try:
            ip, port = peer_addr.split(':')
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(3) # Timeout más largo para redes reales
            s.connect((ip, int(port)))
            s.send(json.dumps({"command": CMD_REQUEST_CHUNK, "file_hash": file_hash, "chunk_index": idx}).encode())
            f = s.makefile('rb')
            head_line = f.readline()
            if not head_line: return False
            head = json.loads(head_line)
            
            if head['status'] == 'ok':
                data = f.read(head['size'])
                fm.write_chunk(idx, data)
                if TIME_DELAY > 0: time.sleep(TIME_DELAY)
                return True
        except: 
            return False # Fallo de conexión o time out
        return False

    def fetch_metadata_from_swarm(self, file_hash, peers_list):
        print(f"[*] Buscando metadatos...")
        for p_data in peers_list:
            if p_data['id'] == self.my_id: continue 
            try:
                ip, port = p_data['id'].split(':')
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.settimeout(2)
                s.connect((ip, int(port)))
                s.send(json.dumps({"command": CMD_GET_METADATA, "file_hash": file_hash}).encode())
                resp = json.loads(s.recv(BUFFER_SIZE).decode())
                s.close()
                if resp.get('status') == 'ok': return resp
            except: pass
        return None

    def contact_tracker(self, command, file_hash=None, trackers=None):
        if not trackers: trackers = KNOWN_TRACKERS
        combined_peers = []
        
        for ip, port in trackers:
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.settimeout(2)
                s.connect((ip, port))
                
                req = {"command": command, "peer_id": self.my_id}
                if file_hash:
                    req["file_hash"] = file_hash
                    mgr = self.managers.get(file_hash)
                    if mgr: req.update({"filename": mgr["filename"], "percent": mgr["fm"].get_progress_percentage()})
                
                s.send(json.dumps(req).encode())
                resp = json.loads(s.recv(BUFFER_SIZE).decode())
                s.close()
                
                if resp['status'] == 'ok':
                    if command == CMD_LIST_FILES: return resp['files']
                    if command == CMD_ANNOUNCE and 'peers' in resp: combined_peers.extend(resp['peers'])
            except: pass
            
        if command == CMD_ANNOUNCE:
            seen = set(); unique = []
            for p in combined_peers:
                if p['id'] not in seen: unique.append(p); seen.add(p['id'])
            return unique
        return []

    def heartbeat_loop(self):
        while self.running:
            time.sleep(5)
            self.scan_folder()
            with self.managers_lock: hashes = list(self.managers.keys())
            for h in hashes:
                mgr = self.managers.get(h)
                if not mgr: continue
                path = os.path.join(self.folder, mgr['filename'])
                if not os.path.exists(path) and not os.path.exists(path + ".progress"):
                    if not mgr['downloading']:
                        self.contact_tracker(CMD_EXIT_SWARM, h, KNOWN_TRACKERS)
                        with self.managers_lock: del self.managers[h]
                        continue
                self.contact_tracker(CMD_ANNOUNCE, h, KNOWN_TRACKERS)

    # --- MENÚS ---
    def convert_local_file(self):
        print("\n--- PUBLICAR ---")
        if not os.path.exists(self.folder): print("Carpeta no existe."); return
        all_f = [f for f in os.listdir(self.folder) if os.path.isfile(os.path.join(self.folder, f))]
        cands = [f for f in all_f if not f.endswith('.json') and not f.endswith('.progress') and f"{f}.json" not in all_f]
        
        if not cands: 
            print(f"[!] Carpeta vacía o sin archivos nuevos ({self.folder}).")
            return

        for i, f in enumerate(cands): print(f"{i+1}. {f}")
        try:
            sel = int(input(">> #: ")) - 1
            if 0 <= sel < len(cands):
                fname = cands[sel]
                fpath = os.path.join(self.folder, fname)
                print("[*] Hashing...")
                fhash = calculate_file_hash(fpath)
                size = os.path.getsize(fpath)
                meta = {"filename": fname, "filesize": size, "filehash": fhash, "trackers": KNOWN_TRACKERS}
                with open(os.path.join(self.folder, f"{fname}.json"), 'w') as f: json.dump(meta, f, indent=4)
                
                self.add_manager(fname, fhash, size, KNOWN_TRACKERS)
                self.contact_tracker(CMD_ANNOUNCE, fhash, KNOWN_TRACKERS)
                print(f"[OK] {fname} publicado.")
        except: pass

    def search_tracker(self):
        files = self.contact_tracker(CMD_LIST_FILES)
        if not files: print("[!] Tracker vacío."); return
        
        print(f"\n--- DISPONIBLES ---")
        for i, f in enumerate(files): print(f"{i+1}. {f['filename']} (Peers: {f['peers_count']})")
        
        try:
            sel = int(input(">> Descargar #: ")) - 1
            if 0 <= sel < len(files):
                target = files[sel]
                f_hash = target['hash']
                jpath = os.path.join(self.folder, f"{target['filename']}.json")
                
                if os.path.exists(jpath):
                    try:
                        with open(jpath, 'r') as f: meta = json.load(f)
                        if 'filehash' in meta:
                            self.add_manager(meta['filename'], meta['filehash'], meta['filesize'], KNOWN_TRACKERS)
                            self.start_download_thread(meta['filehash'])
                            print("[OK] Reanudando...")
                            return
                    except: pass

                peers = self.contact_tracker(CMD_ANNOUNCE, f_hash, KNOWN_TRACKERS)
                if not peers: print("[!] Sin peers."); return
                
                meta = self.fetch_metadata_from_swarm(f_hash, peers)
                if meta and 'filename' in meta:
                    save_meta = {"filename": meta['filename'], "filesize": meta['filesize'], "filehash": f_hash, "trackers": KNOWN_TRACKERS}
                    with open(jpath, 'w') as f: json.dump(save_meta, f, indent=4)
                    self.add_manager(meta['filename'], f_hash, meta['filesize'], KNOWN_TRACKERS)
                    self.start_download_thread(f_hash)
                    print("[OK] Iniciando Smart Download...")
                else: print("[ERROR] Falla de metadatos.")
        except: pass

    def monitor(self):
        try:
            while True:
                os.system('cls' if os.name == 'nt' else 'clear')
                print(f"┌── MONITOR ({len(self.managers)} archivos) ─────────────────┐")
                with self.managers_lock:
                    for h, m in self.managers.items():
                        pct = m['fm'].get_progress_percentage()
                        bar = "█" * int(pct//5) + "░" * (20 - int(pct//5))
                        state = "BAJANDO" if m['downloading'] else ("SEEDING" if pct==100 else "PAUSA")
                        print(f" {m['filename'][:15]:<15} [{bar}] {pct}% {state}")
                
                print("\n┌── RENDIMIENTO DE PEERS (Ping) ───────────────┐")
                for pid, score in list(self.peer_performance.items())[:5]:
                    print(f" Peer {pid:<20} : {score:.4f} seg")
                    
                time.sleep(0.5)
        except KeyboardInterrupt: pass

    def main_menu(self):
        while self.running:
            # os.system('cls' if os.name == 'nt' else 'clear') 
            print(f"\n=== NODE {self.my_ip}:{self.my_port} ===")
            print("1. Publicar | 2. Descargar | 3. Monitor | 4. Salir")
            op = input(">> ")
            if op == '1': self.convert_local_file()
            elif op == '2': self.search_tracker()
            elif op == '3': self.monitor()
            elif op == '4': self.running = False; sys.exit()

if __name__ == "__main__":
    if len(sys.argv) < 2: 
        print("Uso: python3 peer_node.py <PUERTO_LOCAL>")
        sys.exit()
    
    # Detección automática de IP para sugerir al usuario
    ip_sugerida = get_local_ip()
    print("--- CONFIGURACIÓN ---")
    print(f"Detecté que tu IP local es: {ip_sugerida}")
    tracker_hint = input("IP del Tracker Principal [Enter para usar tu IP local]: ")
    if not tracker_hint.strip(): tracker_hint = ip_sugerida
    
    PeerNode(int(sys.argv[1]), tracker_hint).main_menu()
