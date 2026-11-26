"""PWA endpoints serving manifest.webmanifest and root-scoped /sw.js."""

from __future__ import annotations

import json

from django.http import HttpRequest, HttpResponse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET

PWA_CACHE_VERSION = "passage-pwa-v7"

SERVICE_WORKER_JS = f"""/* Service Worker unique pour SchoolLogs Passage (scan hors ligne + Mon badge). */
const CACHE_NAME = "{PWA_CACHE_VERSION}";

const STATIC_ASSETS = [
  "/static/passage/css/app.css",
  "/static/passage/icons/icon.svg",
  "/static/passage/vendor/jsQR.js",
  "/static/passage/vendor/qrcode.js",
  "/static/passage/js/pwa_register.js",
  "/static/passage/js/nav_menu.js",
  "/static/passage/js/table_pager.js",
  "/static/passage/js/badge_crypto.js",
  "/static/passage/js/badge_activate.js",
  "/static/passage/js/my_badge.js",
  "/static/passage/js/agent_scan.js",
  "/manifest.webmanifest",
  "/mon-badge/"
];

const CACHEABLE_NAV_PATHS = new Set(["/scan/", "/mon-badge/"]);

self.addEventListener("install", (event) => {{
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(STATIC_ASSETS)).then(() => self.skipWaiting())
  );
}});

self.addEventListener("activate", (event) => {{
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE_NAME).map((k) => caches.delete(k)))
    ).then(() => self.clients.claim())
  );
}});

self.addEventListener("fetch", (event) => {{
  const req = event.request;
  if (req.method !== "GET") {{
    return;
  }}

  const url = new URL(req.url);
  if (url.origin !== self.location.origin) {{
    return;
  }}

  // Ne jamais mettre en cache les API, l'activation unique, la connexion ou les vues sensibles.
  if (
    url.pathname.startsWith("/api/") ||
    url.pathname.startsWith("/badge/activer/") ||
    url.pathname.startsWith("/responsable/") ||
    url.pathname.startsWith("/parent/") ||
    url.pathname.startsWith("/admin/") ||
    url.pathname === "/connexion/" ||
    url.pathname === "/deconnexion/"
  ) {{
    return;
  }}

  // Ressources statiques : réseau d'abord (mise à jour immédiate) avec repli cache hors ligne.
  if (url.pathname.startsWith("/static/passage/") || url.pathname === "/manifest.webmanifest") {{
    event.respondWith(
      fetch(req)
        .then((resp) => {{
          if (resp && resp.ok && resp.type === "basic") {{
            const copy = resp.clone();
            caches.open(CACHE_NAME).then((cache) => cache.put(req, copy));
          }}
          return resp;
        }})
        .catch(() => caches.match(req))
    );
    return;
  }}

  // Pages PWA (/scan/ et /mon-badge/) : réseau d'abord, puis repli cache hors ligne.
  if (CACHEABLE_NAV_PATHS.has(url.pathname)) {{
    event.respondWith(
      fetch(req)
        .then((resp) => {{
          // Ne mettre en cache que les réponses 200 directes (jamais une redirection vers /connexion/).
          if (resp && resp.status === 200 && !resp.redirected) {{
            const copy = resp.clone();
            caches.open(CACHE_NAME).then((cache) => cache.put(url.pathname, copy));
          }}
          return resp;
        }})
        .catch(() => caches.match(url.pathname))
    );
  }}
}});
"""


@require_GET
def pwa_manifest_view(request: HttpRequest) -> HttpResponse:
    """Serve the Web App Manifest for the unified Passage PWA."""
    manifest = {
        "name": "SchoolLogs Passage",
        "short_name": "Passage",
        "description": "Scan hors ligne des badges et affichage Mon badge",
        "start_url": "/scan/",
        "scope": "/",
        "display": "standalone",
        "background_color": "#f1f3f7",
        "theme_color": "#7c3aed",
        "lang": "fr",
        "icons": [
            {
                "src": "/static/passage/icons/icon.svg",
                "sizes": "any",
                "type": "image/svg+xml",
                "purpose": "any",
            }
        ],
    }
    return HttpResponse(
        json.dumps(manifest, ensure_ascii=False),
        content_type="application/manifest+json; charset=utf-8",
    )


@never_cache
@require_GET
def pwa_service_worker_view(request: HttpRequest) -> HttpResponse:
    """Serve the root-scoped Service Worker covering /scan/ and /mon-badge/."""
    response = HttpResponse(
        SERVICE_WORKER_JS,
        content_type="application/javascript; charset=utf-8",
    )
    response["Service-Worker-Allowed"] = "/"
    return response
