from .manual import ManualAdapter
from .whm_api import WhmApiAdapter

ADAPTERS = {
    "manual": ManualAdapter,
    "whm_api": WhmApiAdapter,
}


def build_adapter(server):
    """Instantiate the adapter class for a ``Server`` row."""
    adapter_class = ADAPTERS[server.kind]
    credentials = {"api_username": server.api_username, "api_token": server.get_api_token()}
    return adapter_class(host=server.hostname, port=server.api_port, credentials=credentials,
                         use_ssl=server.use_ssl, verify_ssl=server.verify_ssl)
