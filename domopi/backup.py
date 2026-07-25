"""DomoPi — sauvegarde et restauration des données utilisateur.

Une sauvegarde est une archive `.tar.gz` déposée dans un dossier local du Pi
(réglage `backup_dir`, défaut `<dossier de la base>/backups`) :

    manifest.json   description de l'archive (date, comptages) — écrit **en
                    premier** dans le tar pour être relu sans tout décompresser
    README.txt      rappel de ce qui n'est pas sauvegardé
    domopi.db       instantané cohérent de la base (VACUUM INTO : la base est
                    en WAL, une copie de fichier à chaud serait incorrecte)
    secret.key      clé de signature des cookies de session
    icons/          icônes (intégrées, uploadées, générées par IA)
    backgrounds/    fonds de page

L'archive contient donc les identifiants des box (table `connectors`) et la clé
de session : elle est créée en 0600 et doit être traitée comme un secret.

Ne sont **pas** sauvegardés (hors de portée du service, cf. domopi.service) :
`/etc/domopi/domopi.env` (mot de passe admin initial, ANTHROPIC_API_KEY),
`/etc/domopi/tls/`, les configurations nginx et mosquitto.

Trois modes de restauration, combinables (cf. `run_restore`) :
  - `icons`   : icônes et fonds de page (écrase les fichiers de même nom) ;
  - `history` : mesures des capteurs présents dans les deux bases (fusion,
                les points déjà en base sont conservés) ;
  - `full`    : base de l'archive à l'identique (écrasement), avec l'option
                `keep_history` pour ré-injecter ensuite l'historique accumulé
                depuis la sauvegarde.

Sauvegarde et restauration sont exclusives (un verrou, un seul worker uvicorn)
et s'exécutent hors boucle asyncio (`asyncio.to_thread` côté API, thread du
collecteur pour les sauvegardes automatiques) : leur état est publié par
`job_state()` et interrogé par l'admin pendant l'opération.
"""
from __future__ import annotations

import datetime
import ftplib
import io
import json
import os
import re
import shutil
import socket
import sqlite3
import ssl
import tarfile
import tempfile
import threading
import time

from . import auth, db, formula, journal

# Dossiers statiques servis par l'application (main.py les réutilise).
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC_DIR = os.environ.get("DOMOPI_STATIC", os.path.join(BASE_DIR, "static"))
ICONS_DIR = os.path.join(STATIC_DIR, "icons")
BACKGROUNDS_DIR = os.path.join(STATIC_DIR, "backgrounds")

# Tables recopiées lors d'une restauration complète (ordre des dépendances).
TABLES = ("users", "settings", "connectors", "scales", "devices", "measures",
          "measures_daily", "pages", "widgets", "journal")

# Périodicités des sauvegardes automatiques : code -> (libellé, unité, n)
PERIODS = {
    "1d": ("tous les jours", "d", 1),
    "2d": ("tous les 2 jours", "d", 2),
    "1w": ("toutes les semaines", "d", 7),
    "2w": ("toutes les 2 semaines", "d", 14),
    "1m": ("tous les mois", "m", 1),
    "2m": ("tous les 2 mois", "m", 2),
    "6m": ("tous les 6 mois", "m", 6),
    "1y": ("tous les ans", "m", 12),
}

# Noms générés par create_backup() — seuls candidats à la purge de rétention
# (une archive déposée à la main ou envoyée depuis l'admin n'est jamais purgée).
_GENERATED = re.compile(r"^domopi-backup-\d{8}-\d{6}(-\d+)?\.tar\.gz$")
# Noms d'archives acceptés (liste, téléchargement, restauration, envoi)
_ARCHIVE = re.compile(r"^[A-Za-z0-9._-]+\.tar\.gz$")
# Membres autorisés dans une archive : rien d'autre n'est extrait (aucune
# traversée de chemin possible, on n'utilise jamais tarfile.extract()).
_FLAT_MEMBERS = {"manifest.json", "README.txt", "domopi.db", "secret.key"}
_ASSET_MEMBER = re.compile(r"^(icons|backgrounds)/[A-Za-z0-9._-]+$")
# Noms de fichiers d'icônes/fonds archivés (même jeu que les uploads, main.py)
_SAFE_FILE = re.compile(r"^[A-Za-z0-9._-]+$")

MAX_UPLOAD_MB = 1024


class BackupError(Exception):
    """Erreur fonctionnelle (message affichable tel quel dans l'admin)."""


class Busy(BackupError):
    """Une sauvegarde ou une restauration est déjà en cours."""


# ================================================================ état du job
_lock = threading.Lock()
_job: dict = {"kind": "", "running": False, "phase": "", "pct": 0,
              "started": 0, "finished": 0, "error": "", "message": "",
              "report": None}


def job_state() -> dict:
    return dict(_job)


def _begin(kind: str):
    if not _lock.acquire(blocking=False):
        raise Busy("Une sauvegarde ou une restauration est déjà en cours")
    _job.update(kind=kind, running=True, phase="démarrage", pct=0,
                started=int(time.time()), finished=0, error="", message="",
                report=None)


def _phase(label: str, pct: int):
    _job.update(phase=label, pct=pct)


def _end(error: str = "", message: str = "", report=None):
    _job.update(running=False, pct=100, finished=int(time.time()),
                phase="terminé" if not error else "échec",
                error=error, message=message, report=report)
    _lock.release()


# ================================================================ réglages
def default_backup_dir() -> str:
    return os.path.join(os.path.dirname(os.path.abspath(db.DB_PATH)), "backups")


