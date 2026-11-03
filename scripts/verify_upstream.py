"""Re-run supplied stage7 checks in isolation; preserve every delivered file."""

import argparse
import csv
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from hashlib import sha256


parser = argparse.ArgumentParser()
parser.add_argument("--root", default="external/perception_delivery")
parser.add_argument("--out", required=True)
parser.add_argument("--processed", help="Reproduce stages 3,5,6,7 from this original processed directory")
args = parser.parse_args()
source, output = Path(args.root).resolve(), Path(args.out).resolve()
if output.exists():
    parser.error("output exists; use a new path")
with tempfile.TemporaryDirectory(prefix="upstream-verification-") as temp:
    work = Path(temp)
    shutil.copytree(source/"pipeline", work/"pipeline")
    shutil.copytree(source/"evaluation_labels", work/"evaluation_labels")
    if args.processed:
        # Override only runtime path selectors; supplied source files stay byte-identical.
        runner = """
import sys
from pathlib import Path
sys.path.insert(0, str(Path(sys.argv[1])/'pipeline'))
import stage3, stage5, stage6, stage7
raw = Path(sys.argv[2])
stage3.main(raw, stage3.ROOT/'outputs/stage3')
stage5.main(raw, stage3.ROOT/'outputs/stage5')
stage6.DEFAULT_SOURCE = raw
stage6.main()
stage7.main()
"""
        subprocess.run([sys.executable, "-c", runner, str(work), str(Path(args.processed).resolve())], cwd=work, check=True)
    else:
        for name in ("stage3", "stage5", "stage6"):
            shutil.copytree(source/"outputs"/name, work/"outputs"/name)
        subprocess.run([sys.executable, str(work/"pipeline/stage7.py")], cwd=work, check=True)
    report = json.loads((work/"outputs/stage7/evaluation_report.json").read_text())
    if report["consistency_validation"] != "PASS" or not all(c["passed"] for c in report["checks"]):
        raise SystemExit("upstream consistency checks failed")
    output.parent.mkdir(parents=True, exist_ok=True)
    if args.processed:
        comparisons = []
        for stage in ("stage3", "stage5", "stage6"):
            for original in sorted((source/"outputs"/stage).iterdir()):
                if not original.is_file():
                    continue
                generated = work/"outputs"/stage/original.name
                byte_equal = generated.exists() and original.read_bytes() == generated.read_bytes()
                content_equal = byte_equal
                if generated.exists() and original.suffix == ".json":
                    content_equal = json.loads(original.read_text(encoding="utf-8")) == json.loads(generated.read_text(encoding="utf-8"))
                elif generated.exists() and original.suffix == ".csv":
                    with original.open(encoding="utf-8-sig", newline="") as a, generated.open(encoding="utf-8-sig", newline="") as b:
                        content_equal = list(csv.reader(a)) == list(csv.reader(b))
                comparisons.append({"file": f"{stage}/{original.name}", "byte_identical": byte_equal, "content_equal": content_equal,
                                    "original_sha256": sha256(original.read_bytes()).hexdigest(),
                                    "reproduced_sha256": sha256(generated.read_bytes()).hexdigest() if generated.exists() else None})
        shutil.copytree(work/"outputs", output)
        (output/"reproduction_report.json").write_text(json.dumps({"comparisons": comparisons,
            "all_byte_identical": all(c["byte_identical"] for c in comparisons),
            "all_content_equal": all(c["content_equal"] for c in comparisons),
            "comparison": "JSON values and CSV fields compared exactly; serialization/newline differences reported separately."}, indent=2)+"\n")
        if not all(c["content_equal"] for c in comparisons):
            raise SystemExit("Reproduction differs from delivered files; inspect reproduction_report.json")
        print(f"Reproduced {len(comparisons)} stage3/5/6 files with exactly equal structured contents.")
    else:
        shutil.copytree(work/"outputs/stage7", output)
    print(f"Verified {len(report['checks'])} supplied checks; outputs saved to {output}. No original files changed.")
