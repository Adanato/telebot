import google.genai as genai
import inspect

print("Inspect genai module:")
print(dir(genai))

if hasattr(genai, "Client"):
    client = genai.Client(api_key="TEST")
    print("\nInspect Client:")
    print(dir(client))
    
if hasattr(genai, "types"):
    print("\nInspect types:")
    for name, obj in inspect.getmembers(genai.types):
         if "Rate" in name or "Limit" in name:
             print(f"Found related type: {name}")
