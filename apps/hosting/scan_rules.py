"""
What a malware scan looks for, in one place so the demo provider's scanner and the one-file scanner WHPanel runs on a customer's server (``care_scanner``) judge files the same way.

It is a pattern check, not proof: it reports and never deletes. Patterns avoid the character ``~`` (the PHP scanner uses it as its delimiter).
"""
import re

MAX_SCAN_FILES = 5000
MAX_SCAN_BYTES = 1_000_000
WEB_EXTENSIONS = (".php", ".phtml", ".php5", ".js", ".html", ".htm", ".htaccess")
SHELL_NAMES = ("c99.php", "r57.php", "wso.php", "b374k.php", "shell.php", "webshell.php", "alfa.php", "indoxploit.php")
UPLOAD_DIRS = ("uploads", "upload", "images", "img", "files", "cache", "tmp")

# rule, severity, regular expression (case-insensitive), what it means
RULES = [
    ("obfuscated-code", "high", r"eval\s*\(\s*(base64_decode|gzinflate|str_rot13|gzuncompress)\s*\(",
     "Code hidden with an encoding trick and then run. Legitimate software rarely does this."),
    ("runs-visitor-input", "high", r"\b(system|shell_exec|passthru|exec|popen|proc_open|assert|eval)\s*\(\s*\$_(GET|POST|REQUEST|COOKIE)",
     "Runs what a visitor sends it, which lets anyone take control of the website."),
    ("hidden-script", "medium", r"document\.write\s*\(\s*unescape\s*\(",
     "A script that writes hidden content into the page. It is often used to inject spam or redirects."),
]
NAME_RULES = {
    "known-shell-name": ("high", "This file has the name of a well-known hacker tool."),
    "double-extension": ("medium", "A script disguised as a picture or text file."),
    "script-in-uploads": ("medium", "A PHP file in a folder meant for uploads. Uploads should never be programs."),
    "hidden-script-file": ("medium", "A hidden PHP file."),
    "core-file-modified": ("high", "A WordPress system file that differs from the official release. It may have been tampered with."),
    "unknown-file-in-core": ("medium", "A PHP file in WordPress's own folders that is not part of the official release."),
}
SEVERITY_ORDER = {"high": 0, "medium": 1}
PATTERNS = [(rule, severity, re.compile(pattern.encode(), re.I), detail) for rule, severity, pattern, detail in RULES]
