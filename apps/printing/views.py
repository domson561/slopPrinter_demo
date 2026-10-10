"""Wydawanie plików do druku dla GH przez podpisany link (kontrakt pkt 4)."""

from django.http import FileResponse, Http404, HttpResponseGone
from django.views.decorators.http import require_GET

from . import links


@require_GET
def signed_file(request, token):
    try:
        version = links.resolve(token)
    except links.LinkExpired:
        return HttpResponseGone("Link wygasł; sklep dośle świeży.")
    except links.LinkError:
        raise Http404 from None
    resp = FileResponse(version.file.open("rb"), content_type="application/pdf")
    resp["X-Content-Type-Options"] = "nosniff"
    resp["Cache-Control"] = "private, no-store"
    return resp
