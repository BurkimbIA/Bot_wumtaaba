"""Single-process server with a successful, graceful exit when the worker is idle."""

import argparse

import uvicorn

from .main import create_app


def run(host="0.0.0.0", port=8000):
    app = create_app()
    server = uvicorn.Server(uvicorn.Config(app, host=host, port=port, access_log=False))

    def request_shutdown():
        # Do not send SIGTERM: Uvicorn re-raises external signals after cleanup.
        # Its ordinary signal handlers remain active for deployments and interrupts.
        server.should_exit = True

    app.state.request_shutdown = request_shutdown
    server.run()
    if not server.started:
        raise SystemExit(3)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    run(args.host, args.port)
