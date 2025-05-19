from motor.motor_asyncio import AsyncIOMotorClient
from pymongo.errors import ConnectionFailure
from typing import Optional, List, Type
import logging
from beanie import init_beanie, Document
from functools import lru_cache

from .config import get_settings

logger = logging.getLogger(__name__)

class MongoDB:
    def __init__(self):
        self._client: Optional[AsyncIOMotorClient] = None
        self._settings = get_settings()
    
    async def initialize(self, document_models: List[Type[Document]]) -> None:
        """Initialize MongoDB connection and Beanie ODM."""
        if self._client is not None:
            return
            
        try:
            # Connect to MongoDB
            self._client = AsyncIOMotorClient(
                self._settings.mongodb_uri,
            )
            # Verify the connection
            await self._client.admin.command('ping')
            logger.info("Successfully connected to MongoDB")
            
            # Initialize Beanie
            await init_beanie(
                database=self._client[self._settings.mongodb_database],
                document_models=document_models
            )
            logger.info("Successfully initialized Beanie")
        except ConnectionFailure as e:
            logger.error(f"Failed to connect to MongoDB: {str(e)}")
            raise
        except Exception as e:
            logger.error(f"Failed to initialize Beanie: {str(e)}")
            raise
    
    async def shutdown(self) -> None:
        """Shutdown MongoDB connection."""
        if self._client is not None:
            self._client.close()
            self._client = None
            logger.info("Disconnected from MongoDB")
        get_mongodb.cache_clear()
            

@lru_cache()
def get_mongodb() -> MongoDB:
    """Get the MongoDB instance."""
    return MongoDB()

