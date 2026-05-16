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
        for yf in sorted(_EXAMPLES_DIR.glob("*.yaml")):
            try:
                yaml_content = yf.read_text(encoding="utf-8")
                parsed = _yaml.safe_load(yaml_content) or {}
                wf_name = (parsed.get("name") or yf.stem).strip()
                if wf_name in existing:
                    continue
                canvas = yaml_to_canvas(yaml_content)
                wf = create_workflow(name=wf_name, canvas=canvas, validate=False)
                update_workflow(wf["id"], {"yaml": yaml_content, "canvas": canvas})
                seeded += 1
                logger.info(f"[seed] 已建立範例工作流:{wf_name}")
            except Exception as e:
                logger.warning(f"[seed] 範例 {yf.name} seed 失敗(略過):{e}")

        # 即使 seeded=0(範例都已存在)也寫 marker,避免每次啟動都掃
        marker.write_text("seeded", encoding="utf-8")
        if seeded:
            print(f"✅ 已建立 {seeded} 個預設範例工作流")
    except Exception as e:
        logger.warning(f"[seed] 範例工作流 seed 流程失敗(忽略):{e}")
