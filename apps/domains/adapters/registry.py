from .manual import ManualAdapter
from .onlinenic import OnlineNicAdapter

ADAPTERS = {
    "manual": ManualAdapter,
    "onlinenic": OnlineNicAdapter,
}


def build_adapter(provider):
    """Instantiate the adapter class for a ``RegistrarProvider`` row."""
    adapter_class = ADAPTERS[provider.kind]
    return adapter_class(provider.get_credentials(), sandbox=provider.sandbox)
