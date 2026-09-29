"""Print the first bindable TCP port at or above the requested port."""

from __future__ import annotations

import argparse
import socket


def available_port(start: int, host: str = "127.0.0.1") -> int:
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    for port in range(start, 65536):
        with socket.socket(family, socket.SOCK_STREAM) as candidate:
            try:
                if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                    candidate.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
                candidate.bind((host, port))
                candidate.listen(1)
            except OSError:
                continue
            return port
    raise RuntimeError(f"no available TCP port at or above {start}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("port", nargs="?", type=int, default=8000)
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("port must be between 1 and 65535")
    print(available_port(args.port, args.host))
