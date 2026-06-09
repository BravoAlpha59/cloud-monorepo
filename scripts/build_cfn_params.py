"""Emit a CloudFormation parameters file from literals and JSON files.

`sam deploy --parameter-overrides` (and the aws-cli shorthand) corrupt
parameter values containing JSON punctuation — samcli's parser truncates
`{"a":"b","c":"d"}` down to `{`. The robust path is
`aws cloudformation deploy --parameter-overrides file://params.json`, where
each value is a JSON string and survives intact. This builds that file.

Usage:
    build_cfn_params.py KEY=literal KEY=@path/to/file.json ... > params.json

* ``KEY=literal``  — value is the literal string after the first ``=``.
* ``KEY=@file``    — value is the contents of *file*, which must be valid
  JSON; it is re-emitted compact (and thereby validated — a malformed
  staging file fails here, loudly, before anything is deployed).

Output is the canonical ``[{"ParameterKey": ..., "ParameterValue": ...}]``
array on stdout.
"""

import json
import sys
from pathlib import Path


def main(argv: list[str]) -> int:
    params = []
    for arg in argv:
        if "=" not in arg:
            print(f"error: expected KEY=value, got {arg!r}", file=sys.stderr)
            return 2
        key, rest = arg.split("=", 1)
        if rest.startswith("@"):
            raw = Path(rest[1:]).read_text()
            try:
                value = json.dumps(json.loads(raw), separators=(",", ":"))
            except json.JSONDecodeError as exc:
                print(f"error: {rest[1:]} is not valid JSON: {exc}", file=sys.stderr)
                return 2
        else:
            value = rest
        params.append({"ParameterKey": key, "ParameterValue": value})
    json.dump(params, sys.stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
