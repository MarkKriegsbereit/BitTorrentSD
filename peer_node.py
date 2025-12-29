# peer_node.py
import socket
import threading
import time
import json
import sys
import os
import glob
import random
import traceback
import hashlib
from common import *
from file_manager import FileManager

# --- CONFIGURACIÓN GLOBAL ---
KNOWN_TRACKERS = [] 

class PeerNode:
    def __init__(self, port, tracker_ip_hint):
        self.my_port = port
        self.my_ip = get_local_ip() 
        self.my_id = f"{self.my_ip}:{port}"
        
        self.folder = f"peer_{port}"
        if not os.path.exists(self.folder): os.makedirs(self.folder)

        self.managers = {} 
        self.managers_lock = threading.Lock()
        
        # Estadísticas y Seguridad
        self.peer_performance = {} 
        self.download_stats = {}
        self.banned_peers = {} # {peer_id: tiempo_expiracion}
        self.running = True

        print(f"[*] MI IDENTIDAD: {self.my_id}")
        print(f"[*] CARPETA: {self.folder}")
        
        # --- CORRECCIÓN MULTI-TRACKER ---
        global KNOWN_TRACKERS
        # Si no hay trackers definidos, usamos el hint. 
        # Si quisieras poner trackers fijos hardcodeados, defínelos arriba en KNOWN_TRACKERS = [...]
        if not KNOWN_TRACKERS:
            # Por defecto añadimos el puerto 5000. 
            # Si quieres redundancia automática en la misma IP, podrías añadir más puertos.
            KNOWN_TRACKERS.append((tracker_ip_hint, 5000))
            KNOWN_TRACKERS.append((tracker_ip_hint, 5001))
        # --------
        
        

        self.scan_folder()

        # Iniciar hilos de fondo
        threading.Thread(target=self.start_server, daemon=True).start()
        threading.Thread(target=self.heartbeat_loop, daemon=True).start()

    # ==========================================
    #               HILO DE DESCARGA
    # ==========================================
    def start_download_thread(self, file_hash):
        mgr = self.managers.get(file_hash)
        if not mgr or mgr['fm'].get_progress_percentage() == 100: return
        mgr['downloading'] = True
        threading.Thread(target=self._download_logic, args=(file_hash,), daemon=True).start()

    def _download_logic(self, file_hash):
        mgr = self.managers.get(file_hash)
        fm = mgr['fm']
        self.download_stats = {} 
        
        print(f"[*] Smart Swarm V4 (Turbo) iniciado para: {mgr['filename']}")
        
        # --- CACHÉ DE PEERS (Optimización CRÍTICA para Windows) ---
        peers = []
        last_tracker_update = 0
        UPDATE_INTERVAL = 10 # Segundos entre consultas al tracker
        
        while fm.get_missing_chunks() and mgr['downloading'] and self.running:
            
            # 1. Actualizar lista de peers (Solo si pasó el tiempo o estamos solos)
            if (time.time() - last_tracker_update > UPDATE_INTERVAL) or not peers:
                try:
                    new_peers = self.contact_tracker(CMD_ANNOUNCE, file_hash, mgr['trackers'])
                    if new_peers:
                        peers = new_peers
                        last_tracker_update = time.time()
                except Exception as e:
                    # Si falla el tracker, seguimos con los peers que ya conocemos
                    pass
            
            # 2. Filtrar candidatos (excluyéndome a mí)
            candidates = [p for p in peers if p['id'] != self.my_id]
            
            if not candidates:
                print("[!] Buscando peers...")
                time.sleep(1)
                last_tracker_update = 0 # Forzar actualización inmediata
                continue

            # 3. Ordenar por rendimiento (Latencia)
            candidates.sort(key=lambda p: self.peer_performance.get(p['id'], 0.5))
            
            # --- MEJORA DE COLABORACIÓN (Shuffle) ---
            # Si hay varios peers buenos, no uses siempre al #1.
            # Tomamos los 3 mejores y elegimos uno al azar. Esto activa la colaboración.
            if len(candidates) > 1:
                top_n = min(len(candidates), 3)
                best_candidates = candidates[:top_n]
                random.shuffle(best_candidates) # <--- Aquí está la magia de colaborar
                candidates = best_candidates + candidates[top_n:]
            
            # 4. Seleccionar qué pieza pedir
            missing = fm.get_missing_chunks()
            if not missing: break
            
            random.shuffle(missing)
            chunk_to_try = missing[0]
            
            chunk_downloaded = False

            # 5. Ronda de intentos
            for p in candidates:
                start_time = time.time()
                
                # Intentar descargar
                result = self.request_chunk(p['id'], chunk_to_try, file_hash, fm)
                
                elapsed = time.time() - start_time
                
                if result == "success":
                    self.update_peer_score(p['id'], elapsed, success=True)
                    self.download_stats[p['id']] = self.download_stats.get(p['id'], 0) + 1
                    
                    # Chismoso: Avisar al tracker (Solo el 10% de las veces para no saturar)
                    if random.random() < 0.1: 
                        threading.Thread(target=self.contact_tracker, 
                                         args=(CMD_ANNOUNCE, file_hash, mgr['trackers']), 
                                         daemon=True).start()
                    chunk_downloaded = True
                    break 

                elif result == "missing":
                    pass 

                elif result == "corrupt":
                    self.update_peer_score(p['id'], elapsed, success=False)

                elif result == "network_error":
                    self.update_peer_score(p['id'], elapsed, success=False)
            
            if not chunk_downloaded: 
                time.sleep(0.1)

        if not fm.get_missing_chunks():
            mgr['downloading'] = False
            self.contact_tracker(CMD_ANNOUNCE, file_hash, mgr['trackers'])
            print(f"\n[★] DESCARGA FINALIZADA: {mgr['filename']}")
            
            # --- BLOQUE DE SEGURIDAD FINAL ---
            print("🕵️ Iniciando validación final post-ensamblaje...")
            full_path = os.path.join(self.folder, mgr['filename'])
            
            if self.verify_integrity(full_path, file_hash):
                print(f"✅ ARCHIVO GUARDADO Y VERIFICADO: {mgr['filename']}")
            else:
                print("🚨 ERROR CRÍTICO: La firma digital del archivo no coincide.")
                print("🗑️ El archivo está corrupto. Eliminando del disco...")
                
                if os.path.exists(full_path): os.remove(full_path)
                if os.path.exists(full_path + ".progress"): os.remove(full_path + ".progress")
                
                # Borramos el JSON también para forzar re-check de metadatos
                json_path = os.path.join(self.folder, f"{mgr['filename']}.json")
                if os.path.exists(json_path): os.remove(json_path)

                with self.managers_lock:
                    if file_hash in self.managers: del self.managers[file_hash]

                print("⚠️ Archivo y metadatos eliminados por seguridad.")

    def update_peer_score(self, peer_id, time_taken, success):
        """ Sistema de Puntuación Dinámica """
        current_avg = self.peer_performance.get(peer_id, 0.5)
        # Si era nuevo (-1), inicializar
        if current_avg < 0: current_avg = time_taken

        if success:
            # Suavizado exponencial (30% peso a la nueva muestra)
            new_score = (current_avg * 0.7) + (time_taken * 0.3)
        else:
            # Castigo fuerte por errores
            new_score = current_avg + 3.0 
            
        self.peer_performance[peer_id] = new_score

    # ==========================================
    #               CLIENTE DE RED
    # ==========================================

    def request_chunk(self, peer_id, idx, file_hash, fm):
        # 1. SEGURIDAD: Verificar lista negra
        if peer_id in self.banned_peers:
            if time.time() < self.banned_peers[peer_id]: return "banned"
            else: del self.banned_peers[peer_id] 

        target_ip, target_port = peer_id.split(':')
        
        # En Bridged mode, usar 127.0.0.1 como fallback puede confundir si target_ip es la propia
        ips_to_try = [target_ip] 
        
        s = None
        for ip_cand in ips_to_try:
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.settimeout(5.0) 
                s.connect((ip_cand, int(target_port)))
                break
            except: 
                if s: s.close()
                s = None
        
        if not s: return "network_error"

        try:
            msg = {"command": CMD_REQUEST_CHUNK, "file_hash": file_hash, "chunk_index": idx}
            
            # --- CORRECCIÓN CRÍTICA: Añadir \n al enviar ---
            s.sendall(json.dumps(msg).encode() + b'\n')
            
            f = s.makefile('rb')
            head_line = f.readline()
            if not head_line: return "network_error"
            
            head = json.loads(head_line)
            
            if head.get('status') == 'ok':
                chunk_data = f.read(head['size'])
                if len(chunk_data) != head['size']: return "network_error"

                # Hash Check
                expected = self.get_expected_hash(file_hash, idx)
                if expected:
                    calc = hashlib.sha256(chunk_data).hexdigest()
                    if calc != expected:
                        self.banned_peers[peer_id] = time.time() + 60
                        return "corrupt"

                fm.write_chunk(idx, chunk_data)
                return "success"
            
            elif head.get('status') == 'missing': return "missing"
        except: return "network_error"
        finally: 
            if s: s.close()
        return "network_error"





    def get_expected_hash(self, file_hash, idx):
        mgr = self.managers.get(file_hash)
        if not mgr: return None
        json_path = os.path.join(self.folder, f"{mgr['filename']}.json")
        try:
            # Nota: En producción esto se debería cachear en memoria
            with open(json_path, 'r') as f:
                data = json.load(f)
                return data['piece_hashes'][idx]
        except: return None

    # ==========================================
    #               SERVIDOR
    # ==========================================
    def start_server(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(('0.0.0.0', self.my_port))
        s.listen(10)
        while self.running:
            try:
                conn, addr = s.accept()
                threading.Thread(target=self.handle_upload, args=(conn,)).start()
            except: pass



    def handle_upload(self, conn):
        try:
            conn.settimeout(10)
            raw = conn.recv(4096).decode()
            if not raw: return
            
            req = json.loads(raw.strip())
            cmd = req.get('command')
            
            if cmd == CMD_REQUEST_CHUNK:
                mgr = self.managers.get(req.get('file_hash'))
                if mgr:
                    # Leemos el bloque del disco
                    data = mgr['fm'].read_chunk(req.get('chunk_index'))
                    
                    if data:
                        header = json.dumps({"status": "ok", "size": len(data)})
                        
                        # --- CORRECCIÓN CRÍTICA AQUÍ ---
                        # Usamos sendall() en lugar de send()
                        # send() trunca los datos si son grandes (como un bloque de 1MB)
                        payload = header.encode() + b'\n' + data
                        conn.sendall(payload)  # <--- CAMBIO: sendall
                        
                    else:
                        conn.sendall(json.dumps({"status": "missing"}).encode() + b'\n')
            
            elif cmd == CMD_GET_METADATA:
                mgr = self.managers.get(req.get('file_hash'))
                if mgr:
                    try:
                        json_path = os.path.join(self.folder, f"{mgr['filename']}.json")
                        with open(json_path, 'r') as f:
                            saved_data = json.load(f)
                            hashes = saved_data.get('piece_hashes', [])
                    except: hashes = []

                    meta = {
                        "status": "ok", 
                        "filename": mgr['filename'], 
                        "filesize": mgr['fm'].total_size, 
                        "trackers": mgr['trackers'],
                        "piece_hashes": hashes 
                    }
                    # Aquí también es bueno usar sendall por seguridad
                    conn.sendall(json.dumps(meta).encode() + b'\n') # <--- CAMBIO: sendall

        except Exception as e:
            # print(f"Error upload: {e}") 
            pass
        finally: 
            conn.close()
        
        
        

    # ==========================================
    #        GESTIÓN Y AUXILIARES
    # ==========================================
    def add_manager(self, filename, file_hash, size, trackers, force_leecher=False):
        with self.managers_lock:
            # Si ya existe en memoria, lo devolvemos (a menos que queramos recargar, 
            # pero eso lo manejamos borrándolo antes en search_tracker)
            if file_hash in self.managers: 
                return self.managers[file_hash]
            
            real_path = os.path.join(self.folder, filename)
            
            # LÓGICA CORREGIDA:
            # Es Seeder SOLO si el tamaño coincide Y no nos están forzando a ser leecher
            size_match = os.path.exists(real_path) and os.path.getsize(real_path) == size
            is_seeder = size_match and not force_leecher
            
            fm = FileManager(self.folder, filename, size, is_seeder)
            self.managers[file_hash] = {"fm": fm, "filename": filename, "trackers": trackers, "downloading": False}
            return self.managers[file_hash]

    def scan_folder(self):
        json_files = glob.glob(os.path.join(self.folder, "*.json"))
        for json_path in json_files:
            try:
                with open(json_path, 'r') as f: meta = json.load(f)
                with self.managers_lock:
                    if meta['filehash'] in self.managers: continue
                
                real_path = os.path.join(self.folder, meta['filename'])
                # Si existe el archivo o su progreso, lo cargamos
                if os.path.exists(real_path) or os.path.exists(real_path + ".progress"):
                    self.add_manager(meta['filename'], meta['filehash'], meta['filesize'], KNOWN_TRACKERS)
                    self.contact_tracker(CMD_ANNOUNCE, meta['filehash'], KNOWN_TRACKERS)
            except: pass

    def fetch_metadata_from_swarm(self, file_hash, peers_list):
        print(f"[*] Buscando metadatos...")
        for p_data in peers_list:
            if p_data['id'] == self.my_id: continue 
            target_ip, target_port = p_data['id'].split(':')
            ips_to_try = [target_ip, '127.0.0.1']
            for ip_cand in ips_to_try:
                s = None
                try:
                    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    s.settimeout(2)
                    s.connect((ip_cand, int(target_port)))
                    
                    # Corrección importante: Enviar \n al final
                    msg = {"command": CMD_GET_METADATA, "file_hash": file_hash}
                    s.send(json.dumps(msg).encode() + b'\n') 
                    
                    f = s.makefile('rb')
                    line = f.readline()
                    if line: 
                        resp = json.loads(line)
                        s.close()
                        return resp
                    s.close()
                except: 
                    if s: s.close()
        return None

    def contact_tracker(self, command, file_hash=None, trackers=None):
        if not trackers: trackers = KNOWN_TRACKERS
        combined_peers = []
        combined_files = [] # Para CMD_LIST_FILES
        success_count = 0
        
        # Iteramos por TODOS los trackers conocidos
        for ip, port in trackers:
            s = None
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
                raw_resp = s.recv(BUFFER_SIZE).decode()
                if not raw_resp: continue
                
                resp = json.loads(raw_resp)
                
                if resp.get('status') == 'ok':
                    success_count += 1
                    # Si pedimos lista de archivos, acumulamos resultados de todos los trackers
                    if command == CMD_LIST_FILES and 'files' in resp:
                        combined_files.extend(resp['files'])
                    
                    # Si es un anuncio, acumulamos peers de todos los trackers
                    if command == CMD_ANNOUNCE and 'peers' in resp:
                        combined_peers.extend(resp['peers'])
            except:
                # Si un tracker falla, simplemente probamos el siguiente
                pass
            finally:
                if s: s.close()
        
        # Procesar resultados combinados
        if command == CMD_LIST_FILES:
            # Eliminar duplicados de archivos basados en hash
            seen_hashes = set()
            unique_files = []
            for f in combined_files:
                if f['hash'] not in seen_hashes:
                    unique_files.append(f)
                    seen_hashes.add(f['hash'])
            return unique_files
            
        if command == CMD_ANNOUNCE:
            # Eliminar duplicados de peers
            seen = set()
            unique_peers = []
            for p in combined_peers:
                if p['id'] not in seen:
                    unique_peers.append(p)
                    seen.add(p['id'])
            return unique_peers
            
        return []




    def heartbeat_loop(self):
        while self.running:
            time.sleep(2)
            self.scan_folder()
            with self.managers_lock: hashes = list(self.managers.keys())
            for h in hashes:
                mgr = self.managers.get(h)
                if not mgr['downloading']:
                    self.contact_tracker(CMD_ANNOUNCE, h, KNOWN_TRACKERS)

    # ==========================================
    #               INTERFAZ / MONITOR
    # ==========================================
    def monitor(self):
        try:
            while True:
                os.system('cls' if os.name == 'nt' else 'clear')
                print(f"\n┌── ESTADO DE ARCHIVOS ────────────────────────────┐")
                with self.managers_lock:
                    if not self.managers: print(" [Sin actividad]")
                    for h, m in self.managers.items():
                        pct = m['fm'].get_progress_percentage()
                        filled = int(pct / 5)
                        bar = "█" * filled + "░" * (20 - filled)
                        state = "BAJANDO ⬇" if m['downloading'] else ("SEEDING ⬆" if pct==100 else "PAUSA ⏸")
                        print(f" {m['filename'][:15]:<15} [{bar}] {pct:>3.0f}% {state}")
                
                print(f"\n┌── BALANCEO DE CARGA (Quién me alimenta) ─────────┐")
                print(f"│ {'PEER ID':<20} | {'LATENCIA':<9} | {'PIEZAS':<8} │")
                print(f"├──────────────────────┼───────────┼──────────┤")
                all_peers = set(list(self.peer_performance.keys()) + list(self.download_stats.keys()))
                if not all_peers: print(f"│ {'(Esperando datos...)':<43} │")
                for pid in all_peers:
                    lat = self.peer_performance.get(pid, 0.0)
                    chunks = self.download_stats.get(pid, 0)
                    print(f"│ {pid:<20} | {lat:.4f}s  | {chunks:<8} │")
                print(f"└──────────────────────┴───────────┴──────────┘")
                time.sleep(2.5)
        except KeyboardInterrupt: pass

    def sabotage_file(self):
        print("\n--- MODO SABOTAJE (CORRUPCIÓN) ---")
        target = input(">> Ruta del archivo a corromper: ").strip()
        
        if not os.path.exists(target):
            print("❌ El archivo no existe.")
            return

        try:
            file_size = os.path.getsize(target)
            header_safe = 50000  # Protegemos los primeros 50KB (cabecera)
            damage_size = 2048   # Dañamos 2KB
            
            if file_size < (header_safe + damage_size):
                print("⚠️ Archivo muy pequeño para corromper de forma segura.")
                return

            # Calcular punto aleatorio
            max_offset = file_size - damage_size
            random_offset = random.randint(header_safe, max_offset)

            # Inyectar basura
            with open(target, "r+b") as f:
                f.seek(random_offset)
                f.write(os.urandom(damage_size))
            
            print(f"💀 ÉXITO: Se inyectó ruido en el byte {random_offset} del archivo.")
            print("   (El hash del archivo ha cambiado, pero la cabecera sigue intacta)")
            
        except Exception as e:
            print(f"❌ Error: {e}")
            
            
            
    def verify_integrity(self, filepath, expected_hash):
        """
        Calcula el hash SHA-256 del archivo descargado y lo compara con el original.
        Retorna True si son idénticos, False si el archivo está corrupto.
        """
        print(f"🕵️ Verificando integridad de: {filepath}...")
        
        sha256_hash = hashlib.sha256()
        
        try:
            with open(filepath, "rb") as f:
                # Leemos por bloques para no saturar la RAM
                for byte_block in iter(lambda: f.read(4096), b""):
                    sha256_hash.update(byte_block)
            
            calculated_hash = sha256_hash.hexdigest()
            
            if calculated_hash == expected_hash:
                print("✅ INTEGRIDAD APROBADA. El archivo es auténtico.")
                return True
            else:
                print("🚨 ALERTA DE SEGURIDAD: HASH MISMATCH")
                print(f"   Esperado: {expected_hash}")
                print(f"   Calculado: {calculated_hash}")
                return False
                
        except Exception as e:
            print(f"❌ Error al verificar archivo: {e}")
            return False



    def main_menu(self):
        while self.running:
            print(f"\n=== NODE {self.my_ip}:{self.my_port} ===")
            # Agregamos la opción 5 visualmente
            print("1. Publicar | 2. Descargar | 3. Monitor | 4. Salir | 5. 💀 Sabotaje")
            
            op = input(">> ")
            
            if op == '1': self.convert_local_file()
            elif op == '2': self.search_tracker()
            elif op == '3': self.monitor()
            elif op == '4': 
                print("👋 Cerrando sesión en el Tracker...")
                # Avisar a todos los trackers que me voy
                # Enviamos 'None' como hash para indicar que me voy de TODOS los swarms
                self.contact_tracker(CMD_EXIT_SWARM, file_hash=None, trackers=KNOWN_TRACKERS)
                
                self.running = False
                sys.exit()
            # Agregamos la llamada lógica
            elif op == '5': self.sabotage_file()
            else: print("Opción no válida")




    def convert_local_file(self):
        print("\n--- PUBLICAR ---")
        if not os.path.exists(self.folder): print("Carpeta no existe."); return
        
        # Filtramos para no mostrar jsons ni archivos de progreso
        all_f = [f for f in os.listdir(self.folder) if os.path.isfile(os.path.join(self.folder, f))]
        cands = [f for f in all_f if not f.endswith('.json') and not f.endswith('.progress') and f"{f}.json" not in all_f]
        
        if not cands: print("[!] No hay archivos nuevos."); return

        for i, f in enumerate(cands): print(f"{i+1}. {f}")
        try:
            sel = int(input(">> #: ")) - 1
            if 0 <= sel < len(cands):
                fname = cands[sel]
                fpath = os.path.join(self.folder, fname)
                size = os.path.getsize(fpath)
                
                print(f"[*] Generando hashes de seguridad (BLOCK_SIZE={BLOCK_SIZE})...")
                
                # --- LÓGICA INTEGRADA DE CREAR_TORRENT ---
                sha256_global = hashlib.sha256()
                piece_hashes = []
                
                with open(fpath, 'rb') as f:
                    while True:
                        chunk = f.read(BLOCK_SIZE)
                        if not chunk: break
                        
                        # 1. Hash Global
                        sha256_global.update(chunk)
                        
                        # 2. Hash por Pieza (CRÍTICO PARA SEGURIDAD)
                        p_hash = hashlib.sha256(chunk).hexdigest()
                        piece_hashes.append(p_hash)
                
                fhash = sha256_global.hexdigest()
                # -----------------------------------------

                meta = {
                    "filename": fname, 
                    "filesize": size, 
                    "filehash": fhash, 
                    "piece_hashes": piece_hashes, # ¡Ahora sí incluimos la seguridad!
                    "trackers": KNOWN_TRACKERS
                }
                
                # Guardamos el .json
                with open(os.path.join(self.folder, f"{fname}.json"), 'w') as f: 
                    json.dump(meta, f, indent=4)
                
                # Nos registramos como dueños del archivo
                self.add_manager(fname, fhash, size, KNOWN_TRACKERS)
                
                # Avisamos al tracker
                self.contact_tracker(CMD_ANNOUNCE, fhash, KNOWN_TRACKERS)
                
                print(f"[OK] {fname} publicado con {len(piece_hashes)} piezas verificadas.")
        except Exception as e:
            print(f"[ERROR] Al publicar: {e}")



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
                
                # =======================================================
                #   LÓGICA MAESTRA DE RECUPERACIÓN (CORREGIDA)
                # =======================================================
                if os.path.exists(jpath):
                    try:
                        with open(jpath, 'r') as f: meta = json.load(f)
                        
                        if 'filehash' in meta:
                            print(f"[*] Configuración encontrada: {meta['filename']}")
                            local_file = os.path.join(self.folder, meta['filename'])
                            progress_file = local_file + ".progress"
                            
                            # LIMPIEZA DE MEMORIA CRÍTICA:
                            # Borramos cualquier gestor previo para obligar a recargar desde disco
                            with self.managers_lock:
                                if f_hash in self.managers: del self.managers[f_hash]

                            # CASO 1: ¿Existe el archivo de video FÍSICAMENTE?
                            if os.path.exists(local_file) and 'piece_hashes' in meta:
                                print(f"🕵️ El archivo existe. Escaneando integridad bloque a bloque...")
                                
                                valid_chunks = []
                                total_chunks = len(meta['piece_hashes'])
                                corrupted_count = 0
                                
                                with open(local_file, 'rb') as f_check:
                                    for i, expected_hash in enumerate(meta['piece_hashes']):
                                        f_check.seek(i * BLOCK_SIZE)
                                        chunk_data = f_check.read(BLOCK_SIZE)
                                        if not chunk_data: break
                                        
                                        if hashlib.sha256(chunk_data).hexdigest() == expected_hash:
                                            valid_chunks.append(i)
                                        else:
                                            corrupted_count += 1
                                
                                # Reporte
                                if corrupted_count > 0:
                                    print(f"⚠️ Se encontraron {corrupted_count} piezas corruptas/modificadas.")
                                    print(f"✅ Se rescataron {len(valid_chunks)} piezas válidas.")
                                elif len(valid_chunks) < total_chunks:
                                    print(f"ℹ️ Archivo incompleto. Reanudando descarga...")
                                else:
                                    print(f"✅ Archivo verificado al 100%. Listo para Seeding.")

                                # Actualizamos el .progress
                                with open(progress_file, 'w') as f_prog:
                                    json.dump({"downloaded": valid_chunks}, f_prog)
                                
                                # REINICIO CON FORCE_LEECHER
                                # Si hubo corrupción o faltan piezas, pasamos force_leecher=True
                                # para que add_manager NO crea que somos seeders solo por el tamaño.
                                is_imperfect = (corrupted_count > 0) or (len(valid_chunks) < total_chunks)
                                
                                self.add_manager(meta['filename'], meta['filehash'], meta['filesize'], KNOWN_TRACKERS, force_leecher=is_imperfect)
                                self.start_download_thread(meta['filehash'])
                                return

                            # CASO 2: Existe JSON pero NO video
                            else:
                                print("⚠️ JSON encontrado pero sin video. Reiniciando descarga.")
                                if os.path.exists(progress_file): os.remove(progress_file)
                                
                                self.add_manager(meta['filename'], meta['filehash'], meta['filesize'], KNOWN_TRACKERS)
                                self.start_download_thread(meta['filehash'])
                                return

                    except Exception as e: 
                        print(f"[!] Error leyendo configuración local: {e}")
                        if os.path.exists(jpath): os.remove(jpath)

                # ==========================================
                #  PASO 2: Descarga de red (Nuevo)
                # ==========================================
                peers = self.contact_tracker(CMD_ANNOUNCE, f_hash, KNOWN_TRACKERS)
                if not peers: print("[!] Sin peers."); return
                
                meta = self.fetch_metadata_from_swarm(f_hash, peers)
                if meta and 'filename' in meta:
                    save_meta = {
                        "filename": meta['filename'], 
                        "filesize": meta['filesize'], 
                        "filehash": f_hash, 
                        "trackers": KNOWN_TRACKERS,
                        "piece_hashes": meta.get('piece_hashes', []) 
                    }
                    with open(jpath, 'w') as f: json.dump(save_meta, f, indent=4)
                    
                    self.add_manager(meta['filename'], f_hash, meta['filesize'], KNOWN_TRACKERS)
                    self.start_download_thread(f_hash)
                    print("[OK] Metadatos obtenidos. Iniciando descarga...")
                else: 
                    print("[ERROR] No se pudieron obtener metadatos.")

        except Exception as e:
            traceback.print_exc()
            print(f"[CRASH] Error fatal: {e}")




if __name__ == "__main__":
    if len(sys.argv) < 2: 
        print("Uso: python3 peer_node.py <PUERTO_LOCAL>")
        sys.exit()
    
    ip_sugerida = get_local_ip()
    print("--- CONFIGURACIÓN ---")
    print(f"Detecté que tu IP local es: {ip_sugerida}")
    tracker_hint = input("IP del Tracker Principal [Enter para usar tu IP local]: ")
    if not tracker_hint.strip(): tracker_hint = ip_sugerida
    
    PeerNode(int(sys.argv[1]), tracker_hint).main_menu()

