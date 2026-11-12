"""Núcleo compartilhado: configuração, logs, estado do projeto (retomada) e utilitários FFmpeg."""
from __future__ import annotations

import copy
import json
import logging
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

SKILL_ROOT = Path(__file__).resolve().parents[2]
ASSETS = SKILL_ROOT / "assets"
FONTS_DIR = ASSETS / "fonts"

PROJECT_DIRS = ["video_original", "transcricao", "analise", "cortes", "relatorios", "tmp", "logs"]


class PipelineError(RuntimeError):
    """Erro de etapa do pipeline com mensagem acionável."""


# ----------------------------------------------------------------- config
def deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_config(project: Path | None = None, extra: Path | None = None) -> dict:
    cfg = json.loads((SKILL_ROOT / "config" / "default.json").read_text(encoding="utf-8"))
    if project and (project / "config.json").exists():
        cfg = deep_merge(cfg, json.loads((project / "config.json").read_text(encoding="utf-8")))
    if extra:
        cfg = deep_merge(cfg, json.loads(Path(extra).read_text(encoding="utf-8")))
    return cfg


# ----------------------------------------------------------------- logs
def setup_logging(project: Path, verbose: bool = False) -> logging.Logger:
    (project / "logs").mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("sce")
    logger.setLevel(logging.DEBUG)
    logger.handlers.clear()
    fh = logging.FileHandler(project / "logs" / "pipeline.log", encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s [%(module)s] %(message)s"))
    sh = logging.StreamHandler()
    sh.setLevel(logging.DEBUG if verbose else logging.INFO)
    sh.setFormatter(logging.Formatter("%(levelname)-7s %(message)s"))
    logger.addHandler(fh)
    logger.addHandler(sh)
    return logger


log = logging.getLogger("sce")


# ----------------------------------------------------------------- json io
def read_json(path: Path, default: Any = None) -> Any:
    path = Path(path)
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data: Any) -> None:
    """Escrita atômica (tmp + rename) para não corromper checkpoints se o processo cair."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".part")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


# ----------------------------------------------------------------- state
class ProjectState:
    """Checkpoint de etapas em <projeto>/state.json — permite retomar tarefas interrompidas."""

    def __init__(self, project: Path):
        self.project = Path(project)
        self.path = self.project / "state.json"
        self.data = read_json(self.path, {"stages": {}, "meta": {}, "llm_usage": {"usd": 0.0, "calls": 0}})

    def save(self) -> None:
        write_json(self.path, self.data)

    def done(self, stage: str) -> bool:
        return self.data["stages"].get(stage, {}).get("status") == "done"

    def mark(self, stage: str, status: str, **info) -> None:
        self.data["stages"][stage] = {"status": status, "at": time.strftime("%Y-%m-%dT%H:%M:%S"), **info}
        self.save()

    def reset_from(self, stages_order: list[str], stage: str) -> None:
        if stage not in stages_order:
            return
        for s in stages_order[stages_order.index(stage):]:
            self.data["stages"].pop(s, None)
        self.save()

    @property
    def meta(self) -> dict:
        return self.data["meta"]


# ----------------------------------------------------------------- ffmpeg
def require_binaries() -> None:
    missing = [b for b in ("ffmpeg", "ffprobe") if shutil.which(b) is None]
    if missing:
        raise PipelineError(f"Binários ausentes: {missing}. Instale o FFmpeg (veja README.md).")
    out = subprocess.run(["ffmpeg", "-hide_banner", "-filters"], capture_output=True, text=True).stdout
    for f in ("ass", "loudnorm", "sidechaincompress", "acompressor"):
        if f" {f} " not in out:
            raise PipelineError(f"Seu FFmpeg não tem o filtro '{f}'. Use um build completo (libass incluído).")


def run(cmd: list[str], desc: str = "", check: bool = True, capture: bool = True,
        timeout: float | None = None) -> subprocess.CompletedProcess:
    log.debug("RUN %s: %s", desc, " ".join(map(str, cmd))[:2000])
    t0 = time.time()
    p = subprocess.run(list(map(str, cmd)), capture_output=capture, text=True, timeout=timeout)
    log.debug("DONE %s em %.1fs (rc=%s)", desc, time.time() - t0, p.returncode)
    if check and p.returncode != 0:
        tail = (p.stderr or "")[-3000:]
        raise PipelineError(f"Falha em '{desc}' (rc={p.returncode}):\n{tail}")
    return p


def ffprobe_json(path: Path) -> dict:
    p = run(["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", path],
            desc="ffprobe")
    return json.loads(p.stdout)


def parse_rate(r: str | None) -> float:
    if not r or r in ("0/0", "N/A"):
        return 0.0
    if "/" in r:
        a, b = r.split("/")
        return float(a) / float(b) if float(b) else 0.0
    return float(r)


def fmt_ts(sec: float) -> str:
    sec = max(0.0, float(sec))
    h = int(sec // 3600)
    m = int((sec % 3600) // 60)
    s = sec - h * 3600 - m * 60
    return f"{h:02d}:{m:02d}:{s:06.3f}"


def font_path(name: str) -> str:
    p = FONTS_DIR / name
    if p.exists():
        return str(p)
    raise PipelineError(f"Fonte não encontrada: {p}")
