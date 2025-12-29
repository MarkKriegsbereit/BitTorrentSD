# test_handshake.py (CORREGIDO)
import socket
import json

# En lugar de un handshake binario, enviamos un comando JSON que tu servidor entienda
# Por ejemplo: CMD_GET_METADATA
msg = {
    "command": "GET_METADATA",
    "file_hash": "dummy_hash", # O un hash real si quieres probar a fondo
    "peer_id": "TEST_SCRIPT"
}

s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
try:
    # Asegúrate de que la IP sea correcta (la de tu VM o localhost)
    target_ip = "192.168.116.1" 
    port = 15000
    
    print(f"Conectando a {target_ip}:{port}...")
    s.connect((target_ip, port))
    
    print("Conectado! Enviando solicitud JSON...")
    # Tu servidor espera un salto de línea al final en algunos casos, 
    # o simplemente el JSON decodificado.
    s.send(json.dumps(msg).encode())

    # Escuchar respuesta
    response = s.recv(4096)
    print("Respuesta recibida (Raw):", response)
    
    if response:
        print("Respuesta decodificada:", response.decode())
    else:
        print("El servidor cerró la conexión sin responder.")

except Exception as e:
    print(f"Error: {e}")
finally:
    s.close()
