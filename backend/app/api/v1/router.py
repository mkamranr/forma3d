from fastapi import APIRouter

from app.api.v1 import auth, jobs, modeling, projects, versions

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(auth.router)
api_router.include_router(projects.router)
api_router.include_router(modeling.router)
api_router.include_router(versions.router)
api_router.include_router(jobs.router)
