"""LEGACY / UNWIRED — not imported by ``aihub.main`` or any active module (06.07 repair sprint).

Hardcodes a specific, now-stale Cloudflare tunnel URL (`trycloudflare.com`) into the OpenAPI
``servers`` block. Kept for historical reference only. Do not call ``install()`` from active code
without replacing the hardcoded URL with a real, current one (or removing the ``servers``
override entirely and letting clients infer the base URL).
"""

from fastapi.openapi.utils import get_openapi

def install(app):
    def custom_openapi():
        if app.openapi_schema:
            return app.openapi_schema

        schema = get_openapi(
            title="AI Hub Server",
            version="5.0.0",
            routes=app.routes,
        )

        schema["servers"] = [
            {
                "url": "https://representative-fundamentals-secretariat-nsw.trycloudflare.com"
            }
        ]

        app.openapi_schema = schema
        return schema

    app.openapi = custom_openapi
