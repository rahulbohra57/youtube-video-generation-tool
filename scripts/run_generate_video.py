"""Entry point for the generate-video GitHub Actions workflow.

Reads the full job payload from the GENERATE_PAYLOAD env var (JSON string set
by the workflow from the workflow_dispatch input) and calls generator_agent.run().
"""
import json
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.agents import generator_agent

raw = os.environ["GENERATE_PAYLOAD"]
p = json.loads(raw)

generator_agent.run(
    p["headline"],
    p["code"],
    batch_id=p.get("batch_id"),
    job_id=p.get("job_id"),
    public_id=p.get("public_id"),
    force_run=bool(p.get("force_run", False)),
    genre=p.get("genre", ""),
    details=p.get("details", ""),
    virality_score=float(p.get("virality_score", 0) or 0),
    channel_id=p.get("channel_id", "news"),
    script_type=p.get("script_type", "news"),
    language=p.get("language", "en"),
)
