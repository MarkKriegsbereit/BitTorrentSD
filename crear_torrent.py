# En crear_torrent.py
def create_torrent(file_path, tracker_ip="127.0.0.1", tracker_port=5000):
    if not os.path.exists(file_path):
        print(f"Error: El archivo {file_path} no existe.")
        return

    print(f"Generando metadatos blindados para {file_path}...")
    file_size = os.path.getsize(file_path)
    file_name = os.path.basename(file_path)
    
    # Calcular hash global y hash POR PIEZA
    sha256_global = hashlib.sha256()
    piece_hashes = []
    
    with open(file_path, 'rb') as f:
        while True:
            chunk = f.read(BLOCK_SIZE)
            if not chunk: break
            sha256_global.update(chunk)
            # Guardamos el hash de ESTE pedacito
            piece_hashes.append(hashlib.sha256(chunk).hexdigest())
            
    file_hash = sha256_global.hexdigest()
    
    torrent_data = {
        "filename": file_name,
        "filesize": file_size,
        "piece_size": BLOCK_SIZE,
        "filehash": file_hash,
        "piece_hashes": piece_hashes, # <--- ¡ESTO ES LA CLAVE DE LA SEGURIDAD!
        "trackers": [(tracker_ip, tracker_port)]
    }

    output_name = f"{file_name}.json"
    with open(output_name, "w") as f:
        json.dump(torrent_data, f, indent=4)
    
    print(f"¡ÉXITO! .json creado con {len(piece_hashes)} hashes de verificación.")
