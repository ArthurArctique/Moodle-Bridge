"""Tâches planifiées, rattrapées après une veille ou un arrêt : launchd (macOS), Planificateur de tâches
(Windows) ou cron (Linux).

Deux tâches possibles par environnement : la synchronisation des documents (`sync`, toutes les 2 h par défaut)
et, avec l'application macOS, le relevé des notes (`notes`, toutes les 15 min).
"""

import csv
import os
import plistlib
import re
import shlex
import subprocess
import sys
import time
from pathlib import Path
from xml.sax.saxutils import escape

from . import system

LAUNCH_AGENTS = Path.home() / "Library" / "LaunchAgents"
CRON_MARK = "# moodle-bridge:"


# Commande lancée par chaque tâche (après le script) et intervalle par défaut
JOBS = {
    "sync": (["sync"], lambda cfg: cfg.interval_minutes),
    "notes": (["notes", "check"], lambda cfg: int(cfg.data["notes"].get("interval_minutes") or 15)),
}


def _suffix(job):
    return "" if job == "sync" else f".{job}"


def label(cfg, job="sync"):
    return f"local.moodle-bridge.{cfg.slug}{_suffix(job)}"


def task_name(cfg, job="sync"):
    """Nom de la tâche dans le Planificateur de tâches de Windows."""
    return f"MoodleBridge\\{cfg.slug}{_suffix(job)}"


def plist_path(cfg, job="sync"):
    return LAUNCH_AGENTS / f"{label(cfg, job)}.plist"


def log_path(cfg, job="sync"):
    return system.log_root() / f"{cfg.slug}{_suffix(job)}.log"


def entry_script():
    return Path(__file__).resolve().parents[1] / "moodle_bridge.py"


def command(cfg, job="sync"):
    return [str(system.background_python()), "-I", str(entry_script()),
            *JOBS[job][0], "--config", str(cfg.path), "--quiet", "--scheduled"]


def install(cfg, interval_minutes=None, job="sync"):
    interval = max(1, int(interval_minutes or JOBS[job][1](cfg)))
    log_path(cfg, job).parent.mkdir(parents=True, exist_ok=True)
    backend = _mac_install if system.IS_MAC else _windows_install if system.IS_WINDOWS else _cron_install
    return backend(cfg, interval, job), interval


def uninstall(cfg, job="sync"):
    if system.IS_MAC:
        return _mac_uninstall(cfg, job)
    if system.IS_WINDOWS:
        return _windows_uninstall(cfg, job)
    return _cron_uninstall(cfg, job)


def kick(cfg, job="sync"):
    """Passage immédiat, lancé comme le serait la tâche planifiée."""
    if system.IS_MAC:
        _check(_launchctl("kickstart", f"{_domain()}/{label(cfg, job)}"), "launchctl kickstart")
    elif system.IS_WINDOWS:
        _check(_schtasks("/Run", "/TN", task_name(cfg, job)), "schtasks /Run")
    else:
        with open(log_path(cfg, job), "a", encoding="utf-8") as out:
            subprocess.Popen(command(cfg, job), stdout=out, stderr=out, stdin=subprocess.DEVNULL,
                             start_new_session=True)


def installed(cfg, job="sync"):
    if system.IS_MAC:
        return plist_path(cfg, job).exists()
    if system.IS_WINDOWS:
        result = _schtasks("/Query", "/TN", task_name(cfg, job))
        return bool(result and result.returncode == 0)
    return bool(_cron_lines(cfg, job))


def status(cfg, job="sync"):
    if system.IS_MAC:
        return _mac_status(cfg, job)
    if system.IS_WINDOWS:
        return _windows_status(cfg, job)
    return _cron_status(cfg, job)


def _check(result, what):
    if result is None or result.returncode != 0:
        detail = (result.stderr or result.stdout).strip() if result else "commande introuvable"
        raise RuntimeError(f"{what} a échoué : {detail}")


# ---------------------------------------------------------------- macOS : launchd

def _launchctl(*args):
    return system.run_quiet(["launchctl", *args])


