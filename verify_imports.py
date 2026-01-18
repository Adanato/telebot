import sys
import os
# Ensure src is in python path
sys.path.insert(0, os.path.abspath("src"))

try:
    from telebot.infrastructure.agents import AgentOrchestrator
    print("AgentOrchestrator imported successfully")
    
    from google import genai
    print("google.genai imported successfully")
except ImportError as e:
    print(f"ImportError: {e}")
except Exception as e:
    print(f"Error: {e}")
