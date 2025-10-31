#!/usr/bin/env python3
"""
APISwitch — profile switcher for Google/Gemini API keys and gcloud projects
Windows/macOS/Linux. ASCII-only strings.

- Profiles: ~/.apiswitch/profiles.json
- Settings: ~/.apiswitch/settings.json
- Windows env persistence: Registry (HKCU/HKLM) + WM_SETTINGCHANGE (no setx)
- POSIX env persistence: ~/.config/apiswitch/env.sh (+ auto-source in rc)
- All gcloud calls use CLOUDSDK_CONFIG=<selected dir> (fixes elevation profile mixups)
- Purge supports deep mode: ADC, legacy_credentials (all), credentials.db, access_tokens.db
- Project ID/Number: editable + auto-resolve
- gcloud config name is auto-sanitized (lowercase, a-z0-9-)
"""

from __future__ import annotations
import json, os, platform, re, shutil, subprocess, sys, threading, time
from dataclasses import dataclass, asdict
from pathlib import Path
import tkinter as tk
from tkinter import messagebox, ttk, filedialog

# ---------------------------- Paths & files ----------------------------
APP_DIR = Path.home() / "Documents" / "apiswitch"; APP_DIR.mkdir(parents=True, exist_ok=True)
PROFILES_FILE = APP_DIR / "profiles.json"
SETTINGS_FILE = APP_DIR / "settings.json"

ENV_DIR = Path.home() / ".config" / "apiswitch"; ENV_DIR.mkdir(parents=True, exist_ok=True)
ENV_FILE = ENV_DIR / "env.sh"

def default_gcloud_config_dir_current_user() -> Path:
    if platform.system() == "Windows":
        base = Path(os.environ.get("APPDATA", str(Path.home() / "AppData" / "Roaming")))
        return base / "gcloud"
    else:
        return Path.home() / ".config" / "gcloud"

# ---------------------------- Settings ----------------------------
def load_settings() -> dict:
    if SETTINGS_FILE.exists():
        try:
            return json.loads(SETTINGS_FILE.read_text(encoding="utf-8")) or {}
        except Exception:
            return {}
    return {}

def save_settings(s: dict) -> None:
    SETTINGS_FILE.write_text(json.dumps(s, indent=2), encoding="utf-8")

# ---------------------------- Data model ----------------------------
@dataclass
class Profile:
    name: str
    google_api_key: str
    gemini_api_key: str
    gcloud_project: str
    gcloud_project_number: str
    gcloud_account: str
    gcloud_service_account_key_file: str
    def normalized(self) -> "Profile":
        name = (self.name or "").strip() or "default"
        return Profile(
            name=name,
            google_api_key=(self.google_api_key or "").strip(),
            gemini_api_key=(self.gemini_api_key or "").strip(),
            gcloud_project=(self.gcloud_project or "").strip(),
            gcloud_project_number=(self.gcloud_project_number or "").strip(),
            gcloud_account=(self.gcloud_account or "").strip(),
            gcloud_service_account_key_file=(self.gcloud_service_account_key_file or "").strip(),
        )

class ProfileStore:
    def __init__(self, path: Path):
        self.path = path; self._profiles: dict[str, Profile] = {}; self.load()
    def load(self) -> None:
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                items = data.get("profiles", {}) if isinstance(data, dict) else {}
                self._profiles = {k: Profile(**v).normalized() for k, v in items.items()}
            except Exception:
                try: shutil.copy2(self.path, self.path.with_suffix(".corrupt.json"))
                except Exception: pass
                self._profiles = {}
        else:
            self._profiles = {}
    def save(self) -> None:
        data = {k: asdict(v) for k, v in self._profiles.items()}
        self.path.write_text(json.dumps({"profiles": data}, indent=2), encoding="utf-8")
    def names(self) -> list[str]: return sorted(self._profiles.keys())
    def get(self, name: str) -> Profile | None: return self._profiles.get(name)
    def upsert(self, p: Profile) -> None:
        p = p.normalized()
        if not p.name: raise ValueError("Profile name cannot be empty")
        if not (p.google_api_key or p.gemini_api_key): raise ValueError("At least one API key is required")
        self._profiles[p.name] = p; self.save()
    def delete(self, name: str) -> None:
        if name in self._profiles: del self._profiles[name]; self.save()

# ---------------------------- OS + subprocess helpers ----------------------------
def is_windows() -> bool: return platform.system() == "Windows"
def _win_creationflags() -> int: return 0x08000000 if is_windows() else 0  # CREATE_NO_WINDOW
def run(cmd: list[str], check: bool = True, env: dict | None = None) -> subprocess.CompletedProcess:
    kwargs = dict(stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, check=check)
    if is_windows(): kwargs["creationflags"] = _win_creationflags()
    if env is not None: kwargs["env"] = env
    return subprocess.run(cmd, **kwargs)

