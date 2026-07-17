from __future__ import annotations
from functools import lru_cache
from hashlib import sha256
from importlib.resources import files

@lru_cache
def load_ai_assets()->tuple[str,str,str]:
    rubric=files("vietnam_calendar.assets").joinpath("importance-rubric-v1.md").read_text("utf-8")
    prompt=files("vietnam_calendar.assets").joinpath("event-analysis-v1.txt").read_text("utf-8")
    digest=sha256((rubric+"\0"+prompt).encode()).hexdigest()
    return rubric,prompt,digest
