import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.agents import lead_researcher

force_domain = os.environ.get("FORCE_DOMAIN") or None
lead_researcher.run(force_domain=force_domain)