def backup_dir() -> str:
    return (db.get_setting("backup_dir", "") or "").strip() or default_backup_dir()


def archive_path(name: str) -> str:
    """Chemin d'une archive du dossier de sauvegarde (nom validé)."""
    if not _ARCHIVE.match(name):
        raise BackupError("Nom d'archive invalide")
    path = os.path.join(backup_dir(), name)
    if not os.path.isfile(path):
        raise BackupError(f"Archive introuvable : {name}")
    return path


def check_setting(key: str, value: str) -> str:
    """Valide/normalise un réglage de sauvegarde. Lève ValueError si invalide."""
    v = str(value).strip()
    if key == "backup_dir":
        if not v:
            return ""                      # = dossier par défaut
        if not os.path.isabs(v) or ".." in v.split(os.sep):
            raise ValueError("Le dossier des sauvegardes doit être un chemin "
                             "absolu, sans « .. »")
        try:
            os.makedirs(v, exist_ok=True)
            probe = os.path.join(v, ".domopi-write-test")
            with open(probe, "w") as f:
                f.write("ok")
            os.remove(probe)
        except OSError as exc:
            raise ValueError(
                f"Dossier inutilisable ({exc.strerror or exc}). Le service "
                "n'écrit que dans les chemins autorisés par domopi.service "
                f"(ReadWritePaths) — par défaut sous "
                f"{os.path.dirname(os.path.abspath(db.DB_PATH))}.")
        return v
    if key == "backup_period":
        if v not in PERIODS:
            raise ValueError("Périodicité de sauvegarde inconnue")
        return v
    if key == "backup_next_ts":
        return str(max(0, int(float(v or 0))))
    if key == "backup_keep":
        n = int(v or 0)
        if not 0 <= n <= 365:
            raise ValueError("Nombre d'archives conservées : 0 (illimité) à 365")
        return str(n)
    if key == "backup_ftp_port":
        n = int(v or 21)
        if not 1 <= n <= 65535:
            raise ValueError("Port FTP invalide")
        return str(n)
    if key in ("backup_auto", "backup_ftp_enabled", "backup_ftp_anon",
               "backup_ftp_pasv", "backup_ftp_tls"):
        return "1" if v in ("1", "true", "True", "on") else "0"
    return v


# ================================================================ sauvegarde
def _snapshot_db(dest: str):
    """Instantané cohérent de la base vers `dest` (fichier inexistant)."""
    conn = db.get_conn()
    conn.commit()
    try:
        conn.execute("VACUUM INTO ?", (dest,))
    except sqlite3.OperationalError:
        # SQLite < 3.27 : repli sur l'API de sauvegarde en ligne
        out = sqlite3.connect(dest)
        try:
            conn.backup(out)
        finally:
            out.close()


def _human(n: float) -> str:
    for unit in ("o", "Ko", "Mo", "Go"):
        if n < 1024 or unit == "Go":
            return f"{n:.0f} {unit}" if unit == "o" else f"{n:.1f} {unit}"
        n /= 1024.0
    return f"{n:.1f} Go"


def _asset_files(folder: str) -> list[str]:
    if not os.path.isdir(folder):
        return []
    return sorted(f for f in os.listdir(folder)
                  if _SAFE_FILE.match(f)
                  and os.path.isfile(os.path.join(folder, f)))


def _manifest(snap: str, reason: str, icons: list[str], bgs: list[str],
              secret: bool) -> dict:
    counts = {}
    c = sqlite3.connect(snap)
    try:
        for t in TABLES:
            try:
                counts[t] = c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            except sqlite3.Error:
                counts[t] = 0
        try:
            first, last = c.execute("SELECT MIN(ts), MAX(ts) FROM measures").fetchone()
        except sqlite3.Error:
            first = last = None
    finally:
        c.close()
    return {"format": 1, "app": "DomoPi", "reason": reason,
            "created_ts": int(time.time()),
            "created": time.strftime("%Y-%m-%d %H:%M:%S"),
            "host": socket.gethostname(),
            "db_bytes": os.path.getsize(snap), "counts": counts,
            "measures_from": first, "measures_to": last,
            "icons": len(icons), "backgrounds": len(bgs), "secret_key": secret}


README = """Sauvegarde DomoPi du {created} ({host})

Contenu de l'archive
  domopi.db      base complète : réglages, contrôleurs (avec leurs
                 identifiants), périphériques, échelles, pages et widgets,
                 comptes utilisateurs, historique des mesures (brut +
                 archives journalières), journal applicatif
  secret.key     clé de signature des cookies de session
  icons/         icônes (intégrées, uploadées, générées par IA)
  backgrounds/   fonds de page

Ce fichier contient des identifiants : conservez-le comme un secret.

NON sauvegardé (à reposer à la main sur une nouvelle installation)
  /etc/domopi/domopi.env   mot de passe admin initial, ANTHROPIC_API_KEY
  /etc/domopi/tls/         certificat TLS
  configurations nginx et mosquitto (fournies par install.sh)

Restauration : interface d'administration -> « Réglages généraux et comptes »
-> « Sauvegarde et restauration ». Déposez l'archive dans le dossier des
sauvegardes du Pi ou envoyez-la depuis cette page, puis choisissez ce qui doit
être restauré (icônes, historiques par fusion, ou toute la base).
"""


