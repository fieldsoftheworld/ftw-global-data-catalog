import re
from pathlib import Path

HERE = Path(__file__).resolve().parent


def test_launch_fills_every_field_of_the_user_data_templates():
    filled = set(re.findall(r"s\|__([A-Z_]+)__\|", (HERE / "launch.sh").read_text()))
    for name in ("bootstrap.sh", "downstream.sh"):
        fields = set(re.findall(r"__([A-Z][A-Z_]+)__", (HERE / name).read_text()))
        assert fields, name
        assert fields <= filled, f"{name}: {sorted(fields - filled)} not filled by launch.sh"
