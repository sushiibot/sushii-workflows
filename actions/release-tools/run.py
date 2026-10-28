"""Run an explicitly supported release helper from this action checkout."""
import argparse
import runpy
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=['approval_preflight', 'release_intent', 'build_evidence', 'dispatch_importer', 'wait_receipt', 'build_identity', 'deployment_receipt', 'resolve_promotion'])
    command = parser.parse_args().command
    runpy.run_path(str(Path(__file__).resolve().parent / (command + ".py")), run_name="__main__")


if __name__ == "__main__":
    main()
