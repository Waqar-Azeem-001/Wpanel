"""
FTP accounts on a cPanel server, through UAPI ``Ftp``. The account's own login, the log-access login and the anonymous accounts are never listed or
changed: only the logins the customer made. Each is limited to a folder inside the account's home folder.
"""
from .base import HostingError
from .capabilities import FtpAccount, FtpCapability, FtpOptions
from .cpanel_api import CpanelApi, plan_limit
from .ftp_rules import FTP_NAME, check_ftp_password
from .sandbox import clean_relative


class CpanelFtp(CpanelApi, FtpCapability):
    def _main_domain(self, user):
        return self._account_domains(user)[0]

    def _folder_exists(self, user, rel):
        parent, _, leaf = rel.rpartition("/")
        listing = self._uapi(user, "Fileman", "list_files", {"dir": f"{self._home(user)}/{parent}".rstrip("/")}) or []
        return any(str(i.get("file")) == leaf and str(i.get("type", "")).lower() in ("dir", "directory") for i in listing if isinstance(i, dict))

    def ftp_options(self, username):
        return FtpOptions(host=self.host, port=21, secure_port=0, max_accounts=plan_limit(self._summary(username).get("maxftp")), root="")  # None: no limit

    def list_ftp_accounts(self, username):
        found = []
        for item in self._uapi(username, "Ftp", "list_ftp") or []:
            if not isinstance(item, dict) or item.get("type") != "sub" or not item.get("user"):
                continue  # the account's own login, the log login and the anonymous ones are not the customer's to manage here
            login = str(item["user"])
            found.append(FtpAccount(login=login if "@" in login else f"{login}@{self._main_domain(username)}",
                                    directory=self._relative(username, item.get("homedir")), created_at=None))
        return found

    def create_ftp_account(self, username, name, password, directory):
        name = (name or "").strip().lower()
        if not FTP_NAME.match(name):
            raise HostingError("The login name uses 2 to 32 lowercase letters, numbers, dots, dashes or underscores.")
        check_ftp_password(password)
        folder = clean_relative((directory or "").strip().strip("/"))
        if folder and not self._folder_exists(username, folder):
            raise HostingError("That folder does not exist in your hosting space. Create it in the file manager first.")
        domain = self._main_domain(username)
        self._uapi(username, "Ftp", "add_ftp", {"user": name, "pass": password, "homedir": folder or ".", "quota": 0, "domain": domain,
                                                "disallowdot": 1}, secret=True)
        return f"{name}@{domain}"

    def _owned_login(self, username, login):
        login = (login or "").strip().lower()
        if login not in [a.login.lower() for a in self.list_ftp_accounts(username)]:
            raise HostingError("That FTP account no longer exists.")
        return login

    def set_ftp_password(self, username, login, password):
        login = self._owned_login(username, login)
        check_ftp_password(password)
        self._uapi(username, "Ftp", "passwd", {"user": login.split("@")[0], "pass": password, "domain": login.split("@")[1]}, secret=True)

    def delete_ftp_account(self, username, login):
        login = self._owned_login(username, login)
        self._uapi(username, "Ftp", "delete_ftp", {"user": login.split("@")[0], "domain": login.split("@")[1], "destroy": 0})