def create_backup(reason: str = "manuelle") -> dict:
    """Crée l'archive et renvoie {name, size, manifest}."""
    folder = backup_dir()
    try:
        os.makedirs(folder, exist_ok=True)
    except OSError as exc:
        raise BackupError(f"Dossier de sauvegarde inutilisable : {exc}")
    stamp = "domopi-backup-" + time.strftime("%Y%m%d-%H%M%S")
    name = stamp + ".tar.gz"
    # Deux sauvegardes dans la même seconde (manuelle juste après une
    # automatique) porteraient le même nom : suffixe plutôt qu'écrasement
    # silencieux de l'archive précédente.
    n = 1
    while os.path.exists(os.path.join(folder, name)):
        n += 1
        name = f"{stamp}-{n}.tar.gz"
    final = os.path.join(folder, name)
    part = final + ".part"
    tmpdir = tempfile.mkdtemp(prefix="domopi-backup-")
    try:
        _phase("instantané de la base", 10)
        snap = os.path.join(tmpdir, "domopi.db")
        _snapshot_db(snap)

        icons, bgs = _asset_files(ICONS_DIR), _asset_files(BACKGROUNDS_DIR)
        secret = os.path.isfile(auth.ensure_secret())
        man = _manifest(snap, reason, icons, bgs, secret)
        mpath = os.path.join(tmpdir, "manifest.json")
        with open(mpath, "w", encoding="utf-8") as f:
            json.dump(man, f, ensure_ascii=False, indent=1)
        rpath = os.path.join(tmpdir, "README.txt")
        with open(rpath, "w", newline="\n", encoding="utf-8") as f:
            f.write(README.format(created=man["created"], host=man["host"]))

        _phase("compression de l'archive", 35)
        # manifest.json en premier : list_backups() le relit sans décompresser
        # toute l'archive.
        with tarfile.open(part, "w:gz", compresslevel=6) as tf:
            tf.add(mpath, "manifest.json")
            tf.add(rpath, "README.txt")
            tf.add(snap, "domopi.db")
            if secret:
                tf.add(auth.SECRET_PATH, "secret.key")
            for folder_path, arc, files in ((ICONS_DIR, "icons", icons),
                                            (BACKGROUNDS_DIR, "backgrounds", bgs)):
                for f in files:
                    tf.add(os.path.join(folder_path, f), f"{arc}/{f}")
        os.replace(part, final)
        os.chmod(final, 0o600)
    except BackupError:
        raise
    except (OSError, sqlite3.Error, tarfile.TarError) as exc:
        raise BackupError(f"Échec de la sauvegarde : {exc}")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
        if os.path.exists(part):
            try:
                os.remove(part)
            except OSError:
                pass
    return {"name": name, "size": os.path.getsize(final), "manifest": man}


#   path -> (mtime, taille, manifeste) : l'admin interroge /api/backups toutes
#   les 1,5 s pendant une opération, inutile de rouvrir chaque archive.
_manifests: dict[str, tuple[int, int, dict | None]] = {}


def read_manifest(path: str) -> dict | None:
    """Manifeste d'une archive (None si illisible / archive étrangère)."""
    try:
        st = os.stat(path)
        key = (int(st.st_mtime), st.st_size)
    except OSError:
        return None
    hit = _manifests.get(path)
    if hit and hit[:2] == key:
        return hit[2]
    man = _read_manifest(path)
    if len(_manifests) > 64:
        _manifests.clear()
    _manifests[path] = (key[0], key[1], man)
    return man


def _read_manifest(path: str) -> dict | None:
    try:
        with tarfile.open(path, "r:gz") as tf:
            for _ in range(4):               # écrit en premier dans le tar
                m = tf.next()
                if m is None:
                    return None
                if m.name == "manifest.json" and m.isfile():
                    f = tf.extractfile(m)
                    return json.loads(f.read(1 << 20).decode("utf-8")) if f else None
    except (tarfile.TarError, OSError, ValueError, UnicodeDecodeError):
        return None
    return None


def list_backups() -> list[dict]:
    folder = backup_dir()
    out = []
    if os.path.isdir(folder):
        for f in os.listdir(folder):
            if not _ARCHIVE.match(f):
                continue
            path = os.path.join(folder, f)
            try:
                st = os.stat(path)
            except OSError:
                continue
            out.append({"name": f, "size": st.st_size, "mtime": int(st.st_mtime),
                        "manifest": read_manifest(path)})
    out.sort(key=lambda e: e["mtime"], reverse=True)
    return out


def prune_old() -> list[str]:
    """Purge de rétention : ne garde que les `backup_keep` archives générées."""
    keep = int(db.get_setting("backup_keep", "8") or 0)
    folder = backup_dir()
    if keep <= 0 or not os.path.isdir(folder):
        return []
    files = sorted((f for f in os.listdir(folder) if _GENERATED.match(f)),
                   reverse=True)          # le nom porte l'horodatage
    removed = []
    for f in files[keep:]:
        try:
            os.remove(os.path.join(folder, f))
            removed.append(f)
        except OSError:
            pass
    return removed


def delete_backup(name: str) -> None:
    os.remove(archive_path(name))
    journal.info("backup", f"sauvegarde supprimée : {name}")


# ================================================================ export FTP
def _ftp_cfg() -> dict:
    g = db.get_setting
    return {"host": (g("backup_ftp_host", "") or "").strip(),
            "port": int(g("backup_ftp_port", "21") or 21),
            "anon": g("backup_ftp_anon", "0") == "1",
            "user": g("backup_ftp_user", "") or "",
            "password": g("backup_ftp_pass", "") or "",
            "dir": (g("backup_ftp_dir", "") or "").strip(),
            "pasv": g("backup_ftp_pasv", "1") == "1",
            "tls": g("backup_ftp_tls", "0") == "1",
            "enabled": g("backup_ftp_enabled", "0") == "1"}