def _domain():
    return f"gui/{os.getuid()}"


def build_plist(cfg, interval_minutes, job="sync"):
    return {
        "Label": label(cfg, job),
        "ProgramArguments": command(cfg, job),
        # Toutes les N minutes ; un passage manqué pendant la veille est lancé au réveil
        "StartInterval": int(interval_minutes) * 60,
        # Rattrapage à l'ouverture de session si le Mac était éteint
        "RunAtLoad": True,
        "ProcessType": "Background",
        "LowPriorityIO": True,
        "StandardOutPath": str(log_path(cfg, job)),
        "StandardErrorPath": str(log_path(cfg, job)),
    }


def _mac_install(cfg, interval, job):
    LAUNCH_AGENTS.mkdir(parents=True, exist_ok=True)
    path = plist_path(cfg, job)
    if path.exists():
        _launchctl("bootout", f"{_domain()}/{label(cfg, job)}")
    with open(path, "wb") as handle:
        plistlib.dump(build_plist(cfg, interval, job), handle)
    _check(_launchctl("bootstrap", _domain(), str(path)), "launchctl bootstrap")
    return path


def _mac_uninstall(cfg, job):
    path = plist_path(cfg, job)
    _launchctl("bootout", f"{_domain()}/{label(cfg, job)}")
    if path.exists():
        path.unlink()
        return True
    return False


def _mac_status(cfg, job):
    path = plist_path(cfg, job)
    info = {"backend": "launchd", "label": label(cfg, job), "plist": str(path), "installed": path.exists(),
            "loaded": False, "interval_minutes": None, "last_exit": None, "log": str(log_path(cfg, job))}
    if path.exists():
        with open(path, "rb") as handle:
            info["interval_minutes"] = plistlib.load(handle).get("StartInterval", 0) // 60
    result = _launchctl("print", f"{_domain()}/{label(cfg, job)}")
    if result and result.returncode == 0:
        info["loaded"] = True
        for line in result.stdout.splitlines():
            line = line.strip()
            if line.startswith("last exit code ="):
                info["last_exit"] = line.split("=", 1)[1].strip()
            elif line.startswith("state ="):
                info["state"] = line.split("=", 1)[1].strip()
    return info


# ---------------------------------------------------------------- Windows : Planificateur de tâches

def _schtasks(*args):
    return system.run_quiet(["schtasks", *args], timeout=30)


def build_task_xml(cfg, interval_minutes, job="sync", now=None):
    """Définition de la tâche : toutes les N minutes et à l'ouverture de session, rattrapée si elle a été manquée."""
    program, *arguments = command(cfg, job)
    start = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(now or time.time()))
    user, domain = os.environ.get("USERNAME", ""), os.environ.get("USERDOMAIN", "")
    account = domain + "\\" + user if domain and user else user
    user_id = f"<UserId>{escape(account)}</UserId>" if account else ""
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo><Description>Moodle Bridge ({escape(job)}) : {escape(str(cfg.path))}</Description></RegistrationInfo>
  <Triggers>
    <LogonTrigger><Enabled>true</Enabled>{user_id}</LogonTrigger>
    <TimeTrigger>
      <Repetition><Interval>PT{int(interval_minutes)}M</Interval><StopAtDurationEnd>false</StopAtDurationEnd></Repetition>
      <StartBoundary>{start}</StartBoundary>
      <Enabled>true</Enabled>
    </TimeTrigger>
  </Triggers>
  <Principals><Principal id="Author"><LogonType>InteractiveToken</LogonType><RunLevel>LeastPrivilege</RunLevel></Principal></Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <StartWhenAvailable>true</StartWhenAvailable>
    <ExecutionTimeLimit>PT1H</ExecutionTimeLimit>
    <Enabled>true</Enabled>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{escape(program)}</Command>
      <Arguments>{escape(subprocess.list2cmdline(arguments))}</Arguments>
    </Exec>
  </Actions>
