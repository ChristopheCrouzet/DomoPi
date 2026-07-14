"""Journal applicatif DomoPi.

Niveaux de verbosité (réglage `journal_level`) :
  verbose : tout est tracé, y compris chaque changement de capteur/actionneur (DEBUG+)
  medium  : actions principales + warnings + erreurs (INFO+)
  errors  : uniquement absences de réponse des périphériques et erreurs (ERROR)
"""
import time
from . import db

_ORDER = {"DEBUG": 0, "INFO": 1, "WARNING": 2, "ERROR": 3}
_LEVEL_MIN = {"verbose": 0, "medium": 1, "errors": 3}


def log(level: str, source: str, message: str):
    level = level.upper()
    setting = db.get_setting("journal_level", "medium")
    if _ORDER.get(level, 1) < _LEVEL_MIN.get(setting, 1):
        return
    conn = db.get_conn()
    conn.execute("INSERT INTO journal(ts,level,source,message) VALUES(?,?,?,?)",
                 (int(time.time()), level, source, message))
    conn.commit()


def debug(source, msg):   log("DEBUG", source, msg)
def info(source, msg):    log("INFO", source, msg)
def warning(source, msg): log("WARNING", source, msg)
def error(source, msg):   log("ERROR", source, msg)
