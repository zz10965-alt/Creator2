"""FastAPI app: serve the frontend and expose the pipeline as JSON endpoints.

Run:  uvicorn app:app --reload   ->  open http://localhost:8000
"""
import os

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

import config
import pipeline
import chat
import insights

BASE = os.path.dirname(os.path.abspath(__file__))
STATIC = os.path.join(BASE, "static")

app = FastAPI(title="Creator Shortlist")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"],
                   allow_headers=["*"])


def _load_creators(brief, force_demo=False):
    """LIVE if a YouTube key is set and reachable, else built-in sample data.

    force_demo=True skips the live API entirely (used by the "Try a sample" button so
    a demo run is instant and costs no API quota).
    """
    if not force_demo and config.YOUTUBE_API_KEY:
        try:
            import youtube_api
            return youtube_api.get_creators(brief), "LIVE"
        except Exception as e:
            print(f"[warn] live API failed ({e}), falling back to sample data")
    import mock
    return mock.get_creators(), "DEMO"


def _get_trending(region=None, keywords=None):
    if config.YOUTUBE_API_KEY:
        try:
            import youtube_api
            return youtube_api.get_trending(youtube_api._youtube(), region=region,
                                            keywords=keywords)
        except Exception:
            pass
    import mock
    return mock.get_trending(region=region, keywords=keywords)


@app.post("/api/campaign")
async def api_campaign(req: Request):
    body = await req.json()
    campaign_input = body.get("campaign_input") or body or {}
    force_demo = bool(body.get("force_demo"))
    use_gemini = body.get("use_gemini", not force_demo)
    creators, mode = _load_creators(campaign_input, force_demo=force_demo)
    results = pipeline.run_pipeline(campaign_input, creators, mode=mode,
                                    use_gemini=use_gemini)
    return JSONResponse(results)


@app.post("/api/chat")
async def api_chat(req: Request):
    body = await req.json()
    out = chat.chat(body.get("message", ""), body.get("history") or [],
                    results=body.get("results") or None)
    return JSONResponse(out)


@app.post("/api/insights")
async def api_insights(req: Request):
    body = await req.json()
    out = insights.generate(body.get("results") or {}, body.get("campaign_input") or {},
                            use_gemini=body.get("use_gemini", not bool(body.get("force_demo"))))
    return JSONResponse(out)


@app.get("/api/trending")
async def api_trending(region: str = None, keywords: str = None):
    kw = [k.strip() for k in (keywords or "").split(",") if k.strip()]
    return JSONResponse({"trending": _get_trending(region, keywords=kw)})


@app.post("/api/trending/match")
async def api_trending_match(req: Request):
    body = await req.json()
    out = insights.match_trending(body.get("trending") or [],
                                  body.get("creators") or [],
                                  body.get("campaign_input") or {},
                                  use_gemini=body.get("use_gemini", not bool(body.get("force_demo"))))
    return JSONResponse(out)


@app.post("/api/trending/search")
async def api_trending_search(req: Request):
    body = await req.json()
    out = insights.search_trends(body.get("campaign_input") or {},
                                 use_gemini=not bool(body.get("force_demo")))
    return JSONResponse(out)


@app.post("/api/script")
async def api_script(req: Request):
    body = await req.json()
    out = insights.generate_script(body.get("creator") or {},
                                   body.get("campaign_input") or {},
                                   concept=body.get("concept"),
                                   use_gemini=body.get("use_gemini", True))
    return JSONResponse(out)


@app.post("/api/concepts")
async def api_concepts(req: Request):
    body = await req.json()
    out = insights.generate_concepts(body.get("creator") or {},
                                     body.get("campaign_input") or {},
                                     use_gemini=body.get("use_gemini", True))
    return JSONResponse(out)


@app.get("/")
async def index():
    return FileResponse(os.path.join(STATIC, "index.html"))


app.mount("/static", StaticFiles(directory=STATIC), name="static")
