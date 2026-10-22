# WARNING: do not use this script to expose the demo 7 backend (main.py) to the internet.
# That backend downloads every http(s) address that a registered online endpoint names
# (its document and its tss:dataFile), also addresses on localhost and on the private
# network, and passes what it gets on to whoever asked. Anyone who can reach port 8001 can
# register such an endpoint. Through a public tunnel, anyone on the internet could use it
# to read pages and files that only this computer can reach.
# See "Security" in code/webapp/README.md.

from pyngrok import ngrok

# Expose local port 8001 to the public internet
public_url = ngrok.connect(8001)

print(f"Public URL: {public_url}")

# Keep the script running to keep the tunnel open
ngrok_process = ngrok.get_ngrok_process()
ngrok_process.block_until_stopped()