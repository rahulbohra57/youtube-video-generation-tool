import sys
from unittest.mock import MagicMock

# Patch external SDKs at module level — before any app code is imported.
# Several services run init code at import time:
#   llm_service.py  → vertexai.init(), GenerativeModel(...)
#   image_service.py → ImageGenerationModel.from_pretrained(...)
#   tts_service.py  → texttospeech.TextToSpeechClient()
# Injecting mocks into sys.modules prevents those calls from hitting real APIs.

_EXTERNAL_MOCKS = {
    "vertexai": MagicMock(),
    "vertexai.generative_models": MagicMock(),
    "vertexai.preview": MagicMock(),
    "vertexai.preview.vision_models": MagicMock(),
    "google": MagicMock(),
    "google.cloud": MagicMock(),
    "google.cloud.texttospeech": MagicMock(),
    "google.cloud.storage": MagicMock(),
    "moviepy": MagicMock(),
    "moviepy.editor": MagicMock(),
    "moviepy.audio": MagicMock(),
    "moviepy.audio.fx": MagicMock(),
    "moviepy.audio.fx.all": MagicMock(),
}

for _mod_name, _mock in _EXTERNAL_MOCKS.items():
    sys.modules.setdefault(_mod_name, _mock)
