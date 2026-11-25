#!/usr/bin/env python3
"""Build/test the guarded Store DEB in the existing native Linux ARM64 builder."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import tempfile


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir",required=True,type=Path)
    args=parser.parse_args()
    if platform.system()!="Linux" or platform.machine() not in {"aarch64","arm64"}:
        parser.error("native Linux ARM64 builder required")
    if subprocess.check_output(["dpkg","--print-architecture"],text=True).strip()!="arm64":
        parser.error("native Debian ARM64 required")
    source=Path(__file__).resolve().parent;output=args.output_dir.resolve();output.mkdir(parents=True,exist_ok=True)
    env=os.environ.copy();epoch=env.setdefault("SOURCE_DATE_EPOCH","1794528000")
    if not epoch.isdigit():parser.error("SOURCE_DATE_EPOCH must be a nonnegative integer")
    env.update(TZ="UTC",LC_ALL="C.UTF-8",PYTHONDONTWRITEBYTECODE="1")
    def run(command):return subprocess.run(command,check=True,env=env)
    with tempfile.TemporaryDirectory(prefix=".store-build-",dir=output) as temporary:
        build=Path(temporary)
        run(["cmake","-S",str(source),"-B",str(build),"-G","Ninja","-DCMAKE_BUILD_TYPE=Release","-DBUILD_TESTING=ON"])
        run(["cmake","--build",str(build),"--parallel","4"])
        result=subprocess.run(["ctest","--test-dir",str(build),"--output-on-failure"],env=env,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True)
        (output/"store-tests.txt").write_text(result.stdout);print(result.stdout,end="");result.check_returncode()
        detail=build/"Testing/Temporary/LastTest.log"
        if detail.exists():(output/"store-test-detail.txt").write_bytes(detail.read_bytes())
        run(["cpack","--config",str(build/"CPackConfig.cmake"),"-G","DEB","-B",str(output)])
    deb=output/"tyrell-zero-store_0.1.1-1_arm64.deb"
    manifest={"package":"tyrell-zero-store","version":"0.1.1-1","architecture":"arm64",
              "source_date_epoch":int(epoch),"store_source":"1ceeaed715766d336b1a885f58d03d5e4c33d6fa",
              "original_vendor_sha256":"cb1a9db6224c90e3b4efdfd6da2936aa4d6e535c40fede64517ba7b09aa661aa",
              "deb_sha256":hashlib.sha256(deb.read_bytes()).hexdigest(),"service_activation_performed":False,
              "sources":{str(p.relative_to(source)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(source.rglob("*"))
                         if p.is_file() and "__pycache__" not in p.parts and p.suffix!=".pyc"}}
    (output/"store-build-manifest.json").write_text(json.dumps(manifest,indent=2,sort_keys=True)+"\n")
    return 0


if __name__=="__main__":raise SystemExit(main())
