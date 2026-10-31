"""Client minimal de l'API REST de Moodle, celle qu'utilise l'appli mobile officielle.

Le jeton passe toujours dans le corps des requêtes POST, jamais dans une URL.
"""

import json
import os
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from . import __version__

USER_AGENT = f"moodle-bridge/{__version__}"
# Le Python de python.org n'a pas toujours de certificats racines : utiliser ceux de macOS
MACOS_CA_BUNDLE = Path("/etc/ssl/cert.pem")
TOKEN_ERRORCODES = {"invalidtoken", "invalidtimedtoken", "invalidiptoken"}


class MoodleError(Exception):
    def __init__(self, errorcode, message):
        super().__init__(f"{errorcode} : {message}")
        self.errorcode = errorcode
        self.message = message


class TokenError(MoodleError):
    """Jeton absent, expiré ou révoqué : seul un nouveau jeton règle le problème."""


def ssl_context():
    cafile = str(MACOS_CA_BUNDLE) if MACOS_CA_BUNDLE.exists() else None
    return ssl.create_default_context(cafile=cafile)


def flatten(params, prefix=""):
    """Encode les listes et dictionnaires au format attendu par Moodle (courseids[0]=…)."""
    pairs = []
    for key, value in params.items():
        name = f"{prefix}[{key}]" if prefix else str(key)
        if isinstance(value, dict):
            pairs += flatten(value, name)
        elif isinstance(value, (list, tuple)):
            for index, element in enumerate(value):
                if isinstance(element, dict):
                    pairs += flatten(element, f"{name}[{index}]")
                else:
                    pairs.append((f"{name}[{index}]", str(element)))
        elif value is not None:
            pairs.append((name, str(value)))
    return pairs


def public_config(site, timeout=30):
    """Configuration publique destinée à l'appli mobile ; ne demande aucun identifiant."""
    url = f"{site.rstrip('/')}/lib/ajax/service-nologin.php?info=tool_mobile_get_public_config"
    body = json.dumps([{"index": 0, "methodname": "tool_mobile_get_public_config", "args": {}}]).encode()
    request = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json", "User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout, context=ssl_context()) as response:
        answer = json.load(response)
    first = answer[0] if isinstance(answer, list) and answer else {}
    if first.get("error"):
        raise MoodleError(first.get("exception", {}).get("errorcode", "error"),
                          first.get("exception", {}).get("message", "réponse en erreur"))
    return first.get("data", {})


class Client:
    def __init__(self, site, token, timeout=60, retries=3, backoff=(10, 30, 90), sleep=time.sleep):
        self.site = site.rstrip("/")
        self.token = token
        self.timeout = timeout
        self.retries = retries
        self.backoff = backoff
        self.sleep = sleep
        self.context = ssl_context()

    def _open(self, request):
        """Ouvre la requête en réessayant sur les erreurs réseau et 5xx (réveil du Mac, Wi-Fi lent…)."""
        for attempt in range(self.retries + 1):
            try:
                return urllib.request.urlopen(request, timeout=self.timeout, context=self.context)
            except urllib.error.HTTPError as exc:
                if exc.code < 500 or attempt == self.retries:
                    raise
            except (urllib.error.URLError, TimeoutError, ConnectionError):
                if attempt == self.retries:
                    raise
            self.sleep(self.backoff[min(attempt, len(self.backoff) - 1)])

    def _post(self, url, pairs):
        body = urllib.parse.urlencode(pairs).encode()
        return urllib.request.Request(url, data=body, headers={"User-Agent": USER_AGENT})

    def call(self, function, **params):
        pairs = [("wstoken", self.token), ("wsfunction", function),
                 ("moodlewsrestformat", "json")] + flatten(params)
        with self._open(self._post(f"{self.site}/webservice/rest/server.php", pairs)) as response:
            result = json.load(response)
        # Moodle signale ses erreurs en HTTP 200 avec un objet « exception »
        if isinstance(result, dict) and "exception" in result:
            code = result.get("errorcode", "")
            error = TokenError if code in TOKEN_ERRORCODES else MoodleError
            raise error(code, result.get("message", ""))
        return result

    def download(self, fileurl, dest, expected_size=None):
        """Télécharge un fichier via dest + « .part » puis renommage ; vérifie la taille annoncée."""
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        part = dest.with_name(dest.name + ".part")
        with self._open(self._post(fileurl, [("token", self.token)])) as response:
            content_type = response.headers.get("Content-Type", "")
            with open(part, "wb") as handle:
                while True:
                    block = response.read(1 << 20)
                    if not block:
                        break
                    handle.write(block)
        size = part.stat().st_size
        # Une erreur Moodle arrive sous forme de petit JSON à la place du fichier
        if "application/json" in content_type and size < 20_000 and not fileurl.endswith(".json"):
            try:
                answer = json.loads(part.read_text("utf-8"))
            except (ValueError, UnicodeDecodeError):
                answer = None
            if isinstance(answer, dict) and ("error" in answer or "exception" in answer):
                part.unlink()
                code = answer.get("errorcode", "downloaderror")
                error = TokenError if code in TOKEN_ERRORCODES else MoodleError
                raise error(code, answer.get("error") or answer.get("message", "téléchargement refusé"))
        if expected_size and size != expected_size:
            part.unlink()
            raise MoodleError("sizemismatch", f"{size} octets reçus au lieu de {expected_size}")
        os.replace(part, dest)
        return dest
