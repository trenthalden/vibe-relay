#!/usr/bin/env python3
"""Tiny fake executor for dispatch tests — sleep / write receipt / exit N / spawn grandchild."""
from __future__ import annotations

import argparse
import os
import signal
import sys
import time


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--task-file", required=True)
    p.add_argument("--receipt", default="")
    p.add_argument("--sleep", type=float, default=0.0)
    p.add_argument("--exit", type=int, default=0, dest="exit_code")
    p.add_argument("--write-receipt", action="store_true")
    p.add_argument("--spawn-grandchild", action="store_true")
    p.add_argument("--grandchild-sleep", type=float, default=30.0)
    args = p.parse_args(argv)

    # Prove TASK.md was delivered: copy a marker into the receipt when asked.
    task_text = ""
    with open(args.task_file, "r", encoding="utf-8") as fh:
        task_text = fh.read()

    # Evidence for supervisor output.log (stdout+stderr redirected by supervise.py).
    sys.stdout.write("fake-executor:start\n")
    sys.stdout.flush()

    child_pid = None
    if args.spawn_grandchild:
        pid = os.fork()
        if pid == 0:
            # Grandchild ignores TERM briefly so the supervisor must kill the group.
            signal.signal(signal.SIGTERM, signal.SIG_IGN)
            time.sleep(args.grandchild_sleep)
            os._exit(0)
        child_pid = pid
        # Record grandchild pid next to the task for the test to assert.
        marker = args.task_file + ".grandchild_pid"
        with open(marker, "w", encoding="utf-8") as fh:
            fh.write(str(child_pid))

    if args.sleep > 0:
        time.sleep(args.sleep)

    if args.write_receipt and args.receipt:
        with open(args.receipt, "w", encoding="utf-8") as fh:
            fh.write("STATUS: DONE\n")
            if "Project rules delivered by lean-orchestration" in task_text:
                fh.write("RULES_BLOCK: present\n")
            fh.write("TASK_BYTES: %d\n" % len(task_text.encode("utf-8")))

    return int(args.exit_code)


if __name__ == "__main__":
    sys.exit(main())
