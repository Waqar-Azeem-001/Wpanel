"""
The system actor: who performs work no user is present for (fulfilling a paid order, applying a paid
change, scheduled jobs).

The domain, hosting and other services authorise an *actor*. Rather than a second, unchecked code
path for each of them, background work passes ``SYSTEM`` and runs the very same service functions. It
holds every permission, so it must only ever be constructed by code - it is never reachable from a
request, a form or an API field - and audit records it as "system" (a NULL actor).
"""


class SystemActor:
    is_authenticated = True
    is_active = True
    is_system = True
    pk = None

    def has_perm(self, perm, obj=None):
        return True

    def has_perms(self, perms, obj=None):
        return True

    def __str__(self):
        return "system"

    __repr__ = __str__


SYSTEM = SystemActor()


def is_system(actor):
    return bool(getattr(actor, "is_system", False))
