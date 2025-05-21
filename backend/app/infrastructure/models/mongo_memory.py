from typing import List, Dict, Any
from beanie import Document


class MongoMemory(Document):
    """MongoDB document for Agent Memories"""
    messages: List[Dict[str, Any]]

    class Settings:
        name = "memories"