</Task>
"""


def _windows_install(cfg, interval, job):
    definition = cfg.dir / f"task{_suffix(job)}.xml"
    definition.write_text(build_task_xml(cfg, interval, job), encoding="utf-16")
    try:
        _check(_schtasks("/Create", "/TN", task_name(cfg, job), "/XML", str(definition), "/F"), "schtasks /Create")
    finally:
        definition.unlink(missing_ok=True)
    return task_name(cfg, job)


def _windows_uninstall(cfg, job):
    result = _schtasks("/Delete", "/TN", task_name(cfg, job), "/F")
    return bool(result and result.returncode == 0)


def _windows_status(cfg, job):
    info = {"backend": "schtasks", "label": task_name(cfg, job), "installed": False, "loaded": False,
            "interval_minutes": None, "last_exit": None, "log": str(cfg.log_path)}
    result = _schtasks("/Query", "/TN", task_name(cfg, job), "/XML")
    if not result or result.returncode != 0:
        return info
    info["installed"] = info["loaded"] = True
    match = re.search(r"<Interval>PT(?:(\d+)H)?(?:(\d+)M)?</Interval>", result.stdout)
    if match:
        info["interval_minutes"] = int(match.group(1) or 0) * 60 + int(match.group(2) or 0)
    # Colonnes de /V /FO CSV (libellés traduits, ordre fixe) : 2 prochaine exécution, 3 état, 6 dernier résultat
    verbose = _schtasks("/Query", "/TN", task_name(cfg, job), "/V", "/FO", "CSV", "/NH")
    if verbose and verbose.returncode == 0:
        rows = [r for r in csv.reader(verbose.stdout.splitlines()) if len(r) > 6]
        if rows:
            info["next_run"], info["state"], info["last_exit"] = rows[0][2], rows[0][3], rows[0][6]
    return info


# ---------------------------------------------------------------- Linux : cron

def cron_schedule(interval):
    """Expression cron la plus proche de l'intervalle demandé (en minutes)."""
    if interval < 60:
        return f"*/{interval} * * * *"
    hours = max(1, min(23, round(interval / 60)))
    return f"0 */{hours} * * *"


def _crontab():
    result = system.run_quiet(["crontab", "-l"])
    return result.stdout.splitlines() if result and result.returncode == 0 else []


def _cron_lines(cfg, job):
    mark = f"{CRON_MARK}{label(cfg, job)}"
    return [line for line in _crontab() if line.endswith(mark)]


def _write_crontab(lines):
    _check(system.run_quiet(["crontab", "-"], input="\n".join(lines) + "\n"), "crontab")


def _cron_install(cfg, interval, job):
    mark = f"{CRON_MARK}{label(cfg, job)}"
    # Guillemets du shell (cron lance /bin/sh), pas ceux de Windows
    run = f"{shlex.join(str(a) for a in command(cfg, job))} >> {shlex.quote(str(log_path(cfg, job)))} 2>&1"
    lines = [line for line in _crontab() if not line.endswith(mark)]
    # @reboot rattrape un passage manqué pendant l'arrêt de la machine
    lines += [f"{cron_schedule(interval)} {run} {mark}", f"@reboot sleep 120 && {run} {mark}"]
    _write_crontab(lines)
    return "crontab"


def _cron_uninstall(cfg, job):
    mark = f"{CRON_MARK}{label(cfg, job)}"
    lines = _crontab()
    kept = [line for line in lines if not line.endswith(mark)]
    if len(kept) == len(lines):
        return False
    _write_crontab(kept)
    return True


def _cron_status(cfg, job):
    lines = _cron_lines(cfg, job)
    info = {"backend": "cron", "label": label(cfg, job), "installed": bool(lines), "loaded": bool(lines),
            "interval_minutes": None, "last_exit": None, "log": str(log_path(cfg, job))}
    for line in lines:
        minutes = re.match(r"\*/(\d+) ", line)
        hours = re.match(r"0 \*/(\d+) ", line)
        if minutes or hours:
            info["interval_minutes"] = int(minutes.group(1)) if minutes else int(hours.group(1)) * 60
    return info


if __name__ == "__main__":
    sys.exit("Utiliser : moodle_bridge.py schedule … --config …")
