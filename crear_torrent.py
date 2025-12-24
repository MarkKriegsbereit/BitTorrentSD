# crear_torrent.py
import json
import os
import sys
import hashlib
from common import BLOCK_SIZE

def create_torrent(file_path, tracker_ip="127.0.0.1", tracker_port=5000):
    if not os.path.exists(file_path):
        print(f"Error: El archivo {file_path} no existe.")
        return

    print(f"Generando metadatos BLINDADOS para {file_path}...")
    file_size = os.path.getsize(file_path)
    file_name = os.path.basename(file_path)
    
    sha256_global = hashlib.sha256()
    piece_hashes = [] # Lista para guardar los hashes individuales
    
    with open(file_path, 'rb') as f:
        while True:
            chunk = f.read(BLOCK_SIZE)
            if not chunk: break
            
            # Hash global
            sha256_global.update(chunk)
            
            # --- CAMBIO CRÍTICO: HASH POR PIEZA ---
            # Calculamos el SHA256 de este bloque de 1MB y lo guardamos
            p_hash = hashlib.sha256(chunk).hexdigest()
            piece_hashes.append(p_hash)
            # --------------------------------------
            
    file_hash = sha256_global.hexdigest()
    
    torrent_data = {
        "filename": file_name,
        "filesize": file_size,
        "piece_size": BLOCK_SIZE,
        "filehash": file_hash,
        "piece_hashes": piece_hashes, # <--- Aquí va la seguridad
        "trackers": [(tracker_ip, tracker_port)]
    }

    output_name = f"{file_name}.json"
    with open(output_name, "w") as f:
        json.dump(torrent_data, f, indent=4)
    
    print(f"¡ÉXITO! .json creado con {len(piece_hashes)} piezas verificadas.")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Uso: python3 crear_torrent.py <ruta_archivo_mp4>")
    else:
        create_torrent(sys.argv[1])
