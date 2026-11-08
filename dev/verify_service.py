#!/usr/bin/env python3
"""Run the public contract suite against isolated schemas on local PostgreSQL."""

import argparse
import os
from pathlib import Path
import subprocess
import sys

from service_stack import StackError, load_runtime, runtime_path


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-file",required=True)
    args=parser.parse_args()
    try:
        config=load_runtime(runtime_path(args.runtime_file),(55432,57474,57687))
    except StackError as error:
        print(str(error));return 1
    environment=os.environ.copy()
    environment["TEST_DATABASE_URL"]=config["DATABASE_URL"]
    # Every DB test owns a random schema. No private data or runtime secrets
    # enter arguments, output, public fixtures or a test artifact.
    return subprocess.run([sys.executable,"-m","unittest","discover","-s","tests","-q"],
                          cwd=Path(__file__).resolve().parents[1],env=environment).returncode


if __name__=="__main__":raise SystemExit(main())