def _tls_client_context() -> ssl.SSLContext:
    """Contexte TLS du client FTPS.

    **Plafonné à TLS 1.2** : la reprise de session exigée sur le canal de
    données (voir `_FTPS`) repose sur le mécanisme de session TLS ≤ 1.2 ; avec
    les tickets de TLS 1.3, les serveurs embarqués ne reconnaissent pas la
    session et refusent la connexion de données.

    Certificat **non vérifié**, comme le contexte par défaut de `ftplib` : box
    et NAS présentent des certificats auto-signés. Le chiffrement protège donc
    les identifiants et l'archive sur le réseau, mais n'authentifie pas le
    serveur — c'est un transfert sur LAN, pas un canal de confiance.
    """
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    ctx.maximum_version = ssl.TLSVersion.TLSv1_2
    return ctx


class _FTPS(ftplib.FTP_TLS):
    """FTPS explicite corrigé sur deux points que `ftplib` laisse de côté.

    1. **Reprise de session TLS sur le canal de données.**
       `FTP_TLS.ntransfercmd` enveloppe la connexion de données dans une
       session TLS **neuve**. Or la plupart des serveurs FTPS exigent que cette
       session soit la reprise de celle du canal de commande (vsftpd :
       `require_ssl_reuse=YES`, actif **par défaut** ; proftpd :
       `NoSessionReuseRequired` à poser pour s'en passer) — c'est une protection
       contre le détournement de connexion de données. Sans reprise, le serveur
       coupe la connexion de données dès la poignée de main : `ECONNRESET` côté
       Pi, **fichier créé mais vide** côté serveur, et cela quel que soit le
       mode PASV/actif. On enveloppe donc soi-même en passant la session du
       canal de commande. (Symptôme observé sur Freebox le 25/07/2026 ;
       demande CPython jamais retenue.)
    2. **Clôture TLS impolie tolérée.** En fin de `storbinary`, ftplib appelle
       `unwrap()` sur la connexion de données pour échanger le `close_notify`.
       Beaucoup de serveurs embarqués ferment sèchement à la place : ftplib
       remonte un `ECONNRESET` **alors que le fichier est intégralement
       arrivé**. On ignore l'échec de cette seule étape — sans risque, la taille
       déposée étant relue par `SIZE` juste après (`_ftp_send`).
    """

    def ntransfercmd(self, cmd, rest=None):
        # FTP.ntransfercmd (et non super()) : on court-circuite l'enveloppe TLS
        # de FTP_TLS pour poser la nôtre, avec reprise de session.
        conn, size = ftplib.FTP.ntransfercmd(self, cmd, rest)
        if self._prot_p:
            conn = self.context.wrap_socket(
                conn, server_hostname=self.host,
                session=getattr(self.sock, "session", None))
            shutdown = conn.unwrap

            def tolerant():
                try:
                    return shutdown()
                except (OSError, ValueError, ssl.SSLError):
                    return conn        # clôture TLS refusée : sans conséquence
            conn.unwrap = tolerant
        return conn, size


def _pwd(ftp) -> str | None:
    """Dossier courant, ou None si le serveur ne répond pas à PWD."""
    try:
        return ftp.pwd()
    except ftplib.all_errors:
        return None


def _ftp_chdir(ftp, path: str) -> str:
    """Se place dans le dossier distant en créant les niveaux manquants.

    La réponse de `CWD` n'est **pas** prise pour argent comptant : certains
    serveurs (Freebox et ses partages réseau montés, notamment) acquiescent
    sans changer de dossier quand la cible n'existe pas. On compare donc `PWD`
    avant/après chaque niveau — sans quoi le `STOR` suivant partirait dans le
    mauvais dossier et le serveur couperait le canal de données en cours de
    transfert (« Connection reset by peer »), symptôme qui ne dit rien de sa
    cause. Renvoie le dossier réellement atteint.
    """
    reached = _pwd(ftp)
    for part in [p for p in path.replace("\\", "/").strip("/").split("/") if p]:
        before = _pwd(ftp)
        try:
            ftp.cwd(part)
        except ftplib.all_errors:
            pass                           # dossier absent : on le crée plus bas
        after = _pwd(ftp)
        # PWD indisponible : on ne peut que faire confiance à CWD.
        entered = True if (before is None or after is None) else after != before
        if not entered:
            try:
                ftp.mkd(part)
            except ftplib.all_errors as exc:
                raise BackupError(
                    f"dossier distant « {part} » absent dans {before or path} et "
                    f"création refusée par le serveur ({exc})")
            try:
                ftp.cwd(part)
            except ftplib.all_errors as exc:
                raise BackupError(f"dossier distant « {part} » créé mais "
                                  f"inaccessible ({exc})")
            if _pwd(ftp) == before:
                raise BackupError(f"impossible d'entrer dans le dossier distant "
                                  f"« {part} » (le serveur reste dans {before})")
        reached = _pwd(ftp) or (reached or "") + "/" + part
    return reached or "/"


