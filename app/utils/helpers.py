import json
import re
import os
import time

def extract_json(text: str):
    import json
    import re

    # Remove markdown ```json blocks
    text = re.sub(r"```json|```", "", text)

    # Extract JSON array
    match = re.search(r'\[.*\]', text, re.DOTALL)
    
    if match:
        json_str = match.group()
        try:
            return json.loads(json_str)
        except json.JSONDecodeError as e:
            print("JSON parsing failed:", e)
            print("Raw JSON:", json_str)
            raise ValueError("Invalid JSON from LLM")

    raise ValueError("No JSON found in LLM response")

def ensure_dir(path: str):
    if not os.path.exists(path):
        os.makedirs(path)


def cleanup_files_older_than(path: str, days: int = 7):
    """Delete files older than `days` inside `path` recursively."""
    if not os.path.isdir(path):
        return

    cutoff = time.time() - (days * 24 * 60 * 60)
    for root, _, files in os.walk(path):
        for name in files:
            file_path = os.path.join(root, name)
            try:
                if os.path.getmtime(file_path) < cutoff:
                    os.remove(file_path)
            except FileNotFoundError:
                continue
            except Exception:
                continue
