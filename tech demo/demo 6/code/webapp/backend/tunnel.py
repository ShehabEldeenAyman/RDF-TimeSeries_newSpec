from pyngrok import ngrok

# Expose local port 8001 to the public internet
public_url = ngrok.connect(8001)

print(f"Public URL: {public_url}")

# Keep the script running to keep the tunnel open
ngrok_process = ngrok.get_ngrok_process()
ngrok_process.block_until_stopped()