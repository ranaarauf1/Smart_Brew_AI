"""Starts the SmartBrew AI web app (the simulated touchscreen)."""

import os

from app import create_app

app = create_app()


def main():
    """
    Purpose: Runs the Flask server using HOST, PORT and FLASK_DEBUG from the
        environment (Docker sets HOST=0.0.0.0).

    Args:
        None

    Returns:
        None
    """
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "5000"))
    debug = os.environ.get("FLASK_DEBUG", "0") == "1"
    app.run(host=host, port=port, debug=debug)


if __name__ == "__main__":
    main()