if is_windows():
    import ctypes
    from ctypes import wintypes
    def _is_admin_windows() -> bool:
        try: return ctypes.windll.shell32.IsUserAnAdmin() != 0
        except Exception: return False
    def relaunch_as_admin_if_needed() -> None:
        if _is_admin_windows(): return
        exe = Path(sys.executable); pythonw = exe.with_name("pythonw.exe")
        target = str(pythonw if pythonw.exists() else exe)
        args = " ".join(['"' + a + '"' for a in sys.argv])
        ctypes.windll.shell32.ShellExecuteW(None, "runas", target, args, None, 1); sys.exit(0)
    def broadcast_env_change_windows() -> None:
        HWND_BROADCAST = 0xFFFF; WM_SETTINGCHANGE = 0x001A; SMTO_ABORTIFHUNG = 0x0002
        try:
            ctypes.windll.user32.SendMessageTimeoutW(
                HWND_BROADCAST, WM_SETTINGCHANGE, 0, "Environment",
                SMTO_ABORTIFHUNG, 5000, ctypes.byref(wintypes.DWORD())
            )
            time.sleep(0.05) # Give explorer a moment to react
        except Exception: pass
    try: import winreg  # type: ignore
    except Exception: winreg = None
    def windows_set_env(var: str, val: str, machine: bool) -> None:
        if winreg is None: raise RuntimeError("winreg not available")
        root = winreg.HKEY_LOCAL_MACHINE if machine else winreg.HKEY_CURRENT_USER
        sub = r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment" if machine else r"Environment"
        access = winreg.KEY_SET_VALUE
        if machine: access |= winreg.KEY_WOW64_64KEY
        with winreg.OpenKey(root, sub, 0, access) as k:
            winreg.SetValueEx(k, var, 0, winreg.REG_SZ, val)
        broadcast_env_change_windows()
    def read_winenv(name: str) -> tuple[str | None, str | None]:
        user_val = None; mach_val = None
        if winreg is None: return (None, None)
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Environment") as k:
                user_val, _ = winreg.QueryValueEx(k, name)
        except Exception: pass
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment") as k:
                mach_val, _ = winreg.QueryValueEx(k, name)
        except Exception: pass
        return (user_val, mach_val)
    def windows_add_to_path(dir_path: str, machine: bool) -> None:
        if winreg is None: return
        root = winreg.HKEY_LOCAL_MACHINE if machine else winreg.HKEY_CURRENT_USER
        sub = r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment" if machine else r"Environment"
        access = winreg.KEY_QUERY_VALUE | winreg.KEY_SET_VALUE
        if machine: access |= winreg.KEY_WOW64_64KEY
        with winreg.OpenKey(root, sub, 0, access) as k:
            try: current, _ = winreg.QueryValueEx(k, "Path"); cur = str(current)
            except Exception: cur = ""
            parts = [p.strip() for p in cur.split(";") if p.strip()]
            if dir_path not in parts:
                parts.append(dir_path); winreg.SetValueEx(k, "Path", 0, winreg.REG_EXPAND_SZ, ";".join(parts))
        broadcast_env_change_windows()
else:
    def relaunch_as_admin_if_needed() -> None: return
    def broadcast_env_change_windows() -> None: return
    def windows_set_env(var: str, val: str, machine: bool) -> None: return
    def read_winenv(name: str) -> tuple[str | None, str | None]: return (None, None)
    def windows_add_to_path(dir_path: str, machine: bool) -> None: return

# ---------------------------- Env persistence (POSIX) ----------------------------
def _sh_single_quote(s: str) -> str: return "'" + s.replace("'", "'\"'\"'") + "'"
SOURCE_LINE = " ".join(["[ -f", _sh_single_quote(str(ENV_FILE)), "]", ">>", ".", _sh_single_quote(str(ENV_FILE))]) + "\n"
def ensure_env_file(keys: dict[str, str]) -> None:
    lines = ["# Generated by APISwitch", "# Do not edit by hand; use the APISwitch app."]
    for k, v in keys.items():
        safe = (v or "").replace("'", "'\"'\"'"); lines.append("export " + k + "='" + safe + "'")
    lines.append(""); ENV_FILE.write_text("\n".join(lines), encoding="utf-8")
def current_shell_rc_candidates() -> list[Path]:
    shell = os.environ.get("SHELL", "").split("/")[-1]; home = Path.home()
    candidates: list[Path] = []
    if shell in {"zsh", ""}: candidates += [home / ".zshrc", home / ".zprofile", home / ".zshenv"]
    if shell == "bash": candidates += [home / ".bashrc", home / ".bash_profile", home / ".profile"]
    candidates += [home / ".profile"]; out: list[Path] = []; seen: set[Path] = set()
    for p in candidates:
        if p not in seen: out.append(p); seen.add(p)
    return out
def ensure_shell_rc_sources_env() -> None:
    append = "\n# APISwitch\n" + SOURCE_LINE
    for rc in current_shell_rc_candidates():
        try:
            if rc.exists():
                txt = rc.read_text(encoding="utf-8")
                if SOURCE_LINE not in txt:
                    with rc.open("a", encoding="utf-8") as f: f.write(append)
            else:
                rc.write_text("# Created by APISwitch\n" + SOURCE_LINE, encoding="utf-8")
        except Exception: pass

# ---------------------------- gcloud discovery ----------------------------
def candidate_gcloud_paths() -> list[Path]:
    cands: list[Path] = []
    s = load_settings(); p = s.get("gcloud_path") or ""
    if p: cands.append(Path(p))
    for name in ("gcloud.cmd", "gcloud.exe", "gcloud"):
        w = shutil.which(name)
        if w: cands.append(Path(w))
    home = Path.home(); sysdrive = Path(os.environ.get("SystemDrive", "C:"))
    if is_windows():
        local = Path(os.environ.get("LOCALAPPDATA", str(home / "AppData" / "Local")))
        cands += [
            local / "Google" / "Cloud SDK" / "google-cloud-sdk" / "bin" / "gcloud.cmd",
            sysdrive / "Program Files" / "Google" / "Cloud SDK" / "google-cloud-sdk" / "bin" / "gcloud.cmd",
            sysdrive / "Program Files (x86)" / "Google" / "Cloud SDK" / "google-cloud-sdk" / "bin" / "gcloud.cmd",
        ]
        for base in [sysdrive / "Program Files", sysdrive / "Program Files (x86)", local / "Google"]:
            try:
                for sub in base.glob("**/google-cloud-sdk/bin/gcloud.cmd"): cands.append(sub)
            except Exception: pass
    else:
        cands += [
            Path("/usr/local/google-cloud-sdk/bin/gcloud"),
            Path("/usr/lib/google-cloud-sdk/bin/gcloud"),
            home / "google-cloud-sdk" / "bin" / "gcloud",
        ]
        cands += list(Path("/usr/local/Caskroom").glob("google-cloud-sdk/*/google-cloud-sdk/bin/gcloud"))
        cands += list(Path("/opt/homebrew/Caskroom").glob("google-cloud-sdk/*/google-cloud-sdk/bin/gcloud"))
    seen = set(); out: list[Path] = []
    for c in cands:
        try:
            if c and c.exists():
                s = str(c.resolve())
                if s not in seen: out.append(Path(s)); seen.add(s)
        except Exception: pass
    return out

