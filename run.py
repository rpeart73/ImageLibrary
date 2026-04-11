"""Loom Research — Production entry point."""
from app import app
from database import init_db

init_db()
app.run(host='0.0.0.0', port=5050, debug=False)
