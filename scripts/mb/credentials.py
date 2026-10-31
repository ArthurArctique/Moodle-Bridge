"""Jeton d'API Moodle : rangement dans le coffre du système et première connexion.

Le jeton est rangé dans le Trousseau (macOS), le Gestionnaire d'identification (Windows) ou le trousseau de
la session (Linux, `secret-tool`, à défaut un fichier lisible par l'utilisateur seul). Il n'est jamais affiché
ni écrit ailleurs ; le mot de passe Moodle, lui, n'est jamais conservé.

Trois façons de l'obtenir, selon la connexion du site :
- identifiant et mot de passe Moodle (`login/token.php`), pour les comptes propres au Moodle ;
- connexion de l'appli mobile dans le navigateur (`admin/tool/mobile/launch.php`), pour les sites à
  authentification unique (CAS, Shibboleth, SAML…) ;
- jeton copié depuis la page « Clés de sécurité » du profil, quand le site l'affiche.
"""

import base64
import getpass
import hashlib
import json
import os
import re
import secrets
import subprocess
import sys
import urllib.parse
import urllib.request

from . import system

TOKEN_RE = re.compile(r"[0-9a-f]{32}")
ENV_VAR = "MOODLE_BRIDGE_TOKEN"  # prioritaire : utile pour un test ou un serveur sans coffre


class TokenMissing(Exception):
    pass


class LoginError(Exception):
    pass


# ---------------------------------------------------------------- coffre du système

def _windows_api():
    import ctypes
    from ctypes import wintypes

    class CREDENTIAL(ctypes.Structure):
        _fields_ = [("Flags", wintypes.DWORD), ("Type", wintypes.DWORD), ("TargetName", wintypes.LPWSTR),
                    ("Comment", wintypes.LPWSTR), ("LastWritten", wintypes.FILETIME),
                    ("CredentialBlobSize", wintypes.DWORD), ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
                    ("Persist", wintypes.DWORD), ("AttributeCount", wintypes.DWORD),
                    ("Attributes", ctypes.c_void_p), ("TargetAlias", wintypes.LPWSTR),
                    ("UserName", wintypes.LPWSTR)]

    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    advapi32.CredReadW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                   ctypes.POINTER(ctypes.POINTER(CREDENTIAL))]
    advapi32.CredReadW.restype = wintypes.BOOL
    advapi32.CredWriteW.argtypes = [ctypes.POINTER(CREDENTIAL), wintypes.DWORD]
    advapi32.CredWriteW.restype = wintypes.BOOL
    advapi32.CredDeleteW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
    advapi32.CredDeleteW.restype = wintypes.BOOL
    advapi32.CredFree.argtypes = [ctypes.c_void_p]
    return ctypes, CREDENTIAL, advapi32


CRED_TYPE_GENERIC = 1
CRED_PERSIST_LOCAL_MACHINE = 2


def _windows_read(service):
    ctypes, CREDENTIAL, advapi32 = _windows_api()
    pointer = ctypes.POINTER(CREDENTIAL)()
    if not advapi32.CredReadW(service, CRED_TYPE_GENERIC, 0, ctypes.byref(pointer)):
        return None
    try:
        cred = pointer.contents
        return ctypes.string_at(cred.CredentialBlob, cred.CredentialBlobSize).decode("utf-8")
    finally:
        advapi32.CredFree(pointer)


def _windows_store(service, token):
    ctypes, CREDENTIAL, advapi32 = _windows_api()
    blob = token.encode("utf-8")
    buffer = (ctypes.c_ubyte * len(blob)).from_buffer_copy(blob)
    cred = CREDENTIAL(Type=CRED_TYPE_GENERIC, TargetName=service, CredentialBlobSize=len(blob),
                      CredentialBlob=ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)),
                      Persist=CRED_PERSIST_LOCAL_MACHINE, UserName=getpass.getuser())
    if not advapi32.CredWriteW(ctypes.byref(cred), 0):
        raise OSError(f"Gestionnaire d'identification : erreur {ctypes.get_last_error()}")


def _windows_delete(service):
    _ctypes, _cred, advapi32 = _windows_api()
    return bool(advapi32.CredDeleteW(service, CRED_TYPE_GENERIC, 0))


def _token_file(service):
    return system.data_root() / "tokens" / re.sub(r"[^A-Za-z0-9._-]+", "_", service)


def _linux_secret_tool():
    return system.find_command("secret-tool")


def where(service):
    """Où le jeton est rangé, pour l'expliquer à l'utilisateur."""
    if system.IS_MAC:
        return f"Trousseau macOS (service « {service} »)"
    if system.IS_WINDOWS:
        return f"Gestionnaire d'identification Windows (« {service} »)"
    if _linux_secret_tool():
        return f"trousseau de la session (secret-tool, service « {service} »)"
    return f"fichier {_token_file(service)} (lisible par toi seul)"


