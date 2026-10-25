# WARNING: do not use this script to expose the demo 7 backend (main.py) to the internet.
# That backend has no login: anyone who can reach port 8001 can read every data file of the
# bundled endpoints through its /api routes (also the NDA-derived factory data, which may not be
# shared), and make it run long SQL queries and RDF generations.
# See "Security" in code/webapp/README.md.

from pyngrok import ngrok

# Expose local port 8001 to the public internet
public_url = ngrok.connect(8001)

print(f"Public URL: {public_url}")

# Keep the script running to keep the tunnel open
ngrok_process = ngrok.get_ngrok_process()
ngrok_process.block_until_stopped()