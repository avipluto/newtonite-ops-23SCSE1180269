"""Settings, read from environment variables (see .env.example and docker-compose.yml)."""
import os

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql+psycopg://ops:ops@localhost:5434/ops")
TOKEN_TTL_HOURS = int(os.environ.get("TOKEN_TTL_HOURS", "12"))
# Folder containing the built React app (set inside Docker). Empty = serve the API only.
FRONTEND_DIST = os.environ.get("FRONTEND_DIST", "")
