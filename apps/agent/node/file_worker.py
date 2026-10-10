"""The child process started by ``run_as_account``: it has already dropped to the account's uid/gid, reads ONE request from standard input and prints the answer as JSON. UNTESTED on a node."""
import json
import sys

from .files import Files
from .website import HandlerError


def main(stdin=None, stdout=None):
    request = json.loads((stdin or sys.stdin).read())
    files = Files(lambda account: f"/home/{account}/sites")
    try:
        code, data = files.run(request["command"], {**request["params"], "account": request["account"]})
        reply = {"code": code, "data": data}
    except HandlerError as exc:
        reply = {"error": {"code": exc.code, "message": exc.message}}
    (stdout or sys.stdout).write(json.dumps(reply))


if __name__ == "__main__":
    main()
