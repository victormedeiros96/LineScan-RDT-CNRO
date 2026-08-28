from src.api.routes_folders import router as folders_router
from src.api.routes_models import router as models_router
from src.api.routes_projects import router as projects_router
from src.api.routes_reports import router as reports_router
from src.api.routes_debug import router as debug_router
from src.api.routes_uploads import router as uploads_router

__all__ = ["folders_router", "models_router", "projects_router", "reports_router", "debug_router", "uploads_router"]