def find_candidate_gcloud_config_dirs() -> list[Path]:
    cands: list[Path] = []
    s = load_settings(); p = s.get("gcloud_config_dir") or ""
    if p: cands.append(Path(p))
    cands.append(default_gcloud_config_dir_current_user())
    if is_windows():
        users_root = Path("C:/Users")
        try:
            for userdir in users_root.iterdir():
                cand = userdir / "AppData" / "Roaming" / "gcloud"
                if cand.exists(): cands.append(cand)
        except Exception: pass
    seen = set(); out: list[Path] = []
    for c in cands:
        try:
            if c.exists():
                s = str(c.resolve())
                if s not in seen: out.append(Path(s)); seen.add(s)
        except Exception: pass
    return out

def resolve_gcloud_cmd() -> str | None:
    for p in candidate_gcloud_paths():
        try:
            r = run([str(p), "--version"], check=False)
            if (r.stdout or "").strip():
                settings = load_settings()
                settings["gcloud_path"] = str(p); settings["gcloud_bin_dir"] = str(p.parent)
                save_settings(settings); return str(p)
        except Exception: continue
    return None

def gcloud_cmd_or_none() -> str | None:
    s = load_settings(); cached = s.get("gcloud_path")
    if cached and Path(cached).exists(): return cached
    return resolve_gcloud_cmd()

def gcloud_config_dir() -> Path:
    s = load_settings(); p = s.get("gcloud_config_dir")
    if p and Path(p).exists(): return Path(p)
    for cand in find_candidate_gcloud_config_dirs():
        try:
            if (cand / "configurations").exists() or (cand / "application_default_credentials.json").exists():
                settings = load_settings(); settings["gcloud_config_dir"] = str(cand); save_settings(settings)
                return cand
        except Exception: pass
    d = default_gcloud_config_dir_current_user()
    settings = load_settings(); settings["gcloud_config_dir"] = str(d); save_settings(settings)
    return d

def gcloud_env() -> dict:
    env = dict(os.environ); env["CLOUDSDK_CONFIG"] = str(gcloud_config_dir()); return env

def have_gcloud() -> bool: return gcloud_cmd_or_none() is not None

# ---------------------------- gcloud helpers ----------------------------
def gcloud_json(args: list[str]):
    cmd = gcloud_cmd_or_none()
    if not cmd: return None
    try:
        r = run([cmd] + args + ["--format=json"], check=True, env=gcloud_env())
        return json.loads(r.stdout or "null")
    except Exception:
        return None

def read_active_config_from_disk() -> str | None:
    ac = gcloud_config_dir() / "active_config"
    try:
        if ac.exists(): return ac.read_text(encoding="utf-8").strip()
    except Exception: pass
    return None

def read_props_from_config_file(cfg_name: str) -> tuple[str | None, str | None]:
    cfg_file = gcloud_config_dir() / "configurations" / ("config_" + cfg_name)
    proj = acct = None
    try:
        if cfg_file.exists():
            txt = cfg_file.read_text(encoding="utf-8")
            core = re.search(r"(?ms)^[core](.*?)(^[[]|\Z)", txt); block = core.group(1) if core else ""
            m_p = re.search(r"(?m)^\s*project\s*=\s*(.+)\s*$", block)
            m_a = re.search(r"(?m)^\s*account\s*=\s*(.+)\s*$", block)
            proj = m_p.group(1).strip() if m_p else None; acct = m_a.group(1).strip() if m_a else None
    except Exception: pass
    return (proj, acct)

def describe_project(id_or_number: str) -> tuple[str | None, str | None]:
    cmd = gcloud_cmd_or_none()
    if not cmd: return (None, None)
    try:
        r = run([cmd, "projects", "describe", id_or_number, "--format=value(projectId,projectNumber)"], check=False, env=gcloud_env())
        line = (r.stdout or "").strip()
        if line:
            parts = re.split(r"\s+", line)
            if len(parts) == 2: return (parts[0], parts[1])
    except Exception: pass
    try:
        r = run([cmd, "projects", "list", "--filter=projectId="+id_or_number, "--format=value(projectNumber)"], check=False, env=gcloud_env())
        num = (r.stdout or "").strip()
        if num: return (id_or_number, num)
    except Exception: pass
    return (None, None)

def sanitize_cfg_name(name: str) -> str:
    s = (name or "default").strip().lower()
    s = re.sub(r"[^a-z0-9-]", "-", s); s = re.sub(r"-+", "-", s).strip("-")
    if not s: s = "default"
    return s

def purge_gcloud_auth(account_hint: str | None, deep: bool) -> list[str]:
    logs: list[str] = []
    home = gcloud_config_dir()
    adc = home / "application_default_credentials.json"
    try:
        if adc.exists(): adc.unlink(); logs.append("Removed ADC: " + str(adc))
    except Exception as e: logs.append("ADC delete failed: " + str(e))
    leg_dir = home / "legacy_credentials"
    if deep:
        try:
            if leg_dir.exists(): shutil.rmtree(leg_dir, ignore_errors=True); logs.append("Removed legacy_credentials (all)")
        except Exception as e: logs.append("legacy_credentials delete failed: " + str(e))
    else:
        if account_hint:
            try:
                leg = leg_dir / account_hint
                if leg.exists(): shutil.rmtree(leg, ignore_errors=True); logs.append("Removed legacy_credentials for " + account_hint)
            except Exception as e: logs.append("legacy_credentials delete failed: " + str(e))
    if deep:
        for fname in ("credentials.db", "access_tokens.db"):
            try:
                f = home / fname
                if f.exists(): f.unlink(); logs.append("Removed " + fname)
            except Exception as e: logs.append(fname + " delete failed: " + str(e))
    if not logs: logs.append("Nothing to purge (at " + str(home) + ")")
    return logs

