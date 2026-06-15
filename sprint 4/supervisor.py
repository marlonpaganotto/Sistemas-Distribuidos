import argparse
import json
import socket
import threading
from datetime import datetime, timezone

DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 8000


def handle_client(conn, addr):
    with conn:
        try:
            conn.settimeout(5.0)
            data = b""
            while True:
                chunk = conn.recv(4096)
                if not chunk:
                    break
                data += chunk
        except socket.timeout:
            pass
        if not data:
            print(f"[{datetime.now(timezone.utc).isoformat()}] Conexão vazia de {addr}")
            return

        text = data.decode("utf-8", errors="replace").strip()
        for line in text.splitlines():
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
                print(f"[{datetime.now(timezone.utc).isoformat()}] Recebido de {addr}: server_uuid={payload.get('server_uuid')} task={payload.get('task')}")
                print(json.dumps(payload, indent=2, ensure_ascii=False))
            except json.JSONDecodeError as exc:
                print(f"[{datetime.now(timezone.utc).isoformat()}] Falha ao decodificar JSON de {addr}: {exc}")


def run_server(host: str, port: int):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server_sock:
        server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server_sock.bind((host, port))
        server_sock.listen()
        print(f"Supervisor ouvindo em {host}:{port}")
        while True:
            conn, addr = server_sock.accept()
            thread = threading.Thread(target=handle_client, args=(conn, addr), daemon=True)
            thread.start()


def parse_args():
    parser = argparse.ArgumentParser(description="Supervisor de métricas da Sprint 4")
    parser.add_argument("--host", default=DEFAULT_HOST, help="Host de escuta do supervisor")
    parser.add_argument("--port", default=DEFAULT_PORT, type=int, help="Porta de escuta do supervisor")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    run_server(args.host, args.port)