def _ftp_connect(cfg: dict) -> tuple:
    """Connexion + authentification + positionnement. Renvoie (ftp, dossier)."""
    if not cfg["host"]:
        raise BackupError("Adresse du serveur FTP non renseignée")
    ftp = _FTPS(context=_tls_client_context()) if cfg["tls"] else ftplib.FTP()
    try:
        try:
            ftp.connect(cfg["host"], cfg["port"], timeout=30)
        except OSError as exc:
            raise BackupError(f"connexion à {cfg['host']}:{cfg['port']} impossible "
                              f"({exc.strerror or exc})")
        try:
            if cfg["anon"]:
                ftp.login()                # anonymous / adresse quelconque
            else:
                ftp.login(cfg["user"], cfg["password"])
        except ftplib.all_errors as exc:
            raise BackupError(f"authentification refusée ({exc})")
        if cfg["tls"]:
            try:
                ftp.prot_p()               # canal de données chiffré aussi
            except ftplib.all_errors as exc:
                raise BackupError(f"FTPS : le serveur refuse de chiffrer le canal "
                                  f"de données ({exc}) — décochez « FTPS »")
        ftp.set_pasv(cfg["pasv"])
        where = _ftp_chdir(ftp, cfg["dir"])
    except BackupError as exc:
        _ftp_bye(ftp)
        raise BackupError(f"FTP : {exc}")
    except ftplib.all_errors as exc:
        _ftp_bye(ftp)
        raise BackupError(f"FTP : {exc}")
    return ftp, where


def _ftp_bye(ftp):
    try:
        ftp.quit()
    except Exception:
        try:
            ftp.close()
        except Exception:
            pass


def _mode_label(cfg: dict) -> str:
    return ("FTPS explicite" if cfg["tls"] else "FTP") + \
           (", PASV" if cfg["pasv"] else ", actif")


def _data_hint(cfg: dict) -> str:
    """Piste à suivre quand le canal de données ne passe pas.

    Le mode **actif** est le suspect n°1 : c'est le serveur qui doit ouvrir la
    connexion de données vers le Pi, ce que routeurs et pare-feux bloquent —
    et en FTPS, il doit en plus y engager une poignée de main TLS. Résultat
    typique : le fichier est créé côté serveur mais reste à 0 octet.
    """
    return ("essayez de décocher « PASV » (mode actif)" if cfg["pasv"]
            else "cochez « PASV » : en mode actif, c'est le serveur qui doit "
                 "ouvrir la connexion de données vers le Pi, ce qu'un routeur "
                 "ou un pare-feu bloque presque toujours")


def _remote_size(ftp, name: str) -> int | None:
    try:
        return ftp.size(name)
    except ftplib.all_errors:
        return None                        # serveur sans SIZE : contrôle ignoré


def _ftp_cleanup(ftp, name: str) -> str:
    """Retire le fichier laissé par un envoi manqué (souvent 0 octet)."""
    left = _remote_size(ftp, name)
    if left is None:
        return ""
    try:
        ftp.delete(name)
        return f" ; reste incomplet ({_human(left)}) retiré du serveur"
    except ftplib.all_errors:
        return f" ; un fichier incomplet ({_human(left)}) reste sur le serveur"


def _ftp_send(ftp, cfg: dict, name: str, src, size: int, where: str):
    """STOR + vérification de la taille déposée. Messages distincts selon l'étage
    qui a lâché : refus du serveur (droits, quota) ou canal de données coupé."""
    try:
        ftp.storbinary("STOR " + name, src, blocksize=1 << 16)
    except ftplib.error_perm as exc:
        raise BackupError(f"dépôt de « {name} » refusé dans {where} ({exc}) — "
                          f"droits d'écriture ou espace disponible ?")
    except (ftplib.error_temp, ftplib.error_proto, ftplib.error_reply) as exc:
        # 425/426 : le serveur n'a pas pu ouvrir ou tenir le canal de données —
        # même piste que la coupure côté client.
        raise BackupError(f"dépôt de « {name} » interrompu par le serveur dans "
                          f"{where} ({exc}){_ftp_cleanup(ftp, name)} — "
                          f"{_data_hint(cfg)}")
    except (OSError, EOFError) as exc:
        raise BackupError(
            f"canal de données coupé pendant l'envoi de « {name} » vers {where} "
            f"({getattr(exc, 'strerror', None) or exc}){_ftp_cleanup(ftp, name)} — "
            f"{_data_hint(cfg)}")
    # Taille déposée : détecte un transfert tronqué (voire vide) qu'aucune erreur
    # n'a signalé — le cas du fichier créé à 0 octet en mode actif.
    remote = _remote_size(ftp, name)
    if remote is not None and remote != size:
        raise BackupError(f"« {name} » déposé incomplet dans {where} "
                          f"({_human(remote)} sur {_human(size)})"
                          f"{_ftp_cleanup(ftp, name)} — {_data_hint(cfg)}")
    return remote


def ftp_upload(path: str) -> str:
    """Envoie une archive sur le serveur FTP configuré. Renvoie un message."""
    cfg = _ftp_cfg()
    ftp, where = _ftp_connect(cfg)
    name, size = os.path.basename(path), os.path.getsize(path)
    try:
        with open(path, "rb") as f:
            checked = _ftp_send(ftp, cfg, name, f, size, where)
    except BackupError as exc:
        raise BackupError(f"FTP : {exc}")
    except OSError as exc:
        raise BackupError(f"FTP : lecture de l'archive impossible "
                          f"({exc.strerror or exc})")
    finally:
        _ftp_bye(ftp)
    return (f"archive envoyée sur {cfg['host']}:{cfg['port']} dans {where}"
            + (f" ({_human(size)} confirmés)" if checked is not None else ""))


# Le fichier témoin du test doit passer par plusieurs blocs du canal de données :
# un témoin de quelques octets « réussissait » là où une vraie archive se faisait
# couper (mauvais dossier, pare-feu, mode actif/passif), test vert et sauvegarde
# rouge — exactement le piège rencontré en production.
PROBE_BYTES = 256 * 1024


