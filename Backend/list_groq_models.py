import os
import requests

api_key = os.getenv("GROQ_API_KEY")
if not api_key:
    # Just in case it's in a .env file
    from dotenv import load_dotenv
    load_dotenv()
    api_key = os.getenv("GROQ_API_KEY")

if api_key:
    headers = {"Authorization": f"Bearer {api_key}"}
    response = requests.get("https://api.groq.com/openai/v1/models", headers=headers)
    if response.status_code == 200:
        models = response.json().get("data", [])
        for m in models:
            print(m["id"])
    else:
        print("Failed to fetch models:", response.status_code, response.text)
else:
    print("No GROQ_API_KEY found.")
