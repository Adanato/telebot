import os

from dotenv import load_dotenv
from google import genai

load_dotenv()

client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])

try:
    print("Listing models...")
    for model in client.models.list(config={"page_size": 10}):
        print(model.name)
except Exception as e:
    print(f"Error: {e}")
