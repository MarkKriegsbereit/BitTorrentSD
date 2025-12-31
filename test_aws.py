import socket

# Tus IPs de AWS hardcodeadas
TRACKERS = [
    ("3.151.6.85", 5000),     # Tracker 1
    ("3.137.135.235", 5001)   # Tracker 2
]

print("--- DIAGNÓSTICO DE CONEXIÓN A AWS ---")

for ip, port in TRACKERS:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(3) # 3 segundos de tolerancia
    try:
        print(f"📡 Intentando conectar a {ip}:{port}...", end=" ")
        s.connect((ip, port))
        print("✅ ¡ÉXITO! El puerto está abierto y el Tracker responde.")
        s.close()
    except socket.timeout:
        print("❌ TIMEOUT. El Firewall de AWS está bloqueando la conexión (o IP incorrecta).")
    except ConnectionRefusedError:
        print("⛔ RECHAZADA. El Firewall deja pasar, pero NO hay un tracker corriendo en ese puerto.")
    except Exception as e:
        print(f"⚠️ Error: {e}")
