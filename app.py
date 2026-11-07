import os, threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from main import main

class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Spoken English Lab v1.0 OK")
    def log_message(self, format, *args):
        return

def start_health_server():
    port = int(os.getenv("PORT", "0"))
    if port > 0:
        server = HTTPServer(("0.0.0.0", port), HealthHandler)
        threading.Thread(target=server.serve_forever, daemon=True).start()

if __name__ == "__main__":
    start_health_server()
    main()
