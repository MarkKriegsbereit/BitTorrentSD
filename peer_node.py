import socket
import threading
import time
import json
import sys
import os
import glob
from common import *
from file_manager import FileManager

# --- CONFIGURACIÓN ---
KNOWN_TRACKERS = [
    ("127.0.0.1", 5000), # Tracker Principal
    ("127.0.0.1", 5001)  # Tracker de Respaldo
]
TIME_DELAY = 0.2

class PeerNode:
    def __init__(self, port):
        self.my_port = port
        self.my_ip = "127.0.0.1"
        self.my_id = f"{self.my_ip}:{port}"
        self.folder = f"peer_{port}"
        if not os.path.exists(self.folder): os.makedirs(self.folder)

        self.managers = {} 
        self.managers_lock = threading.Lock()
        self.running = True

        # Recuperación silenciosa al inicio
        self.scan_folder()

        # Iniciar servicios
        threading.Thread(target=self.start_server, daemon=True).start()
        threading.Thread(target=self.heartbeat_loop, daemon=True).start()

    # --- GESTIÓN DE ARCHIVOS ---
    def add_manager(self, filename, file_hash, size, trackers):
        with self.managers_lock:
            if file_hash in self.managers: return self.managers[file_hash]
            
            real_path = os.path.join(self.folder, filename)
            is_seeder = os.path.exists(real_path) and os.path.getsize(real_path) == size
            
            fm = FileManager(self.folder, filename, size, is_seeder)
            self.managers[file_hash] = {
                "fm": fm, "filename": filename, "trackers": trackers, "downloading": False
            }
            return self.managers[file_hash]

    def scan_folder(self):
        """Escanea carpeta local para recuperar descargas previas."""
        json_files = glob.glob(os.path.join(self.folder, "*.json"))
        count = 0
        for json_path in json_files:
            try:
                with open(json_path, 'r') as f: meta = json.load(f)
                with self.managers_lock:
                    if meta['filehash'] in self.managers: continue

                real_path = os.path.join(self.folder, meta['filename'])
                if os.path.exists(real_path) or os.path.exists(real_path + ".progress"):
                    self.add_manager(meta['filename'], meta['filehash'], meta['filesize'], meta['trackers'])
                    self.contact_tracker(CMD_ANNOUNCE, meta['filehash'], meta['trackers'])
                    count += 1
            except: pass
        if count > 0:
            print(f"[*] Sistema restaurado: {count} archivos cargados.")

    # --- SERVIDOR ---
    def start_server(self):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind(('0.0.0.0', self.my_port))
            s.listen(10)
            while self.running:
                conn, addr = s.accept()
                threading.Thread(target=self.handle_upload, args=(conn,)).start()
        except: pass

    def handle_upload(self, conn):
        try:
            raw = conn.recv(BUFFER_SIZE).decode()
            if not raw: return
            req = json.loads(raw)
            cmd = req.get('command')
            
            if cmd == CMD_REQUEST_CHUNK:
                mgr = self.managers.get(req.get('file_hash'))
                if mgr:
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

    # --- CLIENTE ---
    def fetch_metadata_from_swarm(self, file_hash, peers_list):
        print(f"[*] Solicitando metadatos al enjambre...")
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

    def start_download_thread(self, file_hash):
        mgr = self.managers.get(file_hash)
        if not mgr or mgr['fm'].get_progress_percentage() == 100: return
        mgr['downloading'] = True
        threading.Thread(target=self._download_logic, args=(file_hash,), daemon=True).start()

    def _download_logic(self, file_hash):
        mgr = self.managers.get(file_hash)
        fm = mgr['fm']
        print(f"[*] Descarga iniciada: {mgr['filename']}")
        
        while fm.get_missing_chunks() and mgr['downloading'] and self.running:
            peers = self.contact_tracker(CMD_ANNOUNCE, file_hash, mgr['trackers'])
            valid = [p for p in peers if p['id'] != self.my_id and p['percent'] > 0]
            
            if not valid: time.sleep(2); continue
            
            for idx in fm.get_missing_chunks():
                if not mgr['downloading'] or not self.running: break
                for p in valid:
                    if self.request_chunk(p['id'], idx, file_hash, fm): break
            time.sleep(0.1)
        
        if not fm.get_missing_chunks():
            mgr['downloading'] = False
            self.contact_tracker(CMD_ANNOUNCE, file_hash, mgr['trackers'])
            print(f"\n[★] ¡DESCARGA COMPLETA!: {mgr['filename']}")

    def request_chunk(self, peer_addr, idx, file_hash, fm):
        try:
            ip, port = peer_addr.split(':')
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(2)
            s.connect((ip, int(port)))
            s.send(json.dumps({"command": CMD_REQUEST_CHUNK, "file_hash": file_hash, "chunk_index": idx}).encode())
            f = s.makefile('rb')
            head = json.loads(f.readline())
            if head['status'] == 'ok':
                fm.write_chunk(idx, f.read(head['size']))
                if TIME_DELAY > 0: time.sleep(TIME_DELAY)
                return True
        except: pass
        return False

    def contact_tracker(self, command, file_hash=None, trackers=None):
        if not trackers: trackers = KNOWN_TRACKERS
        
        # Acumulador de peers (por si un tracker tiene unos y otro tiene otros)
        combined_peers = []
        combined_files = []
        
        for ip, port in trackers:
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.settimeout(1) # Timeout corto para no congelar si uno está caído
                s.connect((ip, port))
                
                req = {"command": command, "peer_id": self.my_id}
                
                if file_hash:
                    req["file_hash"] = file_hash
                    mgr = self.managers.get(file_hash)
                    if mgr: 
                        req.update({
                            "filename": mgr["filename"], 
                            "percent": mgr["fm"].get_progress_percentage()
                        })
                
                s.send(json.dumps(req).encode())
                resp = json.loads(s.recv(BUFFER_SIZE).decode())
                s.close()
                
                if resp['status'] == 'ok':
                    # CASO 1: Si solo estamos leyendo (Buscar archivos),
                    # con que uno responda nos basta.
                    if command == CMD_LIST_FILES:
                        return resp['files']
                    
                    # CASO 2: Si estamos anunciando (ANNOUNCE o EXIT),
                    # ¡DEBEMOS AVISAR A TODOS! NO HACER RETURN AÚN.
                    if command == CMD_ANNOUNCE:
                        if 'peers' in resp:
                            combined_peers.extend(resp['peers'])
                        # IMPORTANTE: No hay 'return' aquí. Seguimos al siguiente tracker.
                    
                    elif command == CMD_EXIT_SWARM:
                        # Avisar a todos que me fui
                        continue 

            except Exception as e:
                # Si un tracker falla (está caído), lo ignoramos y seguimos con el siguiente
                pass
        
        # Al final del bucle, retornamos lo que acumulamos
        if command == CMD_ANNOUNCE:
            # Eliminar duplicados en la lista de peers (opcional pero recomendado)
            unique_peers = {p['id']: p for p in combined_peers}.values()
            return list(unique_peers)
            
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
                        print(f"\n[!] Archivo eliminado localmente. Saliendo del enjambre.")
                        self.contact_tracker(CMD_EXIT_SWARM, h, mgr['trackers'])
                        with self.managers_lock: del self.managers[h]
                        continue
                self.contact_tracker(CMD_ANNOUNCE, h, mgr['trackers'])

    # --- MENÚS ---
    def convert_local_file(self):
        print("\n--- PUBLICAR ARCHIVO ---")
        # 1. Listar archivos en la carpeta del peer
        if not os.path.exists(self.folder):
            print(f"[ERROR] La carpeta {self.folder} no existe.")
            return

        all_f = [f for f in os.listdir(self.folder) if os.path.isfile(os.path.join(self.folder, f))]
        
        # 2. Filtrar: No mostrar .json, .progress, ni archivos que ya tienen torrent
        cands = []
        for f in all_f:
            if f.endswith('.json') or f.endswith('.progress'):
                continue
            if f"{f}.json" in all_f:
                continue
            cands.append(f)
        
        # 3. Validación de lista vacía
        if not cands:
            print(f"[!] No hay archivos nuevos en '{self.folder}' para publicar.")
            print("    -> Copia un archivo (ej. video.mp4) dentro de esa carpeta e intenta de nuevo.")
            return

        # 4. Mostrar menú
        for i, f in enumerate(cands): print(f"{i+1}. {f}")
        try:
            sel = int(input(">> #: ")) - 1
            if 0 <= sel < len(cands):
                fname = cands[sel]
                fpath = os.path.join(self.folder, fname)
                print("[*] Generando Torrent...")
                fhash = calculate_file_hash(fpath)
                size = os.path.getsize(fpath)
                
                # Usar KNOWN_TRACKERS si definiste múltiples, o la lista manual
                meta = {
                    "filename": fname, 
                    "filesize": size, 
                    "filehash": fhash, 
                    "trackers": KNOWN_TRACKERS # Asegúrate de tener KNOWN_TRACKERS definido arriba
                }
                
                with open(os.path.join(self.folder, f"{fname}.json"), 'w') as f: json.dump(meta, f, indent=4)
                self.add_manager(fname, fhash, size, meta['trackers'])
                self.contact_tracker(CMD_ANNOUNCE, fhash, meta['trackers'])
                print(f"[OK] {fname} publicado exitosamente.")
        except Exception as e:
            print(f"Error: {e}")






    def search_tracker(self):
        files = self.contact_tracker(CMD_LIST_FILES)
        if not files: print("[!] Tracker vacío."); return
        
        print(f"\n--- ARCHIVOS DISPONIBLES ---")
        for i, f in enumerate(files): print(f"{i+1}. {f['filename']} (Seeds/Peers: {f['peers_count']})")
        
        try:
            sel = int(input(">> Descargar #: ")) - 1
            if 0 <= sel < len(files):
                target = files[sel]
                f_hash = target['hash']
                jpath = os.path.join(self.folder, f"{target['filename']}.json")
                
                # 1. JSON Local
                if os.path.exists(jpath):
                    try:
                        with open(jpath, 'r') as f: meta = json.load(f)
                        if 'filehash' in meta:
                            self.add_manager(meta['filename'], meta['filehash'], meta['filesize'], meta['trackers'])
                            self.start_download_thread(meta['filehash'])
                            print("[OK] Configuración local cargada.")
                            return
                    except: pass

                # 2. Metadatos remotos
                peers = self.contact_tracker(CMD_ANNOUNCE, f_hash)
                if not peers: print("[!] No hay peers disponibles."); return

                meta = self.fetch_metadata_from_swarm(f_hash, peers)
                if meta and 'filename' in meta:
                    save_meta = {"filename": meta['filename'], "filesize": meta['filesize'], "filehash": f_hash, "trackers": meta['trackers']}
                    with open(jpath, 'w') as f: json.dump(save_meta, f, indent=4)
                    self.add_manager(meta['filename'], f_hash, meta['filesize'], meta['trackers'])
                    self.start_download_thread(f_hash)
                    print("[OK] Metadatos obtenidos. Descarga comenzada.")
                else:
                    print("[ERROR] No se pudieron obtener metadatos del enjambre.")
        except: pass

    def monitor(self):
        try:
            while True:
                os.system('cls' if os.name == 'nt' else 'clear')
                print(f"┌── MONITOR (Ctrl+C para salir) ───────────────────┐")
                with self.managers_lock:
                    if not self.managers: print(" [Sin actividad]")
                    for h, m in self.managers.items():
                        pct = m['fm'].get_progress_percentage()
                        bar = "█" * int(pct//5) + "░" * (20 - int(pct//5))
                        state = "BAJANDO" if m['downloading'] else ("SEEDING" if pct==100 else "PAUSA")
                        print(f" {m['filename'][:15]:<15} [{bar}] {pct}% {state}")
                time.sleep(0.5)
        except KeyboardInterrupt: pass

    def main_menu(self):
        while self.running:
            # Limpieza suave del menú principal
            # os.system('cls' if os.name == 'nt' else 'clear') 
            print(f"\n=== PEER {self.my_port} ===")
            print("1. Publicar Archivo")
            print("2. Descargar del Tracker")
            print("3. Monitor de Progreso")
            print("4. Salir")
            op = input(">> ")
            if op == '1': self.convert_local_file()
            elif op == '2': self.search_tracker()
            elif op == '3': self.monitor()
            elif op == '4': self.running = False; sys.exit()

if __name__ == "__main__":
    if len(sys.argv) < 2: print("Uso: python3 peer_node.py <PUERTO>"); sys.exit()
    PeerNode(int(sys.argv[1])).main_menu()
