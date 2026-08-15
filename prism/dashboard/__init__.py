"""Dashboard route — serves the Prism UI."""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter(tags=["dashboard"])

DASHBOARD_HTML = (Path(__file__).parent / "index.html").read_text()


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard():
    """Serve the Prism dashboard UI."""
    return DASHBOARD_HTML
