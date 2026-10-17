"""Local development only: tell the browser not to keep static files, so an edited script or style shows up on the next reload."""


class NoCacheStaticMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if request.path.startswith("/static/"):
            response["Cache-Control"] = "no-store"
        return response