def clear_environment_variables() -> list[str]:
    logs: list[str] = []
    env_vars_to_clear = [
        "GOOGLE_API_KEY",
        "GEMINI_API_KEY",
        "GOOGLE_CLOUD_PROJECT",
        "GOOGLE_CLOUD_PROJECT_ID",
        "GCLOUD_PROJECT",
        "PROJECT_ID",
        "PROJECT_NUMBER",
    ]
    if is_windows():
        for var in env_vars_to_clear:
            try:
                windows_set_env(var, "", machine=False)
                logs.append(f"Cleared user env var: {var}")
            except Exception: pass
            try:
                windows_set_env(var, "", machine=True)
                logs.append(f"Cleared machine env var: {var}")
            except Exception: pass
    else:
        ensure_env_file({})
        logs.append("Cleared POSIX environment variables by creating an empty env file.")
    return logs

def ensure_gcloud_configuration(p: Profile, safe_revoke: bool) -> str:
    cmd = gcloud_cmd_or_none()
    if not cmd: return "gcloud not found; skipped gcloud configuration (env vars still applied)."
    logs: list[str] = []
    cfg = sanitize_cfg_name(p.name or "default")

    if safe_revoke:
        try: r = run([cmd, "auth", "revoke", "--all", "--quiet"], check=False, env=gcloud_env()); logs.append(r.stdout)
        except Exception as e: logs.append("revoke (user) failed: " + str(e))
        try: r = run([cmd, "auth", "application-default", "revoke", "--quiet"], check=False, env=gcloud_env()); logs.append(r.stdout)
        except Exception as e: logs.append("revoke (ADC) failed: " + str(e))

    try:
        r = run([cmd, "config", "configurations", "list", "--format=value(name)"], check=False, env=gcloud_env())
        configs = set((r.stdout or "").split())
    except Exception as e:
        configs = set(); logs.append("list configurations failed: " + str(e))

    if cfg not in configs:
        r = run([cmd, "config", "configurations", "create", cfg], check=False, env=gcloud_env())
        out = (r.stdout or "").strip()
        if r.returncode != 0 and "already exists" not in out.lower():
            logs.append("create config failed: " + out)

    r = run([cmd, "config", "configurations", "activate", cfg], check=False, env=gcloud_env())
    if r.returncode != 0: logs.append("activate config failed: " + (r.stdout or "").strip())
    else: logs.append(r.stdout)

    if p.gcloud_project:
        r = run([cmd, "config", "set", "project", p.gcloud_project], check=False, env=gcloud_env()); logs.append(r.stdout)
    
    if p.gcloud_service_account_key_file and Path(p.gcloud_service_account_key_file).exists():
        r = run([cmd, "auth", "activate-service-account", f"--key-file={p.gcloud_service_account_key_file}"], check=False, env=gcloud_env())
        logs.append(r.stdout)
        if r.returncode == 0 and p.gcloud_account:
            # After activating the service account, gcloud might not need the account explicitly set in the config.
            # We can try to set it, but it might be redundant.
            run([cmd, "config", "set", "account", p.gcloud_account], check=False, env=gcloud_env())
    elif p.gcloud_account:
        r = run([cmd, "config", "set", "account", p.gcloud_account], check=False, env=gcloud_env()); logs.append(r.stdout)
        try:
            r = run([cmd, "auth", "list", "--filter=status:ACTIVE", "--format=value(account)"], check=False, env=gcloud_env())
            active = (r.stdout or "").strip()
            if p.gcloud_account and active.lower() != p.gcloud_account.lower():
                r = run([cmd, "auth", "login", "--account", p.gcloud_account], check=False, env=gcloud_env()); logs.append(r.stdout)
        except Exception as e: logs.append("auth check failed: " + str(e))

        try:
            r = run([cmd, "auth", "application-default", "login"], check=False, env=gcloud_env()); logs.append(r.stdout)
        except Exception as e: logs.append("adc login failed: " + str(e))

    return "\n".join([x for x in logs if x])

def apply_single_variable(var_name: str, var_value: str, use_machine_env: bool) -> str:
    logs: list[str] = []
    env_values_to_set = {var_name: var_value}
    if var_name in ("GOOGLE_CLOUD_PROJECT", "GCLOUD_PROJECT", "PROJECT_ID"):
        env_values_to_set["GOOGLE_CLOUD_PROJECT"] = var_value
        env_values_to_set["GOOGLE_CLOUD_PROJECT_ID"] = var_value
        env_values_to_set["GCLOUD_PROJECT"] = var_value
        env_values_to_set["PROJECT_ID"] = var_value
        logs.append(f"Recognized {var_name} as a project ID alias; setting all related variables.")

    if is_windows():
        for var, val in env_values_to_set.items():
            try:
                windows_set_env(var, val or "", machine=use_machine_env)
                logs.append(f"Set {var} ({'machine' if use_machine_env else 'user'})")
            except Exception as e:
                logs.append(f"env set failed for {var}: {e}")
    else:
        current_vars = {}
        if ENV_FILE.exists():
            try:
                txt = ENV_FILE.read_text(encoding="utf-8")
                for line in txt.splitlines():
                    match = re.match(r"^\s*export\s+([^=]+)='(.*)'\s*$", line)
                    if match:
                        key = match.group(1)
                        if key not in env_values_to_set:
                            current_vars[key] = match.group(2)
            except Exception as e:
                logs.append(f"Failed to read existing env file: {e}")
        for var, val in env_values_to_set.items():
            current_vars[var] = val or ""
        ensure_env_file(current_vars)
        ensure_shell_rc_sources_env()
        logs.append("Updated POSIX env file and ensured shell rc sources it.")
    return "\n".join([x for x in logs if x])