def ftp_test() -> dict:
    """Vérifie connexion, identifiants, arborescence et droit d'écriture.

    Le témoin déposé fait `PROBE_BYTES` : un test vert prédit donc une vraie
    sauvegarde verte (même dossier, même canal de données, taille relue).
    """
    cfg = _ftp_cfg()
    ftp, where = _ftp_connect(cfg)
    probe = f"domopi-test-{int(time.time())}.bin"
    data = b"DomoPi test\n" * (PROBE_BYTES // 12)
    try:
        try:
            checked = _ftp_send(ftp, cfg, probe, io.BytesIO(data), len(data), where)
        except BackupError as exc:
            raise BackupError(f"connexion et dossier {where} OK, mais {exc}")
        extra = f"{_human(len(data))} déposés"
        extra += " et relus" if checked is not None else " (taille non vérifiable)"
        try:
            ftp.delete(probe)
            extra += ", suppression OK"
        except ftplib.all_errors:
            extra += f", mais suppression refusée (« {probe} » laissé sur place)"
    finally:
        _ftp_bye(ftp)
    return {"ok": True,
            "message": f"{_mode_label(cfg)} — connecté à {cfg['host']}:{cfg['port']}, "
                       f"dossier {where} : {extra}"}


# ================================================================ planification
def _add_months(dt: datetime.datetime, n: int) -> datetime.datetime:
    """Ajoute n mois en ramenant le jour au dernier du mois si besoin."""
    y, m = divmod(dt.month - 1 + n, 12)
    y, m = dt.year + y, m + 1
    last = [31, 29 if (y % 4 == 0 and (y % 100 or y % 400 == 0)) else 28,
            31, 30, 31, 30, 31, 31, 30, 31, 30, 31][m - 1]
    return dt.replace(year=y, month=m, day=min(dt.day, last))


def next_after(ts: int, period: str, now: int | None = None) -> int:
    """Première échéance strictement postérieure à `now`, en partant de `ts`.

    Avance par pas entiers de la périodicité : une coupure de plusieurs jours ne
    déclenche donc qu'une seule sauvegarde de rattrapage, et l'heure choisie par
    l'utilisateur est conservée (calcul en heure locale, changements d'heure
    compris).
    """
    _, unit, n = PERIODS.get(period, PERIODS["1d"])
    now = int(time.time()) if now is None else int(now)
    dt = datetime.datetime.fromtimestamp(max(0, int(ts)))
    for _ in range(5000):
        if int(dt.timestamp()) > now:
            break
        dt = (dt + datetime.timedelta(days=n)) if unit == "d" else _add_months(dt, n)
    return int(dt.timestamp())


def run_scheduled(now: float) -> None:
    """Sauvegarde automatique due ? Appelé à chaque cycle par le collecteur."""
    if db.get_setting("backup_auto", "0") != "1":
        return
    period = db.get_setting("backup_period", "1d")
    try:
        nxt = int(float(db.get_setting("backup_next_ts", "0") or 0))
    except ValueError:
        nxt = 0
    if not nxt:                            # jamais planifiée : première échéance
        db.set_setting("backup_next_ts", str(next_after(int(now), period, int(now))))
        return
    if now < nxt:
        return
    # L'échéance suivante est posée AVANT l'exécution : un échec ne relance pas
    # une sauvegarde à chaque cycle.
    db.set_setting("backup_next_ts", str(next_after(nxt, period, int(now))))
    try:
        run_backup("automatique")
    except Busy:
        journal.warning("backup", "sauvegarde automatique reportée : "
                                  "une opération est déjà en cours")
    except BackupError as exc:
        journal.error("backup", f"sauvegarde automatique échouée : {exc}")


# ================================================================ exécution
def run_backup(reason: str = "manuelle") -> dict:
    """Sauvegarde complète : archive, purge de rétention, export FTP."""
    _begin("backup")
    try:
        info = create_backup(reason)
        report: dict = {"name": info["name"], "size": info["size"],
                        "manifest": info["manifest"], "pruned": [], "ftp": ""}
        _phase("purge des anciennes archives", 80)
        report["pruned"] = prune_old()
        cfg = _ftp_cfg()
        if cfg["enabled"]:
            _phase("export FTP", 85)
            try:
                report["ftp"] = ftp_upload(os.path.join(backup_dir(), info["name"]))
            except BackupError as exc:
                # La sauvegarde locale est faite : l'échec d'export ne l'annule pas.
                report["ftp"] = f"échec — {exc}"
                journal.error("backup", f"export FTP échoué : {exc}")
        msg = f"Sauvegarde « {info['name']} » créée ({_human(info['size'])})"
        if report["pruned"]:
            msg += f", {len(report['pruned'])} ancienne(s) archive(s) purgée(s)"
        if report["ftp"]:
            msg += f" ; {report['ftp']}"
    except BackupError as exc:
        _end(error=str(exc))
        journal.error("backup", f"sauvegarde ({reason}) échouée : {exc}")
        raise
    except Exception as exc:               # garde-fou : le verrou doit être rendu
        _end(error=f"Erreur inattendue : {exc}")
        journal.error("backup", f"sauvegarde ({reason}) échouée : {exc}")
        raise BackupError(f"Erreur inattendue : {exc}")
    _end(message=msg, report=report)
    journal.info("backup", f"sauvegarde {reason} : {msg}")
    return report


def run_restore(name: str, opts: dict) -> dict:
    """Restauration sélective depuis une archive du dossier de sauvegarde."""
    path = archive_path(name)
    if not any(opts.get(k) for k in ("icons", "history", "full")):
        raise BackupError("Rien à restaurer : cochez au moins un élément")
    _begin("restore")
    try:
        report = _restore(path, opts)
    except BackupError as exc:
        _end(error=str(exc))
        journal.error("backup", f"restauration de {name} échouée : {exc}")
        raise
    except Exception as exc:
        _end(error=f"Erreur inattendue : {exc}")
        journal.error("backup", f"restauration de {name} échouée : {exc}")
        raise BackupError(f"Erreur inattendue : {exc}")
    parts = []
    if "assets" in report:
        parts.append(f"{report['assets']['icons']} icône(s) et "
                     f"{report['assets']['backgrounds']} fond(s)")
    if "full" in report:
        parts.append("base restaurée à l'identique")
    if "history" in report:
        h = report["history"]
        parts.append(f"{h['raw']} mesure(s) et {h['daily']} jour(s) d'archive "
                     f"fusionnés sur {h['matched']} capteur(s)")
    if report.get("secret"):
        parts.append("clé de session remplacée")
    msg = "Restauration terminée : " + (", ".join(parts) or "rien à faire")
    _end(message=msg, report=report)
    journal.info("backup", f"restauration depuis {name} — {msg}")
    return report


def _restore(path: str, opts: dict) -> dict:
    report: dict = {}
    tmpdir = tempfile.mkdtemp(prefix="domopi-restore-")
    try:
        snap = None
        try:
            with tarfile.open(path, "r:gz") as tf:
                members = [m for m in tf.getmembers()
                           if m.isfile() and (m.name in _FLAT_MEMBERS
                                              or _ASSET_MEMBER.match(m.name))]
                names = {m.name for m in members}
                if "manifest.json" not in names or "domopi.db" not in names:
                    raise BackupError(
                        "Archive DomoPi invalide (manifeste ou base absente)")
                if opts.get("icons"):
                    _phase("icônes et fonds de page", 10)
                    report["assets"] = _restore_assets(tf, members)
                if opts.get("history") or opts.get("full"):
                    _phase("extraction de la base sauvegardée", 25)
                    snap = os.path.join(tmpdir, "archive.db")
                    _extract(tf, next(m for m in members if m.name == "domopi.db"), snap)
                if opts.get("secret") and "secret.key" in names:
                    key = os.path.join(tmpdir, "secret.key")
                    _extract(tf, next(m for m in members if m.name == "secret.key"), key)
                    os.chmod(key, 0o600)
                    os.replace(key, auth.SECRET_PATH)
                    auth.forget_secret()   # sessions en cours invalidées
                    report["secret"] = True
        except tarfile.TarError as exc:
            raise BackupError(f"Archive illisible : {exc}")

        if opts.get("full"):
            _check_db(snap)
            carried = None
            if opts.get("keep_history"):
                _phase("mise de côté de l'historique actuel", 40)
                carried = os.path.join(tmpdir, "current.db")
                _snapshot_db(carried)
            _phase("restauration de la base", 55)
            report["full"] = _restore_full(snap)
            if carried:
                _phase("réinjection de l'historique accumulé", 80)
                report["history"] = _merge_history(carried)
        elif opts.get("history"):
            _check_db(snap)
            _phase("fusion des historiques", 50)
            report["history"] = _merge_history(snap)
    except (OSError, sqlite3.Error) as exc:
        raise BackupError(f"Échec de la restauration : {exc}")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
    return report


def _extract(tf: tarfile.TarFile, member: tarfile.TarInfo, dest: str):
    src = tf.extractfile(member)
    if src is None:
        raise BackupError(f"Membre illisible dans l'archive : {member.name}")
    with src, open(dest, "wb") as out:
        shutil.copyfileobj(src, out, 1 << 20)


def _restore_assets(tf: tarfile.TarFile, members: list) -> dict:
    """Extrait icons/ et backgrounds/ (écrase les fichiers de même nom)."""
    done = {"icons": 0, "backgrounds": 0}
    for m in members:
        folder, _, fname = m.name.partition("/")
        if folder not in ("icons", "backgrounds"):
            continue
        dest_dir = ICONS_DIR if folder == "icons" else BACKGROUNDS_DIR
        os.makedirs(dest_dir, exist_ok=True)
        tmp = os.path.join(dest_dir, "." + fname + ".part")
        _extract(tf, m, tmp)
        os.replace(tmp, os.path.join(dest_dir, fname))
        done[folder] += 1
    return done


def _check_db(path: str | None):
    if not path or not os.path.isfile(path):
        raise BackupError("Base absente de l'archive")
    c = sqlite3.connect(path)
    try:
        if c.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise BackupError("La base de l'archive est corrompue")
        tabs = {r[0] for r in c.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        missing = {"settings", "devices", "connectors"} - tabs
        if missing:
            raise BackupError("La base de l'archive n'est pas une base DomoPi "
                              f"(tables manquantes : {', '.join(sorted(missing))})")
    except sqlite3.DatabaseError as exc:
        raise BackupError(f"Base de l'archive illisible : {exc}")
    finally:
        c.close()


def _common_cols(conn: sqlite3.Connection, table: str) -> list[str]:
    """Colonnes présentes des deux côtés (archive d'un schéma antérieur)."""
    src = {r[1] for r in conn.execute(f"PRAGMA src.table_info({table})")}
    return [r[1] for r in conn.execute(f"PRAGMA main.table_info({table})")
            if r[1] in src]


def _restore_full(snap: str) -> dict:
    """Remplace le contenu de la base vivante par celui de l'archive.

    On ne substitue pas le fichier (connexions SQLite ouvertes, WAL) : la base
    de l'archive est ATTACHée et chaque table est vidée puis recopiée dans une
    seule transaction — la base reste le même fichier, aux mêmes chemins.
    """
    conn = db.get_conn()
    conn.commit()
    counts: dict[str, int] = {}
    conn.execute("PRAGMA foreign_keys=OFF")
    conn.execute("ATTACH DATABASE ? AS src", (snap,))
    try:
        conn.execute("BEGIN IMMEDIATE")
        for t in reversed(TABLES):
            conn.execute(f"DELETE FROM {t}")
        for t in TABLES:
            cols = _common_cols(conn, t)
            if not cols:
                continue                   # table absente de l'archive
            cl = ",".join(f'"{c}"' for c in cols)
            conn.execute(f"INSERT INTO {t}({cl}) SELECT {cl} FROM src.{t}")
            counts[t] = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.execute("DETACH DATABASE src")
        conn.execute("PRAGMA foreign_keys=ON")
    db.init_db()                           # migrations + réglages par défaut
    # Les contrôleurs viennent de changer : instances en cache à reconstruire.
    from . import poller                   # import tardif (poller importe backup)
    poller.reset_instances()
    return counts


def _merge_history(snap: str) -> dict:
    """Fusionne les mesures de `snap` pour les capteurs présents des deux côtés.

    Appariement : (type de contrôleur, external_id), avec repli sur le nom
    normalisé si non ambigu. Les points déjà en base sont conservés
    (INSERT OR IGNORE) : la fusion ne fait que combler les trous.
    """
    conn = db.get_conn()
    conn.commit()
    rep: dict = {"matched": 0, "unmatched": [], "raw": 0, "daily": 0}
    conn.execute("ATTACH DATABASE ? AS src", (snap,))
    try:
        by_key: dict[tuple, int] = {}
        by_name: dict[str, int] = {}
        ambiguous: set[str] = set()
        for did, dname, ext, ctype in conn.execute(
                "SELECT d.id, d.name, d.external_id, c.type FROM main.devices d "
                "JOIN main.connectors c ON c.id=d.connector_id"):
            by_key[(ctype, ext)] = did
            k = formula._norm(dname)
            if k in by_name:
                ambiguous.add(k)
            else:
                by_name[k] = did
        pairs = []
        for did, dname, ext, ctype in conn.execute(
                "SELECT d.id, d.name, d.external_id, c.type FROM src.devices d "
                "JOIN src.connectors c ON c.id=d.connector_id"):
            target = by_key.get((ctype, ext))
            if target is None:
                k = formula._norm(dname)
                target = None if k in ambiguous else by_name.get(k)
            if target is None:
                rep["unmatched"].append(dname)
            else:
                pairs.append((did, target))
        rep["matched"] = len(pairs)
        conn.execute("BEGIN IMMEDIATE")
        try:
            for sid, tid in pairs:
                before = conn.total_changes
                conn.execute("INSERT OR IGNORE INTO main.measures(device_id,ts,value) "
                             "SELECT ?, ts, value FROM src.measures WHERE device_id=?",
                             (tid, sid))
                rep["raw"] += conn.total_changes - before
                before = conn.total_changes
                try:
                    conn.execute(
                        "INSERT OR IGNORE INTO main.measures_daily"
                        "(device_id,day,vmin,vavg,vmax,n) "
                        "SELECT ?, day, vmin, vavg, vmax, n FROM src.measures_daily "
                        "WHERE device_id=?", (tid, sid))
                except sqlite3.OperationalError:
                    pass                   # archives journalières absentes
                rep["daily"] += conn.total_changes - before
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    finally:
        conn.execute("DETACH DATABASE src")
    rep["unmatched_total"] = len(rep["unmatched"])
    rep["unmatched"] = sorted(rep["unmatched"])[:20]
    return rep


# ================================================================ envoi d'archive
# L'archive envoyée depuis l'admin est écrite **directement dans le dossier de
# sauvegarde** (fichier `.part`), et non dans /tmp : avec PrivateTmp=true et un
# /tmp en tmpfs, une archive d'un giga-octet occuperait la RAM du Pi.
def upload_paths(filename: str) -> tuple[str, str]:
    """(chemin temporaire, chemin final) pour une archive envoyée. Nom validé."""
    name = re.sub(r"[^A-Za-z0-9._-]", "_", os.path.basename(filename or ""))
    if not name.endswith(".tar.gz") or len(name) <= 7:
        raise BackupError("Seules les archives .tar.gz sont acceptées")
    folder = backup_dir()
    try:
        os.makedirs(folder, exist_ok=True)
    except OSError as exc:
        raise BackupError(f"Dossier de sauvegarde inutilisable : {exc}")
    if os.path.exists(os.path.join(folder, name)):
        name = f"{name[:-7]}-{time.strftime('%Y%m%d-%H%M%S')}.tar.gz"
    dest = os.path.join(folder, name)
    return dest + ".part", dest


def finish_upload(part: str, dest: str) -> dict:
    """Valide l'archive reçue (manifeste DomoPi) puis la met en place."""
    try:
        man = read_manifest(part)
        if not man or man.get("app") != "DomoPi":
            raise BackupError("Ce fichier n'est pas une sauvegarde DomoPi")
        size = os.path.getsize(part)
        os.replace(part, dest)
        os.chmod(dest, 0o600)
    except BackupError:
        abort_upload(part)
        raise
    except OSError as exc:
        abort_upload(part)
        raise BackupError(f"Écriture impossible : {exc}")
    name = os.path.basename(dest)
    journal.info("backup", f"archive importée : {name} ({_human(size)})")
    return {"name": name, "size": size, "manifest": man}


def abort_upload(part: str):
    try:
        os.remove(part)
    except OSError:
        pass
