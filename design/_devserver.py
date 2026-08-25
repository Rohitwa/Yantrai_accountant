"""Static server for aifa_site + a POST /_capture sink used during the
design-canvas -> static-HTML pre-render step. Dev-only; not deployed."""
import http.server, os, sys

ROOT = os.path.dirname(os.path.abspath(__file__))

class H(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=ROOT, **kw)

    def do_POST(self):
        if self.path.startswith('/_capture'):
            name = self.path.split('?', 1)[1] if '?' in self.path else 'capture.html'
            name = os.path.basename(name)
            n = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(n)
            out = os.path.join(ROOT, '_captures', name)
            os.makedirs(os.path.dirname(out), exist_ok=True)
            with open(out, 'wb') as f:
                f.write(body)
            self.send_response(200)
            self.send_header('Content-Type', 'text/plain')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(b'ok ' + str(len(body)).encode())
            print('captured', name, len(body), flush=True)
            return
        self.send_error(404)

    def end_headers(self):
        self.send_header('Cache-Control', 'no-store')
        super().end_headers()

if __name__ == '__main__':
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8555
    http.server.ThreadingHTTPServer(('127.0.0.1', port), H).serve_forever()
