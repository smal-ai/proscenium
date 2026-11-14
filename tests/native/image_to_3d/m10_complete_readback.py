"""One independent read-only inspection after actual model publication and native previews."""

import json

import m10_continue_final_readback as retained
from m10_complete_snapshot import ROOT


def main():
    retained.FINAL = ROOT
    retained.main()
    value = json.loads((ROOT / "readback.json").read_text())
    assert abs(value["v2"]["height_m"] - 0.12) < 1e-5
    assert abs(value["v2"]["base_z_m"]) < 1e-5
    assert "M10_Blue_Ceramic" in value["v2"]["materials"]


if __name__ == "__main__":
    main()