# ---------------------------- Apply / Analyze ----------------------------
def apply_profile(p: Profile, use_machine_env: bool, safe_revoke: bool, add_gcloud_to_path: bool) -> str:
    p = p.normalized()
    logs: list[str] = clear_environment_variables()


    # Resolve ID/number if only one present
    if p.gcloud_project and not p.gcloud_project_number:
        pid, pnum = describe_project(p.gcloud_project); 
        if pid: p.gcloud_project = pid
        if pnum: p.gcloud_project_number = pnum
    elif p.gcloud_project_number and not p.gcloud_project:
        pid, pnum = describe_project(p.gcloud_project_number)
        if pid: p.gcloud_project = pid
        if pnum: p.gcloud_project_number = pnum

    env_values = {
        "GOOGLE_API_KEY": p.google_api_key,
        "GEMINI_API_KEY": p.gemini_api_key,
        "GOOGLE_CLOUD_PROJECT": p.gcloud_project,
        "GOOGLE_CLOUD_PROJECT_ID": p.gcloud_project,
        "GCLOUD_PROJECT": p.gcloud_project,
        "PROJECT_ID": p.gcloud_project,
        "PROJECT_NUMBER": p.gcloud_project_number,
    }

    if is_windows():
        for var, val in env_values.items():
            try: windows_set_env(var, val or "", machine=bool(use_machine_env)); logs.append(("set " + var + " (machine)" if use_machine_env else "set " + var + " (user)"))
            except Exception as e: logs.append("env set failed for " + var + ": " + str(e))
        if add_gcloud_to_path:
            d = load_settings().get("gcloud_bin_dir")
            if d:
                try: windows_add_to_path(d, machine=bool(use_machine_env)); logs.append("added to PATH: " + d)
                except Exception as e: logs.append("PATH update failed: " + str(e))
    else:
        ensure_env_file(env_values); ensure_shell_rc_sources_env()
        logs.append("wrote env file and ensured shell rc sources it")

    logs.append("gcloud config dir: " + str(gcloud_config_dir()))
    gp = gcloud_cmd_or_none()
    if gp: logs.append("gcloud exe: " + gp)
    logs.append(ensure_gcloud_configuration(p, safe_revoke=safe_revoke))
    return "\n".join([x for x in logs if x])

def analyze_current_setup() -> tuple[Profile, str | None, str]:
    gkey = os.environ.get("GOOGLE_API_KEY") or ""
    gekey = os.environ.get("GEMINI_API_KEY") or ""
    if is_windows():
        if not gkey:
            u, m = read_winenv("GOOGLE_API_KEY"); gkey = (u or m or "")
        if not gekey:
            u, m = read_winenv("GEMINI_API_KEY"); gekey = (u or m or "")
    else:
        if ENV_FILE.exists():
            try:
                txt = ENV_FILE.read_text(encoding="utf-8")
                m1 = re.search(r"export[ \t]+GOOGLE_API_KEY='([^']*)'", txt); gkey = gkey or (m1.group(1) if m1 else "")
                m2 = re.search(r"export[ \t]+GEMINI_API_KEY='([^']*)'", txt); gekey = gekey or (m2.group(1) if m2 else "")
            except Exception: pass

    gcloud_project = ""; gcloud_project_number = ""; gcloud_account = ""; active_cfg = None; gp = gcloud_cmd_or_none()
    if gp:
        info = gcloud_json(["info"]) or {}
        try:
            ac = info.get("config", {}).get("active_configuration")
            if isinstance(ac, str) and ac: active_cfg = ac
        except Exception: pass
        props = gcloud_json(["config", "list"]) or {}
        try:
            p = props.get("core", {}).get("project", ""); a = props.get("core", {}).get("account", "")
            gcloud_project = gcloud_project or p; gcloud_account = gcloud_account or a
        except Exception: pass
        if gcloud_project:
            pid, pnum = describe_project(gcloud_project)
            if pid: gcloud_project = pid
            if pnum and "ERROR" not in pnum: gcloud_project_number = pnum
        if not gcloud_account:
            try:
                r = run([gp, "auth", "list", "--filter=status:ACTIVE", "--format=value(account)"], check=False, env=gcloud_env())
                gcloud_account = (r.stdout or "").strip()
            except Exception: pass

    prof = Profile(
        name="Default (detected)",
        google_api_key=gkey,
        gemini_api_key=gekey if gekey else gkey,
        gcloud_project=gcloud_project,
        gcloud_project_number=gcloud_project_number,
        gcloud_account=gcloud_account,
        gcloud_service_account_key_file="",
    ).normalized()
    return prof, active_cfg, gp or ""

