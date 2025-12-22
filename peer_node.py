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
TRACKER_IP = "127.0.0.1"

# !!! FRENO DE MANO !!!
# Tiempo en segundos que el programa SE CONGELA después de bajar cada pieza.
# 0.5 = Rápido pero visible. 2.0 = Muy lento (ideal para explicar en video).
TIME_DELAY = .2

class PeerNode:
    def __init__(self, port):
        self.my_port = port
        self.my_ip = "127.0.0.1"
        self.my_id = f"{self.my_ip}:{port}"
        self.folder = f"peer_{port}"
        
        if not os.path.exists(self.folder):
            os.makedirs(self.folder)

        self.managers = {}
        self.managers_lock = threading.Lock()
        self.running = True

        # 1. PRIMERO: Recuperar memoria local (Auto-Discovery)
        self.recover_state()

        # 2. SEGUNDO: Iniciar servicios
        threading.Thread(target=self.start_server, daemon=True).start()
        threading.Thread(target=self.heartbeat_loop, daemon=True).start()


    def recover_state(self):
        """
        Escanea la carpeta local buscando archivos .json conocidos.
        Carga su estado y notifica al tracker INMEDIATAMENTE.
        """
        print(f"\n[*] Escaneando carpeta local '{self.folder}' para recuperación...")
        json_files = glob.glob("*.json")
        
        # También buscar jsons dentro de la carpeta del peer si se movieron ahí
        peer_jsons = glob.glob(os.path.join(self.folder, "*.json"))
        all_jsons = list(set(json_files + peer_jsons))

        count = 0
        for json_path in all_jsons:
            try:
                with open(json_path, 'r') as f:
                    meta = json.load(f)
                
                # Cargamos el gestor (esto lee el .progress automáticamente)
                mgr = self.add_manager(
                    meta['filename'], 
                    meta['filehash'], 
                    meta['filesize'], 
                    meta['trackers']
                )
                
                pct = mgr['fm'].get_progress_percentage()
                print(f"   -> Recuperado: {meta['filename']} ({pct}%)")
                
                # ANUNCIO INMEDIATO:
                # Esto hace que el Tracker sepa QUE YA VOLVIMOS y qué tenemos.
                # Cumple con "Reintegrarse con el Tracker" 
                self.contact_tracker(CMD_ANNOUNCE, meta['filehash'], meta['trackers'])
                count += 1
            except Exception as e:
                print(f"   [!] Error recuperando {json_path}: {e}")
        
        if count > 0:
            print(f"[*] Se recuperaron {count} archivos. El tracker ha sido notificado.")
        else:
            print("[*] No se encontraron descargas previas para recuperar.")






    def get_manager(self, file_hash):
        with self.managers_lock:
            return self.managers.get(file_hash)

    def add_manager(self, filename, file_hash, size, trackers):
        with self.managers_lock:
            if file_hash in self.managers:
                return self.managers[file_hash]
            
            full_path = os.path.join(self.folder, filename)
            is_seeder = False
            if os.path.exists(full_path) and os.path.getsize(full_path) == size:
                is_seeder = True

            fm = FileManager(self.folder, filename, size, is_seeder)
            self.managers[file_hash] = {
                "fm": fm,
                "filename": filename,
                "trackers": trackers,
                "downloading": False,
                "speed": 0 # Para cálculo futuro
            }
            return self.managers[file_hash]

    # --- SERVIDOR (SUBIDA) ---
    def start_server(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1) # Evita error de puerto ocupado
        s.bind(('0.0.0.0', self.my_port))
        s.listen(10)
        while self.running:
            try:
                conn, addr = s.accept()
                threading.Thread(target=self.handle_upload, args=(conn,)).start()
            except: break

    def handle_upload(self, conn):
        try:
            req = json.loads(conn.recv(BUFFER_SIZE).decode())
            if req['command'] == CMD_REQUEST_CHUNK:
                f_hash = req.get('file_hash')
                idx = req.get('chunk_index')
                mgr = self.get_manager(f_hash)
                
                if mgr:
                    data = mgr['fm'].read_chunk(idx)
                    if data:
                        header = json.dumps({"status": "ok", "size": len(data)}).encode()
                        conn.send(header + b'\n' + data)
                        return
                conn.send(json.dumps({"status": "error"}).encode() + b'\n')
        except: pass
        finally: conn.close()

    # --- CLIENTE (DESCARGA) ---
    def start_download_thread(self, file_hash):
        mgr = self.get_manager(file_hash)
        if not mgr: return
        
        if mgr['fm'].get_progress_percentage() == 100:
            return

        mgr['downloading'] = True
        t = threading.Thread(target=self._download_logic, args=(file_hash,))
        t.daemon = True
        t.start()

    def _download_logic(self, file_hash):
        mgr = self.get_manager(file_hash)
        fm = mgr['fm']
        
        while fm.get_missing_chunks() and mgr['downloading'] and self.running:
            peers = self.contact_tracker(CMD_ANNOUNCE, file_hash, mgr['trackers'])
            valid_peers = [p for p in peers if p['id'] != self.my_id and p['percent'] > 0]
            
            if not valid_peers:
                time.sleep(2)
                continue

            for idx in fm.get_missing_chunks():
                if not mgr['downloading'] or not self.running: break
                for p_data in valid_peers:
                    # Intentamos descargar
                    if self.request_chunk(p_data['id'], idx, file_hash, fm):
                        break # Pasamos al siguiente chunk
            
            time.sleep(0.1)

        if not fm.get_missing_chunks():
            mgr['downloading'] = False
            self.contact_tracker(CMD_ANNOUNCE, file_hash, mgr['trackers'])

    def request_chunk(self, peer_addr, idx, file_hash, fm):
        try:
            ip, port = peer_addr.split(':')
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(3)
            s.connect((ip, int(port)))
            
            req = {
                "command": CMD_REQUEST_CHUNK, 
                "file_hash": file_hash, 
                "chunk_index": idx
            }
            s.send(json.dumps(req).encode())
            
            f = s.makefile('rb')
            line = f.readline()
            if not line: return False
            header = json.loads(line)
            
            if header['status'] == 'ok':
                data = f.read(header['size'])
                fm.write_chunk(idx, data)
                
                # --- AQUÍ ESTÁ EL FRENO OBLIGATORIO ---
                # Se ejecuta SIEMPRE que se baja una pieza correctamente
                if TIME_DELAY > 0:
                    time.sleep(TIME_DELAY)
                # --------------------------------------
                return True
        except: pass
        return False

    # --- COMUNICACIÓN TRACKER ---
    def contact_tracker(self, command, file_hash=None, trackers_list=None):
        if trackers_list is None: trackers_list = [(TRACKER_IP, TRACKER_PORT)]
        for ip, port in trackers_list:
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.settimeout(2)
                s.connect((ip, port))
                req = {"command": command, "peer_id": self.my_id}
                if command == CMD_ANNOUNCE and file_hash:
                    mgr = self.get_manager(file_hash)
                    if mgr:
                        req.update({
                            "file_hash": file_hash,
                            "filename": mgr["filename"],
                            "percent": mgr["fm"].get_progress_percentage()
                        })
                s.send(json.dumps(req).encode())
                resp = json.loads(s.recv(BUFFER_SIZE).decode())
                s.close()
                if resp['status'] == 'ok':
                    return resp['peers'] if command == CMD_ANNOUNCE else resp['files']
            except: pass
        return []

    def heartbeat_loop(self):
        while self.running:
            time.sleep(5)
            with self.managers_lock:
                hashes = list(self.managers.keys())
            for h in hashes:
                mgr = self.get_manager(h)
                if mgr: self.contact_tracker(CMD_ANNOUNCE, h, mgr['trackers'])

    # --- VISUALIZACIÓN Y MENÚ ---
    def monitor_downloads(self):
        """Pantalla de estado en tiempo real que se actualiza sola."""
        try:
            while True:
                # Limpiar pantalla sin parpadeo excesivo
                os.system('cls' if os.name == 'nt' else 'clear')
                print(f"┌── MONITOR DE DESCARGAS (Puerto {self.my_port}) ──┐")
                print("│ Presiona Ctrl+C para volver al menú principal │")
                print("└───────────────────────────────────────────────┘\n")
                
                active = False
                with self.managers_lock:
                    for h, m in self.managers.items():
                        active = True
                        pct = m['fm'].get_progress_percentage()
                        # Crear barra de progreso [#####-----]
                        blocks = int(pct // 5)
                        bar = "█" * blocks + "░" * (20 - blocks)
                        status = "BAJANDO ⬇" if m['downloading'] else ("SEEDING ⬆" if pct==100 else "PAUSA ⏸")
                        
                        print(f"Archivo: {m['filename']}")
                        print(f"Estado:  [{bar}] {pct}% | {status}")
                        print("-" * 50)

                if not active:
                    print(" [!] No hay archivos activos.")
                
                time.sleep(0.5) # Actualizar pantalla cada medio segundo
        except KeyboardInterrupt:
            pass # Volver al menú al presionar Ctrl+C

    def main_menu(self):
        while self.running:
            os.system('cls' if os.name == 'nt' else 'clear')
            print("\n┌── BITTORRENT CLIENT ──┐")
            print("│ 1. Cargar .json local │")
            print("│ 2. Buscar en Tracker  │")
            print("│ 3. VER PROGRESO (MONITOR) │ <--- CLIC AQUÍ")
            print("│ 4. Salir              │")
            print("└───────────────────────┘")
            op = input(">> ")
            
            if op == '1': 
                self.load_local_json()
            elif op == '2': 
                self.search_tracker()
            elif op == '3': 
                self.monitor_downloads()
            elif op == '4': 
                self.running = False
                sys.exit()



    def load_local_json(self):
        """
        Escanea la carpeta actual buscando archivos .json (metadatos).
        Permite cargar un archivo nuevo o recuperar uno previo.
        """
        # 1. Buscar archivos .json en el directorio actual
        json_files = glob.glob("*.json")
        
        if not json_files:
            print("\n[!] No se encontraron archivos .json en la carpeta actual.")
            print("    Asegúrate de haber generado el torrent con 'crear_torrent.py'.")
            input("Presiona Enter para volver...")
            return
        
        print("\n┌── SELECCIÓN DE ARCHIVO LOCAL ──┐")
        for i, f in enumerate(json_files):
            print(f"│ {i+1}. {f:<25} │")
        print("└────────────────────────────────┘")
            
        try:
            sel = int(input("\n>> Selecciona el número del archivo: ")) - 1
            if 0 <= sel < len(json_files):
                # 2. Cargar metadatos
                selected_file = json_files[sel]
                with open(selected_file, 'r') as f:
                    meta = json.load(f)
                
                # 3. Inicializar el Gestor (Esto lee automáticamente el .progress si existe)
                mgr = self.add_manager(
                    meta['filename'], 
                    meta['filehash'], 
                    meta['filesize'], 
                    meta['trackers']
                )
                
                # 4. ANUNCIO INMEDIATO (Vital para el punto 4 de la rúbrica)
                # Le decimos al tracker: "¡Estoy aquí y tengo X% de este archivo!"
                # Así, si éramos seeders o leechers desconectados, volvemos a aparecer en la red.
                self.contact_tracker(CMD_ANNOUNCE, meta['filehash'], meta['trackers'])
                
                # 5. Verificar estado actual
                current_pct = mgr['fm'].get_progress_percentage()
                print(f"\n[INFO] Archivo cargado: {meta['filename']}")
                print(f"[INFO] Integridad verificada: {current_pct}% completado.")
                
                # 6. Lógica de Decisión (Descargar, Reanudar o Sembrar)
                if current_pct < 100:
                    if mgr['downloading']:
                        print("[!] Este archivo ya se está descargando activamente en segundo plano.")
                    else:
                        # Si es 0% es "Iniciar", si es >0% es "Reanudar"
                        action = "Reanudar" if current_pct > 0 else "Iniciar"
                        q = input(f"¿{action} descarga ahora? (s/n): ")
                        
                        if q.lower() == 's':
                            self.start_download_thread(meta['filehash'])
                else:
                    print("[★] Archivo completo. Modo SEEDING activo automáticamente.")
                    print("    (Los otros peers ya pueden descargar de ti).")
                
                input("\nPresiona Enter para volver al menú...")
            else:
                print("[!] Selección inválida.")
                time.sleep(1)
        except ValueError:
            print("[!] Por favor ingresa un número válido.")
            time.sleep(1)
        except Exception as e:
            print(f"[ERROR] No se pudo cargar el archivo: {e}")
            input("Enter para continuar...")





    def search_tracker(self):
        files = self.contact_tracker(CMD_LIST_FILES)
        if not files: print("Tracker vacío."); time.sleep(1); return
        print(f"\n{'#':<3} | {'ARCHIVO':<20} | {'PEERS'}")
        for i, f in enumerate(files): print(f"{i+1:<3} | {f['filename']:<20} | {f['peers_count']}")
        try:
            sel = int(input("\nDescargar #: ")) - 1
            if 0 <= sel < len(files):
                target = files[sel]
                json_name = f"{target['filename']}.json"
                if os.path.exists(json_name):
                    with open(json_name, 'r') as f: meta = json.load(f)
                    self.add_manager(meta['filename'], meta['filehash'], meta['filesize'], meta['trackers'])
                    self.start_download_thread(meta['filehash'])
                else: print(f"Falta {json_name}"); time.sleep(2)
        except: pass

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Uso: python3 peer_node.py <PUERTO>")
        sys.exit()
    node = PeerNode(int(sys.argv[1]))
    node.main_menu()
