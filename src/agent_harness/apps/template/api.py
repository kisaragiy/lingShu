"""TODO: Rename this file and all references."""
from fastapi import APIRouter
from fastapi.responses import HTMLResponse
from pathlib import Path

router = APIRouter(prefix="/template", tags=["template"])

STATIC_DIR = Path(__file__).resolve().parent / "static"


@router.get("")
async def template_page():
    """TODO: Main page for this app."""
    index = STATIC_DIR / "index.html"
    if index.exists():
        return HTMLResponse(index.read_text("utf-8"))
    return {"message": "Template app - replace with your app"}


@router.get("/api/hello")
async def hello():
    """TODO: Replace with your API endpoint."""
    return {"message": "Hello from Template App!"}