def read_token(service):
    token = os.environ.get(ENV_VAR, "").strip()
    if not token:
        token = _read_stored(service)
    if not token:
        raise TokenMissing(f"aucun jeton enregistré ({where(service)}) : lancer la commande `token`")
    return token


def _read_stored(service):
    if system.IS_MAC:
        result = system.run_quiet(["security", "find-generic-password", "-s", service, "-w"])
        return result.stdout.strip() if result and result.returncode == 0 else None
    if system.IS_WINDOWS:
        try:
            return (_windows_read(service) or "").strip() or None
        except (OSError, AttributeError, ValueError):
            return None
    tool = _linux_secret_tool()
    if tool:
        result = system.run_quiet([tool, "lookup", "service", service])
        if result and result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    try:
        return _token_file(service).read_text("utf-8").strip() or None
    except OSError:
        return None


def store_token(service, token):
    if system.IS_MAC:
        subprocess.run(["security", "add-generic-password", "-U", "-a", getpass.getuser(), "-s", service,
                        "-w", token], check=True, capture_output=True)
        return
    if system.IS_WINDOWS:
        _windows_store(service, token)
        return
    tool = _linux_secret_tool()
    if tool:
        result = system.run_quiet([tool, "store", "--label", f"Moodle Bridge ({service})", "service", service],
                                  input=token)
        if result and result.returncode == 0:
            return
    path = _token_file(service)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(token)


def delete_token(service):
    """Oublie le jeton (la révocation, elle, se fait sur Moodle, page « Clés de sécurité »)."""
    if system.IS_MAC:
        result = system.run_quiet(["security", "delete-generic-password", "-s", service])
        return bool(result and result.returncode == 0)
    if system.IS_WINDOWS:
        try:
            return _windows_delete(service)
        except (OSError, AttributeError):
            return False
    done = False
    tool = _linux_secret_tool()
    if tool:
        result = system.run_quiet([tool, "clear", "service", service])
        done = bool(result and result.returncode == 0)
    path = _token_file(service)
    if path.exists():
        path.unlink()
        done = True
    return done


# ---------------------------------------------------------------- identifiant et mot de passe

def login_with_password(site, username, password, timeout=30):
    """Jeton du service de l'appli mobile à partir de l'identifiant et du mot de passe Moodle."""
    from .api import USER_AGENT, ssl_context
    body = urllib.parse.urlencode({"username": username, "password": password,
                                   "service": "moodle_mobile_app"}).encode()
    request = urllib.request.Request(f"{site.rstrip('/')}/login/token.php", data=body,
                                     headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout, context=ssl_context()) as response:
        answer = json.load(response)
    token = answer.get("token") if isinstance(answer, dict) else None
    if token and TOKEN_RE.fullmatch(token):
        return token
    code = (answer or {}).get("errorcode") or "error"
    hints = {
        "invalidlogin": "identifiant ou mot de passe refusé. Si ton établissement se connecte par une page "
                        "d'authentification unique (CAS, Shibboleth…), ton mot de passe ne passe pas par Moodle : "
                        "utilise la connexion par le navigateur.",
        "enablewsdescription": "l'API de l'appli mobile est désactivée sur ce site.",
        "sitemaintenance": "le site est en maintenance, réessaie plus tard.",
        "usernotfullysetup": "ton profil Moodle est incomplet : connecte-toi une fois sur le site web.",
    }
    raise LoginError(hints.get(code, f"connexion refusée ({code}) : {(answer or {}).get('error', '')}"))


def obtain_with_password(site, service, ask=input, ask_secret=getpass.getpass):
    print("Connexion avec ton identifiant Moodle. Le mot de passe sert une seule fois à obtenir le jeton :")
    print("il n'est ni affiché, ni enregistré.\n")
    username = ask("Identifiant Moodle : ").strip()
    password = ask_secret("Mot de passe (rien ne s'affiche, c'est normal) : ")
    if not username or not password:
        print("Abandon : rien n'a été enregistré.")
        return False
    try:
        token = login_with_password(site, username, password)
    except (LoginError, OSError, ValueError) as exc:  # OSError : site injoignable
        print(f"→ {exc}")
        return False
    store_token(service, token)
    print(f"Jeton enregistré : {where(service)}.")
    return True


# ---------------------------------------------------------------- connexion par le navigateur (SSO)

def launch_url(site, passport):
    return (f"{site.rstrip('/')}/admin/tool/mobile/launch.php"
            f"?service=moodle_mobile_app&passport={passport}&urlscheme=moodlemobile")


