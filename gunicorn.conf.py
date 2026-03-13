import os

bind = f"0.0.0.0:{os.environ.get('PORT', '5050')}"

# Config generation and debate startup can hold a request open for longer than
# Gunicorn's 30s default, especially on hosted free tiers.
timeout = 180
graceful_timeout = 30
keepalive = 15

# Keep the deployment simple for Render while allowing a few concurrent long
# polling / streaming requests.
workers = 1
threads = 4
