from fastapi import APIRouter
from . import session_routes, file_routes, auth_routes, config_routes, claw_routes, cad_routes
from .cad_v2_routes import build_router as build_cad_v2_router
from app.application.services.cad_v2_service import get_cad_v2_service
from app.interfaces.dependencies import get_current_user

def create_api_router() -> APIRouter:
    """Create and configure the main API router"""
    api_router = APIRouter()
    
    # Include all sub-routers
    api_router.include_router(session_routes.router)
    api_router.include_router(file_routes.router)
    api_router.include_router(auth_routes.router)
    api_router.include_router(config_routes.router)
    api_router.include_router(claw_routes.router)
    api_router.include_router(cad_routes.router)
    api_router.include_router(build_cad_v2_router(get_cad_v2_service, get_current_user))
    
    return api_router

# Create the main router instance
router = create_api_router() 