def parse_launch_payload(text):
    """Extrait (signature, jeton) d'une adresse moodlemobile://token=… ou de sa seule partie base64.

    Renvoie (None, None, problème) si le texte n'est pas exploitable.
    """
    text = (text or "").strip()
    if "launch.php" in text:
        return None, None, ("C'est l'adresse launch.php donnée par le script. Il faut celle de la "
                            "redirection, dans l'en-tête « location » de l'onglet Réseau.")
    match = re.search(r"token=([A-Za-z0-9+/=%_-]+)", text)
    payload = match.group(1) if match else text
    if not re.fullmatch(r"[A-Za-z0-9+/=%_-]{40,}", payload):
        return None, None, "Ce n'est pas une adresse « moodlemobile://token=… »."
    payload = urllib.parse.unquote(payload)
    try:
        decoded = base64.b64decode(payload + "=" * (-len(payload) % 4)).decode()
    except ValueError:
        return None, None, "Contenu illisible : vérifie que toute l'adresse a été copiée."
    parts = decoded.split(":::")
    if len(parts) < 2 or not TOKEN_RE.fullmatch(parts[1]):
        return None, None, "Format inattendu : vérifie que toute l'adresse a été copiée."
    return parts[0], parts[1], None


def expected_signature(site, passport):
    # launch.php signe la réponse avec md5(wwwroot + passport)
    return hashlib.md5((site.rstrip("/") + passport).encode()).hexdigest()


def devtools_shortcut():
    return "Cmd+Option+I" if system.IS_MAC else "F12 (ou Ctrl+Maj+I)"


def _paste(prompt, ask_secret):
    """Texte collé (masqué) ou, sur Entrée seule, contenu du presse-papiers."""
    text = ask_secret(prompt).strip()
    if text:
        return text
    clip = system.read_clipboard()
    if clip is None:
        print("  → Presse-papiers illisible ici : colle l'adresse directement.")
    return clip or ""


def obtain_with_browser(site, service, ask=input, ask_secret=getpass.getpass):
    """Guide l'utilisateur dans son terminal ; à lancer par lui, jamais en arrière-plan."""
    passport = secrets.token_hex(8)
    print("Connexion par le navigateur, comme l'appli mobile Moodle (Chrome, Edge, Brave ou Firefox) :\n")
    print("1. Connecte-toi à Moodle dans ton navigateur, comme d'habitude.")
    print(f"2. Ouvre les outils de développement ({devtools_shortcut()}), onglet « Réseau » (Network),")
    print("   et coche « Conserver le journal » (Preserve log ; Firefox : « Conserver les journaux »).")
    print("3. Dans ce même onglet, ouvre cette adresse :\n")
    print(f"   {launch_url(site, passport)}\n")
    print("   La page semble ne rien faire : c'est normal, le jeton part vers l'appli mobile.")
    print("   Si le navigateur propose d'ouvrir l'appli Moodle, refuse.")
    print("4. Dans l'onglet Réseau, tape « token » dans le filtre. Clic droit sur la ligne")
    print("   moodlemobile://token=… > Copier > Copier l'URL. À défaut : ligne « launch.php »,")
    print("   En-têtes > En-têtes de réponse > valeur de « location ».\n")
    while True:
        try:
            text = _paste("Colle l'adresse puis Entrée (rien ne s'affiche), ou Entrée seule pour lire le "
                          "presse-papiers ; Ctrl+C pour abandonner : ", ask_secret)
        except (KeyboardInterrupt, EOFError):
            print("\nAbandon : rien n'a été enregistré.")
            return False
        signature, token, problem = parse_launch_payload(text)
        if not problem:
            break
        print(f"  → {problem}\n")
    if signature != expected_signature(site, passport):
        answer = ask("Signature inattendue (adresse issue d'une autre tentative ?). Enregistrer quand même ? [o/N] ")
        if answer.strip().lower() not in ("o", "oui", "y", "yes"):
            print("Rien n'a été enregistré. Relance la commande.")
            return False
    store_token(service, token)
    system.clear_clipboard()
    print(f"Jeton enregistré : {where(service)}. Presse-papiers vidé.")
    return True


def obtain_by_paste(service, ask_secret=getpass.getpass):
    print("Sur Moodle : profil > Préférences > Clés de sécurité, ligne « Moodle mobile web service ».")
    print("Copie la clé (32 caractères).\n")
    token = ask_secret("Colle la clé puis Entrée (rien ne s'affiche) : ").strip().lower()
    if not TOKEN_RE.fullmatch(token):
        print("→ Ce n'est pas une clé de 32 caractères hexadécimaux. Rien n'a été enregistré.")
        return False
    store_token(service, token)
    print(f"Jeton enregistré : {where(service)}.")
    return True


if __name__ == "__main__":
    sys.exit("Utiliser : moodle_bridge.py token --config …")
