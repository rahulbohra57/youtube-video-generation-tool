import json
import re
import os
import time

def extract_json(text: str):
    import json
    import re

    # Remove markdown ```json blocks
    text = re.sub(r"```json|```", "", text)

    # Parse only the first complete JSON array, stopping at its closing bracket.
    # raw_decode stops as soon as it finishes the first valid JSON value, so
    # duplicate/trailing arrays from the LLM don't cause "Extra data" errors.
    start = text.find('[')
    if start != -1:
        try:
            result, _ = json.JSONDecoder().raw_decode(text, start)
            return result
        except json.JSONDecodeError as e:
            print("JSON parsing failed:", e)
            print("Raw JSON (first 500 chars):", text[start:start + 500])
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
