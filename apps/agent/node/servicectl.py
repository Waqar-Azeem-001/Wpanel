"""
The only repair the agent will carry out on WHPanel's request (N10): restart one of a short list of the node's own services (``catalog.HEAL_SERVICES``). It does nothing else: no files are
deleted, no customer process is touched, nothing is rebooted.

Safety built in: the web server's and SSH server's configuration is tested first and a failing test stops the restart (restarting on a broken configuration would take every site down for good);
after the restart the service must report active, otherwise the command fails and WHPanel counts it as a failed attempt.
"""
import time

from .. import catalog
from .system import SystemError_
from .website import HandlerError

TEST_FIRST = {"httpd": ["httpd", "-t"], "sshd": ["sshd", "-t"]}


class ServiceControl:
    def __init__(self, system, sleep=time.sleep, tries=5):
        self.s, self.sleep, self.tries = system, sleep, tries

    def run(self, command, params):
        try:
            p = catalog.validate(command, params)
        except catalog.InvalidCommand as exc:
            raise HandlerError("invalid", str(exc)) from None
        service = p["service"]
        try:
            if service in TEST_FIRST:
                try:
                    self.s.run(TEST_FIRST[service])
                except SystemError_:
                    raise HandlerError("config_invalid", f"The {service} configuration does not pass its own test, so it was not restarted.") from None
            self.s.run(["systemctl", "restart", service])
        except SystemError_ as exc:
            raise HandlerError("system_error", str(exc)[:300]) from None
        for attempt in range(self.tries):
            status, _ = self.s.probe(["systemctl", "is-active", service])
            if status == 0:
                return "restarted", {"service": service}
            if attempt + 1 < self.tries:
                self.sleep(1)
        raise HandlerError("still_down", f"{service} was restarted but is not running.")