# ---------------------------- GUI ----------------------------
class App(tk.Tk):
    def __init__(self, store: ProfileStore):
        super().__init__()
        self.title("APISwitch"); self.geometry("1120x780"); self.resizable(True, True)
        self.store = store; self._selected_name: str | None = None; self._resolve_timer = None
        self._build(); self.refresh_list()

    def _build(self) -> None:
        self.columnconfigure(1, weight=1); self.rowconfigure(0, weight=1)
        sidebar = ttk.Frame(self); sidebar.grid(row=0, column=0, sticky="nsw", padx=12, pady=12)
        ttk.Label(sidebar, text="Profiles").pack(anchor="w")
        self.lb = tk.Listbox(sidebar, height=28, exportselection=False); self.lb.pack(fill="both", expand=True)
        self.lb.bind("<<ListboxSelect>>", self.on_select)
        btns = ttk.Frame(sidebar); btns.pack(fill="x", pady=(8, 0))
        ttk.Button(btns, text="New", command=self.on_new).pack(side="left")
        ttk.Button(btns, text="Delete", command=self.on_delete).pack(side="left", padx=6)
        ttk.Button(btns, text="Analyze Current Setup", command=self.on_analyze).pack(side="left", padx=6)

        form = ttk.Frame(self); form.grid(row=0, column=1, sticky="nsew", padx=12, pady=12)
        for i in range(3): form.columnconfigure(i, weight=1)

        self.var_name = tk.StringVar(); self.var_google = tk.StringVar(); self.var_gemini = tk.StringVar()
        self.var_sync_keys = tk.BooleanVar(value=True)
        self.var_show_keys = tk.BooleanVar(value=False)
        self.var_proj = tk.StringVar(); self.var_projnum = tk.StringVar(); self.var_acct = tk.StringVar()
        self.var_key_file = tk.StringVar()
        self.var_machine = tk.BooleanVar(value=False); self.var_safe = tk.BooleanVar(value=True)
        self.var_add_gcloud_to_path = tk.BooleanVar(value=True); self.var_deep_purge = tk.BooleanVar(value=False)

        row = 0
        ttk.Label(form, text="Profile name").grid(row=row, column=0, sticky="w")
        ttk.Entry(form, textvariable=self.var_name).grid(row=row, column=1, columnspan=2, sticky="ew"); row += 1
        ttk.Label(form, text="GOOGLE_API_KEY").grid(row=row, column=0, sticky="w")
        google_key_frame = ttk.Frame(form)
        google_key_frame.grid(row=row, column=1, columnspan=2, sticky="ew")
        self.google_key_entry = ttk.Entry(google_key_frame, textvariable=self.var_google, show="*")
        self.google_key_entry.pack(side="left", fill="x", expand=True)
        ttk.Button(google_key_frame, text="\u25B6", width=3, command=lambda: self.on_set_single_var("GOOGLE_API_KEY", self.var_google.get())).pack(side="left", padx=(4,0)); row += 1
        ttk.Label(form, text="GEMINI_API_KEY").grid(row=row, column=0, sticky="w")
        gemini_key_frame = ttk.Frame(form)
        gemini_key_frame.grid(row=row, column=1, columnspan=2, sticky="ew")
        self.gemini_key_entry = ttk.Entry(gemini_key_frame, textvariable=self.var_gemini, show="*")
        self.gemini_key_entry.pack(side="left", fill="x", expand=True)
        ttk.Button(gemini_key_frame, text="\u25B6", width=3, command=lambda: self.on_set_single_var("GEMINI_API_KEY", self.var_gemini.get())).pack(side="left", padx=(4,0)); row += 1
        
        key_opts_frame = ttk.Frame(form)
        key_opts_frame.grid(row=row, column=0, columnspan=3, sticky="w")
        ttk.Checkbutton(key_opts_frame, text="Keep keys in sync", variable=self.var_sync_keys, command=self._sync_keys_now).pack(side="left", anchor="w")
        ttk.Checkbutton(key_opts_frame, text="Show keys", variable=self.var_show_keys, command=self._toggle_key_visibility).pack(side="left", anchor="w", padx=12)
        row += 1
        ttk.Label(form, text="gcloud project ID").grid(row=row, column=0, sticky="w")
        proj_frame = ttk.Frame(form)
        proj_frame.grid(row=row, column=1, sticky="ew")
        ttk.Entry(proj_frame, textvariable=self.var_proj).pack(side="left", fill="x", expand=True)
        ttk.Button(proj_frame, text="\u25B6", width=3, command=lambda: self.on_set_single_var("GOOGLE_CLOUD_PROJECT", self.var_proj.get())).pack(side="left", padx=(4,0))
        ttk.Button(form, text="Resolve now", command=self.resolve_project_fields).grid(row=row, column=2, sticky="w"); row += 1
        ttk.Label(form, text="gcloud project number").grid(row=row, column=0, sticky="w")
        ttk.Entry(form, textvariable=self.var_projnum).grid(row=row, column=1, sticky="ew")
        ttk.Button(form, text="Resolve now", command=self.resolve_project_fields).grid(row=row, column=2, sticky="w"); row += 1
        ttk.Label(form, text="gcloud account (email)").grid(row=row, column=0, sticky="w")
        ttk.Entry(form, textvariable=self.var_acct).grid(row=row, column=1, columnspan=2, sticky="ew"); row += 1

        ttk.Label(form, text="Service Account Key File").grid(row=row, column=0, sticky="w")
        ttk.Entry(form, textvariable=self.var_key_file).grid(row=row, column=1, sticky="ew")
        ttk.Button(form, text="Browse...", command=self.on_browse_key_file).grid(row=row, column=2, sticky="w"); row += 1

        toggles = ttk.Frame(form); toggles.grid(row=row, column=0, columnspan=3, pady=(6, 6), sticky="w")
        ttk.Checkbutton(toggles, text="Windows: Machine-wide env vars (/M)", variable=self.var_machine).pack(side="left")
        ttk.Checkbutton(toggles, text="Safe switch (revoke creds first)", variable=self.var_safe).pack(side="left", padx=12)
        ttk.Checkbutton(toggles, text="Add detected gcloud bin to PATH", variable=self.var_add_gcloud_to_path).pack(side="left", padx=12)
        ttk.Checkbutton(toggles, text="Deep purge (all accounts)", variable=self.var_deep_purge).pack(side="left", padx=12)
        row += 1

        actions = ttk.Frame(form); actions.grid(row=row, column=0, columnspan=3, pady=8, sticky="ew")
        ttk.Button(actions, text="Save/Update", command=self.on_save).pack(side="left")
        ttk.Button(actions, text="Apply and Save", command=self.on_apply).pack(side="left", padx=8)
        ttk.Button(actions, text="Purge gcloud auth cache", command=self.on_purge).pack(side="left", padx=8)
        ttk.Button(actions, text="Open gcloud folder", command=self.on_open_gcloud_dir).pack(side="left", padx=8)
        ttk.Button(actions, text="Open profiles folder", command=self.on_open_profiles_dir).pack(side="left", padx=8)
        ttk.Button(actions, text="Locate gcloud...", command=self.on_locate_gcloud).pack(side="left", padx=8)
        ttk.Button(actions, text="Locate gcloud config dir...", command=self.on_locate_gcloud_config).pack(side="left", padx=8)

        self.txt = tk.Text(form, height=18); self.txt.grid(row=row + 1, column=0, columnspan=3, sticky="nsew")
        form.rowconfigure(row + 1, weight=1)

        footer = ttk.Frame(self); footer.grid(row=1, column=0, columnspan=2, sticky="ew", padx=12, pady=(0, 12))
        self.footer_label = ttk.Label(footer, text="Profiles: " + str(PROFILES_FILE) + " | Settings: " + str(SETTINGS_FILE) + " | gcloud config dir: " + str(gcloud_config_dir()))
        self.footer_label.pack(anchor="w")

        # Debounced auto-resolve on edits
        def on_change(*_):
            if self._resolve_timer is not None: self.after_cancel(self._resolve_timer)
            self._resolve_timer = self.after(600, self.resolve_project_fields)
        self.var_proj.trace_add("write", lambda *_: on_change())
        self.var_projnum.trace_add("write", lambda *_: on_change())

    def on_browse_key_file(self):
        path = filedialog.askopenfilename(
            title="Select Service Account Key File",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")]
        )
        if path:
            self.var_key_file.set(path)

    def _toggle_key_visibility(self):
        show = "" if self.var_show_keys.get() else "*"
        self.google_key_entry.config(show=show)
        self.gemini_key_entry.config(show=show)

    def _sync_keys_now(self):
        if self.var_sync_keys.get(): self.var_gemini.set(self.var_google.get())

    def refresh_list(self) -> None:
        self.lb.delete(0, tk.END)
        for name in self.store.names(): self.lb.insert(tk.END, name)
        self._selected_name = None

    def on_select(self, _event=None) -> None:
        sel = self.lb.curselection()
        if not sel: self._selected_name = None; return
        name = self.lb.get(sel[0]); p = self.store.get(name)
        if not p: return
        self._selected_name = name; p = p.normalized()
        self.var_name.set(p.name); self.var_google.set(p.google_api_key); self.var_gemini.set(p.gemini_api_key)
        self.var_proj.set(p.gcloud_project); self.var_projnum.set(p.gcloud_project_number); self.var_acct.set(p.gcloud_account)
        self.var_key_file.set(p.gcloud_service_account_key_file)

    def on_new(self) -> None:
        self._selected_name = None
        self.var_name.set(""); self.var_google.set(""); self.var_gemini.set("")
        self.var_proj.set(""); self.var_projnum.set(""); self.var_acct.set(""); self.var_key_file.set("")
        self.lb.selection_clear(0, tk.END)

    def on_delete(self) -> None:
        sel = self.lb.curselection()
        if not sel: return
        name = self.lb.get(sel[0])
        if messagebox.askyesno("Delete", "Delete profile '" + name + "'?"):
            self.store.delete(name); self.refresh_list(); self.on_new()

    def _collect_profile_from_form(self) -> Profile:
        return Profile(
            name=self.var_name.get(),
            google_api_key=self.var_google.get(),
            gemini_api_key=self.var_gemini.get(),
            gcloud_project=self.var_proj.get(),
            gcloud_project_number=self.var_projnum.get(),
            gcloud_account=self.var_acct.get(),
            gcloud_service_account_key_file=self.var_key_file.get(),
        ).normalized()

    def on_set_single_var(self, var_name: str, var_value: str):
        self.txt.delete("1.0", tk.END)
        self.txt.insert(tk.END, f"Applying single variable: {var_name}...\n\n"); self.update_idletasks()
        use_machine_env = bool(self.var_machine.get())
        def worker():
            try:
                logs = apply_single_variable(var_name, var_value, use_machine_env)
                def update_gui():
                    self.txt.insert(tk.END, logs + "\n\nDone. Open a NEW terminal to use the updated credentials.\n")
                self.after(0, update_gui)
            except Exception as e:
                def update_gui_error():
                    self.txt.insert(tk.END, "Error: " + str(e) + "\n")
                self.after(0, update_gui_error)
        threading.Thread(target=worker, daemon=True).start()

    def on_save(self) -> None:
        try:
            p = self._collect_profile_from_form()
            if self._selected_name and self._selected_name != p.name: self.store.delete(self._selected_name)
            self.store.upsert(p); self.refresh_list()
            try: idx = self.store.names().index(p.name); self.lb.selection_set(idx); self._selected_name = p.name
            except Exception: pass
            messagebox.showinfo("Saved", "Profile '" + p.name + "' saved.")
        except Exception as e:
            messagebox.showerror("Error", str(e))

    def on_apply(self) -> None:
        self.on_save()
        try:
            p = self._collect_profile_from_form()
            if not p.name or not (p.google_api_key or p.gemini_api_key):
                raise ValueError("Profile name and at least one API key are required")
        except Exception as e:
            messagebox.showerror("Error", str(e)); return
        self.txt.delete("1.0", tk.END); self.txt.insert(tk.END, "Applying profile...\n\n"); self.update_idletasks()
        use_machine_env = bool(self.var_machine.get()); safe_revoke = bool(self.var_safe.get())
        add_to_path = bool(self.var_add_gcloud_to_path.get())
        def worker():
            try:
                logs = apply_profile(p, use_machine_env=use_machine_env, safe_revoke=safe_revoke, add_gcloud_to_path=add_to_path)
                gp = gcloud_cmd_or_none()

                # Write commands to a temporary file for the PowerShell wrapper to source
                if is_windows():
                    temp_dir = Path(os.environ.get("TEMP", str(Path.home() / "AppData" / "Local" / "Temp")))
                    temp_file = temp_dir / "apiswitch_apply.ps1"
                    commands = []
                    if p.google_api_key: commands.append(f'$env:GOOGLE_API_KEY="{p.google_api_key}"')
                    if p.gemini_api_key: commands.append(f'$env:GEMINI_API_KEY="{p.gemini_api_key}"')
                    if p.gcloud_project: commands.append(f'$env:GOOGLE_CLOUD_PROJECT="{p.gcloud_project}"')
                    # Also set the gcloud config for the wrapper's context if needed
                    commands.append(f'$env:CLOUDSDK_CONFIG="{str(gcloud_config_dir())}"')
                    temp_file.write_text("\n".join(commands), encoding="utf-8")

                def update_gui():
                    self.txt.insert(tk.END, "gcloud exe: " + (gp or "(not found)") + "\n")
                    self.txt.insert(tk.END, "CLOUDSDK_CONFIG: " + str(gcloud_config_dir()) + "\n\n")
                    self.txt.insert(tk.END, logs + "\n\n")
                    self.txt.insert(tk.END, "#"*80 + "\n")
                    self.txt.insert(tk.END, "# Profile saved and ready to be applied.                             #\n")
                    self.txt.insert(tk.END, "# Close this window to apply the settings to your current terminal. #\n")
                    self.txt.insert(tk.END, "#"*80 + "\n")

                self.after(0, update_gui)
            except Exception as e:
                def update_gui_error():
                    self.txt.insert(tk.END, "Error: " + str(e) + "\n")
                self.after(0, update_gui_error)
        threading.Thread(target=worker, daemon=True).start()




    def on_analyze(self) -> None:
        self.txt.delete("1.0", tk.END); self.txt.insert(tk.END, "Analyzing current setup...\n\n"); self.update_idletasks()
        def worker():
            prof, active_cfg, gp = analyze_current_setup()
            def update_gui():
                self.var_name.set(prof.name); self.var_google.set(prof.google_api_key); self.var_gemini.set(prof.gemini_api_key)
                self.var_proj.set(prof.gcloud_project); self.var_projnum.set(prof.gcloud_project_number); self.var_acct.set(prof.gcloud_account)
                try: self.store.upsert(prof)
                except Exception: pass
                self.refresh_list()
                try: idx = self.store.names().index(prof.name); self.lb.selection_set(idx); self._selected_name = prof.name
                except Exception: pass
                self.txt.insert(tk.END, "Detected GOOGLE_API_KEY: " + ("********" if prof.google_api_key else "(none)") + "\n")
                self.txt.insert(tk.END, "Detected GEMINI_API_KEY: " + ("********" if prof.gemini_api_key else "(none)") + "\n")
                if active_cfg: self.txt.insert(tk.END, "Active gcloud config: " + active_cfg + "\n")
                self.txt.insert(tk.END, "gcloud exe: " + (gp or "(not found)") + "\n")
                self.txt.insert(tk.END, "CLOUDSDK_CONFIG: " + str(gcloud_config_dir()) + "\n")
                self.txt.insert(tk.END, "Active account: " + (prof.gcloud_account or "(unknown)") + "\n")
                self.txt.insert(tk.END, "Active project ID: " + (prof.gcloud_project or "(unknown)") + "\n")
                self.txt.insert(tk.END, "Active project number: " + (prof.gcloud_project_number or "(unknown)") + "\n")
                self.txt.insert(tk.END, "\nProfiles: " + str(PROFILES_FILE) + "\nSettings: " + str(SETTINGS_FILE) + "\n")
                self.txt.insert(tk.END, "gcloud config dir: " + str(gcloud_config_dir()) + "\n")
            self.after(0, update_gui)
        threading.Thread(target=worker, daemon=True).start()

    def on_purge(self) -> None:
        acc = (self.var_acct.get() or "").strip(); deep = bool(self.var_deep_purge.get())
        logs = purge_gcloud_auth(acc if acc else None, deep=deep)
        messagebox.showinfo("Purge", "\n".join(logs))
        self.txt.insert(tk.END, "[Purge]\n" + "\n".join(logs) + "\n")

    def on_open_gcloud_dir(self) -> None:
        path = gcloud_config_dir()
        try:
            if is_windows(): os.startfile(str(path))  # type: ignore
            elif sys.platform == "darwin": subprocess.run(["open", str(path)])
            else: subprocess.run(["xdg-open", str(path)])
        except Exception as e: messagebox.showerror("Error", str(e))

    def on_open_profiles_dir(self) -> None:
        path = PROFILES_FILE.parent
        try:
            if is_windows(): os.startfile(str(path))  # type: ignore
            elif sys.platform == "darwin": subprocess.run(["open", str(path)])
            else: subprocess.run(["xdg-open", str(path)])
        except Exception as e: messagebox.showerror("Error", str(e))

    def on_locate_gcloud(self) -> None:
        title = "Select gcloud executable (gcloud.cmd on Windows)"
        filetypes = [("gcloud.cmd", "gcloud.cmd"), ("gcloud.exe", "gcloud.exe"), ("All files", "*.*")] if is_windows() else [("gcloud", "gcloud"), ("All files", "*.*")]
        path = filedialog.askopenfilename(title=title, filetypes=filetypes)
        if path:
            s = load_settings(); s["gcloud_path"] = path; s["gcloud_bin_dir"] = str(Path(path).parent); save_settings(s)
            self.txt.insert(tk.END, "Saved gcloud path: " + path + "\n")

    def on_locate_gcloud_config(self) -> None:
        title = "Select gcloud config directory (contains 'configurations' folder)"
        if is_windows():
            initial = str(default_gcloud_config_dir_current_user())
            path = filedialog.askdirectory(title=title, initialdir=initial)
        else:
            path = filedialog.askdirectory(title=title)
        if path:
            s = load_settings(); s["gcloud_config_dir"] = path; save_settings(s)
            self.footer_label.config(text="Profiles: " + str(PROFILES_FILE) + " | Settings: " + str(SETTINGS_FILE) + " | gcloud config dir: " + path)
            self.txt.insert(tk.END, "Saved gcloud config dir: " + path + "\n")

    def resolve_project_fields(self) -> None:
        pid = (self.var_proj.get() or "").strip(); pnum = (self.var_projnum.get() or "").strip()
        if not gcloud_cmd_or_none(): return
        if not pid and not pnum: return
        def worker():
            try:
                target = pid if pid else pnum
                rid, rnum = describe_project(target)
                def update_gui():
                    if rid and not pid: self.var_proj.set(rid)
                    if rnum and not pnum: self.var_projnum.set(rnum)
                self.after(0, update_gui)
            except Exception: pass
        threading.Thread(target=worker, daemon=True).start()

# ---------------------------- Main ----------------------------
def main() -> None:
    relaunch_as_admin_if_needed()
    store = ProfileStore(PROFILES_FILE)
    app = App(store)
    app.mainloop()

if __name__ == "__main__":
    main()