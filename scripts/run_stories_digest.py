import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.routes.stories import _send_stories_daily_digest

_send_stories_daily_digest()
