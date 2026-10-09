from .demo import DemoAdapter
from .manual import ManualAdapter
from .native import NativeAdapter
from .whm_api import WhmApiAdapter

ADAPTERS = {
    "manual": ManualAdapter,
    "whm_api": WhmApiAdapter,
    "demo": DemoAdapter,
    "native": NativeAdapter,
}


def build_adapter(server):
    """Instantiate the adapter class for a ``Server`` row."""
    adapter_class = ADAPTERS[server.kind]
    credentials = {"api_username": server.api_username, "api_token": server.get_api_token()}
    adapter = adapter_class(host=server.hostname, port=server.api_port, credentials=credentials,
                            use_ssl=server.use_ssl, verify_ssl=server.verify_ssl)
    adapter.server = server  # a native adapter needs to know which node it speaks for
    return adapter
