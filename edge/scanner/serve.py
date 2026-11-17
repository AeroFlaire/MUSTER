"""Serve the MUSTER phone scanner (PBI-12) so a phone can open it over HTTPS.

Phones only allow the camera on https:// pages, so this server can make its own
self-signed certificate. The page decodes everything on the phone and sends nothing
back; this server only hands out the page's files.

    python edge/scanner/serve.py                 # HTTPS on port 8443, self-signed certificate
    python edge/scanner/serve.py --http --port 8080 --host 127.0.0.1   # plain HTTP, for localhost
                                                                       # or behind a tunnel

Then on the phone (same Wi-Fi or the laptop on the phone's hotspot) open the
https://<laptop-ip>:8443 address printed below and accept the certificate warning once.

Security headers on every response:
  - Content-Security-Policy: the page may load only its own files and may not contact any
    other site (so even a bug cannot send a scan anywhere).
  - Permissions-Policy: camera only, no microphone or location.
  - No caching, no referrer, no framing.
"""
from __future__ import annotations

import argparse
import datetime as dt
import ipaddress
import os
import socket
import ssl
import sys
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
CERT_DIR = HERE / ".cert"   # *.pem is git-ignored; this folder never gets committed

CSP = ("default-src 'none'; script-src 'self' 'wasm-unsafe-eval'; style-src 'self'; "
       "img-src 'self' data: blob:; media-src 'self' blob:; connect-src 'self'; "
       "base-uri 'none'; form-action 'none'; frame-ancestors 'none'")


class Handler(SimpleHTTPRequestHandler):
    extensions_map = {**SimpleHTTPRequestHandler.extensions_map,
                      ".wasm": "application/wasm", ".js": "text/javascript",
                      ".cjs": "text/javascript", ".json": "application/json"}

    def end_headers(self):
        self.send_header("Content-Security-Policy", CSP)
        self.send_header("Permissions-Policy", "camera=(self), microphone=(), geolocation=()")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def do_GET(self):
        # Serve the page and its own files only: not the tests, the certificate, or dotfiles.
        path = self.path.split("?", 1)[0]
        if any(part.startswith(".") for part in path.split("/")) or path.startswith("/tests"):
            self.send_error(404)
            return
        super().do_GET()

    def log_message(self, fmt, *args):
        # Request lines only (method, path, status). The page never sends scan data here.
        sys.stderr.write("[scanner] %s\n" % (fmt % args))


def lan_addresses() -> list[str]:
    addrs = set()
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("192.0.2.1", 9))          # no packet is sent; this just picks the LAN route
        addrs.add(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            addrs.add(info[4][0])
    except OSError:
        pass
    return sorted(a for a in addrs if not a.startswith("127."))


def self_signed(ips: list[str]) -> tuple[Path, Path]:
    """A 30-day certificate for localhost and this laptop's LAN addresses."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    CERT_DIR.mkdir(exist_ok=True)
    key_path, cert_path = CERT_DIR / "scanner-key.pem", CERT_DIR / "scanner-cert.pem"
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "MUSTER scanner (dev)")])
    sans = [x509.DNSName("localhost"), x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]
    sans += [x509.IPAddress(ipaddress.ip_address(ip)) for ip in ips]
    now = dt.datetime.now(dt.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - dt.timedelta(minutes=5))
            .not_valid_after(now + dt.timedelta(days=30))
            .add_extension(x509.SubjectAlternativeName(sans), critical=False)
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .sign(key, hashes.SHA256()))
    key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM,
                                           serialization.PrivateFormat.PKCS8,
                                           serialization.NoEncryption()))
    os.chmod(key_path, 0o600)
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    return cert_path, key_path


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Serve the MUSTER phone scanner")
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--port", type=int, default=None)
    p.add_argument("--http", action="store_true", help="plain HTTP (localhost or behind a tunnel)")
    p.add_argument("--cert", help="use this certificate instead of a self-signed one")
    p.add_argument("--key", help="private key for --cert")
    args = p.parse_args(argv)

    port = args.port or (8080 if args.http else 8443)
    server = ThreadingHTTPServer((args.host, port), partial(Handler, directory=str(HERE)))
    ips = lan_addresses()
    scheme = "http" if args.http else "https"
    if not args.http:
        cert, key = (args.cert, args.key) if args.cert else self_signed(ips)
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        ctx.load_cert_chain(cert, key)
        server.socket = ctx.wrap_socket(server.socket, server_side=True)

    print(f"MUSTER scanner on {scheme}://localhost:{port}/")
    if args.host == "0.0.0.0":
        for ip in ips:
            print(f"  phone: {scheme}://{ip}:{port}/")
    if not args.http and not args.cert:
        print("  (self-signed certificate: the phone will warn once; choose to continue)")
    print("Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
