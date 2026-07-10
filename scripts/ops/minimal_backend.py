#!/usr/bin/env python3
"""Minimal backend do testowania proxy i portu."""

from fastapi import FastAPI

app = FastAPI(title="Minimal Test Backend", version="1.0.0")


@app.get("/")
def root():
    return {"status": "ok", "message": "Minimal backend working"}


@app.get("/system/ping")
def ping():
    return {"status": "ok", "service": "minimal", "timestamp": "2026-03-20T22:20:00Z"}


@app.get("/api/aihub/system/ping")
def aihub_ping():
    return {
        "status": "ok",
        "service": "aihub_minimal",
        "timestamp": "2026-03-20T22:20:00Z",
    }


if __name__ == "__main__":
    import uvicorn

    print("Starting minimal backend on 127.0.0.1:8080")
    uvicorn.run(
        app,
        host="127.0.0.1",
        port=8080,
        reload=False,
        log_level="info",
    )
