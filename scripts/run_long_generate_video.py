#!/usr/bin/env python3
# scripts/run_long_generate_video.py
#
# Entry point for generate-long-video.yml workflow.
# Reads GENERATE_PAYLOAD env var and calls long_generator_agent.run().

import os
import json
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def main():
    raw = os.getenv("GENERATE_PAYLOAD", "")
    if not raw:
        raise RuntimeError("GENERATE_PAYLOAD env var is required")

    payload = json.loads(raw)
    logger.info("Starting long video generation: %s", payload.get("job_id"))

    from app.agents.long_generator_agent import run
    run(
        headline=payload["headline"],
        code=payload["code"],
        batch_id=payload.get("batch_id"),
        job_id=payload.get("job_id"),
        public_id=payload.get("public_id"),
        force_run=payload.get("force_run", False),
        genre=payload.get("genre", ""),
        details=payload.get("details", ""),
        channel_id=payload.get("channel_id", "stories_long"),
    )


if __name__ == "__main__":
    main()
