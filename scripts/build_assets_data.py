"""Regenerate src/tutnext/assets_data/*.py from the JSON files in src/tutnext/data/.

Cloudflare Python Workers only upload ``*.py`` modules, so the static data files
are embedded as Python modules for the Worker build.

Usage: uv run python scripts/build_assets_data.py
"""
import json
import pprint
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "src" / "tutnext"


def main() -> None:
    teachers = json.loads((ROOT / "data" / "teachers.json").read_text(encoding="utf-8"))
    bus = json.loads((ROOT / "data" / "bus_data.json").read_text(encoding="utf-8"))
    out = ROOT / "assets_data"
    out.mkdir(exist_ok=True)
    (out / "teachers_data.py").write_text(
        "# Generated from data/teachers.json — regenerate with scripts/build_assets_data.py\n"
        "TEACHERS: list[dict] = " + pprint.pformat(teachers, width=110, sort_dicts=False) + "\n",
        encoding="utf-8",
    )
    (out / "bus_data_default.py").write_text(
        "# Generated from data/bus_data.json — baseline timetable used until the weekly scraper stores a newer one.\n"
        "# Regenerate with scripts/build_assets_data.py\n"
        "BUS_DATA: dict = " + pprint.pformat(bus, width=110, sort_dicts=False) + "\n",
        encoding="utf-8",
    )
    print(f"wrote {out / 'teachers_data.py'} and {out / 'bus_data_default.py'}")


if __name__ == "__main__":
    main()
