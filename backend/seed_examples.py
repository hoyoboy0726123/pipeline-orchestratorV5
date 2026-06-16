"""安裝後預設範例工作流的 seed。

第一次啟動(全新安裝)時,把 backend/examples/*.yaml 載入成工作流,
讓使用者打開畫布就看得到可直接參考 / 執行的範例。

用一個 marker 檔(DB 同目錄的 .examples_seeded)記住「已 seed 過」,
之後即使使用者把範例刪掉也不會重生 —— 只在全新安裝那一次出現。
"""
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_EXAMPLES_DIR = Path(__file__).parent / "examples"


def _marker_path() -> Path:
    """marker 檔放在 DB 同一個目錄。"""
    from db import DB_PATH
    return Path(DB_PATH).parent / ".examples_seeded"


def _seed_recipes_and_assets(wf_id: str, wf_name: str, yaml_content: str, seed_file: Path) -> None:
    """為「附 sidecar 的範例」灌入預烤好的確定性步 recipe + 複製範例輸入檔。

    sidecar(`<stem>.seed.json`)格式:
      {"recipes":[{step_name, input_fingerprints, output_path, python_version, code}, ...],
       "assets":[{src, dest}, ...]}

    - recipe 的 task_hash 一律「**從本機載入的 YAML 解析後 batch 重算**」(_sha1),
      確保跟 runner 算的 key 一致、不依賴打包當下的 hash。
    - input_fingerprints 直接用 sidecar 的值(schema 級、與機器/內容無關;
      match_recipe 比的是值、忽略 path key)。
    - assets 複製到該工作流輸出夾 <OUTPUT_BASE_PATH>/<wf_name>/;已存在則不覆蓋
      (避免蓋掉使用者換上的新輸入檔)。
    全程 non-fatal。
    """
    try:
        import json as _json
        import yaml as _yaml
        import db as _db
        from pipeline.recipe import _sha1
        from config import OUTPUT_BASE_PATH

        spec = _json.loads(seed_file.read_text(encoding="utf-8"))
        parsed = _yaml.safe_load(yaml_content) or {}
        batch_by_name = {s.get("name"): (s.get("batch") or "")
                         for s in (parsed.get("steps") or [])}

        for rec in spec.get("recipes", []):
            step_name = rec["step_name"]                      # e.g. "1:transcribe"
            bare = step_name.split(":", 1)[1] if ":" in step_name else step_name
            task_hash = _sha1(batch_by_name.get(bare, ""))    # 從本機 YAML 重算
            _db.save_recipe(
                wf_id, step_name, task_hash,
                rec.get("input_fingerprints") or {},
                rec.get("output_path"),
                rec.get("code") or "",
                rec.get("python_version") or "",
                0.0, was_interactive=False,
            )
        if spec.get("recipes"):
            logger.info(f"[seed] {wf_name}:已灌 {len(spec['recipes'])} 個確定性步 recipe")

        wf_dir = Path(OUTPUT_BASE_PATH) / wf_name
        for asset in spec.get("assets", []):
            src = seed_file.parent / asset["src"]
            dst = wf_dir / asset["dest"]
            if not src.exists():
                logger.warning(f"[seed] 範例輸入檔不存在、略過:{src}")
                continue
            if dst.exists():
                continue                                       # 不覆蓋使用者既有檔
            wf_dir.mkdir(parents=True, exist_ok=True)
            import shutil as _shutil
            _shutil.copy2(src, dst)
            logger.info(f"[seed] {wf_name}:已放入範例輸入檔 {asset['dest']}")
    except Exception as e:
        logger.warning(f"[seed] {wf_name} 的 recipe/asset seed 失敗(略過):{e}")


def seed_example_workflows() -> None:
    """全新安裝時 seed 範例工作流。已 seed 過 / 出錯都安靜略過(non-fatal)。"""
    try:
        marker = _marker_path()
        if marker.exists():
            return
        if not _EXAMPLES_DIR.is_dir():
            return

        import yaml as _yaml
        from yaml_to_canvas import yaml_to_canvas
        from db import create_workflow, update_workflow, list_workflows

        existing = {(w.get("name") or "").strip() for w in (list_workflows() or [])}
        seeded = 0
        # 範例 YAML 裡的路徑佔位符 → 換成這台安裝環境的實際路徑。
        # __PROJECT_ROOT__ = 專案根;__PYTHON__ = 跑後端的 Python(venv,
        # 已裝好 pandas / openpyxl 等套件,確保財務腳本範例能直接跑)。
        import sys
        _root = Path(__file__).resolve().parent.parent
        _subst = {
            "__PROJECT_ROOT__": str(_root).replace("\\", "/"),
            "__PYTHON__": str(Path(sys.executable)).replace("\\", "/"),
        }

        for yf in sorted(_EXAMPLES_DIR.glob("*.yaml")):
            try:
                yaml_content = yf.read_text(encoding="utf-8")
                for _ph, _val in _subst.items():
                    yaml_content = yaml_content.replace(_ph, _val)
                parsed = _yaml.safe_load(yaml_content) or {}
                wf_name = (parsed.get("name") or yf.stem).strip()
                if wf_name in existing:
                    continue
                canvas = yaml_to_canvas(yaml_content)
                wf = create_workflow(name=wf_name, canvas=canvas, validate=False)
                update_workflow(wf["id"], {"yaml": yaml_content, "canvas": canvas})
                seeded += 1
                logger.info(f"[seed] 已建立範例工作流:{wf_name}")
                # 若該範例附帶 sidecar(預烤好的確定性步 recipe + 範例輸入檔)→ 一併 seed
                seed_file = yf.with_suffix(".seed.json")
                if seed_file.exists():
                    _seed_recipes_and_assets(wf["id"], wf_name, yaml_content, seed_file)
            except Exception as e:
                logger.warning(f"[seed] 範例 {yf.name} seed 失敗(略過):{e}")

        # 即使 seeded=0(範例都已存在)也寫 marker,避免每次啟動都掃
        marker.write_text("seeded", encoding="utf-8")
        if seeded:
            print(f"✅ 已建立 {seeded} 個預設範例工作流")
    except Exception as e:
        logger.warning(f"[seed] 範例工作流 seed 流程失敗(忽略):{e}")
