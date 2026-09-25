"""Run the gateway with ``python -m gateway --config config/local.json``."""

import argparse

from .config import load_config
from .events import emit
from .http import create_server


def main():
    parser = argparse.ArgumentParser(description="Run the inference gateway")
    parser.add_argument("--config", required=True, help="Path to a JSON configuration file")
    args = parser.parse_args()
    server = create_server(load_config(args.config))
    emit("gateway_started", address="http://%s:%s" % server.server_address)
    try:
        server.serve_forever(poll_interval=0.05)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        emit("gateway_stopped")


if __name__ == "__main__":
    main()
