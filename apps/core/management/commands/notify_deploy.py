"""
Tell the team that a new version was published (or that its tests failed). Run by the CI pipeline over SSH after a push, so the message goes out through the email provider that is
configured inside WHPanel itself (Setup > Email), to the alert addresses there, or to the Super Admins when none are set. It also appears as an in-app notification for the
staff who manage system settings.

    python manage.py notify_deploy --tests success --deploy success --sha 4441f28 --message "CI: ..." --run-url <address of the run>
"""
from django.core.management.base import BaseCommand

from apps.notifications import services

RESULT_WORDS = {"success": "passed", "failure": "FAILED", "cancelled": "was cancelled", "skipped": "was not run"}


class Command(BaseCommand):
    help = "Send the team a message about a pipeline run (tests and production deploy)."

    def add_arguments(self, parser):
        parser.add_argument("--tests", default="unknown")
        parser.add_argument("--deploy", default="unknown")
        parser.add_argument("--sha", default="")
        parser.add_argument("--message", default="")
        parser.add_argument("--actor", default="")
        parser.add_argument("--run-url", default="")

    def handle(self, *args, **options):
        tests, deploy = options["tests"], options["deploy"]
        good = tests == "success" and deploy in ("success", "skipped")
        title = ("New version published" if deploy == "success" and tests == "success" else "Pipeline run " + ("finished" if good else "needs attention"))
        title += f": {options['message'][:80]}" if options["message"] else ""
        body = "\n".join(line for line in (
            f"Tests {RESULT_WORDS.get(tests, tests)}.",
            f"Deploy to production {RESULT_WORDS.get(deploy, deploy)}.",
            f"Commit: {options['sha'][:12]}" if options["sha"] else "",
            f"By: {options['actor']}" if options["actor"] else "",
            f"Run: {options['run_url']}" if options["run_url"] else "") if line)
        sent = services.notify_team("system.deploy", "manage_settings", title=title, body=body, link=options["run_url"])
        self.stdout.write(self.style.SUCCESS(f"Team notified ({len(sent)} staff, plus the alert addresses). {title}"))
